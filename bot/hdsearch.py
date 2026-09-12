import json,re
from urllib.parse import urlsplit,urljoin,urlencode
from .models import SearchResult,Reference,FlowError,uid
from .source_parsers import clean_title
from .parser import release_year
from .network import reject_blocked

async def search(network,site,query):
    values=[]
    async with network.session() as session:
        for page in range(1,site.search.max_pages+1):
            api = urljoin(site.base_url, site.search_api)
            params = {'q':query,'page':page} if site.parser == 'rogmovies' else {'q':query,'query_by':'post_title,category,stars,director,imdb_id','query_by_weights':'4,2,2,2,4','sort_by':'sort_by_date:desc','limit':15,'highlight_fields':'none','use_cache':'true','page':page}
            url=api+'?'+urlencode(params)
            response=await network.fetch(session,url,[urlsplit(api).hostname],headers={'Origin':site.base_url.rstrip('/'),'Referer':site.base_url.rstrip('/')+'/'},max_bytes=1500000)
            reject_blocked(response)
            try:data=json.loads(response.text);hits=data['hits']
            except (ValueError,KeyError,TypeError):raise FlowError('PARSER_CHANGED','HDHub search API response changed')
            for hit in hits:
                doc=hit.get('document',{});raw=doc.get('post_title','');path=urlsplit(doc.get('permalink','')).path
                if not raw or not path:continue
                target=urljoin(site.base_url,path)
                values.append(SearchResult(id=uid(site.id,target),title=clean_title(raw),year=release_year(raw),kind='series' if re.search(r'season|series',raw,re.I) else 'movie',poster=doc.get('post_thumbnail',''),tags=raw,refs=[Reference(site=site.id,url=target)]))
            if not hits or len(values)>=int(data.get('found',len(values))):break
    return values
