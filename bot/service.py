from __future__ import annotations

import asyncio
import time
from pathlib import Path
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from .config import Registry
from .models import FlowError, Media, SearchResult, merge_results, split_query, uid
from .network import Browser, Network, reject_blocked
from .parser import parse_media, parse_search, soup
from .storage import redact


class SharedRequests:
    def __init__(self): self.entries = {}

    async def get(self, key, factory):
        if key not in self.entries:
            task = asyncio.create_task(factory())
            self.entries[key] = [task, 0]
        entry = self.entries[key]
        entry[1] += 1
        try: return await asyncio.shield(entry[0])
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                if not entry[0].done(): entry[0].cancel()
                if self.entries.get(key) is entry: self.entries.pop(key, None)
                # Retrieve cancellation/failure to avoid orphaned-task warnings.
                await asyncio.gather(entry[0], return_exceptions=True)


class Catalog:
    def __init__(self, registry: Registry, network: Network, store):
        self.registry, self.network, self.store = registry, network, store
        self.shared = SharedRequests()
        self.health_lock = asyncio.Lock()

    async def enabled(self, site):
        return site.enabled and await self.store.get("site_enabled", site.id, True)

    async def health(self, site_id):
        return await self.store.get("health", site_id, {"success": 0, "failed": 0, "consecutive": 0, "latency": 0, "cooldown_until": 0})

    async def record_health(self, site_id, ok, latency, error_code=None):
        async with self.health_lock:
            state = await self.health(site_id)
            state["success" if ok else "failed"] += 1
            transient = error_code in {None, 'BLOCKED', 'TIMEOUT', 'UNAVAILABLE'}
            state["consecutive"] = 0 if ok else state["consecutive"] + int(transient)
            state["last_success" if ok else "last_failure"] = time.time()
            state["latency"] = round(latency, 3) if not state["latency"] else round(state["latency"] * .8 + latency * .2, 3)
            if ok: state["cooldown_until"] = 0
            elif transient and state["consecutive"] >= self.registry.settings.cooldown_failures:
                state["cooldown_until"] = time.time() + self.registry.settings.cooldown_seconds
            await self.store.put("health", site_id, state)

    async def snapshot(self, html, site, stage, job):
        if not self.registry.settings.debug_snapshots: return
        doc = soup(html)
        for node in doc.select("script, style, input, textarea, meta, link"): node.decompose()
        for node in doc.find_all(True):
            node.attrs = {k: (redact(str(v)) if k in {"href", "src", "action"} else v) for k, v in node.attrs.items() if k in {"class", "id", "href", "src", "action"}}
        folder = self.registry.root / "data/snapshots"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{int(time.time())}-{site}-{job}-{stage}.html"
        await asyncio.to_thread(path.write_text, str(doc), encoding="utf-8")

    async def fetch_site(self, url, site):
        allowed = [urlsplit(x).hostname for x in [site.base_url, *site.mirrors]]
        if site.request.transport == "browser":
            if not self.registry.settings.browser_enabled: raise FlowError("BROWSER_REQUIRED", "Website requires browser mode")
            async with Browser(allowed, self.network) as browser:
                response = await browser.fetch(url, timeout=site.request.timeout, wait_selector=site.request.wait_selector, headers=site.request.headers)
        else:
            async with self.network.session() as session:
                for attempt in range(site.request.retries + 1):
                    try:
                        response = await self.network.fetch(session, url, allowed, timeout=site.request.timeout, headers=site.request.headers, max_redirects=site.request.max_redirects)
                        reject_blocked(response)
                        break
                    except FlowError as e:
                        if e.code not in {"TIMEOUT", "UNAVAILABLE"} or attempt == site.request.retries: raise
                        await asyncio.sleep(2 ** attempt)
        reject_blocked(response)
        if response.truncated: raise FlowError("INVALID_RESPONSE", "Catalogue page exceeds the configured response-size budget")
        return response.text, response.url

    async def search_one(self, site, query, job, user):
        settings = self.registry.settings
        key = uid("search", site.id, site.version, site.model_dump_json(), query.casefold(), settings.mock_mode)
        cached = await self.store.get("cache", key) if site.features.get("search_cache", True) else None
        if cached is not None:
            await self.store.log(job, user, "CACHE_HIT", site=site.id, stage="search")
            return [SearchResult.model_validate(x) for x in cached]

        async def load():
            start = time.monotonic()
            all_results, last_error = [], None
            if site.parser in {"hdhub", "rogmovies"} and site.search_api and not settings.mock_mode:
                from .hdsearch import search
                try:
                    all_results=await search(self.network,site,query)
                    await self.record_health(site.id,True,time.monotonic()-start)
                    await self.store.put("cache",key,[x.model_dump() for x in all_results],settings.search_ttl)
                    return all_results
                except FlowError as error:
                    await self.record_health(site.id,False,time.monotonic()-start,error.code)
                    await self.store.log(job,user,'SITE_FAILED',site=site.id,stage='search',reason=error.code,message=error.message)
                    raise
            for base in [site.base_url, *site.mirrors]:
                url = urljoin(base, site.search.path)
                url += ("&" if "?" in url else "?") + urlencode({site.search.parameter: query})
                seen = set()
                try:
                    for page in range(site.search.max_pages):
                        if url in seen: break
                        seen.add(url)
                        if settings.mock_mode:
                            html, final = self.registry.fixture(site, "search"), url
                        elif site.search.method == "POST" and page == 0:
                            async with self.network.session() as session:
                                response = await self.network.fetch(session, urljoin(base, site.search.path), [urlsplit(x).hostname for x in [site.base_url, *site.mirrors]], method="POST", data={site.search.parameter: query}, timeout=site.request.timeout, headers=site.request.headers)
                                reject_blocked(response)
                                html, final = response.text, response.url
                        else: html, final = await self.fetch_site(url, site)
                        try: results, next_url = parse_search(html, final, site)
                        except FlowError:
                            await self.snapshot(html, site.id, "search", job)
                            raise
                        all_results.extend(results)
                        if not next_url or settings.mock_mode: break
                        url = next_url
                    await self.record_health(site.id, True, time.monotonic() - start)
                    if site.features.get("search_cache", True): await self.store.put("cache", key, [x.model_dump() for x in all_results], settings.search_ttl)
                    await self.store.log(job, user, "SEARCH_SITE_SUCCESS", site=site.id, count=len(all_results), pages=len(seen), latency=round(time.monotonic() - start, 3))
                    return all_results
                except FlowError as e:
                    last_error = e
                    await self.store.log(job, user, "SITE_FAILED", site=site.id, reason=e.code, message=e.message)
                    if e.code == "BLOCKED": break
            await self.record_health(site.id, False, time.monotonic() - start, last_error.code if last_error else 'UNAVAILABLE')
            raise last_error or FlowError("UNAVAILABLE", "Site unavailable")
        return await self.shared.get(key, load)

    async def search(self, text, job, user):
        query, filters = split_query(text)
        if not query or len(query) > 200: raise FlowError("INPUT", "Use a search name between 1 and 200 characters")
        sites, omitted = [], []
        for site in self.registry.sites.values():
            if site.parser == "kuroiru": continue
            if not await self.enabled(site): continue
            if (await self.health(site.id))["cooldown_until"] > time.time(): omitted.append(site.name); continue
            sites.append(site)
        if not sites: raise FlowError("UNAVAILABLE", "No enabled website is available; please try later")
        answers = await asyncio.gather(*(self.search_one(site, query, job, user) for site in sites), return_exceptions=True)
        results, failed, succeeded = [], list(omitted), 0
        for site, answer in zip(sites, answers):
            if isinstance(answer, BaseException): failed.append(site.name)
            else: results += answer; succeeded += 1
        if not succeeded: raise FlowError("UNAVAILABLE", "All selected websites failed; the owner can inspect this job")
        merged = merge_results(results, query)
        selected = []
        for item in merged:
            if self.registry.settings.mock_mode and query.casefold() not in item.title.casefold(): continue
            blob = (item.tags + " " + item.kind).casefold()
            accepted = True
            for term in filters:
                if term in {"4k", "2160p"}: accepted &= "2160" in blob or "4k" in blob
                elif term in {"movie", "series", "anime"}: accepted &= item.kind == term or (term == "series" and item.kind == "anime")
                else: accepted &= term in blob
            if accepted: selected.append(item)
        await self.store.log(job, user, "SEARCH_COMPLETE", count=len(selected), partial_sites=failed, filters=filters)
        return selected, failed

    async def anime_search(self, query, job, user):
        from .anime import search
        if not query or len(query)>200: raise FlowError("INPUT","Use an anime name between 1 and 200 characters")
        site=self.registry.sites["kuroiru"]
        if not await self.enabled(site): raise FlowError("UNAVAILABLE","Anime source is disabled")
        key=uid("anime-search",site.model_dump_json(),query)
        cached=await self.store.get("cache",key)
        if cached is not None:return [SearchResult.model_validate(x) for x in cached],[]
        result=await self.shared.get(key,lambda:search(self.network,site,query))
        await self.store.put("cache",key,[x.model_dump() for x in result],self.registry.settings.search_ttl)
        return result,[]

    async def expand_collection(self,media,parent):
        if self.registry.sites[media.refs[0].site].parser == 'rogmovies':
            from .rogmovies import expand_collection
            return await expand_collection(self, media, parent)
        from .source_parsers import episode_collection
        from .network import validate_url
        site=self.registry.sites[media.refs[0].site]
        if not parent.collection_url:raise FlowError("INPUT","Not an episode collection")
        validate_url(parent.collection_url,site.provider_hosts)
        async with self.network.session() as session:
            response=await self.network.fetch(session,parent.collection_url,site.provider_hosts,max_bytes=1500000)
            reject_blocked(response)
        return episode_collection(response.text,response.url,parent,site)

    async def details(self, result: SearchResult, job, user):
        result = result.model_copy(update={'refs': [ref for ref in result.refs if ref.site in self.registry.sites]})
        if not result.refs: raise FlowError('UNAVAILABLE', 'This source has been removed. Start a new search.')
        if result.refs and self.registry.sites[result.refs[0].site].parser == "kuroiru":
            from .anime import details
            site=self.registry.sites[result.refs[0].site]
            if not await self.enabled(site):raise FlowError("UNAVAILABLE","Anime source is disabled")
            key=uid("anime-detail",site.model_dump_json(),result.id)
            cached=await self.store.get("cache",key)
            if cached:return Media.model_validate(cached)
            media=await self.shared.get(key,lambda:details(self.network,site,result))
            await self.store.put("cache",key,media.model_dump(),self.registry.settings.detail_ttl)
            return media

        async def score(ref):
            site = self.registry.sites.get(ref.site)
            if not site or not await self.enabled(site): return 1e9
            health = await self.health(ref.site)
            return site.priority + health["consecutive"] * 20 + health["latency"] + (10000 if health["cooldown_until"] > time.time() else 0)
        refs = sorted(zip(result.refs, await asyncio.gather(*(score(r) for r in result.refs))), key=lambda x: x[1])
        last = None
        for ref, value in refs:
            if value >= 10000: continue
            site = self.registry.sites[ref.site]
            key = uid("details", site.id, site.model_dump_json(), ref.url, self.registry.settings.mock_mode)
            cached = await self.store.get("cache", key) if site.features.get("detail_cache", True) else None
            if cached:
                await self.store.log(job, user, "CACHE_HIT", site=site.id, stage="details")
                media = Media.model_validate(cached)
                media.id, media.refs = result.id, [ref, *[r for r in result.refs if r != ref]]
                return media
            async def load():
                begin = time.monotonic()
                try:
                    if self.registry.settings.mock_mode:
                        html, final = self.registry.fixture(site, "series" if result.kind in {"series", "anime"} else "movie"), ref.url
                    else:
                        last_fetch_error = None
                        for base in [site.base_url, *site.mirrors]:
                            parsed = urlsplit(ref.url); target = urlsplit(base)
                            try:
                                html, final = await self.fetch_site(urlunsplit((target.scheme, target.netloc, parsed.path, parsed.query, "")), site)
                                break
                            except FlowError as error:
                                last_fetch_error = error
                                if error.code == "BLOCKED": raise
                        else: raise last_fetch_error
                    try: media = parse_media(html, final, site, result)
                    except FlowError:
                        await self.snapshot(html, site.id, "details", job)
                        raise
                    if site.features.get("detail_cache", True): await self.store.put("cache", key, media.model_dump(), self.registry.settings.detail_ttl)
                    await self.record_health(site.id, True, time.monotonic() - begin)
                    await self.store.put("inspect", job, redact(media.model_dump()), 86400)
                    await self.store.log(job, user, "DETAIL_SUCCESS", site=site.id, variants=len(media.variants), config=site.version)
                    return media
                except FlowError as error:
                    await self.record_health(site.id, False, time.monotonic() - begin, error.code)
                    raise
            try:
                media = (await self.shared.get(key, load)).model_copy(deep=True)
                media.id, media.refs = result.id, [ref, *[r for r in result.refs if r != ref]]
                return media
            except FlowError as e:
                last = e
                await self.store.log(job, user, "SITE_FAILED", site=site.id, stage='details', title=result.title, reason=e.code, message=e.message)
                if e.code == "BLOCKED" or not site.features.get("fallback", True): raise
                await self.store.log(job, user, "SITE_FALLBACK", site=site.id)
        raise last or FlowError("UNAVAILABLE", "No source is currently available for this title")
