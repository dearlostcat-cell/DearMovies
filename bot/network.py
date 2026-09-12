from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
import aiohttp
from aiohttp.resolver import DefaultResolver
from .models import FlowError

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def host_matches(host: str, patterns: list[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == p.lower().rstrip(".") or (p.startswith("*.") and host.endswith(p[1:].lower()) and host != p[2:].lower()) for p in patterns)


def validate_url(url: str, allowed: list[str]) -> str:
    if len(url) > 16000: raise FlowError("UNSUPPORTED", "URL is too long")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password or not host or parsed.port not in {None, 80, 443}:
            raise ValueError()
        try:
            addr = ipaddress.ip_address(host)
            if not addr.is_global: raise FlowError("UNSAFE_URL", "Private network destination rejected")
        except ValueError: pass
        if not host_matches(host, allowed):
            raise FlowError("UNSUPPORTED", f"Unconfigured destination: {host}")
        return url
    except ValueError:
        raise FlowError("UNSAFE_URL", "Only public HTTP(S) destinations on standard ports are supported")


class PublicResolver(DefaultResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        results = await super().resolve(host, port, family)
        if not results or any(not ipaddress.ip_address(r["host"]).is_global for r in results):
            raise FlowError("UNSAFE_URL", "DNS resolved to a private or reserved network")
        return results


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes
    truncated: bool = False

    @property
    def text(self): return self.body.decode("utf-8", errors="replace")


def reject_blocked(response: Response):
    title = re.search(r"<title[^>]*>(.*?)</title>", response.text, re.I | re.S)
    title = title[1].casefold() if title else ""
    if response.status in {401, 403}:
        raise FlowError("BLOCKED", f"Host returned HTTP {response.status}; owner attention required")
    if response.status == 404: raise FlowError("LINK_EXPIRED", "Page or file was not found")
    if response.status == 429 or response.status >= 500:
        raise FlowError("UNAVAILABLE", f"Host returned HTTP {response.status}")
    if response.status >= 400: raise FlowError("INVALID_RESPONSE", f"Host returned HTTP {response.status}")
    if any(x in title for x in ("just a moment", "attention required", "verify you are human", "access denied", "sign in", "login")):
        raise FlowError("BLOCKED", "Verification or login page; owner attention required")


def configured_headers(headers: dict[str, str]) -> dict[str, str]:
    result = {"User-Agent": USER_AGENT}
    for key, value in headers.items():
        if value.startswith("env:"):
            value = os.environ.get(value[4:], "")
            if not value: raise FlowError("CONFIG_ERROR", f"Missing environment variable for header {key}")
        if key.casefold() in {"host", "content-length"}: raise FlowError("CONFIG_ERROR", "Host and Content-Length headers cannot be configured")
        result[key] = value
    return result


class Network:
    def __init__(self, concurrency=5):
        self.semaphore = asyncio.Semaphore(concurrency)

    def session(self):
        return aiohttp.ClientSession(connector=aiohttp.TCPConnector(resolver=PublicResolver(), ttl_dns_cache=10, limit=10), cookie_jar=aiohttp.CookieJar(), trust_env=False)

    async def fetch(self, session, url, allowed, timeout=20, headers=None, method="GET", data=None, max_bytes=2_000_000, range_probe=False, max_redirects=8):
        seen = set()
        headers = configured_headers(headers or {})
        if range_probe: headers["Range"] = "bytes=0-65535"
        initial_host = urlsplit(url).hostname
        for _ in range(max_redirects + 1):
            validate_url(url, allowed)
            if url in seen: raise FlowError("REDIRECT_LOOP", "Redirect loop detected")
            seen.add(url)
            safe_headers = dict(headers)
            if urlsplit(url).hostname != initial_host:
                safe_headers = {k: v for k, v in safe_headers.items() if k.lower() not in {"authorization", "cookie", "proxy-authorization"}}
            try:
                async with self.semaphore:
                    async with session.request(method, url, headers=safe_headers, data=data, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
                        if response.status in {301, 302, 303, 307, 308}:
                            target = response.headers.get("Location")
                            if not target: raise FlowError("INVALID_RESPONSE", "Redirect without a destination")
                            new_url = urljoin(url, target)
                            if method == "POST" and urlsplit(new_url).hostname != urlsplit(url).hostname and response.status in {307, 308}:
                                raise FlowError("BLOCKED", "Cross-host form forwarding requires an explicit provider step")
                            if response.status == 303 or (response.status in {301, 302} and method == "POST"):
                                method, data = "GET", None
                            url = new_url
                            continue
                        parts, length, truncated = [], 0, False
                        async for chunk in response.content.iter_chunked(min(65536, max_bytes)):
                            remaining = max_bytes - length
                            parts.append(chunk[:remaining]); length += len(chunk[:remaining])
                            if length >= max_bytes:
                                truncated = True
                                response.close()
                                break
                        return Response(str(response.url), response.status, {k.lower(): v for k, v in response.headers.items()}, b"".join(parts), truncated)
            except asyncio.TimeoutError:
                raise FlowError("TIMEOUT", "The remote host did not respond in time")
            except (aiohttp.ClientError, OSError):
                raise FlowError("UNAVAILABLE", "Could not connect to the remote host")
        raise FlowError("REDIRECT_LOOP", "Redirect limit exceeded")


class Browser:
    """Optional bounded browser session. No arbitrary JS evaluation from YAML."""
    def __init__(self, allowed, network: Network):
        self.allowed, self.network = allowed, network
        self.manager = self.browser = self.context = self.page = None

    async def __aenter__(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise FlowError("BROWSER_REQUIRED", "Install requirements-browser.txt and Chromium to enable this provider")
        self.manager = await async_playwright().start()
        try:
            executable = os.getenv("CHROMIUM_EXECUTABLE")
            self.browser = await self.manager.chromium.launch(headless=True, **({"executable_path": executable} if executable else {}))
            self.context = await self.browser.new_context(accept_downloads=False, service_workers="block", user_agent=USER_AGENT)
            async def guard(route):
                request = route.request
                try:
                    validate_url(request.url, self.allowed)
                    host = urlsplit(request.url).hostname
                    records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                    if any(not ipaddress.ip_address(r[4][0]).is_global for r in records): raise ValueError()
                    if request.resource_type in {"image", "media", "font"}: await route.abort()
                    else: await route.continue_()
                except Exception: await route.abort()
            await self.context.route("**/*", guard)
            self.page = await self.context.new_page()
            return self
        except Exception:
            await self.__aexit__(None, None, None)
            raise FlowError("BROWSER_REQUIRED", "Chromium could not start; check browser installation")

    async def fetch(self, url, timeout=20, wait_selector=None, headers=None):
        validate_url(url, self.allowed)
        try:
            # Only initial-origin configured headers are used; route handoffs must not leak them.
            if headers and any(k.lower() in {"authorization", "cookie"} for k in headers):
                raise FlowError("UNSUPPORTED", "Sensitive browser headers require a dedicated adapter")
            if headers: await self.page.set_extra_http_headers(configured_headers(headers))
            async with self.network.semaphore:
                response = await self.page.goto(url, wait_until="domcontentloaded", timeout=timeout * 1000)
                if wait_selector: await self.page.locator(wait_selector).first.wait_for(timeout=timeout * 1000)
                content = (await self.page.content()).encode()
                if len(content) > 2_000_000: raise FlowError("INVALID_RESPONSE", "Browser page exceeds size limit")
                return Response(self.page.url, response.status if response else 200, {"content-type": "text/html"}, content)
        except FlowError: raise
        except Exception: raise FlowError("BLOCKED", "Browser step failed or requires owner attention")

    async def click(self, selector, timeout=20):
        try:
            await self.page.locator(selector).first.click(timeout=timeout * 1000)
            await asyncio.sleep(0.3)
            if len(self.context.pages) > 1: self.page = self.context.pages[-1]
            await self.page.wait_for_load_state("domcontentloaded", timeout=timeout * 1000)
            validate_url(self.page.url, self.allowed)
            return Response(self.page.url, 200, {"content-type": "text/html"}, (await self.page.content()).encode()[:2_000_000])
        except FlowError: raise
        except Exception: raise FlowError("BLOCKED", "Configured browser button could not be used")

    async def __aexit__(self, *args):
        if self.context: await self.context.close()
        if self.browser: await self.browser.close()
        if self.manager: await self.manager.stop()
