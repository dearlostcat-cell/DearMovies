"""Independent web control and lifecycle for the Telegram worker."""
import asyncio
import hashlib
import hmac
import secrets
import time
from collections import deque
from urllib.parse import urlsplit
from aiohttp import web
from . import __version__
from .app import BotApp
from .models import FlowError
from .runtime_config import load_runtime
from .telegram import Telegram


class BotController:
    def __init__(self, root, store, token, telegram_factory=Telegram, app_factory=BotApp):
        self.root,self.store,self.token=root,store,token
        self.telegram_factory,self.app_factory=telegram_factory,app_factory
        self.task=None;self.app=None;self.enabled=False;self.error=None
        self.started=time.time();self.lock=asyncio.Lock();self.transition=False

    async def initialize(self):
        self.enabled=bool(await self.store.get('system','bot_enabled',True))
        if self.enabled and self.token:self.task=asyncio.create_task(self.run())

    async def run(self):
        try:
            registry=await load_runtime(self.root,self.store)
            async with self.telegram_factory(self.token) as telegram:
                self.app=self.app_factory(registry,self.store,telegram)
                try:await self.app.poll()
                finally:await self.app.close()
        except asyncio.CancelledError:raise
        except Exception as error:
            self.error=error.code if isinstance(error,FlowError) else 'STARTUP_ERROR'
            await self.store.log('CONTROL',0,'BOT_STOPPED',reason=self.error)
        finally:self.app=None

    def status(self):
        running=bool(self.task and not self.task.done())
        polling=bool(self.app and self.app.polling_ok and time.time()-self.app.last_poll<90)
        state='off' if not self.enabled else 'setup' if not self.token else 'error' if self.error else 'online' if polling else 'starting' if running else 'offline'
        if self.transition:state='changing'
        return dict(service='Lost Movies',version=__version__,configured=bool(self.token),enabled=self.enabled,polling=polling,state=state,uptime_seconds=int(time.time()-self.started))

    async def set_enabled(self, enabled):
        async with self.lock:
            if enabled and not self.token:raise FlowError('SETUP','Add BOT_TOKEN in Replit Secrets and restart the app.')
            self.transition=True
            try:
                await self.store.put('system','bot_enabled',enabled)
                self.enabled=enabled
                if not enabled:
                    if self.task and not self.task.done():
                        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
                    self.task=None;self.error=None
                elif not self.task or self.task.done():
                    self.error=None;self.task=asyncio.create_task(self.run())
                await self.store.log('CONTROL',0,'BOT_ON' if enabled else 'BOT_OFF')
            finally:self.transition=False
        return self.status()

    async def close(self):
        async with self.lock:
            if self.task:
                self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
            self.task=None


def create_control_web(controller, password):
    sessions={};attempts=deque();ttl=8*3600
    password_digest=hashlib.sha256(password.encode()).digest()

    @web.middleware
    async def headers(request,handler):
        try:response=await handler(request)
        except web.HTTPException as e:response=web.Response(status=e.status,text=e.text,headers=e.headers)
        response.headers.update({'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','X-Frame-Options':'DENY','Content-Security-Policy':"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        return response

    def origin_check(request):
        origin=request.headers.get('Origin')
        if origin and (urlsplit(origin).netloc!=request.host or urlsplit(origin).scheme not in {'http','https'}):raise web.HTTPForbidden(text='Origin rejected')

    def session(request,csrf=False):
        key=request.cookies.get('control_session','');value=sessions.get(key)
        if not value or value['expires']<time.time():
            sessions.pop(key,None);raise web.HTTPUnauthorized(text='Sign in again')
        if csrf and not hmac.compare_digest(request.headers.get('X-CSRF-Token',''),value['csrf']):raise web.HTTPForbidden(text='Invalid session request')
        return value

    async def page(request):return web.FileResponse(controller.root/'status.html')
    async def health(request):return web.json_response(controller.status())
    async def auth(request):
        value=sessions.get(request.cookies.get('control_session',''))
        valid=bool(value and value['expires']>time.time())
        return web.json_response({'authenticated':valid,'control_configured':len(password)>=16,'csrf':value['csrf'] if valid else None})
    async def login(request):
        origin_check(request)
        if len(password)<16:raise web.HTTPServiceUnavailable(text='Set WEB_CONTROL_PASSWORD to at least 16 characters in Replit Secrets and restart.')
        now=time.time()
        while attempts and attempts[0]<now-60:attempts.popleft()
        if len(attempts)>=20:raise web.HTTPTooManyRequests(text='Too many attempts. Try again in one minute.')
        attempts.append(now)
        if request.content_type!='application/json':raise web.HTTPBadRequest(text='Expected JSON')
        try:data=await request.json()
        except ValueError:raise web.HTTPBadRequest(text='Invalid request')
        supplied=data.get('password','') if isinstance(data,dict) else ''
        if not isinstance(supplied,str) or not hmac.compare_digest(hashlib.sha256(supplied.encode()).digest(),password_digest):raise web.HTTPUnauthorized(text='Incorrect password')
        for key in list(sessions):
            if sessions[key]['expires']<now:sessions.pop(key)
        if len(sessions)>=100:sessions.pop(next(iter(sessions)))
        sessions.pop(request.cookies.get('control_session',''),None)
        key=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        sessions[key]={'csrf':csrf,'expires':now+ttl}
        response=web.json_response({'authenticated':True,'csrf':csrf})
        hostname=request.host.split(':')[0]
        response.set_cookie('control_session',key,max_age=ttl,httponly=True,secure=hostname not in {'localhost','127.0.0.1','[::1]'},samesite='Strict',path='/')
        return response
    async def logout(request):
        origin_check(request);session(request,csrf=True)
        sessions.pop(request.cookies.get('control_session'),None)
        response=web.json_response({'ok':True});response.del_cookie('control_session',path='/');return response
    async def change(request):
        origin_check(request);session(request,csrf=True)
        if request.content_type!='application/json':raise web.HTTPBadRequest(text='Expected JSON')
        try:data=await request.json()
        except ValueError:raise web.HTTPBadRequest(text='Invalid request')
        if not isinstance(data,dict) or type(data.get('enabled')) is not bool:raise web.HTTPBadRequest(text='enabled must be a boolean')
        try:return web.json_response(await controller.set_enabled(data['enabled']))
        except FlowError as error:return web.json_response({'error':error.message},status=409)
    async def private_status(request):
        session(request)
        result=controller.status();result.update(error=controller.error,active_jobs=len(controller.app.jobs) if controller.app else 0,durable_storage=controller.store.durable)
        return web.json_response(result)
    app=web.Application(middlewares=[headers],client_max_size=4096)
    app.router.add_get('/',page);app.router.add_get('/health',health)
    app.router.add_get('/api/session',auth);app.router.add_post('/api/login',login)
    app.router.add_post('/api/logout',logout);app.router.add_post('/api/bot',change)
    app.router.add_get('/api/bot',private_status)
    return app
