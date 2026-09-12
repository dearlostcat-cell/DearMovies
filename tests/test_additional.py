import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
import pytest
import yaml
from bot.config import ROOT, Registry, Provider, Site
from bot.models import FlowError, Variant, Link
from bot.network import Network, Response
from bot.parser import transform, extract_number, soup
from bot.resolver import Resolver, file_response, validate_expected
from bot.storage import Store
from test_core import FakeNetwork


def test_configurable_normalization():
    site=Site(id='example',name='Example',base_url='https://example.com',transform={'title':['html_unescape','collapse_spaces','strip']},extract={'season':[r'Volume (\d+)']})
    assert transform('  A &amp;   B ', 'title', site)=='A & B'
    assert extract_number('Volume 12', 'season', site)==12


def test_gamerxyt_fixture_matches_observed_generator_controls():
    registry = Registry(); registry.load()
    provider = registry.providers['generator']
    html = (ROOT / 'tests/fixtures/gamerxyt_generator.html').read_text()
    document = soup(html)
    selected = {
        a.get('href')
        for selector in provider.link_selectors
        for a in document.select(selector)
        if a.get('href')
    }
    assert 'https://pixel.hubcloud.cx/fixture-route' in selected
    assert 'https://pixeldrain.dev/fixture-route' in selected
    script_urls = {
        match[1]
        for pattern in provider.literal_url_patterns
        for match in re.finditer(pattern, html)
    }
    assert 'https://pixeldrain.dev/fixture-route' in script_urls
    assert not document.select('form')


def test_specific_provider_error_survives_generic_fallback_error():
    specific = FlowError('UNSUPPORTED', 'No configured next link at gamerxyt.com')
    generic = FlowError('INVALID_RESPONSE', 'None of the configured routes produced a file')
    assert Resolver.prefer_error(generic, specific).code == 'UNSUPPORTED'
    assert Resolver.prefer_error(specific, generic).code == 'UNSUPPORTED'


@pytest.mark.asyncio
async def test_duplicate_provider_failure_is_logged_once(tmp_path):
    store = Store('sqlite:///' + str(tmp_path / 'duplicate.db'))
    try:
        registry = Registry(); registry.load()
        resolver = Resolver(registry, Network(), store)
        provider = Provider(id='test', hosts=['example.test'], allowed_hosts=['example.test'])
        ctx = {'job': 'DUPLICATE', 'user': 1}
        error = FlowError('UNSUPPORTED', 'No configured next link at example.test')
        await resolver.log_provider_failure(ctx, provider, error)
        await resolver.log_provider_failure(ctx, provider, error)
        events = await store.logs(job='DUPLICATE')
        assert [event['code'] for event in events] == ['PROVIDER_FAILED']
    finally:
        await store.close()


def test_atomic_reload_retains_valid_registry(tmp_path):
    import shutil
    shutil.copytree(ROOT/'config',tmp_path/'config')
    registry=Registry(tmp_path); registry.load(atomic=True)
    previous=registry.sites
    (tmp_path/'config/sites/bad.yaml').write_text('id: broken\nunknown_field: true')
    with pytest.raises(ValueError): registry.load(atomic=True)
    assert registry.sites is previous and registry.sites['4khdhub'].name=='4KHDHub'


def test_mislabeled_zip_and_wrong_file():
    result=file_response(Response('https://files.example/file',206,{'content-type':'video/x-eac3','content-range':'bytes 0-5/46376535351'},b'PK\x03\x04abcdef'))
    assert result.detected_container=='zip'
    assert validate_expected(result,Variant(id='1',filename='season.zip',size='43.19 GB')) is result
    with pytest.raises(FlowError): validate_expected(result,Variant(id='1',filename='episode.mkv'))
    with pytest.raises(FlowError): validate_expected(result,Variant(id='1',filename='season.zip',size='1 GB'))


