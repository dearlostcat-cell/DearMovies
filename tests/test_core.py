import asyncio
import copy
import json
import time
from pathlib import Path
import pytest
from bot.config import ROOT, Registry, Site, Provider
from bot.models import FlowError, Reference, SearchResult, Variant, Link, merge_results, split_query
from bot.parser import parse_search, parse_media, episode_identity
from bot.navigation import selection, matching, rows
from bot.network import Network, Response, validate_url, host_matches
from bot.resolver import Resolver, file_response
from bot.service import Catalog, SharedRequests
from bot.storage import Store, redact
from bot.app import BotApp


@pytest.fixture
def registry():
    r = Registry(); r.load(atomic=True); r.settings.mock_mode = True
    r.settings.allowed_users = [7, 8]
    r.sites = {'4khdhub': r.sites['4khdhub']}
    return r


def media_fixture(registry, kind):
    site = registry.sites['4khdhub']
    return parse_media(registry.fixture(site,kind),site.base_url,site,SearchResult(id=kind,title=kind,kind=kind,refs=[Reference(site=site.id,url=site.base_url+'/sample')]))


def test_movie_versions(registry):
    media = media_fixture(registry,'movie')
    assert media.title == 'My Dearest Assassin'
    assert len(media.variants) == 5
    versions = [v for v in media.variants if v.resolution == '1080p']
    assert {v.size for v in versions} == {'7.34 GB','5.89 GB','5.58 GB'}
    assert all(v.languages == 'Hindi, English, Thai' for v in versions)
    assert len({v.id for v in versions}) == 3
    assert all(len(v.links)==2 for v in media.variants)


def test_series_pack_episode_scoping(registry):
    media = media_fixture(registry,'series')
    assert len(media.variants) == 165
    assert sum(v.mode=='pack' for v in media.variants)==12
    assert sum(v.mode=='episode' for v in media.variants)==153
    assert not any(v.season==4 and v.mode=='pack' for v in media.variants)
    assert all(v.size != 'Unknown' for v in media.variants)
    assert len({(v.season,v.episode) for v in media.variants if v.mode=='episode'}) == 31
    assert {v.episode for v in media.variants if v.season==4} == set(range(1,8))


def test_search_and_related_exclusion(registry):
    site = registry.sites['4khdhub']
    found,_=parse_search(registry.fixture(site,'search'),site.base_url,site)
    assert len(found)==12
    assert found[0].title=='Dear X'
    assert all(r.poster.startswith('https://') for r in found)
    with pytest.raises(FlowError): parse_search(registry.fixture(site,'series'),site.base_url,site)


def test_changed_vs_empty(registry):
    site = registry.sites['4khdhub']
    assert parse_search('<main><div class="latest-releases"><p class="no-results">No results</p></div></main>',site.base_url,site)[0] == []
    with pytest.raises(FlowError,match='no cards'): parse_search('<main><div class="latest-releases">Changed</div></main>',site.base_url,site)


def test_navigation_no_nonexistent_pack(registry):
    media=media_fixture(registry,'series'); filters={'season':4}
    field,options,skipped=selection(media,filters)
    assert filters['mode']=='episode'
    assert field=='resolution'
    filters['resolution']='1080p'
    field,options,_=selection(media,filters)
    assert field=='version'
    assert len(options)==4
    filters['version']=options[0]['value']
    field,options,_=selection(media,filters)
    assert field=='episode_key'
    assert [o['label'] for o in options]==[f'Episode {i}' for i in range(1,8)]
    filters['episode_key']=options[0]['value']
    assert selection(media,filters)[0]=='ready'
    assert len(matching(media,filters))==1


@pytest.mark.parametrize('filename,expected', [('A.S01E01.mkv',(1,1,None)),('A.S01E01-E02.mkv',(1,1,2)),('A.S01E01E02.mkv',(1,1,2)),('A.S01E01-08.mkv',(1,1,8)),('Season 1 Complete.zip',(1,None,None))])
def test_episode_ranges(filename,expected): assert episode_identity(filename)==expected


def test_rank_merge_and_unknown_year():
    def result(title,year,site): return SearchResult(id=site+str(year),title=title,year=year,kind='movie',refs=[Reference(site=site,url='https://example.com/'+title)])
    merged=merge_results([result('The Thing',1982,'a'),result('The Thing',1982,'b'),result('The Thing',2011,'c'),result('The Thing',None,'d')],'The Thing')
    assert len(merged)==3
    assert len(next(x for x in merged if x.year==1982).refs)==2
    assert split_query('dear 1080p hdr')==('dear',['1080p','hdr'])
    assert split_query('"Movie"')==('Movie',[])
    assert split_query('Movie')==('Movie',[])


