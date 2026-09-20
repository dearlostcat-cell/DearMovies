"""Bounded link recovery. No JavaScript evaluation or automatic host trust."""
import html
import re
from urllib.parse import urljoin, urlsplit, urlunsplit
from .network import validate_url
from .models import FlowError


def authentication_host(host):
    host = (host or '').lower().rstrip('.')
    return host == 'accounts.google.com' or host.endswith('.accounts.google.com')


def excluded_route(url):
    parsed = urlsplit(url)
    return authentication_host(parsed.hostname) or bool(re.search(
        r'(?:^|/)(?:login|logout|signin|sign-in|signup|sign-up|register|account|accounts|oauth|wp-login\.php)(?:/|$)',
        parsed.path, re.I))


def route_identity(url):
    p=urlsplit(url)
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path,p.query,''))


def recovery_links(doc, base, allowed, provider_for):
    candidates=[]; unknown=set()
    def add(raw, eligible=True):
        if not raw or not eligible:return
        target=urljoin(base,html.unescape(raw).replace('\\/','/'))
        if excluded_route(target) or route_identity(target)==route_identity(base):return
        try:validate_url(target,allowed)
        except FlowError as error:
            if error.code=='UNSUPPORTED':
                host=urlsplit(target).hostname
                if host and re.fullmatch(r'[A-Za-z0-9.-]{1,253}',host):unknown.add(host.lower())
            return
        if target not in candidates and len(candidates)<16:candidates.append(target)
    for node in doc.select('a[href], [data-href], [data-url], [data-download]')[:250]:
        if any(parent.name in {'nav', 'header', 'footer', 'aside'} or re.search(
            r'(?:^|[\s_-])(?:ad|ads|advertisement|menu|related|widget)(?:$|[\s_-])',
            ' '.join(parent.get('class', []))+' '+parent.get('id', ''), re.I)
            for parent in [node, *node.parents] if getattr(parent, 'attrs', None) is not None):
            continue
        label=node.get_text(' ',strip=True)+' '+str(node.get('id',''))+' '+str(node.get('class',''))
        target=node.get('href') or node.get('data-href') or node.get('data-url') or node.get('data-download')
        if not target:continue
        resolved=urljoin(base,target)
        actionable=bool(re.search(r'download|resume|get[\s_-]*link|continue|generate',label,re.I))
        if re.search(r'\b(?:log\s*in|sign\s*in|sign\s*up|register|logout|privacy|terms|contact|home)\b',label,re.I):continue
        # A known host alone does not make its own navigation a download route.
        origin_provider, target_provider = provider_for(base), provider_for(resolved)
        different_provider = target_provider is not None and (
            origin_provider is None or origin_provider.id != target_provider.id)
        add(target, actionable or different_provider)
    for node in doc.select('meta[http-equiv]')[:10]:
        if node.get('http-equiv','').lower()=='refresh':
            match=re.search(r'url\s*=\s*["\x27]?([^"\x27]+)',node.get('content',''),re.I)
            if match:add(match[1].strip())
    # Literal assignments only; never run arbitrary page code or decode opaque tokens.
    for node in doc.select('script:not([src])')[:40]:
        for m in re.finditer(r'(?:downloadUrl|downloadLink|download_url|location\.href|location)\s*=\s*["\x27]([^"\x27]+)',node.get_text()[:100000]):add(m[1])
    return candidates,sorted(unknown)[:8]


async def save_host_proposal(store, provider, host, job):
    # Domains only: no signed URL, cookies or page contents in proposals.
    from .models import uid
    ident=uid(provider,host)[:12]
    await store.put('host_proposals',ident,{'provider':provider,'host':host,'job':job},7*86400)
    return ident
