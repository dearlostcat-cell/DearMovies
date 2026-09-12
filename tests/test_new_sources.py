import pytest
from bot.config import Registry,ROOT
from bot.models import SearchResult,Reference,Variant,Link
from bot.parser import parse_media,parse_search
from bot.rogmovies import parse_episodes
from bot.resolver import Resolver
from bot.storage import Store
from test_rogmovies import Pages

F=ROOT/'tests/fixtures/new_sources'
def media(name,siteid):
 r=Registry();r.load();s=r.sites[siteid]
 return s,parse_media((F/(name+'.html')).read_text(encoding='utf-8'),s.base_url,s,SearchResult(id=name,title='Title',refs=[Reference(site=siteid,url=s.base_url)]))

@pytest.mark.parametrize('ident',['gokuhd','vegamovies'])
def test_new_source_search(ident):
 r=Registry();r.load();s=r.sites[ident]
 values,_=parse_search((F/(ident+'-search.html')).read_text(encoding='utf-8'),s.base_url,s)
 assert values and all(x.refs[0].site==ident for x in values)

def test_goku_multiple_seasons_and_screenshot_section():
 s,m=media('gokuhd-solo','gokuhd')
 assert len(m.variants)==8 and {v.season for v in m.variants}=={1,2}
 assert {v.size for v in m.variants if v.mode=='pack'}=={'4.7GB','18.2GB','2.1GB','12GB'}
 assert len(m.screenshots)==5

def test_goku_source_buttons_are_collections():
 s,m=media('gokuhd-thunder','gokuhd')
 assert len(m.variants)==2 and all(v.collection_url and len(v.links)==2 for v in m.variants)

@pytest.mark.parametrize('name',['vegamovies-dear','vegamovies-fauda'])
def test_vega_pack_and_episode_options(name):
 s,m=media(name,'vegamovies')
 assert len(m.variants)==6
 assert len([v for v in m.variants if v.mode=='pack'])==3
 assert not any('V-Drive' in l.label for v in m.variants for l in v.links)

def test_plural_episode_labels_keep_matching_fallback():
 s,m=media('vegamovies-fauda','vegamovies');parent=next(v for v in m.variants if v.collection_url)
 episodes=parse_episodes((F/'nexdrive-episodes.html').read_text(encoding='utf-8'),'https://nexdrive.fit/',parent,s)
 assert [v.episode for v in episodes]==list(range(1,8))
 assert all(any('vcloud.fit' in l.url for l in v.links) for v in episodes)
 assert all(any('fastdl.zip' in l.url for l in v.links) for v in episodes if v.episode!=6)
 assert len(episodes[5].links)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('only',['fastdl','vcloud'])
async def test_missing_provider_option_uses_available_one(tmp_path,only):
 r=Registry();r.load();nex='https://nexdrive.fit/movie';fast='https://fastdl.zip/embed?download=test';vc='https://vcloud.fit/file';pixel='https://pixeldrain.dev/u/test123'
 # The FastDL HTML points onward to a configured host; no real requests occur.
 target=fast if only=='fastdl' else vc
 pages={nex:f'<a href="{target}">Download</a>',fast:f'<a id="vd" href="{vc}">Final</a>',vc:f'<a id="pxl-1" href="{pixel}">Download</a>'}
 network=Pages(pages);store=Store('sqlite:///'+str(tmp_path/'one.db'))
 try:
  result=await Resolver(r,network,store).resolve(Variant(id='one',filename='Movie',links=[Link(label='Download',url=nex)]),['nexdrive'],'one',7)
  assert result.url==pixel
  if only=='vcloud':assert fast not in network.seen
 finally:await store.close()
