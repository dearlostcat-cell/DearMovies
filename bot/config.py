from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
import yaml
import soupsieve
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).resolve().parent.parent


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Settings(Strict):
    owner_ids: list[int] = [8037733052]
    allowed_users: list[int] = []  # Restricted mode: empty permits staff only.
    restricted_access: bool = True
    results_per_page: int = Field(12, ge=2, le=30)
    episodes_per_page: int = Field(12, ge=2, le=30)
    requests_concurrency: int = Field(5, ge=1, le=20)
    active_jobs_per_user: int = Field(2, ge=1, le=5)
    max_active_jobs: int = Field(30, ge=1, le=200)
    searches_per_minute: int = Field(10, ge=1, le=100)
    search_ttl: int = 600
    detail_ttl: int = 1800
    session_ttl: int = 86400
    job_retention_days: int = 7
    snapshot_retention_days: int = 7
    snapshot_max_count: int = 100
    debug_snapshots: bool = False
    cooldown_failures: int = 5
    cooldown_seconds: int = 600
    max_job_seconds: int = Field(120, ge=5, le=600)
    maintenance: bool = False
    mock_mode: bool = False
    dry_run: bool = False
    browser_enabled: bool = False
    welcome_video: str = "assets/dear.mp4"
    channel_url: str = "https://t.me/DearWhyme"
    support_url: str = "https://t.me/DearMe_Chat"
    bot_url: str = "https://t.me/Lostmoviexbot"


class RequestConfig(Strict):
    timeout: int = Field(20, ge=1, le=60)
    retries: int = Field(2, ge=0, le=3)
    transport: Literal["http", "browser"] = "http"
    headers: dict[str, str] = Field(default_factory=dict)
    wait_selector: str | None = None
    max_redirects: int = Field(8, ge=0, le=16)


class SearchConfig(Strict):
    path: str = "/"
    parameter: str = "s"
    method: Literal["GET", "POST"] = "GET"
    container: str = "main"
    card: str = "a.movie-card"
    title: list[str] = [".movie-card-title"]
    poster: list[str] = ["img"]
    metadata: list[str] = [".movie-card-meta"]
    tags: list[str] = [".movie-card-formats", ".quality-badges"]
    link: str | None = None
    empty: list[str] = [".no-results"]
    next_page: list[str] = ["a.next.page-numbers"]
    max_pages: int = Field(3, ge=1, le=10)


class Selectors(Strict):
    title: list[str] = ["h1.page-title"]
    poster: list[str] = [".poster-image img"]
    description: list[str] = [".detail-description"]
    tagline: list[str] = [".movie-tagline"]
    metadata_item: str = ".metadata-item"
    metadata_label: str = ".metadata-label"
    metadata_value: str = ".metadata-value"
    movie_root: str = ".download-groups"
    pack_root: str = "#complete-pack"
    episodes_root: str = "#episodes"
    season_group: str = ".series-season-group"
    season_title: str = ".series-season-toggle strong"
    group: str = ".download-group"
    group_title: str = ".download-group-title"
    item: str = ".download-item"
    header: str = ".download-header"
    filename: str = ".file-title"
    episode: str = ".episode-download-item"
    episode_container: str = ".episode-item"
    episode_header: str = ".episode-header"
    episode_filename: str = ".episode-file-title"
    episode_number: str = ".badge-psa"
    episode_size: str = ".badge-size"
    links: str = "a[href]"


