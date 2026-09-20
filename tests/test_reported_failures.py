import json
import pytest
from bot.config import Registry
from bot.runtime_config import load_runtime
from bot.storage import Store
from bot.service import Catalog
from bot.site_tests import DiagnosticNetwork
from bot.models import SearchResult,Reference,FlowError
from bot.network import Response,validate_url
from test_core import FakeNetwork

@pytest.mark.asyncio
async def test_rog_redirect_allowed_for_saved_api_config(tmp_path):
    r=Registry();r.load();raw=r.sites['rogmovies'].model_dump();raw['mirrors']=[]
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    class Redirect(FakeNetwork):
        async def fetch(self,session,url,allowed,**kwargs):
            validate_url('https://rogmovies.onl/ts-search.php',allowed)
            with pytest.raises(FlowError):validate_url('https://unrelated.example/',allowed)
            return Response('https://rogmovies.onl/ts-search.php',200,{},b'{"hits":[],"found":0}')
    try:
        r=await load_runtime(r.root,store,{'sites':{'rogmovies':raw}})
        assert await Catalog(r,Redirect({}),store).search_one(r.sites['rogmovies'],'dear','test',1)==[]
    finally:await store.close()

@pytest.mark.asyncio
async def test_failed_details_keep_sanitized_structure(tmp_path):
    r=Registry();r.load();site=r.sites['4khdhub']
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    html='<h1 class="page-title">Dear X</h1><div class="new-download-layout"><a href="https://hubcloud.cx/secret?token=secret">Download</a><input value="secret"><script>secret</script></div>'
    class Page(FakeNetwork):
        async def fetch(self,session,url,allowed,**kwargs):return Response(url,200,{},html.encode())
    catalog=Catalog(r,DiagnosticNetwork(Page({}),store,'report',1,site.id,'details'),store)
    try:
        with pytest.raises(FlowError):
            await catalog.details(SearchResult(id='x',title='Dear X',kind='series',refs=[Reference(site=site.id,url=site.base_url+'/x')]),'report',1,force_refresh=True)
        report=await store.get('inspect','report')
        assert report and 'new-download-layout' in report['html']
        assert 'secret' not in json.dumps(report) and '<script' not in report['html']
    finally:await store.close()
