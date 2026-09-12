import asyncio,json
import pytest
from bot.config import Registry,ROOT,Site
import yaml
from bot.models import SearchResult,Reference
from bot.parser import parse_media,parse_search
from bot.source_parsers import episode_collection
from bot.anime import parse_detail
from bot.storage import Store
from bot.app import BotApp
from bot.runtime_config import load_runtime
from test_core import FakeTelegram

F=ROOT/'tests/fixtures/trial'

def fixture_site(ident):
 return Site.model_validate(yaml.safe_load((F/(ident+'-config.yaml')).read_text(encoding='utf-8')))

@pytest.mark.asyncio
async def test_five_sources_concurrent_and_partial_failure(tmp_path):
 from bot.service import Catalog
 r=Registry();r.load();store=Store('sqlite:///'+str(tmp_path/'search.db'))
 catalog=Catalog(r,None,store);started=set();ready=asyncio.Event()
 async def one(site,query,job,user):
  started.add(site.id)
  if len(started)==5:ready.set()
  await asyncio.wait_for(ready.wait(),1)
  if site.id=='hdhub4u':raise RuntimeError('offline')
  return [SearchResult(id=site.id,title='Dear '+site.id,refs=[Reference(site=site.id,url=site.base_url+'/dear')])]
 catalog.search_one=one
 try:
  results,failed=await catalog.search('dear','test',7)
  assert started=={'4khdhub','hdhub4u','rogmovies','gokuhd','vegamovies'}
  assert len(results)==4 and failed==[r.sites['hdhub4u'].name]
 finally:await store.close()

@pytest.mark.asyncio
async def test_search_pagination_retains_all_results(tmp_path):
 r=Registry();r.load();r.settings.allowed_users=[7];r.settings.results_per_page=2
 store=Store('sqlite:///'+str(tmp_path/'pages.db'));tg=FakeTelegram();app=BotApp(r,store,tg)
 try:
  s=await app.new_session(7,7,'dear')
  s['results']=[SearchResult(id=str(i),title='Dear '+str(i),refs=[Reference(site='4khdhub',url='https://4khdhub.one/'+str(i))]).model_dump() for i in range(5)]
  await app.render(s)
  await app.callback({'id':'next','from':{'id':7},'message':{'chat':{'id':7}},'data':app.cb(s,'page',1)})
  saved=await store.get('sessions',s['id'])
  assert saved['page']==1 and len(saved['results'])==5
 finally:await app.close();await store.close()
def media(name,ident):
 r=Registry();r.load();s=fixture_site(ident) if ident in {"moviesmod","moviesleech"} else r.sites[ident]
 return parse_media((F/(name+'.html')).read_text(encoding='utf-8'),s.base_url,s,SearchResult(id='title',title='Example',refs=[Reference(site=ident,url=s.base_url+'/example')]))

def test_movie_options_and_samples():
 m=media('moviesmod-movie','moviesmod');assert len(m.variants)==5 and len(m.screenshots)==9
 assert [v.format for v in m.variants].count('10Bit')==2
 assert m.title=='My Dearest Assassin'
 leech=media('moviesleech-movie','moviesleech')
 assert [v.resolution for v in leech.variants]==['480p','720p','1080p']
 assert len(leech.screenshots)==7

def test_series_collection_and_unknown_pack_sizes():
 m=media('moviesmod-series','moviesmod');assert len(m.variants)==16
 parent=next(v for v in m.variants if v.collection_url)
 r=Registry();r.load()
 episodes=episode_collection((F/'modpro-episodes.html').read_text(encoding='utf-8'),parent.collection_url,parent,fixture_site('moviesmod'))
 assert [x.episode for x in episodes]==list(range(1,12))
 assert all(x.mode=='episode' and not x.collection_url for x in episodes)
 assert all(v.size=='Unknown' for v in m.variants if v.mode=='pack')