class Site(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    name: str
    version: str = "1"
    parser: Literal["html_catalog", "wordpress", "hdhub", "kuroiru", "rogmovies"] = "html_catalog"
    content_root: str = ".thecontent"
    screenshot_hosts: list[str] = Field(default_factory=list)
    image_base: str = "https://static.kuroiru.co"
    search_api: str = ""
    enabled: bool = True
    priority: int = 10
    base_url: str
    mirrors: list[str] = Field(default_factory=list)
    capabilities: dict[str, bool] = {"movies": True, "series": True, "anime": True, "packs": True, "episodes": True, "languages": True}
    request: RequestConfig = Field(default_factory=RequestConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    selectors: Selectors = Field(default_factory=Selectors)
    metadata_labels: dict[str, list[str]] = {
        "director": ["Director"], "stars": ["Stars"], "release": ["Release"],
        "last_air": ["Last Air"], "audios": ["Audios"], "seasons": ["Seasons"], "prints": ["Print", "Prints"]}
    providers: list[str] = Field(default_factory=list)
    provider_hosts: list[str] = Field(default_factory=list)
    fixtures: dict[str, str] = Field(default_factory=dict)
    normalization: dict[str, dict[str, str]] = Field(default_factory=dict)
    extract: dict[str, list[str]] = Field(default_factory=dict)
    transform: dict[str, list[str]] = Field(default_factory=dict)
    features: dict[str, bool] = {"search_cache": True, "detail_cache": True, "fallback": True, "language_menu": True, "variant_menu": True, "description": True, "tagline": True, "director": True}

    @field_validator("base_url", "mirrors")
    @classmethod
    def valid_urls(cls, value):
        for url in value if isinstance(value, list) else [value]:
            p = urlsplit(url)
            if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
                raise ValueError("Use an HTTP(S) URL without embedded credentials")
        return value

    @model_validator(mode="after")
    def validate_selectors(self):
        for rules in self.extract.values():
            for regex in rules: re.compile(regex)
        for field, transforms in self.transform.items():
            if field not in {"title", "description", "tagline"}: raise ValueError("Unsupported transform field")
            if set(transforms) - {"strip", "collapse_spaces", "html_unescape", "unicode_nfkc"}: raise ValueError("Unsupported safe transformation")
        data = self.selectors.model_dump()
        data.update({k: v for k, v in self.search.model_dump().items() if k in {"container", "card", "title", "poster", "metadata", "tags", "link", "empty", "next_page"}})
        for value in data.values():
            for selector in value if isinstance(value, list) else [value]:
                if selector: soupsieve.compile(selector)
        return self


class Extract(Strict):
    selector: str | None = None
    attribute: str = "href"
    json_path: str | None = None
    regex: str | None = None
    required: bool = True


class Step(Strict):
    id: str
    action: Literal["fetch", "click", "form", "validate", "dispatch", "wait"] = "fetch"
    transport: Literal["http", "browser"] = "http"
    url_from: str = "current_url"
    method: Literal["GET", "POST"] = "GET"
    form: dict[str, str] = Field(default_factory=dict)
    selector: str | None = None
    wait_selector: str | None = None
    wait_seconds: float = Field(0, ge=0, le=20)
    extract: dict[str, Extract] = Field(default_factory=dict)
    candidates: list[str] = Field(default_factory=list)
    next: str | None = None


class Provider(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]+$")
    version: str = "1"
    hosts: list[str]
    path_pattern: str = ".*"
    allowed_hosts: list[str]
    mode: Literal["links", "steps"] = "links"
    request: RequestConfig = Field(default_factory=RequestConfig)
    link_selectors: list[str] = Field(default_factory=list)
    literal_url_patterns: list[str] = Field(default_factory=list)
    query_url_parameters: list[str] = Field(default_factory=list)
    steps: list[Step] = Field(default_factory=list)
    max_steps: int = Field(20, ge=1, le=40)
    max_hops: int = Field(16, ge=1, le=32)
    fallback_on: list[str] = ["TIMEOUT", "UNAVAILABLE", "INVALID_RESPONSE", "UNSUPPORTED", "LINK_EXPIRED", "ROUTE_REVISITED", "REDIRECT_LOOP"]

    @model_validator(mode="after")
    def validate_flow(self):
        re.compile(self.path_pattern)
        for selector in self.link_selectors: soupsieve.compile(selector)
        for regex in self.literal_url_patterns: re.compile(regex)
        ids = [s.id for s in self.steps]
        if len(ids) != len(set(ids)): raise ValueError("Duplicate step ID")
        if self.mode == "steps" and not self.steps: raise ValueError("Steps mode needs steps")
        for step in self.steps:
            if step.next and step.next not in ids: raise ValueError("Unknown next step")
            for selector in [step.selector, step.wait_selector]:
                if selector: soupsieve.compile(selector)
            for rule in step.extract.values():
                if rule.selector: soupsieve.compile(rule.selector)
                if rule.regex: re.compile(rule.regex)
        return self


def deep_merge(base: dict, update: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in update.items():
        result[key] = deep_merge(result[key], value) if isinstance(value, dict) and isinstance(result.get(key), dict) else value
    return result


REMOVED_SITE_IDS = frozenset({'moviesmod', 'moviesleech'})


class Registry:
    def __init__(self, root: Path = ROOT):
        self.root = root
        self.settings = Settings()
        self.sites: dict[str, Site] = {}
        self.providers: dict[str, Provider] = {}
        self.errors: dict[str, str] = {}

    def read(self, path: Path) -> dict:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(value, dict): raise ValueError(f"{path.name}: expected mapping")
        return value

    def load(self, atomic: bool = False):
        overrides = {}
        for path in sorted((self.root / "config/overrides").glob("*.yaml")):
            overrides = deep_merge(overrides, self.read(path))
        raw = deep_merge(self.read(self.root / "config/settings.yaml"), overrides.get("settings", {}))
        for key in ("mock_mode", "dry_run", "browser_enabled"):
            if key.upper() in os.environ: raw[key] = os.environ[key.upper()].lower() == "true"
        if os.getenv("OWNER_IDS"): raw["owner_ids"] = [int(x.strip()) for x in os.environ["OWNER_IDS"].split(",")]
        settings = Settings.model_validate(raw)
        errors, providers, sites = {}, {}, {}
        for folder, model, dest in [("providers", Provider, providers), ("sites", Site, sites)]:
            for path in sorted((self.root / "config" / folder).glob("*.yaml")):
                if path.name.startswith("_"): continue
                try:
                    data = self.read(path)
                    if folder == 'sites' and data.get('id') in REMOVED_SITE_IDS: continue
                    data = deep_merge(data, overrides.get(folder, {}).get(data.get("id"), {}))
                    obj = model.model_validate(data)
                    if obj.id in dest: raise ValueError("Duplicate ID")
                    dest[obj.id] = obj
                except Exception as e:
                    errors[path.name] = str(e)
        for key, site in list(sites.items()):
            if any(p not in providers for p in site.providers):
                errors[key] = "Unknown provider reference"
                del sites[key]
        if atomic and errors: raise ValueError(str(errors))
        if not sites: raise ValueError("No valid sites: " + str(errors))
        self.settings, self.sites, self.providers, self.errors = settings, sites, providers, errors

    def fixture(self, site: Site, key: str) -> str:
        path = (self.root / site.fixtures[key]).resolve()
        if not path.is_relative_to(self.root.resolve()): raise ValueError("Fixture must be inside project")
        return path.read_text(encoding="utf-8")
