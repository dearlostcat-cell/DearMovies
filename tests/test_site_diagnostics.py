import asyncio
import json
import pytest
from bot.app import BotApp
from bot.config import Registry
from bot.models import SearchResult, Reference, FlowError, Media
from bot.service import Catalog
from bot.storage import Store
from bot.parser import soup
from bot.recovery import recovery_links
from test_core import FakeTelegram, FakeNetwork
from bot.network import Response

def result(site):
    return SearchResult(id=site.id,title='Sample',refs=[Reference(site=site.id,url=site.base_url+'/sample')])

def app_at(tmp_path):
    r=Registry();r.load();r.settings.owner_ids=[1]
    return BotApp(r,Store('sqlite:///'+str(tmp_path/'state.db')),FakeTelegram())

async def drain(app):
    await asyncio.gather(*list(app.site_test_tasks.values()))

@pytest.mark.asyncio
async def test_sites_auto_run_enabled_only_and_deduplicate(tmp_path,monkeypatch):
    app=app_at(tmp_path);calls=[];gate=asyncio.Event();active=0;peak=0
    async def search(self,site,q,j,u,force_refresh=False):
        nonlocal active,peak
        assert force_refresh;calls.append(site.id);active+=1;peak=max(peak,active)
        await gate.wait();active-=1;return [result(site)]
    monkeypatch.setattr(Catalog,'search_one',search)
    try:
        await app.store.put('site_enabled','gokuhd',False)
        await app.admin_command(1,1,'/sites','')
        await app.admin_command(1,1,'/sites','')
        assert len(app.site_test_tasks)==5
        gate.set();await drain(app)
        assert len(calls)==5 and 'gokuhd' not in calls
        assert peak<=2
        await app.admin_command(1,1,'/sites','')
        assert not app.site_test_tasks
        assert any('View logs'==b['text'] for _,_,keyboard in app.tg.messages for row in (keyboard or []) for b in row)
    finally:gate.set();await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_kuro_test_uses_api_and_records_health(tmp_path,monkeypatch):
    import bot.anime as anime
    app=app_at(tmp_path);seen=[]
    async def search(net,site,q):seen.append(q);return [result(site)]
    monkeypatch.setattr(anime,'search',search)
    try:
        await app.admin_callback(1,1,'admin:test:kuroiru');await drain(app)
        assert seen==['dear']
        assert (await app.catalog.health('kuroiru'))['success']==1
        latest=await app.store.get('site_test_latest','kuroiru:search')
        await app.admin_callback(1,1,'admin:sitelogs:'+latest)
        assert 'SITE_TEST_SUCCESS' in app.tg.messages[-1][1]
    finally:await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_failure_logs_http_status_without_secret_and_retry(tmp_path):
    app=app_at(tmp_path);site=app.registry.sites['gokuhd']
    class Blocked(FakeNetwork):
        async def fetch(self,session,url,allowed,**kwargs):
            return Response(url,403,{'content-type':'text/html'},b'<html>Forbidden secret</html>')
    app.network=Blocked({})
    try:
        await app.admin_callback(1,1,'admin:test:gokuhd');await drain(app)
        job=await app.store.get('site_test_latest','gokuhd:search')
        events=await app.store.logs(job=job)
        assert any(json.loads(e['payload']).get('status')==403 for e in events)
        assert any(e['code']=='SITE_TEST_FAILED' for e in events)
        assert 'Forbidden secret' not in str(events)
        await app.admin_callback(1,1,'admin:siteretry:'+job)
        assert not app.site_test_tasks
        assert 'wait' in app.tg.messages[-1][1].lower()
    finally:await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_details_test_is_fresh_single_source_and_logs_counts(tmp_path,monkeypatch):
    app=app_at(tmp_path)
    async def search(self,site,*args,**kw):return [result(site)]
    async def details(self,r,j,u,force_refresh=False):
        assert force_refresh and len(r.refs)==1
        return Media(**r.model_dump())
    monkeypatch.setattr(Catalog,'search_one',search);monkeypatch.setattr(Catalog,'details',details)
    try:
        await app.admin_callback(1,1,'admin:test:rogmovies');await drain(app)
        job=await app.store.get('site_test_latest','rogmovies:search')
        await app.admin_callback(1,1,'admin:sitedetails:'+job);await drain(app)
        detail_job=await app.store.get('site_test_latest','rogmovies:details')
        events=await app.store.logs(job=detail_job)
        assert any(json.loads(x['payload']).get('variants')==0 for x in events)
    finally:await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_non_staff_callback_cannot_start_test(tmp_path):
    app=app_at(tmp_path)
    try:
        await app.store.put('access',2,True)
        await app.callback({'id':'x','from':{'id':2},'message':{'chat':{'id':2}},'data':'admin:test:gokuhd'})
        assert not app.site_test_tasks
    finally:await app.close();await app.store.close()