@pytest.mark.parametrize('url', ['http://127.0.0.1/','http://169.254.169.254/','https://localhost/','file:///etc/passwd','https://example.com.evil.test/','https://user:password@example.com/','https://example.com:8081/'])
def test_url_rejection(url):
    with pytest.raises(FlowError): validate_url(url,['example.com'])


def test_allowlist_subdomains():
    assert host_matches('video.googleusercontent.com',['*.googleusercontent.com'])
    assert not host_matches('googleusercontent.com.evil.test',['*.googleusercontent.com'])


def test_validation_does_not_accept_html_or_error():
    assert file_response(Response('https://files.example/a',200,{'content-type':'text/html'},b'<html>download</html>')) is None
    assert file_response(Response('https://files.example/a',200,{'content-type':'application/octet-stream'},b'<html>Error</html>')) is None
    with pytest.raises(FlowError): file_response(Response('https://files.example/a',403,{},b''))
    result=file_response(Response('https://files.example/a?token=secret',206,{'content-type':'video/x-matroska','content-range':'bytes 0-99/123456','content-length':'100'},b'\x1aE\xdf\xa3'))
    assert result.size==123456 and result.temporary=='likely'


def test_redaction():
    data=redact({'cookie':'abc','url':'https://files.example/sensitive-path?sig=abcd','note':'123456789:abcdefghijklmnopqrstuvwx0123456789'})
    assert 'abc' not in json.dumps(data)
    assert 'sensitive-path' not in json.dumps(data)
    assert 'files.example' in data['url']


def test_config_rejects_arbitrary_code():
    with pytest.raises(Exception): Site(id='x',name='X',base_url='https://x.example',run_python='evil')
    with pytest.raises(Exception): Provider(id='x',hosts=['x.example'],allowed_hosts=['x.example'],mode='steps',steps=[{'id':'start','next':'missing'}])


@pytest.mark.asyncio
async def test_store_ttl_and_backup(tmp_path):
    store=Store('sqlite:///'+str(tmp_path/'test.db'))
    try:
        await store.put('example','x',{'value':42},ttl=-1)
        assert await store.get('example','x') is None
        await store.put('favorites_1','movie',{'title':'Film'})
        await store.put('sessions','sid',{'signed':'token'})
        exported=await store.export()
        assert len(exported)==2  # expired example plus favorite; sessions excluded
        assert not any(r['space']=='sessions' for r in exported)
        await store.log('A',1,'JOB_FAILED',url='https://files.example/token')
        assert len(await store.logs(errors=True))==1
        await store.cleanup()
    finally: await store.close()


@pytest.mark.asyncio
async def test_shared_cancellation_isolated():
    shared=SharedRequests(); gate=asyncio.Event(); calls=0
    async def factory():
        nonlocal calls
        calls+=1; await gate.wait(); return 42
    a=asyncio.create_task(shared.get('same',factory)); b=asyncio.create_task(shared.get('same',factory))
    await asyncio.sleep(.01); a.cancel(); await asyncio.gather(a,return_exceptions=True)
    assert not b.done()
    gate.set(); assert await b==42 and calls==1
    assert not shared.entries


class FakeSession:
    async def __aenter__(self): return self
    async def __aexit__(self,*args): pass


class FakeNetwork:
    def __init__(self,pages): self.pages,self.calls=pages,[]
    def session(self): return FakeSession()
    async def fetch(self,session,url,*args,**kwargs):
        self.calls.append(url)
        response=self.pages[url]
        if isinstance(response,Exception): raise response
        return response


@pytest.mark.asyncio
async def test_multi_hop_fallback_and_final(tmp_path,registry):
    registry.settings.mock_mode=False
    registry.providers={'test':Provider(id='test',hosts=['files.example','generator.example'],allowed_hosts=['files.example','generator.example','cdn.example'],link_selectors=['a.primary','a.backup','a.final'])}
    pages={
      'https://files.example/a':Response('https://files.example/a',200,{'content-type':'text/html'},b'<a class="primary" href="https://generator.example/b">go</a>'),
      'https://generator.example/b':Response('https://generator.example/b',200,{'content-type':'text/html'},b'<a class="primary" href="https://cdn.example/dead">primary</a><a class="backup" href="https://cdn.example/file">backup</a>'),
      'https://cdn.example/dead':FlowError('UNAVAILABLE','Down'),
      'https://cdn.example/file':Response('https://cdn.example/file',206,{'content-type':'video/mp4','content-range':'bytes 0-15/200'},b'\x00\x00\x00\x18ftypisom')}
    net=FakeNetwork(pages); store=Store('sqlite:///'+str(tmp_path/'test.db'))
    try:
        resolver=Resolver(registry,net,store)
        result=await resolver.resolve(Variant(id='a',filename='movie.mp4',links=[Link(label='get',url='https://files.example/a')]),['test'],'A',1)
        assert result.url=='https://cdn.example/file'
        assert net.calls==list(pages)
        logs=await store.logs(job='A')
        assert any(x['code']=='PROVIDER_SUCCESS' for x in logs)
    finally: await store.close()


