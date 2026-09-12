"""Parsers for heading-based catalogues and numbered episode collections."""
import re
from urllib.parse import urljoin, urlsplit
from .models import Media, Variant, Link, Screenshot, FlowError, uid
from .parser import soup, file_properties, release_year, first
from .network import host_matches


def clean_title(text):
    text = re.sub(r'^\W*(?:Download\s+)?', '', text, flags=re.I)
    return re.split(r'\s*(?:\((?:19|20)\d{2}\)|\(Season\b|\b(?:Dual Audio|Multi Audio|WEB-DL|WeB-DL|BluRay)\b)', text, maxsplit=1, flags=re.I)[0].strip(' -–')


def heading_text(node):
    text = node.get_text(' ', strip=True)
    return re.sub(r'\b(4|7|10|21)\s+(80|20|60)p\b', r'\1\2p', text, flags=re.I)


def screenshots(root, site, url):
    active = False; values = []
    for node in root.descendants:
        if getattr(node, 'name', '') in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            label = node.get_text(' ', strip=True)
            if re.search(r'screen\W*shots?', label, re.I): active = True
            elif active and label.strip(): active = False
        if active and getattr(node, 'name', '') == 'img':
            src = node.get('data-src') or node.get('src', '')
            target = urljoin(url, src)
            if not host_matches(urlsplit(target).hostname or '', site.screenshot_hosts): continue
            parent = node.find_parent('a'); original = urljoin(url, parent.get('href', '')) if parent else target
            if urlsplit(original).scheme not in {'http', 'https'}: original = target
            if target not in [s.url for s in values]: values.append(Screenshot(url=target, original=original))
    return values[:30]


def article_media(html, url, site, result):
    doc = soup(html); root = doc.select_one(site.content_root)
    if not root: raise FlowError('PARSER_CHANGED', 'Article content container is missing')
    title = first(doc, site.selectors.title) or (doc.title.get_text() if doc.title else result.title)
    media = Media(**result.model_dump()); media.title = clean_title(title)
    media.year = release_year(title) or media.year
    media.kind = 'series' if re.search(r'season|full series', title, re.I) else 'movie'
    poster = first(root, site.selectors.poster, 'src')
    if poster: media.poster = urljoin(url, poster)
    media.screenshots = screenshots(root, site, url)
    text = root.get_text('\n', strip=True)
    for key, labels in {'audios':['Language','Audio'], 'director':['Director'], 'release':['Release Date','Released']}.items():
        m = re.search(r'(?:'+ '|'.join(labels)+r')\s*:\s*([^\n]+)', text, re.I)
        if m: media.metadata[key] = m[1].strip()
    for h in root.select('h2,h3,h4'):
        if re.search(r'storyline|synopsis', h.get_text(), re.I):
            paragraph = h.find_next('p')
            if paragraph: media.description = paragraph.get_text(' ', strip=True)[:6000]
            break
    season_match = re.search(r'Season\s*(\d+)', title, re.I)
    season = int(season_match[1]) if season_match else None
    variants=[]; current=''; episode=None; downloads=False
    for node in root.descendants:
        name = getattr(node, 'name', '')
        if name in {'h2','h3','h4'}:
            heading = heading_text(node)
            if re.search(r'download links|single episode',heading,re.I): downloads=True
            ep = re.search(r'\bEPiSODE\s*(\d+)\b',heading,re.I)
            if ep: episode=int(ep[1]); current=''; downloads=True
            if re.search(r'\b(?:480|720|1080|2160)p\b|\b4K\b',heading,re.I): current=heading
        if name != 'a': continue
        target = urljoin(url,node.get('href',''))
        if urlsplit(target).scheme not in {'http','https'}: continue
        if not host_matches(urlsplit(target).hostname or '',site.provider_hosts): continue
        label=node.get_text(' ',strip=True)
        if re.search(r'watch|player',label,re.I): continue
        if site.parser=='wordpress' and 'maxbutton' not in node.get('class',[]): continue
        if site.parser=='hdhub' and not downloads: continue
        local=heading_text(node)
        context = local if re.search(r'\b(?:480|720|1080|2160)p|\b4K\b',local,re.I) else current
        if not context: continue
        props=file_properties(context)
        fallback=file_properties(title)
        for k in ['languages','source']:
            if props[k]=='Unknown': props[k]=fallback[k]
        if re.search(r'10\s*bit',context,re.I): props['format'] = (props['format']+' 10Bit').replace('Unknown ','')
        found_season=re.search(r'Season\s*(\d+)',context,re.I)
        vs=int(found_season[1]) if found_season else season
        collection='episode' in label.lower() and episode is None
        mode='episode' if collection or episode is not None else 'pack' if media.kind=='series' else 'movie'
        size_match=re.search(r'\b\d+(?:\.\d+)?\s*(?:MB|GB|TB)\b',context,re.I)
        size=size_match[0] if size_match and mode!='pack' else 'Unknown'
        # Series heading sizes may describe one episode rather than the archive.
        if mode=='pack' and site.parser=='hdhub' and size_match: size=size_match[0]
        key=uid(site.id,vs,episode,mode,context, target if collection else '')
        existing=next((v for v in variants if v.id==key),None)
        link=Link(label=label or 'Download',url=target)
        if existing:
            if target not in [x.url for x in existing.links]: existing.links.append(link)
            continue
        variants.append(Variant(id=key,filename=context,website=site.id,season=vs,episode=episode,mode=mode,size=size,collection_url=target if collection else '',links=[link],**props))
    media.variants=variants
    if not variants: raise FlowError('PARSER_CHANGED','No downloadable versions matched this article')
    return media


def episode_collection(html, url, parent, site):
    doc=soup(html); root=doc.select_one('.entry-content') or doc.select_one('article') or doc
    episode=None; values={}
    for node in root.descendants:
        if getattr(node,'name','') in {'h2','h3','h4'}:
            m=re.search(r'\bEpisode\s*(\d+)\b',node.get_text(' ',strip=True),re.I)
            if m: episode=int(m[1])
        if getattr(node,'name','')!='a' or episode is None: continue
        target=urljoin(url,node.get('href',''))
        if not host_matches(urlsplit(target).hostname or '',site.provider_hosts): continue
        label=node.get_text(' ',strip=True)
        if re.search(r'watch|telegram|batch',label,re.I): continue
        if episode not in values:
            values[episode]=parent.model_copy(deep=True,update={'id':uid(parent.id,episode),'episode':episode,'filename':f'{parent.filename} • Episode {episode}','collection_url':'','links':[]})
        if target not in [x.url for x in values[episode].links]: values[episode].links.append(Link(label=label or 'Download',url=target))
    if not values: raise FlowError('PARSER_CHANGED','No numbered episode links found on the collection page')
    return list(values.values())