def test_recovery_ignores_menu_and_ads_even_with_download_label():
    doc=soup('''<nav><a href="https://catalog.example/">Download Movies</a></nav>
      <div class="ad-container"><a href="https://advert.example/">Continue</a></div>
      <a href="https://real.example/file">Download now</a>''')
    links,unknown=recovery_links(doc,'https://host.example/a',['host.example','real.example'],lambda _:None)
    assert links==['https://real.example/file'] and unknown==[]

@pytest.mark.asyncio
async def test_status_only_empty_results_and_timeout(tmp_path,monkeypatch):
    app=app_at(tmp_path)
    async def empty(*args,**kwargs):return []
    monkeypatch.setattr(Catalog,'search_one',empty)
    try:
        await app.admin_command(1,1,'/sites','status')
        assert not app.site_test_tasks
        await app.admin_callback(1,1,'admin:test:rogmovies');await drain(app)
        job=await app.store.get('site_test_latest','rogmovies:search')
        assert (await app.store.get('site_tests',job))['counts']=={'results':0}
        async def timeout(*args,**kwargs):raise TimeoutError()
        monkeypatch.setattr(Catalog,'search_one',timeout)
        await app.admin_callback(1,1,'admin:test:gokuhd');await drain(app)
        job=await app.store.get('site_test_latest','gokuhd:search')
        assert (await app.store.get('site_tests',job))['reason']=='TIMEOUT'
        assert any(e['code']=='SITE_TEST_FAILED' for e in await app.store.logs(errors=True))
    finally:await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_goku_api_query_and_pagination(tmp_path):
    from urllib.parse import urlsplit,parse_qs
    app=app_at(tmp_path);site=app.registry.sites['gokuhd'];site.search.max_pages=2
    class API(FakeNetwork):
        async def fetch(self,session,url,allowed,**kwargs):
            parts=urlsplit(url);params=parse_qs(parts.query)
            assert parts.path=='/search.php' and params['q']==['dear child']
            self.calls.append(url)
            page=params['page'][0]
            data={'found':2,'hits':[{'document':{'post_title':'Dear Child Season '+page,'permalink':'https://gokuhd.com/child-'+page}}]}
            return Response(url,200,{},json.dumps(data).encode())
    network=API({});app.catalog.network=network
    try:
        found=await app.catalog.search_one(site,'dear child','api',1,force_refresh=True)
        assert len(found)==2 and len(network.calls)==2
    finally:await app.close();await app.store.close()

@pytest.mark.asyncio
async def test_hdhub_alias_survives_saved_override(tmp_path):
    from bot.runtime_config import load_runtime
    from bot.network import validate_url
    from urllib.parse import urlsplit
    app=app_at(tmp_path)
    try:
        raw=app.registry.sites['hdhub4u'].model_dump();raw['mirrors']=[]
        r=await load_runtime(app.registry.root,app.store,{'sites':{'hdhub4u':raw}})
        site=r.sites['hdhub4u'];hosts=[urlsplit(x).hostname for x in [site.base_url,*site.mirrors]]
        validate_url('https://new6.hdhub4u.cl/mayday/',hosts)
        with pytest.raises(FlowError):validate_url('https://unrelated.example/mayday/',hosts)
    finally:await app.close();await app.store.close()

@pytest.mark.parametrize('name,count',[('goku-daemons',2),('goku-solo',8)])
def test_supplied_goku_detail_pages(name,count):
    from pathlib import Path
    from bot.parser import parse_media
    from bot.rogmovies import parse_episodes
    r=Registry();r.load();site=r.sites['gokuhd']
    folder=Path(__file__).parent/'fixtures/diagnostics'
    media=parse_media((folder/(name+'.html')).read_text(encoding='utf-8'),site.base_url,site,result(site))
    assert len(media.variants)==count
    if name=='goku-daemons':
        episodes=parse_episodes((folder/'nexdrive-episodes.html').read_text(encoding='utf-8'),'https://nexdrive.fit/',media.variants[0],site)
        assert [v.episode for v in episodes]==list(range(1,21))

@pytest.mark.asyncio
async def test_detail_refresh_does_not_reuse_cached_success(tmp_path):
    from pathlib import Path
    app=app_at(tmp_path);site=app.registry.sites['gokuhd'];calls=[]
    html=(Path(__file__).parent/'fixtures/diagnostics/goku-daemons.html').read_text(encoding='utf-8')
    async def fetch(url,site):
        calls.append(url)
        if len(calls)>1:raise FlowError('BLOCKED','Host returned HTTP 403')
        return html,url
    app.catalog.fetch_site=fetch
    try:
        await app.catalog.details(result(site),'one',1)
        await app.catalog.details(result(site),'cached',1)
        assert len(calls)==1
        with pytest.raises(FlowError,match='403'):
            await app.catalog.details(result(site),'fresh',1,force_refresh=True)
        assert len(calls)==2
    finally:await app.close();await app.store.close()