@pytest.mark.asyncio
async def test_blocked_does_not_fallback(tmp_path,registry):
    registry.settings.mock_mode=False
    registry.providers={'test':Provider(id='test',hosts=['files.example'],allowed_hosts=['files.example'],link_selectors=['a'])}
    net=FakeNetwork({'https://files.example/a':Response('https://files.example/a',403,{},b'blocked')})
    store=Store('sqlite:///'+str(tmp_path/'test.db'))
    try:
        with pytest.raises(FlowError,match='403'):
            await Resolver(registry,net,store).resolve(Variant(id='a',filename='x',links=[Link(label='a',url='https://files.example/a'),Link(label='b',url='https://files.example/b')]),['test'],'A',1)
        assert len(net.calls)==1
    finally: await store.close()


class FakeTelegram:
    def __init__(self): self.messages=[]; self.counter=0
    async def text(self,chat,text,keyboard=None,message=None):
        self.counter+=1; self.messages.append((chat,text,keyboard)); return {'message_id':message or self.counter}
    async def call(self,method,**kwargs):
        self.messages.append((kwargs.get('chat_id'),method,kwargs)); self.counter+=1
        return {'message_id':self.counter,'photo':[{'file_id':'cached-photo'}]}


@pytest.mark.asyncio
async def test_bot_search_choose_save_dryrun_and_owner_guard(tmp_path,registry):
    store=Store('sqlite:///'+str(tmp_path/'test.db')); tg=FakeTelegram(); app=BotApp(registry,store,tg)
    try:
        await app.command({'from':{'id':7},'chat':{'id':7},'text':'/s dear'})
        await asyncio.gather(*list(app.jobs.values()))
        sid=await store.get('last_session',7); s=await store.get('sessions',sid)
        assert s['screen']=='results' and len(s['results'])==12
        idx=next(i for i,x in enumerate(s['results']) if x['title']=='My Dearest Assassin')
        async def click(s,action,arg=''):
            await app.callback({'id':'callback','from':{'id':7},'message':{'chat':{'id':7}},'data':app.cb(s,action,arg)})
        await click(s,'title',idx)
        await asyncio.gather(*list(app.jobs.values()))
        s=await store.get('sessions',sid)
        assert s['screen']=='choice' and s['stage']=='resolution'
        await click(s,'favorite'); s=await store.get('sessions',sid)
        assert await store.get('favorites_7',s['media']['id'])
        for _ in range(8):
            if s['stage']=='ready': break
            await click(s,'choose',0); s=await store.get('sessions',sid)
        assert s['stage']=='ready'
        await click(s,'resolve'); await asyncio.gather(*list(app.jobs.values()))
        s=await store.get('sessions',sid)
        assert 'Dry run complete' in s['error']
        await app.command({'from':{'id':7},'chat':{'id':7},'text':'/maintenance on'})
        assert app.maintenance is False
        await app.command({'from':{'id':8037733052},'chat':{'id':8037733052},'text':'/maintenance on'})
        assert app.maintenance is True
    finally: await app.close(); await store.close()


@pytest.mark.asyncio
async def test_foreign_and_stale_callbacks(tmp_path,registry):
    store=Store('sqlite:///'+str(tmp_path/'test.db')); tg=FakeTelegram(); app=BotApp(registry,store,tg)
    try:
        s=await app.new_session(7,7,'x'); await app.render(s)
        stale=app.cb(s,'page',1); await app.render(s)
        await app.callback({'id':'cb','from':{'id':7},'message':{'chat':{'id':7}},'data':stale})
        assert 'older menu' in tg.messages[-1][1]
        await app.callback({'id':'cb','from':{'id':8},'message':{'chat':{'id':7}},'data':app.cb(s,'page',1)})
        assert 'another person' in tg.messages[-1][1]
    finally: await app.close(); await store.close()
