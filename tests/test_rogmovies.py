import base64,json
from contextlib import asynccontextmanager
import pytest
from bot.config import Registry,ROOT
from bot.models import SearchResult,Reference,Variant,Link,FlowError
from bot.parser import parse_media,parse_search
from bot.rogmovies import parse_episodes
from bot.intermediates import vcloud_target
from bot.resolver import Resolver
from bot.network import Response
from bot.storage import Store

F=ROOT/'tests/fixtures/rog'
def setup(name):
 r=Registry();r.load();s=r.sites['rogmovies']
 m=parse_media((F/(name+'.html')).read_text(encoding='utf-8'),s.base_url,s,SearchResult(id=name,title='Example',refs=[Reference(site=s.id,url=s.base_url)]))
 return r,s,m

def test_rog_search_cards():
 r=Registry();r.load();s=r.sites['rogmovies']
 items,_=parse_search((F/'search.html').read_text(encoding='utf-8'),s.base_url,s)
 assert len(items)==13 and items[0].title=='Thank You Dear'
 assert all(x.refs[0].site=='rogmovies' for x in items)

def test_rog_pack_sizes_not_episode_sizes():
 r,s,m=setup('series-panchayat')
 assert len(m.variants)==10
 assert [v.size for v in m.variants if v.mode=='pack']==['650MB','1.4GB','2.1GB','3.4GB','13.6GB']
 assert len([v for v in m.variants if v.collection_url])==5
 assert m.screenshots and m.poster not in {x.url for x in m.screenshots}

def test_episode_lists_keep_fallbacks_with_same_quality():
 r,s,m=setup('series-undekhi');parents=[v for v in m.variants if v.collection_url]
 assert [len(v.links) for v in parents]==[2,2,1]
 for name,host in [('episodes-fast','fastdl.zip'),('episodes-vcloud','vcloud.fit')]:
  episodes=parse_episodes((F/(name+'.html')).read_text(encoding='utf-8'),'https://nexdrive.fit/',parents[0],s)
  assert [v.episode for v in episodes]==list(range(1,9))
  assert all(v.season==4 and v.resolution=='480p' and not v.collection_url for v in episodes)
  assert all(host in v.links[0].url for v in episodes)

def test_vcloud_literal_decoder_is_not_script_execution():
 url='https://vcloud.fit/file?token=test'
 encoded=base64.b64encode(base64.b64encode(url.encode())).decode()
 assert vcloud_target("var url = atob(atob('"+encoded+"')); ")==url
 assert vcloud_target("var url = atob(atob(fetch('https://example.com')))") is None

class Pages:
 def __init__(self,pages):self.pages=pages;self.seen=[]
 @asynccontextmanager
 async def session(self):yield None
 async def fetch(self,session,url,allowed,**kwargs):
  self.seen.append(url)
  value=self.pages[url]
  if isinstance(value,Exception):raise value
  return Response(url,200,{'content-type':'text/html'},value.encode())

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['UNAVAILABLE','BLOCKED'])
async def test_fastdl_failure_uses_vcloud_and_returns_pixel_host_link(tmp_path,failure):
 r=Registry();r.load();nex='https://nexdrive.fit/movie';fast='https://fastdl.zip/embed?download=test';vc='https://vcloud.fit/file';pixel='https://pixeldrain.dev/u/abc123'
 network=Pages({nex:f'<a href="{fast}">Instant</a><a href="{vc}">Resume</a>',fast:FlowError(failure,'Host unavailable or blocked'),vc:f'<a id="pxl-1" href="{pixel}">Download</a>'})
 store=Store('sqlite:///'+str(tmp_path/'resolver.db'))
 try:
  result=await Resolver(r,network,store).resolve(Variant(id='v',filename='Movie',links=[Link(label='Download',url=nex)]),['nexdrive','fastdl','vcloud'],'job',7)
  assert result.url==pixel and 'not been verified' in result.note
  assert network.seen.index(fast)<network.seen.index(vc) and pixel not in network.seen
 finally:await store.close()

@pytest.mark.asyncio
async def test_rog_api_uses_relative_endpoint_and_page_number(tmp_path):
 from bot.hdsearch import search
 r=Registry();r.load();s=r.sites['rogmovies']
 data=json.loads((F/'search.json').read_text(encoding='utf-8'))
 network=Pages({s.base_url+'/ts-search.php?q=dear&page=1':json.dumps(data)})
 items=await search(network,s,'dear')
 assert len(items)==13 and len(network.seen)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('vcloud_blocked',[False,True])
async def test_episode_mirror_403_fallback_and_final_error(tmp_path,vcloud_blocked):
 r=Registry();r.load();fast='https://fastdl.zip/embed?download=episode';vc='https://vcloud.fit/episode';pixel='https://pixeldrain.dev/u/ep123'
 network=Pages({fast:FlowError('BLOCKED','Host returned HTTP 403'),vc:FlowError('BLOCKED','Host returned HTTP 403') if vcloud_blocked else f'<a id="pxl-1" href="{pixel}">Download</a>'})
 store=Store('sqlite:///'+str(tmp_path/'episode.db'))
 try:
  resolver=Resolver(r,network,store)
  variant=Variant(id='ep',filename='Episode 1',links=[Link(label='Instant',url=fast),Link(label='Resume',url=vc)])
  if vcloud_blocked:
   with pytest.raises(FlowError) as caught:await resolver.resolve(variant,['fastdl','vcloud'],'ep',7)
   assert caught.value.code=='BLOCKED'
  else:
   result=await resolver.resolve(variant,['fastdl','vcloud'],'ep',7)
   assert result.url==pixel
  assert network.seen==[fast,vc]
 finally:await store.close()
