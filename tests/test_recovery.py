import pytest
from bot.config import Registry,Provider
from bot.models import FlowError,Variant,Link
from bot.network import Response,validate_url
from bot.resolver import Resolver
from bot.storage import Store
from bot.recovery import recovery_links,save_host_proposal
from bot.parser import soup
from test_core import FakeNetwork,FakeTelegram

def page(url,body,truncated=False):return Response(url,200,{'content-type':'text/html'},body.encode(),truncated)
def file(url):return Response(url,206,{'content-type':'video/mp4'},b'\0\0\0\x18ftypisom')
def variant(url):return Variant(id='v',filename='test.mp4',links=[Link(label='Get',url=url)])

@pytest.mark.asyncio
async def test_hubdrive_redirect_then_hubcloud_generator_new_button(tmp_path):
    r=Registry();r.load()
    start='https://hubdrive.tips/file/a';landed='https://hubdrive.sbs/file/a'
    hub='https://hubcloud.ist/drive/a';gen='https://gamerxyt.com/new';end='https://video-downloads.googleusercontent.com/file'
    class RedirectNetwork(FakeNetwork):
        async def fetch(self,session,url,allowed,**kwargs):
            response=await super().fetch(session,url,allowed,**kwargs)
            validate_url(response.url,allowed)
            return response
    net=RedirectNetwork({start:page(landed,f'<a data-url="{hub}">Continue</a>'),hub:page(hub,f'<a href="{gen}" id="download">Generate</a>'),gen:page(gen,f'<a href="{end}" class="new-design">Download now</a>'),end:file(end)})
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        result=await Resolver(r,net,store).resolve(variant(start),['hubdrive'],'test',7)
        assert result.url==end and net.calls==[start,hub,gen,end]
        assert any(x['code']=='RECOVERY_LINKS_FOUND' for x in await store.logs())
    finally:await store.close()

def test_recovery_ignores_ads_and_private_destinations():
    doc=soup('<a href="https://ads.example/x">Ad</a><a href="http://127.0.0.1/x">Download</a><a href="https://new.example/x?token=secret">Download now</a><a href="https://cdn.example/f">Download</a>')
    links,unknown=recovery_links(doc,'https://host.example/a',['host.example','cdn.example'],lambda _:None)
    assert links==['https://cdn.example/f'] and unknown==['new.example']


def test_known_provider_navigation_and_auth_are_not_downloads():
    r=Registry();r.load()
    resolver=Resolver(r,None,None)
    base='https://hubdrive.sbs/file/a'
    doc=soup('''<a href="/">Home</a><a href="/terms">Terms</a>
        <a href="/file/unrelated">Other file</a><a href="/login">Continue</a>
        <a href="https://accounts.google.com/ServiceLogin">Download</a>
        <a href="https://hubcloud.ist/drive/a">Mirror</a>
        <a href="/download/a">Download now</a>''')
    links,unknown=recovery_links(doc,base,r.providers['hubdrive'].allowed_hosts,resolver.provider_for)
    assert links==['https://hubcloud.ist/drive/a','https://hubdrive.sbs/download/a']
    assert unknown==[]


@pytest.mark.asyncio
async def test_failed_pages_not_refetched_within_provider_attempt(tmp_path):
    r=Registry();r.load()
    a,b,c,shared=['https://hubdrive.sbs/'+x for x in ('file/a','download/b','download/c','download/shared')]
    end='https://hubcloud.ist/drive/end'
    net=FakeNetwork({a:page(a,f'<a href="{b}">Download</a><a href="{c}">Download</a>'),
        b:page(b,f'<a href="{shared}">Download</a>'),shared:page(shared,'No links'),
        c:page(c,f'<a href="{shared}">Download</a><a href="{end}">Download</a>'),end:file(end)})
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        result=await Resolver(r,net,store).resolve(variant(a),['hubdrive'],'test',7)
        assert result.url==end and net.calls.count(shared)==1
    finally:await store.close()


@pytest.mark.asyncio
async def test_hop_exhaustion_leaves_budget_for_other_provider(tmp_path):
    r=Registry();r.load();r.providers['hubdrive'].max_hops=2
    a,b,c=['https://hubdrive.sbs/download/'+x for x in 'abc']
    hub='https://hubcloud.ist/drive/backup';end='https://video-downloads.googleusercontent.com/f'
    net=FakeNetwork({a:page(a,f'<a href="{b}">Download</a>'),b:page(b,f'<a href="{c}">Download</a>'),
        hub:page(hub,f'<a href="{end}">Download</a>'),end:file(end)})
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        v=variant(a);v.links.append(Link(label='Backup',url=hub))
        result=await Resolver(r,net,store).resolve(v,['hubdrive','hubcloud'],'test',7)
        assert result.url==end and net.calls==[a,b,hub,end]
    finally:await store.close()


@pytest.mark.asyncio
async def test_google_login_redirect_not_proposed_as_provider(tmp_path):
    r=Registry();r.load();start='https://hubdrive.sbs/file/a'
    class AuthRedirect(FakeNetwork):
        async def fetch(self,*args,**kwargs):
            raise FlowError('UNSUPPORTED','Unconfigured destination: accounts.google.com')
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        with pytest.raises(FlowError):await Resolver(r,AuthRedirect({}),store).resolve(variant(start),['hubdrive'],'test',7)
        assert await store.list('host_proposals',50)==[]
        assert not any(x['code']=='HOST_APPROVAL_NEEDED' for x in await store.logs())
    finally:await store.close()


