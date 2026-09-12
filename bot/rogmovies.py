"""RogMovies quality groups and NexDrive episode lists."""
import re
from urllib.parse import urljoin, urlsplit
from .models import Media, Variant, Link, Screenshot, FlowError, uid
from .parser import soup, first, file_properties, release_year
from .source_parsers import clean_title, screenshots
from .network import host_matches, reject_blocked, validate_url

QUALITY = re.compile(r'\b(?:480|720|1080|2160)p\b|\b4K\b', re.I)
SIZE = re.compile(r'\d+(?:\.\d+)?\s*(?:MB|GB|TB)', re.I)

def parse_detail(html, url, site, result):
    doc=soup(html);root=doc.select_one(site.content_root)
    if not root:raise FlowError('PARSER_CHANGED','RogMovies article is missing')
    raw=first(doc,site.selectors.title) or result.title
    media=Media(**result.model_dump());media.title=clean_title(raw)
    media.title=re.split(r'\s*\{S\d|\s*\[Season',media.title,1,flags=re.I)[0].strip()
    media.kind='series' if re.search(r'season|series|\bS\d',raw,re.I) else 'movie'
    lines=root.get_text('\n',strip=True)
    year=re.search(r'Released Year:\s*(\d{4})',lines,re.I)
    media.year=release_year(raw) or (int(year[1]) if year else result.year)
    season=re.search(r'(?:Season[:\s]*|\bS)(\d+)',raw+' '+lines,re.I)
    season=int(season[1]) if season else None
    media.screenshots=screenshots(root,site,url)
    for block in root.select('.su-spoiler'):
        heading=block.select_one('.su-spoiler-title')
        if not heading or not re.search('screenshot',heading.get_text(),re.I):continue
        for img in block.select('.su-spoiler-content img[src]'):
            target=urljoin(url,img['src'])
            if host_matches(urlsplit(target).hostname or '',site.screenshot_hosts) and target not in {x.url for x in media.screenshots}:
                media.screenshots.append(Screenshot(url=target,original=target))
    media.screenshots=media.screenshots[:30]
    sample_urls={x.url for x in media.screenshots}
    for img in root.select('img[src]'):
        target=urljoin(url,img['src'])
        if target not in sample_urls and 'emoji' not in img.get('class',[]):media.poster=target;break
    for h in root.select('h3,h4'):
        if re.search(r'synopsis|plot',h.get_text(),re.I):
            p=h.find_next_sibling('p')
            if p:media.description=p.get_text(' ',strip=True)[:6000]
            break
    current='';variants={}
    for node in root.descendants:
        name=getattr(node,'name','')
        if name in {'h1','h2','h3','h4','h5','h6'}:
            label=node.get_text(' ',strip=True)
            if QUALITY.search(label):current=label
        if name!='a' or not current:continue
        target=urljoin(url,node.get('href',''))
        if not host_matches(urlsplit(target).hostname or '',site.provider_hosts):continue
        label=node.get_text(' ',strip=True)
        if re.search(r'watch|telegram|v-drive|filepress',label,re.I):continue
        pack=bool(re.search(r'batch|zip',label,re.I))
        collection=media.kind=='series' and not pack
        mode='pack' if pack else 'episode' if collection else 'movie'
        props=file_properties(current)
        if re.search('10bit',current,re.I):props['format']=(props['format']+' 10Bit').replace('Unknown ','')
        size=SIZE.search(label if pack else current)
        group_season=re.search(r'(?:Season\s*|\bS)(\d+)',current,re.I)
        selected_season=int(group_season[1]) if group_season else season
        key=uid(site.id,selected_season,current,mode)
        if key not in variants:
            variants[key]=Variant(id=key,filename=current,website=site.id,season=selected_season,mode=mode,size=size[0] if size else 'Unknown',collection_url=target if collection else '',links=[],**props)
        variants[key].links.append(Link(label=label or 'Download',url=target))
    media.variants=list(variants.values())
    if not media.variants:raise FlowError('PARSER_CHANGED','No RogMovies quality groups found')
    return media

def parse_episodes(html,url,parent,site):
    doc=soup(html);root=doc.select_one('.thecontent') or doc.select_one('.entry-content') or doc.select_one('article')
    if not root:raise FlowError('PARSER_CHANGED','Episode article missing')
    episode=None;values={}
    for node in root.descendants:
        if getattr(node,'name','') in {'h2','h3','h4','h5'}:
            m=re.search(r'\bEpisodes?\s*[:.\-]*\s*(\d+)\b',node.get_text(' ',strip=True),re.I)
            episode=int(m[1]) if m else None
        if getattr(node,'name','')!='a' or episode is None:continue
        target=urljoin(url,node.get('href',''))
        if not host_matches(urlsplit(target).hostname or '',site.provider_hosts):continue
        if episode not in values:
            values[episode]=parent.model_copy(deep=True,update={'id':uid(parent.id,episode),'episode':episode,'mode':'episode','filename':parent.filename+' • Episode '+str(episode),'links':[],'collection_url':''})
        values[episode].links.append(Link(label=node.get_text(' ',strip=True) or 'Download',url=target))
    if not values:raise FlowError('PARSER_CHANGED','No numbered episodes on this page')
    return list(values.values())

async def expand_collection(catalog,media,parent):
    site=catalog.registry.sites[media.refs[0].site];values={};last=None
    async with catalog.network.session() as session:
        for link in parent.links:
            try:
                validate_url(link.url,site.provider_hosts)
                response=await catalog.network.fetch(session,link.url,site.provider_hosts,max_bytes=1500000)
                reject_blocked(response)
                for episode in parse_episodes(response.text,response.url,parent,site):
                    if episode.episode not in values:values[episode.episode]=episode
                    else:values[episode.episode].links.extend(episode.links)
            except FlowError as e:last=e
    if not values:raise last or FlowError('UNAVAILABLE','No episode lists available')
    for value in values.values():
        value.links=list({x.url:x for x in value.links}.values())
        value.links.sort(key=lambda x:0 if 'fastdl' in x.url else 1)
    return sorted(values.values(),key=lambda v:v.episode)
