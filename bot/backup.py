import asyncio
import io
import json
import zipfile
from pathlib import Path


async def backup_bytes(store, root):
    records = await store.export()
    def make():
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("data-export.json", json.dumps({"schema": 1, "records": records}, ensure_ascii=False))
            for path in (root / "config").rglob("*.yaml"):
                archive.write(path, path.relative_to(root).as_posix())
        return buffer.getvalue()
    return await asyncio.to_thread(make)


async def restore(store, root, filename, confirmed=False):
    if not confirmed: raise ValueError("Restore requires --confirm. Stop the bot and create a backup first.")
    with zipfile.ZipFile(filename) as archive:
        if sum(x.file_size for x in archive.infolist()) > 20_000_000: raise ValueError("Backup exceeds size limit")
        payload = json.loads(archive.read("data-export.json"))
        if payload.get("schema") != 1: raise ValueError("Unsupported backup schema")
        config_files = []
        for name in archive.namelist():
            if name == "data-export.json": continue
            dest = (root / name).resolve()
            if not dest.is_relative_to((root / "config").resolve()) or dest.suffix != ".yaml": raise ValueError("Unsafe archive path")
            config_files.append((dest, archive.read(name)))
        # Validate all proposed configs in a separate directory before changing anything.
        import tempfile
        from .config import Registry
        with tempfile.TemporaryDirectory() as tmp:
            staging = Path(tmp)
            for dest, data in config_files:
                target = staging / dest.relative_to(root.resolve())
                target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(data)
            Registry(staging).load(atomic=True)
        for row in payload["records"]:
            if row["space"] in {"cache", "sessions", "inspect", "poster"}: continue
            await store.put(row["space"], row["key"], json.loads(row["value"]))
        for dest, data in config_files:
            dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes(data)