@pytest.mark.asyncio
async def test_existing_google_login_proposal_cannot_be_approved(tmp_path):
    from bot.app import BotApp
    r=Registry();r.load();r.settings.owner_ids=[1]
    store=Store('sqlite:///'+str(tmp_path/'state.db'));app=BotApp(r,store,FakeTelegram())
    try:
        ident=await save_host_proposal(store,'hubdrive','accounts.google.com','test')
        await app.management(1,1,'/hostapprove',ident+' hubdrive')
        assert await store.get('runtime','configuration') is None
        assert 'Google sign-in' in app.tg.messages[-1][1]
    finally:await app.close();await store.close()

@pytest.mark.asyncio
async def test_unknown_host_is_queued_without_request_and_not_cooldown(tmp_path):
    r=Registry();r.load();url='https://gamerxyt.com/a'
    net=FakeNetwork({url:page(url,'<a href="https://new.example/file?token=secret">Download</a>')})
    store=Store('sqlite:///'+str(tmp_path/'state.db'));resolver=Resolver(r,net,store)
    try:
        for _ in range(6):
            with pytest.raises(FlowError,match='No configured next link'):await resolver.resolve(variant(url),['generator'],'test',7)
        assert set(net.calls)=={url}
        proposals=await store.list('host_proposals',50)
        assert len(proposals)==1 and proposals[0][1]['host']=='new.example'
        assert 'secret' not in str(proposals) and await store.get('provider_health','generator',{})=={}
    finally:await store.close()

@pytest.mark.asyncio
async def test_fragment_loop_terminates_without_refetch(tmp_path):
    r=Registry();r.load();a='https://gamerxyt.com/a';b='https://gamerxyt.com/b'
    net=FakeNetwork({a:page(a,f'<a id="download" href="{b}">Next</a>'),b:page(b,f'<a id="download" href="{a}#again">Next</a>')})
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        with pytest.raises(FlowError):await Resolver(r,net,store).resolve(variant(a),['generator'],'test',7)
        assert net.calls==[a,b]
    finally:await store.close()

@pytest.mark.asyncio
async def test_truncated_html_retried_without_range(tmp_path):
    r=Registry();r.load();start='https://gamerxyt.com/a';end='https://video-downloads.googleusercontent.com/f'
    class FullPage(FakeNetwork):
        async def fetch(self,session,url,allowed,**kw):
            self.calls.append((url,kw.get('range_probe')))
            if url==end:return file(end)
            if kw.get('range_probe'):return page(url,'<html>',True)
            return page(url,f'<a href="{end}">Download now</a>')
    net=FullPage({});store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        result=await Resolver(r,net,store).resolve(variant(start),['generator'],'test',7)
        assert result.url==end and net.calls[:2]==[(start,True),(start,False)]
    finally:await store.close()

@pytest.mark.asyncio
async def test_owner_approval_persists_and_staff_cannot_approve(tmp_path,monkeypatch):
    from bot.app import BotApp
    from bot.runtime_config import load_runtime
    import bot.runtime_config as runtime
    class DNS:
        async def resolve(self,*args):return []
        async def close(self):pass
    monkeypatch.setattr(runtime,'PublicResolver',DNS)
    r=Registry();r.load();r.settings.owner_ids=[1]
    store=Store('sqlite:///'+str(tmp_path/'state.db'));tg=FakeTelegram();app=BotApp(r,store,tg)
    try:
        ident=await save_host_proposal(store,'generator','new.example','job')
        await store.put('admins',2,True)
        await app.management(2,2,'/hostapprove',ident+' hubdrive')
        assert await store.get('runtime','configuration') is None
        await app.management(1,1,'/hostapprove',ident+' hubdrive')
        loaded=await load_runtime(r.root,store)
        assert 'new.example' in loaded.providers['hubdrive'].hosts
        assert await store.get('host_proposals',ident) is None
    finally:await app.close();await store.close()

@pytest.mark.asyncio
async def test_failed_mirror_does_not_poison_sibling_route(tmp_path):
    r=Registry();r.load();a='https://gamerxyt.com/a';b='https://hubdrive.sbs/file/b';shared='https://gamerxyt.com/shared';end='https://video-downloads.googleusercontent.com/f'
    class RetryRoute(FakeNetwork):
        async def fetch(self,session,url,*args,**kw):
            self.calls.append(url)
            if url==shared:
                return page(url,'<html>No link</html>' if self.calls.count(url)==1 else f'<a href="{end}">Download</a>')
            return self.pages[url]
    net=RetryRoute({a:page(a,f'<a id="download" href="{shared}">Next</a>'),b:page(b,f'<a href="{shared}">Continue</a>'),end:file(end)})
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        v=variant(a);v.links.append(Link(label='Backup',url=b))
        result=await Resolver(r,net,store).resolve(v,['generator','hubdrive'],'test',7)
        assert result.url==end and net.calls.count(shared)==2
    finally:await store.close()

@pytest.mark.asyncio
async def test_private_host_approval_rejected(tmp_path):
    from bot.app import BotApp
    r=Registry();r.load();r.settings.owner_ids=[1]
    store=Store('sqlite:///'+str(tmp_path/'state.db'));app=BotApp(r,store,FakeTelegram())
    try:
        ident=await save_host_proposal(store,'hubdrive','127.0.0.1','test')
        await app.management(1,1,'/hostapprove',ident+' hubdrive')
        assert await store.get('runtime','configuration') is None
        assert 'Change rejected' in app.tg.messages[-1][1]
    finally:await app.close();await store.close()
