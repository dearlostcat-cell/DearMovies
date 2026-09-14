from __future__ import annotations

import asyncio
import html
import json
import re
import time
from contextlib import AsyncExitStack
from urllib.parse import parse_qs, urljoin, urlsplit
from .config import Registry, Provider
from .models import FlowError, Resolution, Variant, uid
from .network import Browser, Network, Response, host_matches, reject_blocked, validate_url
from .parser import soup
from .recovery import recovery_links, route_identity, save_host_proposal, authentication_host, excluded_route


def file_response(response: Response) -> Resolution | None:
    reject_blocked(response)
    content_type = response.headers.get("content-type", "").split(";")[0].lower()
    prefix = response.body[:1024].lstrip().lower()
    if "html" in content_type or any(x in prefix for x in (b"<!doctype html", b"<html", b"<head", b"<body")):
        return None
    detected = "zip" if response.body.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")) else "mkv" if response.body.startswith(b"\x1aE\xdf\xa3") else "mp4" if response.body[4:8] == b"ftyp" else "unknown"
    disposition = response.headers.get("content-disposition", "").lower()
    good_type = content_type.startswith(("video/", "audio/")) or content_type in {"application/octet-stream", "application/zip", "application/x-zip-compressed", "application/x-rar-compressed", "application/vnd.rar", "application/x-7z-compressed", "binary/octet-stream"}
    if not good_type and not ("attachment" in disposition and prefix.startswith((b"PK\x03\x04", b"\x1aE\xdf\xa3"))): return None
    if not response.body and response.headers.get("content-length") == "0":
        raise FlowError("INVALID_RESPONSE", "Download is empty")
    # A range response's Content-Length is just the probe size, not the whole file.
    total = response.headers.get("content-range", "").split("/")[-1]
    length = total if total.isdigit() else response.headers.get("content-length", "") if response.status != 206 else ""
    temporary = "likely" if re.search(r"[?&](?:exp|expires|expiry|sig|signature|token|auth|x-amz-signature)=", response.url, re.I) else "unknown"
    if "googleusercontent.com" in (urlsplit(response.url).hostname or ""): temporary = "likely"
    return Resolution(url=response.url, host=urlsplit(response.url).hostname or "", content_type=content_type, size=int(length) if length.isdigit() else None, temporary=temporary, detected_container=detected)


def validate_expected(result, variant):
    if not variant: return result
    extension = variant.filename.rsplit(".", 1)[-1].lower()
    if extension in {"zip", "mkv", "mp4"} and result.detected_container != extension:
        raise FlowError("INVALID_RESPONSE", "The returned file signature does not match the selected file type")
    size = re.search(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB|TB)", variant.size, re.I)
    if size and result.size:
        expected = float(size[1]) * 1000 ** {"KB": 1, "MB": 2, "GB": 3, "TB": 4}[size[2].upper()]
        if result.size < expected * .8 or result.size > expected * 1.2:
            raise FlowError("INVALID_RESPONSE", "Returned file size is inconsistent with the selected version")
    return result


