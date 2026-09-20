from pathlib import Path
import pytest
from bot.config import Registry
from bot.parser import parse_search
from bot.service import Catalog
from bot.models import FlowError
from bot.storage import Store
from bot.runtime_config import load_runtime

def test_goku_supplied_card_layout():
    r=Registry();r.load();s=r.sites['gokuhd']
    html=(Path(__file__).parent/'fixtures/goku_home_cards.html').read_text(encoding='utf-8')
    values,_=parse_search(html,s.base_url,s)
    assert len(values)==10
    assert all(v.refs[0].url.startswith('https://gokuhd.com/') and v.title for v in values)
    assert s.search_api=='/search.php'

@pytest.mark.asyncio
async def test_fresh_test_cannot_hide_block_behind_cache(tmp_path):
    r=Registry();r.load();s=r.sites['gokuhd'];s.search.max_pages=1;s.search_api=''
    store=Store('sqlite:///'+str(tmp_path/'state.db'));c=Catalog(r,None,store);calls=[]
    html=(Path(__file__).parent/'fixtures/goku_home_cards.html').read_text(encoding='utf-8')
    async def fetch(url,site):
        calls.append(url)
        if len(calls)>1:raise FlowError('BLOCKED','Host returned HTTP 403')
        return html,url
    c.fetch_site=fetch
    try:
        assert len(await c.search_one(s,'dear','first',1))==10
        assert len(await c.search_one(s,'dear','cached',1))==10
        assert len(calls)==1 and calls[0]=='https://gokuhd.com/?s=dear'
        with pytest.raises(FlowError,match='403'):await c.search_one(s,'dear','fresh',1,force_refresh=True)
        assert len(calls)==2
    finally:await store.close()

@pytest.mark.asyncio
async def test_old_goku_override_keeps_owner_domain_but_migrates_layout(tmp_path):
    r=Registry();r.load();raw=r.sites['gokuhd'].model_dump();raw.update(search_api='',base_url='https://custom.example')
    raw['search']['parameter']='q';store=Store('sqlite:///'+str(tmp_path/'state.db'))
    try:
        loaded=await load_runtime(r.root,store,{'sites':{'gokuhd':raw}})
        s=loaded.sites['gokuhd']
        assert s.base_url=='https://custom.example' and s.search_api=='/search.php'
    finally:await store.close()