def test_hdhub_packs_episodes_separate_watch():
 m=media('hdhub-series','hdhub4u')
 assert len([v for v in m.variants if v.mode=='pack'])==3
 episodes=[v for v in m.variants if v.mode=='episode']
 assert len(episodes)==24 and {v.episode for v in episodes}==set(range(1,13))
 assert all('Watch' not in l.label for v in episodes for l in v.links)
 assert m.poster and len(m.screenshots)==6

def test_kuroiru_groups_and_episode_expansion():
 r=Registry();r.load();s=r.sites['kuroiru']
 m=parse_detail(json.loads((F/'anime-details.json').read_text(encoding='utf-8')),SearchResult(id='a',title='a',refs=[Reference(site=s.id,url=s.base_url+'/anime/50380')]),s)
 assert {l.group for l in m.watch_links}=={'Free','Free (Scrapers)'}
 assert len(m.watch_links)>=20 and not any('{ep}' in l.url for l in m.watch_links)
 assert 'P.A. Works' in m.description

@pytest.mark.asyncio
async def test_plain_search_and_search_command_share_route(tmp_path):
 r=Registry();r.load();r.settings.allowed_users=[7]
 store=Store('sqlite:///'+str(tmp_path/'bot.db'));tg=FakeTelegram();app=BotApp(r,store,tg);calls=[]
 async def search(q,j,u):calls.append(('movies',q));return [],[]
 async def anime(q,j,u):calls.append(('anime',q));return [],[]
 app.catalog.search=search;app.catalog.anime_search=anime
 try:
  for text in ['dear','/search dear','/anime dear','/unknown dear']:
   await app.command({'from':{'id':7},'chat':{'id':7},'text':text});await asyncio.gather(*list(app.jobs.values()))
  assert calls==[('movies','dear'),('movies','dear'),('anime','dear')]
 finally:await app.close();await store.close()

@pytest.mark.asyncio
async def test_roles_restricted_default_and_revocation(tmp_path):
 r=Registry();r.load();store=Store('sqlite:///'+str(tmp_path/'bot.db'));app=BotApp(r,store,FakeTelegram());owner=r.settings.owner_ids[0]
 try:
  assert not await app.permitted(7) and await app.permitted(owner)
  await app.management(owner,owner,'/admin','add 8');assert await app.staff(8)
  await app.management(8,8,'/allow','7');assert await app.permitted(7)
  await app.management(8,8,'/admin','add 9');assert not await app.staff(9)
  await app.management(8,-100,'/allow','9');assert not await app.permitted(9)
  await app.management(8,8,'/revoke','7');assert not await app.permitted(7)
  await app.management(owner,owner,'/admin','remove 8');assert not await app.permitted(8)
 finally:await app.close();await store.close()

@pytest.mark.asyncio
async def test_runtime_config_persistence_and_validation(tmp_path):
 r=Registry();r.load();store=Store('sqlite:///'+str(tmp_path/'bot.db'))
 try:
  raw=r.sites['hdhub4u'].model_dump();raw['base_url']='https://example.com';raw['mirrors']=['https://hdhub4u.zone'];raw['version']='new'
  await store.put('runtime','configuration',{'sites':{'hdhub4u':raw}})
  loaded=await load_runtime(ROOT,store);assert loaded.sites['hdhub4u'].base_url=='https://example.com'
  raw['parser']='invalid'
  with pytest.raises(ValueError):await load_runtime(ROOT,store,{'sites':{'hdhub4u':raw}})
  assert loaded.sites['hdhub4u'].parser=='hdhub'
 finally:await store.close()

@pytest.mark.asyncio
async def test_removed_sources_cannot_return_from_database(tmp_path):
 r=Registry();r.load();assert set(r.sites)=={'4khdhub','hdhub4u','kuroiru','rogmovies','gokuhd','vegamovies'}
 store=Store('sqlite:///'+str(tmp_path/'removed.db'))
 try:
  await store.put('runtime','configuration',{'sites':{name:fixture_site(name).model_dump() for name in ['moviesmod','moviesleech']}})
  loaded=await load_runtime(ROOT,store)
  assert set(loaded.sites)=={'4khdhub','hdhub4u','kuroiru','rogmovies','gokuhd','vegamovies'}
 finally:await store.close()
