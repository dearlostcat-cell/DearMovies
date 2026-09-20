import asyncio
from types import SimpleNamespace
import pytest
from bot.config import ROOT, Registry
from bot.runtime_config import Administration, load_runtime
from bot.storage import Store
from bot.models import FlowError

class Admin(Administration):
    def owner(self, user): return user == 7
    async def install_registry(self, candidate): self.registry = candidate

@pytest.mark.asyncio
async def test_save_does_not_depend_on_search_and_supports_rollback(tmp_path, monkeypatch):
    import bot.runtime_config as runtime
    from bot.service import Catalog
    class DNS:
        async def resolve(self,*args): return []
        async def close(self): pass
    monkeypatch.setattr(runtime,'PublicResolver',DNS)
    async def blocked(*args): raise FlowError('BLOCKED','Host returned HTTP 403')
    monkeypatch.setattr(Catalog,'search_one',blocked)
    app=Admin();app.registry=Registry();app.registry.load()
    app.store=Store('sqlite:///'+str(tmp_path/'state.db'))
    app.management_lock=asyncio.Lock();app.network=None
    messages=[]
    async def text(chat,message): messages.append(message)
    app.tg=SimpleNamespace(text=text)
    old=app.registry.sites['gokuhd'].base_url
    try:
        await app.management(7,7,'/domain','gokuhd https://example.com/')
        loaded=await load_runtime(ROOT,app.store)
        assert loaded.sites['gokuhd'].base_url=='https://example.com'
        assert 'not tested' in messages[-1].lower()
        await app.management(7,7,'/domainrollback','gokuhd')
        assert app.registry.sites['gokuhd'].base_url==old
        await app.management(8,8,'/domain','gokuhd https://example.com')
        assert app.registry.sites['gokuhd'].base_url==old
        await app.management(7,7,'/domain','gokuhd http://127.0.0.1')
        assert app.registry.sites['gokuhd'].base_url==old
        assert messages[-1].startswith('Change rejected:')
    finally: await app.store.close()