class Resolver:
    @staticmethod
    def can_fallback(provider, error):
        # A blocked mirror must not prevent trying a different supported host.
        # Keep the original block if every alternative fails.
        return error.code in provider.fallback_on or (
            error.code == "BLOCKED" and provider.id in {"nexdrive", "fastdl", "vcloud", "hubdrive", "hubcloud", "generator"}
        )

    def __init__(self, registry: Registry, network: Network, store):
        self.registry, self.network, self.store = registry, network, store
        self.health_lock = asyncio.Lock()

    async def record_health(self, provider, success, elapsed):
        async with self.health_lock:
            state = await self.store.get("provider_health", provider, {"success": 0, "failed": 0, "consecutive": 0, "latency": 0, "cooldown_until": 0})
            state["success" if success else "failed"] += 1
            state["consecutive"] = 0 if success else state["consecutive"] + 1
            state["last_success" if success else "last_failure"] = time.time()
            state["latency"] = round(elapsed if not state["latency"] else state["latency"] * .8 + elapsed * .2, 3)
            if success: state["cooldown_until"] = 0
            elif state["consecutive"] >= self.registry.settings.cooldown_failures:
                state["cooldown_until"] = time.time() + self.registry.settings.cooldown_seconds
            await self.store.put("provider_health", provider, state)

    def provider_for(self, url):
        p = urlsplit(url)
        return next((x for x in self.registry.providers.values() if host_matches(p.hostname or "", x.hosts) and re.search(x.path_pattern, p.path)), None)

    @staticmethod
    def prefer_error(current, candidate):
        if current is None:
            return candidate
        generic = {"INVALID_RESPONSE", "ROUTE_REVISITED"}
        if current.code in generic and candidate.code not in generic:
            return candidate
        return current

    async def log_provider_failure(self, ctx, provider, error):
        key = (provider.id, error.code, error.message)
        failures = ctx.setdefault("logged_provider_failures", set())
        if key in failures:
            return
        failures.add(key)
        await self.store.log(ctx["job"], ctx["user"], "PROVIDER_FAILED", provider=provider.id, reason=error.code, message=error.message)
        match=re.fullmatch(r'Unconfigured destination: ([A-Za-z0-9.-]+)',error.message)
        if match and not authentication_host(match[1]):
            ident=await save_host_proposal(self.store,provider.id,match[1],ctx['job'])
            await self.store.log(ctx['job'],ctx['user'],'HOST_APPROVAL_NEEDED',provider=provider.id,host=match[1],proposal=ident)

    async def resolve(self, variant: Variant, priority: list[str], job: str, user: int):
        if self.registry.settings.mock_mode or self.registry.settings.dry_run:
            raise FlowError("DRY_RUN", "Dry run complete: final stage reached. No provider was contacted.")
        ordered = sorted(variant.links, key=lambda l: priority.index(self.provider_for(l.url).id) if self.provider_for(l.url) and self.provider_for(l.url).id in priority else 999)
        last = None
        async with self.network.session() as session, AsyncExitStack() as stack:
            ctx = {"visited": set(), "hops": 0, "max_hops": 16, "variant": variant, "job": job, "user": user, "browser": None, "session": session, "stack": stack, "logged_provider_failures": set()}
            for link in ordered:
                provider = self.provider_for(link.url)
                if not provider:
                    await self.store.log(job, user, "PROVIDER_FAILED", host=urlsplit(link.url).hostname, reason="UNSUPPORTED")
                    continue
                ctx["max_hops"] = provider.max_hops
                # Budget each provider separately, with a job-wide ceiling as well.
                if ctx.get('total_hops', 0) >= 64:break
                ctx['hops'] = 0
                ctx['failed_routes'] = set()
                health = await self.store.get("provider_health", provider.id, {})
                if health.get("cooldown_until", 0) > time.time():
                    remaining=max(1,int(health['cooldown_until']-time.time()))
                    last = self.prefer_error(last,FlowError("UNAVAILABLE", f"Provider {provider.id} is cooling down; retry in {remaining} seconds"))
                    await self.store.log(job, user, "PROVIDER_COOLDOWN", provider=provider.id, retry_after_seconds=remaining)
                    continue
                started = time.monotonic()
                await self.store.log(job, user, "PROVIDER_ATTEMPT", provider=provider.id, url=link.url)
                try:
                    result = await self.walk(link.url, provider.allowed_hosts, ctx)
                    await self.record_health(provider.id, True, time.monotonic() - started)
                    await self.store.log(job, user, "PROVIDER_SUCCESS", provider=provider.id, result=result.model_dump(exclude={"url"}))
                    return result
                except FlowError as e:
                    if e.code in {'TIMEOUT','UNAVAILABLE','BLOCKED'}: await self.record_health(provider.id, False, time.monotonic() - started)
                    if e.code != "ROUTE_REVISITED": last = self.prefer_error(last, e)
                    await self.log_provider_failure(ctx, provider, e)
                    if not self.can_fallback(provider, e): raise
                    await self.store.log(job, user, "PROVIDER_FALLBACK", provider=provider.id)
        raise last or FlowError("UNSUPPORTED", "No configured provider for this file")

    async def fetch(self, url, provider, allowed, ctx, force_browser=False):
        request = provider.request if provider else None
        if force_browser or (request and request.transport == "browser"):
            if not self.registry.settings.browser_enabled: raise FlowError("BROWSER_REQUIRED", "This provider needs browser mode; enable it after installing Chromium")
            browser = await ctx["stack"].enter_async_context(Browser(allowed, self.network))
            ctx["browser"] = browser
            # Transfer the current HTTP chain's cookies to this isolated browser context.
            cookies = [{"name": c.key, "value": c.value, "url": url} for c in ctx["session"].cookie_jar.filter_cookies(__import__('yarl').URL(url)).values()]
            if cookies: await browser.context.add_cookies(cookies)
            return await browser.fetch(url, timeout=request.timeout if request else 20, wait_selector=request.wait_selector if request else None, headers=request.headers if request else None)
        attempts = (request.retries if request else 0) + 1
        for attempt in range(attempts):
            try:
                # Preserve cookies created by a preceding browser step for this host.
                if ctx.get("browser"):
                    from yarl import URL
                    for cookie in await ctx["browser"].context.cookies([url]):
                        ctx["session"].cookie_jar.update_cookies({cookie["name"]: cookie["value"]}, response_url=URL(url))
                response = await self.network.fetch(ctx["session"], url, allowed, timeout=request.timeout if request else 20, headers=request.headers if request else None, max_bytes=262144, range_probe=True, max_redirects=request.max_redirects if request else 8)
                reject_blocked(response)
                return response
            except FlowError as e:
                if e.code not in {"TIMEOUT", "UNAVAILABLE"} or attempt == attempts - 1: raise
                await asyncio.sleep(min(2 ** attempt, 4))

    async def walk(self, url, allowed, ctx):
        # Visited pages belong to this route, not failed sibling alternatives.
        previous=ctx['visited'].copy()
        key = uid(route_identity(url))
        failed = ctx.setdefault('failed_routes', set())
        if key in failed:
            raise FlowError('ROUTE_REVISITED', 'This page already failed in the current provider attempt')
        try:return await self._walk(url,allowed,ctx)
        except FlowError:
            failed.add(key)
            raise
        finally:ctx['visited']=previous

    async def _walk(self, url, allowed, ctx):
        from urllib.parse import urlunsplit
        parsed=urlsplit(url)
        alias=getattr(self.registry,"provider_aliases",{}).get(parsed.hostname)
        if alias:
            url=urlunsplit((parsed.scheme,alias,parsed.path,parsed.query,parsed.fragment))
            allowed=list(set([*allowed,alias]))
        validate_url(url, allowed)
        if excluded_route(url):raise FlowError('BLOCKED', 'Authentication route is not a download destination')
        identity = uid(route_identity(url))
        if identity in ctx["visited"]: raise FlowError("ROUTE_REVISITED", "This route loops back to an already attempted page")
        if ctx["hops"] >= ctx.get("max_hops", 16): raise FlowError("INVALID_RESPONSE", "Resolution hop limit reached")
        if ctx.get('total_hops', 0) >= 64:raise FlowError('INVALID_RESPONSE', 'Resolution job limit reached')
        ctx['total_hops'] = ctx.get('total_hops', 0) + 1
        ctx["visited"].add(identity); ctx["hops"] += 1
        provider = self.provider_for(url)
        allowed = list(set(allowed + (provider.allowed_hosts if provider else [])))
        # Registered provider destinations are explicit trust, not arbitrary redirects.
        allowed=list(set(allowed+[host for p in self.registry.providers.values() for host in p.hosts]))
        await self.store.log(ctx["job"], ctx["user"], "RESOLUTION_STEP", provider=provider.id if provider else "direct", hop=ctx["hops"], url=url)
        if provider and provider.mode == "steps": return await self.run_steps(url, provider, allowed, ctx)
        response = await self.fetch(url, provider, allowed, ctx)
        result = file_response(response)
        if result: return validate_expected(result, ctx.get("variant"))
        landed_identity=uid(route_identity(response.url))
        if landed_identity!=identity:
            if landed_identity in ctx['visited']:raise FlowError('ROUTE_REVISITED','Redirect returned to an earlier page')
            ctx['visited'].add(landed_identity)
        # A redirect can change which provider owns the HTML response.
        landed = self.provider_for(response.url)
        if landed: provider = landed; allowed = list(set(allowed + landed.allowed_hosts))
        if not provider: raise FlowError("UNSUPPORTED", f"HTML page at an unconfigured provider: {urlsplit(response.url).hostname}")
        if response.truncated:
            response=await self.network.fetch(ctx['session'],response.url,allowed,timeout=provider.request.timeout,headers=provider.request.headers,max_bytes=1_000_000,range_probe=False)
            reject_blocked(response)
            result=file_response(response)
            if result:return validate_expected(result,ctx.get('variant'))
            landed=self.provider_for(response.url)
            if landed:provider=landed;allowed=list(set(allowed+landed.allowed_hosts))
        doc, candidates = soup(response.text), []
        if provider.id == 'hubdrive' and any(
            re.fullmatch(r'file\s+not\s+found\s*[!.]?', node.get_text(' ', strip=True), re.I)
            for node in doc.select('h1,h2,h3,h4,h5,h6')
        ):
            raise FlowError('LINK_EXPIRED', 'HubDrive reports File not found; trying the next available mirror')
        if provider.id == 'vcloud':
            from .intermediates import vcloud_target
            target = vcloud_target(response.text)
            if target: candidates.append(target)
        if provider.id == "cloudbridge":
            # Follow only this provider's observed landing forms, with the same cookie jar.
            from urllib.parse import urlencode
            for _ in range(6):
                form=doc.select_one("form#landing")
                if not form:break
                target=urljoin(response.url,form.get("action",response.url))
                data={n["name"]:n.get("value","") for n in form.select("input[name]") if n.get("type","").lower() not in {"file","password"}}
                fingerprint=uid("form",target,urlencode(data))
                if fingerprint in ctx["visited"]:raise FlowError("ROUTE_REVISITED","Intermediate form repeated")
                if ctx["hops"]>=ctx["max_hops"]:raise FlowError("INVALID_RESPONSE","Intermediate hop limit reached")
                if ctx.get('total_hops',0)>=64:raise FlowError('INVALID_RESPONSE','Resolution job limit reached')
                ctx['total_hops']=ctx.get('total_hops',0)+1
                ctx["visited"].add(fingerprint);ctx["hops"]+=1
                if form.get("method","GET").upper()!="POST":raise FlowError("UNSUPPORTED","Unexpected landing form method")
                response=await self.network.fetch(ctx["session"],target,allowed,method="POST",data=data,max_bytes=500000)
                reject_blocked(response);doc=soup(response.text)
        if provider.id == "greenmount":
            from .intermediates import greenmount_target
            target=greenmount_target(response.text)
            if target:candidates.append(target)
        query = parse_qs(urlsplit(response.url).query)
        for parameter in provider.query_url_parameters:
            candidates.extend(query.get(parameter, []))
        for selector in provider.link_selectors:
            for a in doc.select(selector):
                target = a.get("href", "")
                if target and not target.startswith(("#", "javascript:")):
                    candidates.append(urljoin(response.url, html.unescape(target)))
        for pattern in provider.literal_url_patterns:
            for match in re.finditer(pattern, response.text):
                if match.lastindex: candidates.append(urljoin(response.url, html.unescape(match[1]).replace("\\/", "/")))
        recovered,unknown=recovery_links(doc,response.url,allowed,self.provider_for)
        additions=[x for x in recovered if x not in candidates]
        candidates=list(dict.fromkeys(x for x in candidates+additions if not excluded_route(x) and route_identity(x)!=route_identity(response.url)))[:32]
        if additions:await self.store.log(ctx['job'],ctx['user'],'RECOVERY_LINKS_FOUND',provider=provider.id,count=len(additions))
        for host in unknown:
            ident=await save_host_proposal(self.store,provider.id,host,ctx['job'])
            await self.store.log(ctx['job'],ctx['user'],'HOST_APPROVAL_NEEDED',provider=provider.id,host=host,proposal=ident)
        if not candidates:
            await self.store.log(ctx['job'],ctx['user'],'PAGE_DIAGNOSTIC',provider=provider.id,host=urlsplit(response.url).hostname,status=response.status,bytes=len(response.body),truncated=response.truncated,anchors=len(doc.select('a[href]')),scripts=len(doc.select('script')),forms=len(doc.select('form')),configured_selectors=len(provider.link_selectors))
            raise FlowError("UNSUPPORTED", f"No configured next link at {urlsplit(response.url).hostname}; owner can inspect this provider")
        last = None
        for target in candidates:
            if provider.id == 'vcloud' and urlsplit(target).hostname == 'pixeldrain.dev' and re.fullmatch(r'/u/[A-Za-z0-9]+', urlsplit(target).path):
                validate_url(target, allowed)
                return Resolution(url=target,host='pixeldrain.dev',note='PixelDrain host link supplied by VCloud; file availability has not been verified.')
            try: return await self.walk(target, allowed, ctx)
            except FlowError as e:
                if e.code != "ROUTE_REVISITED": last = self.prefer_error(last, e)
                await self.log_provider_failure(ctx, provider, e)
                if not self.can_fallback(provider, e): raise
        raise last or FlowError("INVALID_RESPONSE", "None of the configured routes produced a file")

    async def run_steps(self, url, provider, allowed, ctx):
        values, response = {"current_url": url}, None
        steps = {s.id: s for s in provider.steps}
        step_id = provider.steps[0].id
        for _ in range(provider.max_steps):
            step = steps[step_id]
            current = values.get(step.url_from)
            if not current: raise FlowError("INVALID_RESPONSE", f"Missing value for step {step.id}")
            await self.store.log(ctx["job"], ctx["user"], "PROVIDER_STEP", provider=provider.id, step=step.id, action=step.action)
            if step.action == "dispatch": return await self.walk(current, allowed, ctx)
            if step.action == "validate":
                response = await self.network.fetch(ctx["session"], current, allowed, max_bytes=65536, range_probe=True)
                result = file_response(response)
                if not result: raise FlowError("INVALID_RESPONSE", "Final URL did not return a recognizable file")
                return validate_expected(result, ctx.get("variant"))
            if step.action == "wait":
                await asyncio.sleep(step.wait_seconds)
                if ctx["browser"]:
                    if step.wait_selector:
                        try: await ctx["browser"].page.locator(step.wait_selector).first.wait_for(timeout=provider.request.timeout * 1000)
                        except Exception: raise FlowError("BLOCKED", "Expected browser element did not appear")
                    response = Response(ctx["browser"].page.url, 200, {"content-type": "text/html"}, (await ctx["browser"].page.content()).encode()[:262144])
            elif step.action == "click":
                browser = ctx["browser"]
                if not browser or not step.selector: raise FlowError("CONFIG_ERROR", "Click step requires a preceding browser fetch and selector")
                response = await browser.click(step.selector, provider.request.timeout)
            elif step.action == "form":
                form = {k: values[v[1:]] if v.startswith("$") else v for k, v in step.form.items()}
                response = await self.network.fetch(ctx["session"], current, allowed, method=step.method, data=form, timeout=provider.request.timeout, headers=provider.request.headers, max_bytes=262144)
            else:
                response = await self.fetch(current, provider, allowed, ctx, force_browser=step.transport == "browser")
                if step.wait_selector and ctx["browser"]:
                    try:
                        await ctx["browser"].page.locator(step.wait_selector).first.wait_for(timeout=provider.request.timeout * 1000)
                        response.body = (await ctx["browser"].page.content()).encode()[:262144]
                    except Exception: raise FlowError("BLOCKED", "Expected generated link did not appear")
            if response:
                reject_blocked(response)
                values["current_url"] = response.url
                doc = soup(response.text)
                for key, rule in step.extract.items():
                    value = None
                    if rule.selector:
                        node = doc.select_one(rule.selector)
                        if node: value = node.get_text(" ", strip=True) if rule.attribute == "text" else node.get(rule.attribute)
                    elif rule.json_path:
                        try:
                            value = json.loads(response.text)
                            for part in rule.json_path.split("."): value = value[int(part)] if isinstance(value, list) else value[part]
                        except (ValueError, KeyError, IndexError, TypeError): value = None
                    elif rule.regex:
                        match = re.search(rule.regex, response.text)
                        if match: value = match[1]
                    if value is None and rule.required: raise FlowError("PARSER_CHANGED", f"Extraction failed at {step.id}: {key}")
                    if value is not None:
                        values[key] = urljoin(response.url, str(value)) if rule.attribute in {"href", "action", "src"} and not rule.json_path and not rule.regex else str(value)
            if step.candidates:
                last = None
                for key in step.candidates:
                    if key not in values: continue
                    try: return await self.walk(urljoin(values["current_url"], values[key]), allowed, ctx)
                    except FlowError as e:
                        last = self.prefer_error(last, e)
                        if not self.can_fallback(provider, e): raise
                raise last or FlowError("INVALID_RESPONSE", "No extracted candidate was usable")
            if not step.next: raise FlowError("CONFIG_ERROR", f"Step {step.id} has no next or terminal action")
            step_id = step.next
        raise FlowError("INVALID_RESPONSE", "Provider step limit reached")
