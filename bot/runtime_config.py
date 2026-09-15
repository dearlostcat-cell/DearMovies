import asyncio
import copy
from urllib.parse import urlsplit, urlunsplit
from html import escape
from .config import Registry, Site, Provider, REMOVED_SITE_IDS
from .models import FlowError
from .network import validate_url, PublicResolver


async def load_runtime(root, store, value=None):
    registry=Registry(root);registry.load(atomic=True)
    value=await store.get('runtime','configuration',{}) if value is None else value
    for key, cls in [('sites',Site),('providers',Provider)]:
        dest=getattr(registry,key)
        for ident, raw in value.get(key,{}).items():
            if key == 'sites' and ident in REMOVED_SITE_IDS: continue
            obj=cls.model_validate(raw)
            # Migrate only the obsolete bundled GokuHD API layout, retaining
            # owner domains, timeouts and other independent settings.
            if key == 'sites' and ident == 'gokuhd' and obj.search_api == '/search.php':
                bundled = dest.get(ident)
                if bundled and not bundled.search_api:
                    obj.search_api = bundled.search_api
                    obj.search = bundled.search.model_copy(deep=True)
            if ident!=obj.id: raise ValueError('Configuration ID mismatch')
            dest[ident]=obj
    # Add this exact supported download host to persisted configurations too.
    # A saved override must not hide the bundled v2.2.2 host correction.
    for ident in ('generator', 'hubcloud'):
        provider = registry.providers.get(ident)
        if provider and 'pixel.hubcloud.ist' not in provider.allowed_hosts:
            provider.allowed_hosts.append('pixel.hubcloud.ist')
    if any(p not in registry.providers for s in registry.sites.values() for p in s.providers):
        raise ValueError('Unknown provider in runtime configuration')
    registry.provider_aliases = value.get("provider_aliases",{})
    return registry


