from pathlib import Path
import json
import pytest
from bot.config import Registry
from bot.models import SearchResult, FlowError
from bot.parser import parse_media
from bot.resolver import Resolver
from bot.storage import Store
from bot.runtime_config import load_runtime
from bot.network import Response
from test_core import FakeNetwork
from test_recovery import page

FIXTURES = Path(__file__).parent / 'fixtures'
HUB = 'https://hubcloud.ist/drive/q2dzkp2psnhfzff'
DRIVE = 'https://hubdrive.tips/file/16254736976'


def selected_variant(registry):
    media = parse_media((FIXTURES/'arrival_mirrors.html').read_text(encoding='utf-8'),
        'https://4khdhub.one/arrival-movie/', registry.sites['4khdhub'],
        SearchResult(id='arrival',title='Arrival',year=2016,kind='movie'))
    selected = next(v for v in media.variants if any(x.url == HUB for x in v.links))
    assert {x.url for x in selected.links} == {HUB,DRIVE}
    assert 'Weasley HONE-LUMiX' in selected.filename
    return selected


@pytest.mark.asyncio
@pytest.mark.parametrize('generator', [False, True])
async def test_real_missing_page_falls_back_to_same_version(tmp_path, generator):
    registry=Registry();registry.load()
    variant=selected_variant(registry)
    end='https://video-downloads.googleusercontent.com/test-file'
    pixel='https://pixel.hubcloud.ist/test-file'
    gen='https://gamerxyt.com/test-generator'
    final=Response(end,206,{'content-type':'video/mkv'},b'\x1aE\xdf\xa3fixture')
    pages={DRIVE:page('https://hubdrive.sbs/file/16254736976',
        (FIXTURES/'hubdrive_missing.html').read_text(encoding='utf-8')),
        HUB:page(HUB,f'<a id="download" href="{gen if generator else pixel}">Generate Download Link</a>'),
        pixel:final, gen:page(gen,f'<a href="{pixel}">Download</a>')}
    net=FakeNetwork(pages);store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        result=await Resolver(registry,net,store).resolve(variant,['hubdrive','hubcloud'],'test',7)
        assert result.url==end
        assert net.calls==[DRIVE,HUB]+([gen] if generator else [])+[pixel]
        logs=await store.logs()
        assert any(x['code']=='PROVIDER_FAILED' and json.loads(x['payload']).get('reason')=='LINK_EXPIRED' for x in logs)
        assert not await store.list('host_proposals',50)
    finally:await store.close()


@pytest.mark.asyncio
async def test_persisted_provider_override_receives_exact_pixel_host(tmp_path):
    registry=Registry();registry.load()
    saved={'providers':{}}
    for name in ('generator','hubcloud'):
        raw=registry.providers[name].model_dump()
        raw['allowed_hosts'].remove('pixel.hubcloud.ist')
        saved['providers'][name]=raw
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        await store.put('runtime','configuration',saved)
        loaded=await load_runtime(registry.root,store)
        for name in saved['providers']:
            hosts=loaded.providers[name].allowed_hosts
            assert 'pixel.hubcloud.ist' in hosts
            assert '*.workers.dev' not in hosts
            assert 'bonuscaf.com' not in hosts
    finally:await store.close()
