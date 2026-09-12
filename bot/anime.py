import json,re
from urllib.parse import urljoin,urlsplit,urlencode
from .models import SearchResult,Reference,Media,WatchLink,uid,FlowError
from .network import reject_blocked,validate_url


async def request(network,site,path,data=None):
    url=urljoin(site.base_url,path)
    async with network.session() as session:
        response=await network.fetch(session,url,[urlsplit(site.base_url).hostname],method='POST' if data else 'GET',data=data,max_bytes=1500000)
    reject_blocked(response)
    try:return json.loads(response.text)
    except ValueError:raise FlowError('PARSER_CHANGED','Anime service did not return expected JSON')


async def search(network,site,query):
    data=await request(network,site,'/backend/searchget?'+urlencode({'q':query,'more':'1'}))
    if not isinstance(data,list):raise FlowError('PARSER_CHANGED','Anime search response changed')
    values=[]
    for item in data:
        ident=str(item.get('id',''))
        if not ident.isdigit():continue
        date=re.search(r'\b(19\d{2}|20\d{2})\b',str(item.get('time','')))
        values.append(SearchResult(id=uid(site.id,ident),title=item['title'],kind='anime',year=int(date[1]) if date else None,poster=urljoin(site.image_base,item.get('image','')),refs=[Reference(site=site.id,url=urljoin(site.base_url,'/anime/'+ident))]))
    return values


def parse_detail(data,result,site):
    info=data.get('info',{}); media=Media(**result.model_dump())
    media.title=data.get('title_en') or data.get('title') or media.title
    media.poster=urljoin(site.image_base,data.get('picture',''))
    for key in ['aired','rating','genres','source','studios','season']:
        value=info.get(key,'')
        if isinstance(value,list):value=', '.join(str(v.get('name','')) if isinstance(v,dict) else str(v) for v in value)
        if isinstance(value,dict):value=', '.join(str(x) for x in value.values())
        if value:media.metadata[key]=str(value)
    media.description='\n'.join(f'{k.title()}: {v}' for k,v in media.metadata.items())
    for key,group in [('main','Free'),('scrap','Free (Scrapers)')]:
        for provider in data.get('streams',{}).get(key,[]):
            for link in provider.get('links',[]):
                target=urljoin(site.base_url,link.get('url','').replace('{ep}','1'))
                if '{' in target or not link.get('url'):continue
                try:validate_url(target,[urlsplit(target).hostname or ''])
                except FlowError:continue
                label=provider.get('site','Watch')
                extra=link.get('title','')
                if extra not in {'','def','-'} and len(provider['links'])>1:label+=' '+extra
                if target not in [x.url for x in media.watch_links]:media.watch_links.append(WatchLink(label=label,url=target,group=group))
    return media


async def details(network,site,result):
    match=re.search(r'/anime/(\d+)',result.refs[0].url)
    if not match:raise FlowError('INPUT','Invalid anime reference')
    data=await request(network,site,'/backend/api',{'prompt':match[1]})
    if not isinstance(data,dict) or 'title' not in data:raise FlowError('PARSER_CHANGED','Anime detail response changed')
    return parse_detail(data,result,site)
