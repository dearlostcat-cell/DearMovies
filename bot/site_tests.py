"""Bounded staff diagnostics using the same catalogue paths as user searches."""
import asyncio
import secrets
import time
from html import escape
from urllib.parse import urlsplit
from .models import FlowError, SearchResult
from .service import Catalog


def test_buttons(job, details=False):
    rows = [[{'text': 'View logs', 'callback_data': 'admin:sitelogs:'+job},
             {'text': 'Retry test', 'callback_data': 'admin:siteretry:'+job}]]
    if details:
        rows.append([{'text': 'Test details (first result)', 'callback_data': 'admin:sitedetails:'+job}])
    return rows


class DiagnosticNetwork:
    def __init__(self, network, store, job, user, site, stage):
        self.network, self.store = network, store
        self.job, self.user, self.site, self.stage = job, user, site, stage

    def __getattr__(self, name):
        return getattr(self.network, name)

    async def fetch(self, session, url, allowed, **kwargs):
        await self.store.log(self.job, self.user, 'SITE_TEST_REQUEST', site=self.site,
                             stage=self.stage, host=urlsplit(url).hostname)
        response = await self.network.fetch(session, url, allowed, **kwargs)
        await self.store.log(self.job, self.user, 'SITE_TEST_RESPONSE', site=self.site,
                             stage=self.stage, host=urlsplit(response.url).hostname,
                             status=response.status, bytes=len(response.body), truncated=response.truncated)
        return response


class SiteTests:
    def init_site_tests(self):
        self.site_test_tasks = {}
        self.site_test_lock = asyncio.Lock()
        self.site_test_slots = asyncio.Semaphore(2)

    async def start_site_test(self, user, chat, ident, stage='search', query='dear', sample=None, quiet=False):
        if chat != user or not await self.staff(user): return
        site = self.registry.sites.get(ident)
        if not site or not await self.catalog.enabled(site):
            if not quiet: await self.tg.text(chat, 'Enable this site before testing it.')
            return
        if not query.strip() or len(query) > 200:
            raise FlowError('INPUT', 'Test query must contain 1 to 200 characters')
        key = ident+':'+stage
        async with self.site_test_lock:
            latest = await self.store.get('site_test_latest', key)
            previous = await self.store.get('site_tests', latest, {}) if latest else {}
            if key in self.site_test_tasks or time.time()-previous.get('started', 0) < 60:
                if not quiet: await self.tg.text(chat, 'Please wait: this test is running or was started within the last 60 seconds.')
                return
            job = secrets.token_hex(4).upper()
            record = dict(site=ident, stage=stage, query=query, sample=sample,
                          started=time.time(), status='queued', config=site.version)
            await self.store.put('site_tests', job, record, 86400)
            await self.store.put('site_test_latest', key, job, 86400)
            registry, network = self.registry, self.network
            catalog = Catalog(registry, DiagnosticNetwork(network, self.store, job, user, ident, stage), self.store)
            catalog.health_lock = self.catalog.health_lock

            async def run():
                try:
                    async with self.site_test_slots:
                        if not await self.staff(user) or not await self.catalog.enabled(site): return
                        record['status'] = 'running'
                        await self.store.put('site_tests', job, record, 86400)
                        await self.store.log(job, user, 'SITE_TEST_START', site=ident, stage=stage, parser=site.parser, config=site.version)
                        await self.tg.text(chat, f'Testing {escape(site.name)} {stage}…\nJob: {job}', test_buttons(job))
                        async with asyncio.timeout(registry.settings.max_job_seconds):
                            if stage == 'search':
                                found = await catalog.search_one(site, query, job, user, force_refresh=True)
                                record['sample'] = found[0].model_dump() if found else None
                                counts = {'results': len(found)}
                                summary = f'Search parser: {len(found)} titles. Download links have not been tested.'
                            else:
                                if not sample: raise FlowError('INPUT', 'Run a search test with results first')
                                media = await catalog.details(SearchResult.model_validate(sample), job, user, force_refresh=True)
                                counts = dict(variants=len(media.variants), screenshots=len(media.screenshots), watch_links=len(media.watch_links))
                                summary = f'Details parser: {counts["variants"]} variants, {counts["screenshots"]} screenshots, {counts["watch_links"]} watch links. Download resolution has not been tested.'
                        record.update(status='passed', counts=counts)
                        await self.store.log(job, user, 'SITE_TEST_SUCCESS', site=ident, stage=stage, **counts)
                        await self.tg.text(chat, escape(site.name+': '+summary)+f'\nJob: {job}', test_buttons(job, bool(record['sample']) and stage == 'search'))
                except asyncio.CancelledError:
                    record['status'] = 'cancelled'
                    await self.store.log(job, user, 'SITE_TEST_CANCELLED', site=ident, stage=stage)
                    raise
                except Exception as error:
                    reason = error.code if isinstance(error, FlowError) else 'TIMEOUT' if isinstance(error, TimeoutError) else 'INTERNAL_ERROR'
                    message = error.message if isinstance(error, FlowError) else 'Test timed out' if isinstance(error, TimeoutError) else type(error).__name__
                    record.update(status='failed', reason=reason)
                    await self.store.log(job, user, 'SITE_TEST_FAILED', site=ident, stage=stage, reason=reason, message=message)
                    try: await self.tg.text(chat, escape(f'{site.name} {stage} test failed: {message}')+f'\nJob: {job}', test_buttons(job))
                    except FlowError: pass
                finally:
                    if record['status'] in {'queued', 'running'}: record['status'] = 'cancelled'
                    try: await self.store.put('site_tests', job, record, 86400)
                    finally: self.site_test_tasks.pop(key, None)

            task = asyncio.create_task(run())
            self.site_test_tasks[key] = task
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
            return job

    async def site_test_callback(self, user, chat, action, job):
        record = await self.store.get('site_tests', job)
        if not record:
            await self.tg.text(chat, 'This test has expired. Run /sites again.'); return
        if action == 'sitelogs':
            await self.show_logs(chat, {'job': job}, limit=100)
        else:
            stage = 'details' if action == 'sitedetails' else record['stage']
            await self.start_site_test(user, chat, record['site'], stage, record['query'], record.get('sample'))