@pytest.mark.asyncio
async def test_query_parameter_handoff(tmp_path):
    registry=Registry();registry.load()
    registry.providers={'p':Provider(id='p',hosts=['landing.example'],allowed_hosts=['landing.example','files.example'],query_url_parameters=['link'])}
    url='https://landing.example/bgmi/?link=https%3A%2F%2Ffiles.example%2Fvideo'
    network=FakeNetwork({url:Response(url,200,{'content-type':'text/html'},b'<a id="downloadBtn"></a>'), 'https://files.example/video':Response('https://files.example/video',206,{'content-type':'video/mkv'},b'\x1aE\xdf\xa3')})
    store=Store('sqlite:///'+str(tmp_path/'test.db'))
    try:
        result=await Resolver(registry,network,store).resolve(Variant(id='a',filename='movie.mkv',links=[Link(label='download',url=url)]),['p'],'QUERY',1)
        assert result.detected_container=='mkv'
    finally: await store.close()


@pytest.mark.asyncio
async def test_declarative_form_json_flow(tmp_path):
    registry=Registry();registry.load()
    registry.providers={'p':Provider(id='p',hosts=['api.example'],allowed_hosts=['api.example','files.example'],mode='steps',steps=[
      {'id':'page','extract':{'csrf':{'selector':'input[name=csrf]','attribute':'value'},'submit_url':{'selector':'form','attribute':'action'}},'next':'submit'},
      {'id':'submit','action':'form','url_from':'submit_url','method':'POST','form':{'csrf':'$csrf'},'extract':{'file':{'json_path':'result.url'}},'next':'check'},
      {'id':'check','action':'validate','url_from':'file'}])}
    class RecordingNetwork(FakeNetwork):
        async def fetch(self,session,url,*args,**kwargs):
            if url.endswith('/submit'): assert kwargs['method']=='POST' and kwargs['data']=={'csrf':'example-csrf'}
            return await super().fetch(session,url,*args,**kwargs)
    network=RecordingNetwork({
      'https://api.example/start':Response('https://api.example/start',200,{'content-type':'text/html'},b'<form action="/submit"><input name="csrf" value="example-csrf"></form>'),
      'https://api.example/submit':Response('https://api.example/submit',200,{'content-type':'application/json'},b'{"result":{"url":"https://files.example/file"}}'),
      'https://files.example/file':Response('https://files.example/file',206,{'content-type':'application/zip'},b'PK\x03\x04')})
    store=Store('sqlite:///'+str(tmp_path/'test.db'))
    try:
        result=await Resolver(registry,network,store).resolve(Variant(id='a',filename='file.zip',links=[Link(label='download',url='https://api.example/start')]),['p'],'STEPS',1)
        assert result.detected_container=='zip'
    finally: await store.close()


@pytest.mark.asyncio
async def test_unbounded_remote_body_is_cut_off():
    class Stream:
        def __init__(self): self.chunks=0
        async def iter_chunked(self,size):
            for _ in range(100000): self.chunks+=1; yield b'x'*size
    class FakeResponse:
        def __init__(self):
            self.content=Stream(); self.status=200; self.headers={'Content-Type':'application/octet-stream'}; self.url='https://files.example/file'; self.closed=False
        async def __aenter__(self): return self
        async def __aexit__(self,*args): self.closed=True
        def close(self): self.closed=True
    response=FakeResponse()
    class Session:
        def request(self,*args,**kwargs): return response
    answer=await Network().fetch(Session(),'https://files.example/file',['files.example'],max_bytes=65536,range_probe=True)
    assert len(answer.body)==65536 and response.closed and response.content.chunks==1


def test_tokenless_startup_and_health(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    env=os.environ.copy();env.update(BOT_TOKEN='',DATABASE_URL='sqlite:///'+str(tmp_path/'health.db'),PORT=str(port))
    proc=subprocess.Popen([sys.executable,str(ROOT/'main.py')],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        deadline=time.time()+15
        while time.time()<deadline:
            if proc.poll() is not None: raise AssertionError(proc.communicate()[1].decode())
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/health',timeout=1) as response: result=json.load(response)
                break
            except OSError: time.sleep(.2)
        else: raise AssertionError('Health endpoint did not start')
        assert result['configured'] is False and result['polling'] is False
        assert 'BOT_TOKEN' not in json.dumps(result)
    finally:
        proc.terminate();proc.communicate(timeout=10)
