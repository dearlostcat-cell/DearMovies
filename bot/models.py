from __future__ import annotations

import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Literal
from pydantic import BaseModel, Field


def uid(*parts: object) -> str:
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:20]


def normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(re.sub(r"[^\w\s]", " ", value).split())


def rank(query: str, title: str) -> float:
    q, t = normalized(query), normalized(title)
    if q == t: return 100
    if t.startswith(q + " "): return 90
    if f" {q} " in f" {t} ": return 80
    if q in t: return 70
    return SequenceMatcher(None, q, t).ratio() * 60


FILTERS = {"movie", "series", "anime", "4k", "2160p", "1080p", "720p", "480p", "hdr", "bluray", "web-dl", "remux"}


def split_query(query: str) -> tuple[str, list[str]]:
    # Quoted queries preserve words that would otherwise look like filters.
    if query.startswith('"') and query.endswith('"'):
        return query[1:-1].strip(), []
    words = query.split()
    filters = []
    while len(words) > 1 and words[-1].casefold() in FILTERS:
        filters.insert(0, words.pop().casefold())
    return " ".join(words), filters


def quality(text: str) -> str:
    if re.search(r"2160|\b4k\b", text, re.I): return "4K"
    m = re.search(r"\b(1080|720|576|480|360)(?:p|\b)", text, re.I)
    return m[1] + "p" if m else "Unknown"


def quality_order(value: str) -> int:
    return {"4K": 0, "1080p": 1, "720p": 2, "576p": 3, "480p": 4, "360p": 5}.get(value, 99)


class Reference(BaseModel):
    site: str
    url: str


class SearchResult(BaseModel):
    id: str
    title: str
    year: int | None = None
    kind: str = "unknown"
    poster: str = ""
    tags: str = ""
    refs: list[Reference] = Field(default_factory=list)


class Link(BaseModel):
    label: str
    url: str


class Variant(BaseModel):
    id: str
    filename: str
    collection_url: str = ""
    website: str = ""
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None
    episode_label: str = ""
    mode: Literal["movie", "pack", "episode"] = "movie"
    resolution: str = "Unknown"
    codec: str = "Unknown"
    source: str = "Unknown"
    format: str = "Unknown"
    languages: str = "Unknown"
    size: str = "Unknown"
    links: list[Link] = Field(default_factory=list)

    @property
    def version_label(self) -> str:
        return " / ".join(x for x in (self.source, self.format, self.codec) if x != "Unknown") or "Original"


class Screenshot(BaseModel):
    url: str
    original: str = ""


class WatchLink(BaseModel):
    label: str
    url: str
    group: str


class Media(SearchResult):
    screenshots: list[Screenshot] = Field(default_factory=list)
    watch_links: list[WatchLink] = Field(default_factory=list)
    tagline: str = ""
    description: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)
    variants: list[Variant] = Field(default_factory=list)


class Resolution(BaseModel):
    url: str
    host: str
    content_type: str = ""
    size: int | None = None
    temporary: str = "unknown"
    detected_container: str = "unknown"
    note: str = "Checked from the bot server; availability on your device may differ."


class FlowError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(message)


def merge_results(results: list[SearchResult], query: str) -> list[SearchResult]:
    merged: dict[tuple, SearchResult] = {}
    for item in results:
        # Unknown years are kept source-specific, avoiding accidental remake merges.
        identity = (normalized(item.title), item.year, item.kind)
        if item.year is None:
            identity += (item.refs[0].site, item.refs[0].url)
        if identity not in merged:
            merged[identity] = item.model_copy(deep=True)
        else:
            old = merged[identity]
            old.refs += [r for r in item.refs if r not in old.refs]
            old.poster = old.poster or item.poster
            old.tags += " " + item.tags
    return sorted(merged.values(), key=lambda x: (-rank(query, x.title), normalized(x.title), x.year or 0, x.id))
