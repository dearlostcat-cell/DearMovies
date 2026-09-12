import asyncio,time
import pytest
from aiohttp.test_utils import TestClient,TestServer
from bot.config import ROOT
from bot.control import BotController,create_control_web
from bot.models import FlowError
from bot.storage import Store

PASSWORD='test-password-for-control-only'

class FakeTelegram:
    def __init__(self,token):pass
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass

class Worker:
    running=0
    maximum=0
    closed=0
    def __init__(self,*args):self.polling_ok=False;self.last_poll=0;self.jobs={}
    async def poll(self):
        Worker.running+=1;Worker.maximum=max(Worker.maximum,Worker.running)
        self.polling_ok=True;self.last_poll=time.time()
        await asyncio.Event().wait()
    async def close(self):Worker.running-=1;Worker.closed+=1

async def controller(tmp_path,token='fake',worker=Worker):
    Worker.running=Worker.maximum=Worker.closed=0
    store=Store('sqlite:///'+str(tmp_path/'control.db'))
    return BotController(ROOT,store,token,FakeTelegram,worker)

@pytest.mark.asyncio
async def test_on_off_persistence_and_no_duplicate_workers(tmp_path):
    c=await controller(tmp_path)
    try:
        await c.initialize();await asyncio.sleep(.05)
        await asyncio.gather(*(c.set_enabled(True) for _ in range(5)))
        assert c.status()['polling'] and Worker.maximum==1
        await c.set_enabled(False)
        assert c.status()['state']=='off' and Worker.running==0 and Worker.closed==1
        c2=BotController(ROOT,c.store,'fake',FakeTelegram,Worker)
        await c2.initialize();assert c2.task is None and c2.status()['state']=='off'
        await c2.set_enabled(True);await asyncio.sleep(.05)
        assert c2.status()['polling'];await c2.close()
    finally:await c.close();await c.store.close()

@pytest.mark.asyncio
async def test_worker_error_does_not_kill_web_controller(tmp_path):
    class Broken(Worker):
        async def poll(self):raise FlowError('TELEGRAM_AUTH','secret should not be exposed')
        async def close(self):pass
    c=await controller(tmp_path,worker=Broken)
    try:
        await c.initialize();await asyncio.wait_for(asyncio.shield(c.task),timeout=5)
        assert c.status()['state']=='error' and c.error=='TELEGRAM_AUTH'
        assert 'secret' not in str(c.status())
        await c.set_enabled(False);assert c.status()['state']=='off'
    finally:await c.close();await c.store.close()

@pytest.mark.asyncio
async def test_web_auth_csrf_and_health_stays_live_while_off(tmp_path):
    c=await controller(tmp_path)
    client=TestClient(TestServer(create_control_web(c,PASSWORD)))
    try:
        await c.initialize();await client.start_server()
        assert (await client.get('/')).status==200
        assert (await client.post('/api/bot',json={'enabled':False})).status==401
        assert (await client.post('/api/login',json={'password':'wrong'})).status==401
        assert (await client.post('/api/login',json={'password':PASSWORD},headers={'Origin':'https://evil.example'})).status==403
        response=await client.post('/api/login',json={'password':PASSWORD})
        assert response.status==200
        csrf=(await response.json())['csrf']
        assert 'HttpOnly' in response.headers['Set-Cookie'] and 'SameSite=Strict' in response.headers['Set-Cookie']
        assert (await client.post('/api/bot',json={'enabled':False})).status==403
        response=await client.post('/api/bot',json={'enabled':False},headers={'X-CSRF-Token':csrf})
        assert response.status==200 and (await response.json())['state']=='off'
        response=await client.get('/health');assert response.status==200 and (await response.json())['state']=='off'
        assert (await client.get('/')).status==200
        assert (await client.post('/api/bot',json={'enabled':'false'},headers={'X-CSRF-Token':csrf})).status==400
        assert (await client.post('/api/logout',json={},headers={'X-CSRF-Token':csrf})).status==200
        assert (await client.post('/api/bot',json={'enabled':True},headers={'X-CSRF-Token':csrf})).status==401
    finally:await client.close();await c.close();await c.store.close()

@pytest.mark.asyncio
async def test_unconfigured_controls_and_login_throttle(tmp_path):
    c=await controller(tmp_path,token='')
    client=TestClient(TestServer(create_control_web(c,'')))
    try:
        await c.initialize();await client.start_server()
        assert c.status()['state']=='setup'
        assert (await client.post('/api/login',json={'password':''})).status==503
        with pytest.raises(FlowError):await c.set_enabled(True)
    finally:await client.close()
    client=TestClient(TestServer(create_control_web(c,PASSWORD)))
    try:
        await client.start_server()
        for _ in range(20):assert (await client.post('/api/login',json={'password':'wrong'})).status==401
        assert (await client.post('/api/login',json={'password':'wrong'})).status==429
    finally:await client.close();await c.close();await c.store.close()
