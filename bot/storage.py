from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit
from sqlalchemy import Column, Float, Integer, MetaData, String, Table, Text, create_engine, delete, insert, select, update, event


def redact(value):
    if isinstance(value, dict):
        return {k: ("[redacted]" if re.search(r"token|cookie|secret|authorization|password", k, re.I) else redact(v)) for k, v in value.items()}
    if isinstance(value, list): return [redact(x) for x in value]
    if not isinstance(value, str): return value
    value = re.sub(r"\b\d{6,12}:[A-Za-z0-9_-]{25,}\b", "[bot-token]", value)
    # Keep hosts only: tokens can be in paths as well as query parameters.
    value = re.sub(r"https?://[^\s<>\"']+", lambda m: f"https://{urlsplit(m[0]).hostname or 'redacted'}/[path-redacted]", value)
    return value[:8000]


class Store:
    def __init__(self, url: str | None = None):
        url = url or os.getenv("DATABASE_URL") or "sqlite:///data/bot.db"
        if url.startswith("postgres://"): url = "postgresql+psycopg://" + url[len("postgres://"):]
        elif url.startswith("postgresql://"): url = "postgresql+psycopg://" + url[len("postgresql://"):]
        self.durable = not url.startswith("sqlite")
        if url.startswith("sqlite:/// "): raise ValueError("Invalid database path")
        if url.startswith("sqlite"):
            Path("data").mkdir(exist_ok=True)
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def pragmas(dbapi, record):
                dbapi.execute("PRAGMA journal_mode=WAL")
                dbapi.execute("PRAGMA busy_timeout=10000")
        meta = MetaData()
        self.kv = Table("bot_state", meta, Column("space", String(80), primary_key=True), Column("key", String(250), primary_key=True), Column("value", Text), Column("expires", Float, index=True))
        self.events = Table("bot_events", meta, Column("id", Integer, primary_key=True), Column("timestamp", Float, index=True), Column("job", String(30), index=True), Column("user_id", String(30), index=True), Column("site", String(100), index=True), Column("provider", String(100), index=True), Column("code", String(80), index=True), Column("payload", Text))
        meta.create_all(self.engine)
        self.write_lock = asyncio.Lock()

    async def get(self, space, key, default=None):
        def run():
            with self.engine.connect() as conn:
                row = conn.execute(select(self.kv).where(self.kv.c.space == space, self.kv.c.key == str(key))).mappings().first()
                return json.loads(row["value"]) if row and (not row["expires"] or row["expires"] > time.time()) else default
        return await asyncio.to_thread(run)

    async def put(self, space, key, value, ttl=0):
        def run():
            with self.engine.begin() as conn:
                condition = (self.kv.c.space == space) & (self.kv.c.key == str(key))
                data = {"value": json.dumps(value, ensure_ascii=False), "expires": time.time() + ttl if ttl else 0}
                found = conn.execute(select(self.kv.c.key).where(condition)).first()
                if found: conn.execute(update(self.kv).where(condition).values(**data))
                else: conn.execute(insert(self.kv).values(space=space, key=str(key), **data))
        async with self.write_lock: await asyncio.to_thread(run)

    async def remove(self, space, key):
        def run():
            with self.engine.begin() as conn: conn.execute(delete(self.kv).where(self.kv.c.space == space, self.kv.c.key == str(key)))
        async with self.write_lock: await asyncio.to_thread(run)

    async def list(self, space, limit=100):
        def run():
            with self.engine.connect() as conn:
                rows = conn.execute(select(self.kv).where(self.kv.c.space == space).limit(limit)).mappings()
                return [(r["key"], json.loads(r["value"])) for r in rows if not r["expires"] or r["expires"] > time.time()]
        return await asyncio.to_thread(run)

    async def log(self, job, user, code, site="", provider="", **payload):
        def run():
            with self.engine.begin() as conn:
                conn.execute(insert(self.events).values(timestamp=time.time(), job=job, user_id=str(user), code=code, site=site, provider=provider, payload=json.dumps(redact(payload), ensure_ascii=False)))
        await asyncio.to_thread(run)

    async def logs(self, limit=30, **filters):
        def run():
            query = select(self.events)
            for key, value in filters.items():
                if key == "since": query = query.where(self.events.c.timestamp >= value)
                elif key == "errors": query = query.where(self.events.c.code.in_(["JOB_FAILED", "PROVIDER_FAILED", "SITE_FAILED", "SITE_TEST_FAILED", "PARSER_CHANGED", "BLOCKED", "CONFIG_ERROR"]))
                elif key in {"job", "user_id", "site", "provider"}: query = query.where(self.events.c[key] == str(value))
            with self.engine.connect() as conn:
                return [dict(r) for r in conn.execute(query.order_by(self.events.c.id.desc()).limit(limit)).mappings()]
        return await asyncio.to_thread(run)

    async def cleanup(self, days=7):
        def run():
            with self.engine.begin() as conn:
                conn.execute(delete(self.kv).where(self.kv.c.expires > 0, self.kv.c.expires < time.time()))
                conn.execute(delete(self.events).where(self.events.c.timestamp < time.time() - days * 86400))
        await asyncio.to_thread(run)

    async def export(self):
        def run():
            with self.engine.connect() as conn:
                # Cache/session records may contain transient source tokens; exclude them.
                return [dict(r) for r in conn.execute(select(self.kv).where(~self.kv.c.space.in_(["cache", "sessions", "inspect", "poster", "site_tests", "site_test_latest"]))).mappings()]
        return await asyncio.to_thread(run)

    async def close(self): await asyncio.to_thread(self.engine.dispose)
