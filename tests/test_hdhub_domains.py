from pathlib import Path
import pytest
from bot.config import Registry
from bot.parser import parse_media
from bot.models import SearchResult,Reference
from bot.runtime_config import load_runtime
from bot.storage import Store
from bot.resolver import Resolver

def check(r):
    site=r.sites['hdhub4u']
    html=(Path(__file__).parent/'fixtures/diagnostics/hdhub-dear.html').read_text(encoding='utf-8')
    result=SearchResult(id='dear',title='DeAr',kind='movie',year=2024,refs=[Reference(site=site.id,url=site.base_url+'/dear/')])
    media=parse_media(html,result.refs[0].url,site,result)
    assert len(media.variants)==5
    assert [v.resolution for v in media.variants]==['480p','720p','720p','1080p','1080p']
    assert [v.size for v in media.variants]==['400MB','700MB','1GB','2.6GB','3.1GB']
    assert all('WATCH' not in v.filename.upper() for v in media.variants)
    resolver=Resolver(r,None,None)
    assert resolver.provider_for('https://hubdrive.pics/file/example').id=='hubdrive'
    assert resolver.provider_for('https://greenmotors.club/example').id=='greenmount'

def test_supplied_dear_downloads():
    r=Registry();r.load();check(r)

@pytest.mark.asyncio
async def test_saved_configuration_migrates_exact_hosts(tmp_path):
    r=Registry();r.load();site=r.sites['hdhub4u'].model_dump()
    hosts={'hubcdn.wiki','greenmotors.club','hubdrive.pics'}
    site['provider_hosts']=[h for h in site['provider_hosts'] if h not in hosts]
    providers={k:v.model_dump() for k,v in r.providers.items()}
    for p in providers.values():
        p['hosts']=[h for h in p['hosts'] if h not in hosts]
        p['allowed_hosts']=[h for h in p['allowed_hosts'] if h not in hosts]
    store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:check(await load_runtime(r.root,store,{'sites':{'hdhub4u':site},'providers':providers}))
    finally:await store.close()
