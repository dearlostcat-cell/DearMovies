from __future__ import annotations

from html import escape
from datetime import datetime
from .models import Media, Variant, quality_order


def matching(media: Media, filters: dict) -> list[Variant]:
    values = media.variants
    for key, value in filters.items():
        if key == "version": values = [v for v in values if v.version_label == value]
        elif key == "file": values = [v for v in values if v.id == value]
        elif key == "episode_key": values = [v for v in values if episode_key(v) == value]
        else: values = [v for v in values if getattr(v, key) == value]
    return values


def episode_key(v):
    return f"{v.episode}:{v.episode_end}:{v.episode_label if v.episode is None else ''}"


def episode_label(v):
    if v.episode is None: return v.episode_label or "Special / unnumbered"
    return f"Episodes {v.episode}–{v.episode_end}" if v.episode_end else f"Episode {v.episode}"


def selection(media: Media, filters: dict, features=None):
    """Returns one actual choice; filters are advanced through singleton dimensions."""
    features = features or {}
    skipped = []
    for field in ["season", "mode", "resolution", "source", "languages", "version", "episode_key", "file"]:
        if field in filters: continue
        values = matching(media, filters)
        if not values: return None, [], skipped
        if field == "season" and media.kind == "movie": continue
        if field == "episode_key" and all(v.mode != "episode" for v in values): continue
        if field == "languages" and not features.get("language_menu", True): continue
        if field == "version" and not features.get("variant_menu", True): continue
        options = {}
        for variant in values:
            key = variant.version_label if field == "version" else variant.id if field == "file" else episode_key(variant) if field == "episode_key" else getattr(variant, field)
            label = str(key)
            if field == "season": label = f"Season {key}" if key is not None else "Unnumbered season"
            elif field == "mode": label = {"pack": "📦 ZIP / Pack", "episode": "🎞 Single episodes", "movie": "Movie"}[key]
            elif field == "episode_key": label = episode_label(variant)
            elif field == "file": label = f"{variant.version_label} • {variant.languages} • {variant.size}"
            options.setdefault(key, [label, []])[1].append(variant)
        if len(options) == 1:
            filters[field] = next(iter(options)); skipped.append(field); continue
        items = []
        for key, (label, variants) in options.items():
            if field in {"resolution", "version"} and all(v.mode != "episode" for v in variants):
                sizes = sorted(set(v.size for v in variants))
                label += " • " + (sizes[0] if len(sizes) == 1 else f"{len(variants)} versions")
            items.append({"value": key, "label": label})
        if field == "resolution": items.sort(key=lambda x: quality_order(x["value"]))
        elif field == "season": items.sort(key=lambda x: x["value"] if x["value"] is not None else 9999)
        elif field == "episode_key": items.sort(key=lambda x: (int(x["value"].split(":")[0]) if x["value"].split(":")[0] != "None" else 100000, x["label"]))
        else: items.sort(key=lambda x: x["label"])
        return field, items, skipped
    return "ready", [], skipped


def date_label(text):
    try: return datetime.fromisoformat(text).strftime("%d %B %Y")
    except ValueError: return text


def details_text(media: Media, features=None):
    features = features or {}
    lines = [f"🎬 <b>{escape(media.title)}{f' ({media.year})' if media.year else ''}</b>"]
    if media.tagline and features.get("tagline", True): lines += ["", "💬 " + escape(media.tagline)]
    if media.description and features.get("description", True): lines += ["", "📝 " + escape(media.description)]
    lines.append("")
    if media.metadata.get("director") and features.get("director", True): lines.append("🎥 Director: " + escape(media.metadata["director"]))
    audio = media.metadata.get("audios", "Unknown")
    lines.append("🔊 Audios: " + escape(audio))
    if media.kind in {"series", "anime"}:
        seasons = sorted({v.season for v in media.variants if v.season is not None})
        lines.append("📚 Seasons: " + ", ".join(map(str, seasons)))
    date = media.metadata.get("release" if media.kind == "movie" else "last_air", "")
    if date: lines.append(("📅 Release: " if media.kind == "movie" else "📅 Last air: ") + escape(date_label(date)))
    lines.append("📺 Quality: " + ", ".join(sorted({v.resolution for v in media.variants}, key=quality_order)))
    lines.append("💿 Source: " + escape(", ".join(sorted({v.source for v in media.variants}))))
    formats = sorted({x for v in media.variants for x in [v.format, v.codec] if x != "Unknown"})
    if formats: lines.append("✨ Formats: " + escape(", ".join(formats)))
    return "\n".join(lines)


def rows(items, max_label=24):
    result, row = [], []
    for item in items:
        if len(item["text"]) > max_label:
            if row: result.append(row); row = []
            result.append([item])
        else:
            row.append(item)
            if len(row) == 2: result.append(row); row = []
    if row: result.append(row)
    return result
