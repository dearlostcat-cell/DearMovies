from __future__ import annotations

import re
import html as html_module
import unicodedata
from urllib.parse import urljoin, urlsplit
from bs4 import BeautifulSoup, Tag
import soupsieve
from .config import Site
from .models import FlowError, Link, Media, Reference, SearchResult, Variant, quality, uid
from .network import host_matches


def soup(html): return BeautifulSoup(html, "lxml")


def first(root, selectors, attribute=None):
    for selector in selectors if isinstance(selectors, list) else [selectors]:
        node = root.select_one(selector)
        if node:
            value = node.get(attribute, "") if attribute else node.get_text(" ", strip=True)
            if value: return str(value).strip()
    return ""


def release_year(text):
    m = re.search(r"\b((?:19|20)\d{2})\b", text)
    return int(m[1]) if m else None


def transform(text, field, site):
    for operation in site.transform.get(field, ["strip", "collapse_spaces", "html_unescape"]):
        if operation == "strip": text = text.strip()
        elif operation == "collapse_spaces": text = " ".join(text.split())
        elif operation == "html_unescape": text = html_module.unescape(text)
        elif operation == "unicode_nfkc": text = unicodedata.normalize("NFKC", text)
    return text


def extract_number(text, field, site, default=None):
    for pattern in site.extract.get(field, []):
        match = re.search(pattern, text, re.I)
        if match and match.lastindex and match[1].isdigit(): return int(match[1])
    return default


def parse_search(html: str, url: str, site: Site):
    doc = soup(html)
    root = doc.select_one(site.search.container)
    if not root: raise FlowError("PARSER_CHANGED", "Search results container is missing")
    cards = root.select(site.search.card)
    if not cards and not any(root.select_one(x) for x in site.search.empty):
        text = root.get_text(" ", strip=True).lower()
        if not any(x in text for x in ("no results", "nothing found", "no movies", "no posts", "not found")):
            raise FlowError("PARSER_CHANGED", "Search returned no cards and no recognized empty-result marker")
    results = []
    for card in cards:
        title = transform(first(card, site.search.title), "title", site)
        anchor = card if card.name == 'a' else card.select_one(site.search.link) if site.search.link else card
        href = anchor.get("href", "") if anchor else ""
        if not title or not href: raise FlowError("PARSER_CHANGED", "Search card is missing a title or link")
        href = urljoin(url, href)
        if urlsplit(href).hostname not in {urlsplit(x).hostname for x in [url, site.base_url, *site.mirrors]}: continue
        meta = first(card, site.search.metadata)
        tags = " ".join(first(card, [x]) for x in site.search.tags)
        kind = "series" if re.search(r"series|\bS\d", href + " " + meta + " " + tags, re.I) else "movie"
        if "anime" in tags.casefold(): kind = "anime"
        if not site.capabilities.get({"movie": "movies", "series": "series", "anime": "anime"}[kind], True): continue
        year = extract_number(meta, "year", site, release_year(meta))
        if site.parser in {"wordpress", "hdhub", "rogmovies"}:
            from .source_parsers import clean_title
            tags = title + " " + tags
            kind = "series" if re.search(r"season|series|S\d+", title, re.I) else "movie"
            year = release_year(title)
            title = clean_title(title)
        results.append(SearchResult(id=uid(site.id, href), title=title, year=year, kind=kind, poster=urljoin(url, first(card, site.search.poster, "src")) if first(card, site.search.poster, "src") else "", tags=tags, refs=[Reference(site=site.id, url=href)]))
    next_url = first(root, site.search.next_page, "href") or first(doc, site.search.next_page, "href")
    return results, urljoin(url, next_url) if next_url else None


LANGUAGES = ["Hindi", "English", "Thai", "Japanese", "Korean", "Tamil", "Telugu", "Malayalam", "Kannada", "Bengali", "Punjabi", "Spanish", "French", "German", "Chinese", "Mandarin", "Cantonese", "Italian", "Russian", "Arabic"]


def file_properties(text: str):
    codec = "AV1" if re.search(r"\bAV1\b", text, re.I) else "H.265" if re.search(r"H[. ]?265|x265|HEVC", text, re.I) else "H.264" if re.search(r"H[. ]?264|x264|AVC", text, re.I) else "Unknown"
    source = next((x for x, pattern in [("BluRay REMUX", r"(?:Blu.?Ray.*REMUX|REMUX.*Blu.?Ray)"), ("REMUX", r"REMUX"), ("BluRay", r"Blu.?Ray"), ("WEB-DL", r"WEB[ .-]?DL"), ("WEBRip", r"WEB[ .-]?Rip"), ("HDTV", r"HDTV")] if re.search(pattern, text, re.I)), "Unknown")
    formats = []
    if re.search(r"\bDV\b|Dolby.?Vision", text, re.I): formats.append("DV")
    if re.search(r"HDR10\+", text, re.I): formats.append("HDR10+")
    elif re.search(r"HDR10", text, re.I): formats.append("HDR10")
    elif re.search(r"HDR", text, re.I): formats.append("HDR")
    if re.search(r"\bSDR\b", text, re.I): formats.append("SDR")
    langs = [lang for lang in LANGUAGES if re.search(r"\b" + lang + r"\b", text, re.I)]
    return dict(resolution=quality(text), codec=codec, source=source, format=" ".join(formats) or "Unknown", languages=", ".join(langs) or "Unknown")