class Administration:
    async def staff(self,user):
        return self.owner(user) or bool(await self.store.get('admins',user,False))

    async def permitted(self,user):
        if await self.staff(user): return True
        override=await self.store.get('access',user)
        if override is not None: return bool(override)
        return user in self.registry.settings.allowed_users or not self.registry.settings.restricted_access

    async def install_registry(self,candidate):
        from .service import Catalog
        from .resolver import Resolver
        from .network import Network
        self.registry=candidate; self.network=Network(candidate.settings.requests_concurrency)
        self.catalog=Catalog(candidate,self.network,self.store);self.resolver=Resolver(candidate,self.network,self.store)

    async def management(self,user,chat,cmd,arg):
        commands={'/allow','/revoke','/allowed','/admin','/domain','/domains','/domainrollback','/providerdomain','/hostproposals','/hostapprove','/hostreject','/providerreset'}
        if cmd not in commands:return False
        if chat!=user or not await self.staff(user):
            await self.tg.text(chat,'This command is available to staff in a private chat.');return True
        try:
            async with self.management_lock:
                if cmd in {'/hostproposals','/hostapprove','/hostreject','/providerreset'}:
                    if not self.owner(user):raise ValueError('Only owners can approve hosts or reset provider cooldowns')
                    if cmd=='/hostproposals':
                        rows=await self.store.list('host_proposals',50)
                        await self.tg.text(chat,'\n'.join(f'{ident}: {escape(p["host"])} (seen from {escape(p["provider"])})' for ident,p in rows) or 'No pending host proposals.')
                    elif cmd=='/hostreject':
                        await self.store.remove('host_proposals',arg)
                        await self.tg.text(chat,'Proposal dismissed. No host permissions changed.')
                    elif cmd=='/providerreset':
                        if arg not in self.registry.providers:raise ValueError('Unknown provider ID')
                        async with self.resolver.health_lock:
                            state=await self.store.get('provider_health',arg,{})
                            if state:
                                state.update(consecutive=0,cooldown_until=0)
                                await self.store.put('provider_health',arg,state)
                        await self.store.log('ADMIN',user,'PROVIDER_RESET',provider=arg)
                        await self.tg.text(chat,'Cooldown cleared. This does not fix a blocked host; retry once.')
                    else:
                        ident,provider_id=arg.split()
                        proposal=await self.store.get('host_proposals',ident)
                        if not proposal:raise ValueError('Proposal expired or not found')
                        provider=self.registry.providers.get(provider_id)
                        if not provider:raise ValueError('Choose the existing provider whose page format this host uses')
                        host=proposal['host']
                        from .recovery import authentication_host
                        if authentication_host(host):raise ValueError('Google sign-in is not a download provider; reject this proposal')
                        validate_url('https://'+host,[host])
                        dns=PublicResolver()
                        try:await dns.resolve(host,443)
                        finally:await dns.close()
                        value=copy.deepcopy(await self.store.get('runtime','configuration',{}))
                        raw=provider.model_dump()
                        raw['hosts']=list(dict.fromkeys([*raw['hosts'],host]))
                        raw['allowed_hosts']=list(dict.fromkeys([*raw['allowed_hosts'],host]))
                        value.setdefault('providers',{})[provider_id]=raw
                        for site in self.registry.sites.values():
                            if provider_id in site.providers:
                                raw_site=site.model_dump();raw_site['provider_hosts']=list(dict.fromkeys([*raw_site['provider_hosts'],host]))
                                value.setdefault('sites',{})[site.id]=raw_site
                        candidate=await load_runtime(self.registry.root,self.store,value)
                        await self.store.put('runtime','configuration',value)
                        await self.install_registry(candidate)
                        await self.store.remove('host_proposals',ident)
                        await self.store.log('ADMIN',user,'HOST_APPROVED',provider=provider_id,host=host)
                        await self.tg.text(chat,'Host approved for the selected provider. Retry to test it; file resolution is not yet verified.')
                elif cmd in {'/allow','/revoke'}:
                    target=int(arg)
                    if target<=0:raise ValueError('Use a positive personal Telegram user ID')
                    if await self.staff(target):raise ValueError('Staff access is managed through roles')
                    await self.store.put('access',target,cmd=='/allow')
                    if cmd=='/revoke':
                        tasks=[self.jobs[s] for s in self.user_jobs[target] if s in self.jobs]
                        for task in tasks:task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)
                    await self.tg.text(chat,f'User {target}: '+('allowed' if cmd=='/allow' else 'revoked'))
                elif cmd=='/allowed':
                    values={str(x):True for x in self.registry.settings.allowed_users}
                    values.update(dict(await self.store.list('access',10000)))
                    await self.tg.text(chat,'Allowed user IDs:\n'+(', '.join(k for k,v in values.items() if v) or 'None. Staff still have access.')[:3500])
                elif cmd=='/admin':
                    if not self.owner(user):raise ValueError('Only owners can change administrator roles')
                    action,target=arg.split();target=int(target)
                    if target<=0 or self.owner(target):raise ValueError('Choose a positive non-owner user ID')
                    if action not in {'add','remove'}:raise ValueError('Use /admin add ID or /admin remove ID')
                    await self.store.put('admins',target,action=='add')
                    if action=='remove':
                        await self.store.put('access',target,False)
                        tasks=[self.jobs[s] for s in self.user_jobs[target] if s in self.jobs]
                        for task in tasks:task.cancel()
                        await asyncio.gather(*tasks,return_exceptions=True)
                    await self.tg.text(chat,f'Administrator {target}: {action}')
                elif cmd=='/domains':
                    await self.tg.text(chat,'\n'.join(f'{s.id}: {escape(s.base_url)}' for s in self.registry.sites.values()))
                else:
                    old=await self.store.get('runtime','configuration',{})
                    value=copy.deepcopy(old)
                    if cmd=='/domainrollback':
                        previous=await self.store.get('runtime_history',arg)
                        if previous is None:raise ValueError('No saved domain revision for that ID')
                        before,after=previous['before'],previous['after']
                        for group in set(before)|set(after):
                            for key in set(before.get(group,{}))|set(after.get(group,{})):
                                if before.get(group,{}).get(key)==after.get(group,{}).get(key):continue
                                if value.get(group,{}).get(key)!=after.get(group,{}).get(key):raise ValueError('A newer edit overlaps this rollback; update that domain explicitly')
                                if key in before.get(group,{}):value.setdefault(group,{})[key]=before[group][key]
                                else:value.setdefault(group,{}).pop(key,None)
                    elif cmd=='/domain':
                        ident,target=arg.split()
                        site=self.registry.sites.get(ident)
                        if not site:raise ValueError('Unknown website ID')
                        parsed=urlsplit(target); validate_url(target,[parsed.hostname or ''])
                        if parsed.path not in {'','/'} or parsed.query or parsed.fragment:raise ValueError('Use the domain only, without a path or tracking parameters')
                        dns=PublicResolver()
                        try:await dns.resolve(parsed.hostname,parsed.port or 443)
                        finally:await dns.close()
                        candidate=site.model_copy(deep=True)
                        candidate.base_url=target.rstrip('/')
                        candidate.mirrors=list(dict.fromkeys([site.base_url,*site.mirrors]))
                        candidate.version=str(int(__import__('time').time_ns()))
                        from .service import Catalog
                        probe=Catalog(self.registry,self.network,self.store)
                        await probe.search_one(candidate,'dear','DOMAIN_TEST',user)
                        value.setdefault('sites',{})[ident]=candidate.model_dump()
                    else:
                        ident,oldhost,newhost=arg.split()
                        provider=self.registry.providers.get(ident)
                        if not provider or oldhost not in provider.hosts:raise ValueError('Unknown provider or old hostname')
                        validate_url('https://'+newhost,[newhost])
                        if urlsplit('https://'+newhost).netloc!=newhost:raise ValueError('Use only a hostname')
                        dns=PublicResolver()
                        try:await dns.resolve(newhost,443)
                        finally:await dns.close()
                        # Change explicit provider references and selectors; retain old input host aliases.
                        for p in self.registry.providers.values():
                            raw=p.model_dump()
                            if oldhost in raw['hosts'] or oldhost in raw['allowed_hosts']:
                                raw['allowed_hosts']=list(dict.fromkeys([*raw['allowed_hosts'],newhost]))
                                if p.id==ident: raw['hosts']=list(dict.fromkeys([*raw['hosts'],newhost]))
                                raw['link_selectors']=[s.replace(oldhost,newhost) for s in raw['link_selectors']]
                                value.setdefault('providers',{})[p.id]=raw
                        for s in self.registry.sites.values():
                            if oldhost in s.provider_hosts:
                                raw=s.model_dump();raw['provider_hosts']=list(dict.fromkeys([*s.provider_hosts,newhost]));value.setdefault('sites',{})[s.id]=raw
                        value.setdefault('provider_aliases',{})[oldhost]=newhost
                    candidate=await load_runtime(self.registry.root,self.store,value)
                    history_key=arg.split()[0]
                    await self.store.put('runtime_history',history_key,{'before':old,'after':value})
                    await self.store.put('runtime','configuration',value)
                    await self.install_registry(candidate)
                    await self.tg.text(chat,'Domain configuration activated and saved. Existing jobs retain their original configuration. Provider changes need a representative file test.')
                await self.store.log('ADMIN',user,'ADMIN_CHANGE',command=cmd,target=arg)
        except (ValueError,FlowError) as e:
            await self.tg.text(chat,'Change rejected: '+escape(e.message if isinstance(e,FlowError) else str(e))[:2000])
        return True