def episode_identity(filename, label=""):
    m = re.search(r"S(\d{1,3})[ ._-]*E(\d{1,4})(?:(?:[- ]?E|[-])(\d{1,4}))?", filename, re.I)
    if m: return int(m[1]), int(m[2]), int(m[3]) if m[3] else None
    season = re.search(r"(?:\bS|Season[ ._-]*)(\d{1,3})", filename, re.I)
    ep = re.search(r"(?:Episode[ ._-]*|\bE)(\d{1,4})(?:\s*[-–]\s*(\d{1,4}))?", label or filename, re.I)
    return int(season[1]) if season else None, int(ep[1]) if ep else None, int(ep[2]) if ep and ep[2] else None


def parse_media(html: str, url: str, site: Site, result: SearchResult):
    if site.parser == "rogmovies":
        from .rogmovies import parse_detail
        return parse_detail(html, url, site, result)
    if site.parser in {"wordpress", "hdhub"}:
        from .source_parsers import article_media
        return article_media(html, url, site, result)
    doc, sel = soup(html), site.selectors
    title = first(doc, sel.title)
    if not title: raise FlowError("PARSER_CHANGED", "Detail title selector did not match")
    media = Media(**result.model_dump())
    year_match = re.search(r"\((\d{4})\)\s*$", title)
    if year_match: media.year = int(year_match[1]); title = title[:year_match.start()].strip()
    media.title = transform(title, "title", site)
    poster = first(doc, sel.poster, "src")
    if poster: media.poster = urljoin(url, poster)
    media.description = transform(first(doc, sel.description), "description", site)
    media.tagline = transform(first(doc, sel.tagline), "tagline", site)
    for item in doc.select(sel.metadata_item):
        label = first(item, sel.metadata_label).rstrip(":").strip().casefold()
        value = first(item, sel.metadata_value)
        for key, labels in site.metadata_labels.items():
            if label in [x.rstrip(":").strip().casefold() for x in labels]: media.metadata[key] = value
    is_series = bool(doc.select_one(sel.pack_root) or doc.select_one(sel.episodes_root))
    if is_series and media.kind != "anime": media.kind = "series"
    if not site.capabilities.get({"movie": "movies", "series": "series", "anime": "anime"}[media.kind], True):
        raise FlowError("UNSUPPORTED", "This content type is disabled for the source")
    if not site.capabilities.get("languages", True): media.metadata.pop("audios", None)
    if media.year is None and media.kind == "movie": media.year = release_year(media.metadata.get("release", ""))
    variants = []

    def parse_item(item, mode, filename_selector):
        filename = first(item, filename_selector)
        if not filename: return
        group = item.find_parent(lambda x: isinstance(x, Tag) and soupsieve.match(sel.group, x))
        # The per-version header is more specific than the resolution group.
        ep_parent = item.find_parent(lambda x: isinstance(x, Tag) and soupsieve.match(sel.episode_container, x))
        header_text = first(item, sel.header) if mode != "episode" else first(ep_parent or item, sel.episode_header)
        group_text = first(group, sel.group_title) if group else ""
        props = file_properties(header_text)
        fallback = file_properties(filename + " " + group_text)
        props = {key: value if value != "Unknown" else fallback[key] for key, value in props.items()}
        if not site.capabilities.get("languages", True): props["languages"] = "Unknown"
        season, episode, end = episode_identity(filename, first(item, sel.episode_number))
        season = extract_number(filename, "season", site, season)
        episode = extract_number(filename, "episode", site, episode)
        for field, mapping in site.normalization.items():
            if field == "quality": field = "resolution"
            if field in props: props[field] = mapping.get(props[field], props[field])
        if season is None:
            section = item.find_parent(lambda x: isinstance(x, Tag) and soupsieve.match(sel.season_group, x))
            match = re.search(r"\d+", first(section, sel.season_title)) if section else None
            season = int(match[0]) if match else None
        size = first(item, sel.episode_size) if mode == "episode" else ""
        if not size:
            sizes = re.findall(r"\b\d+(?:\.\d+)?\s*(?:TB|GB|MB|KB)\b", header_text, re.I)
            size = sizes[0] if sizes else "Unknown"
        links = []
        for anchor in item.select(sel.links):
            target = urljoin(url, anchor.get("href", ""))
            if urlsplit(target).scheme not in {"http", "https"}: continue
            if site.provider_hosts and not host_matches(urlsplit(target).hostname or "", site.provider_hosts): continue
            if target not in [x.url for x in links]: links.append(Link(label=anchor.get_text(" ", strip=True) or "Download", url=target))
        if not links: return
        variants.append(Variant(id=uid(site.id, filename, season, episode, mode), filename=filename, mode=mode, season=season, episode=episode, episode_end=end, episode_label=first(item, sel.episode_number) if mode == "episode" else "", size=size, links=links, **props))

    if is_series:
        packs = doc.select_one(sel.pack_root)
        episodes = doc.select_one(sel.episodes_root)
        if packs and site.capabilities.get("packs", False):
            for item in packs.select(sel.item): parse_item(item, "pack", sel.filename)
        if episodes and site.capabilities.get("episodes", False):
            for item in episodes.select(sel.episode): parse_item(item, "episode", sel.episode_filename)
    elif site.capabilities.get("movies", False):
        for root in doc.select(sel.movie_root):
            for item in root.select(sel.item): parse_item(item, "movie", sel.filename)
    media.variants = list({v.id: v for v in variants}.values())
    if not media.variants: raise FlowError("PARSER_CHANGED", "No downloadable entries matched the configured structure")
    return media
