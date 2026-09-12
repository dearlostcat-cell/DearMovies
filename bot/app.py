from __future__ import annotations

import asyncio
import copy
import json
import math
import secrets
import time
from collections import Counter, defaultdict, deque
from html import escape
from pathlib import Path
from . import __version__
from .config import Registry
from .models import FlowError, Media, SearchResult, uid
from .navigation import details_text, matching, rows, selection
from .network import Network
from .resolver import Resolver
from .service import Catalog


WELCOME = """нєу Luv

๏ ᴛʜɪs ɪs <a href="{bot_url}">˹ʟᴏsᴛ ꭙ ᴍᴏᴠɪᴇs˼</a> 🎬

➻ ᴀ ғᴀsᴛ &amp; ᴘᴏᴡᴇʀғᴜʟ ᴛᴇʟᴇɢʀᴀᴍ ᴍᴏᴠɪᴇ ʙᴏᴛ ᴍᴀᴅᴇ ᴛᴏ ʜᴇʟᴘ ʏᴏᴜ ꜰɪɴᴅ ᴍᴏᴠɪᴇꜱ, ꜱᴇʀɪᴇꜱ &amp; ᴇɴᴛᴇʀᴛᴀɪɴᴍᴇɴᴛ ᴄᴏɴᴛᴇɴᴛ ǫᴜɪᴄᴋʟʏ. 🍿

๏ ꜱᴇᴀʀᴄʜ ʙʏ ɴᴀᴍᴇ, ᴇxᴘʟᴏʀᴇ ᴀᴠᴀɪʟᴀʙʟᴇ ǫᴜᴀʟɪᴛɪᴇꜱ, ᴄʜᴇᴄᴋ ᴍᴏᴠɪᴇ ᴅᴇᴛᴀɪʟꜱ &amp; ɢᴇᴛ ᴛʜᴇ ʀɪɢʜᴛ ʟɪɴᴋ ᴡɪᴛʜᴏᴜᴛ ᴡᴀꜱᴛɪɴɢ ᴛɪᴍᴇ.

๏ ғʀᴏᴍ ᴛʜᴇ ʟᴀᴛᴇꜱᴛ ʀᴇʟᴇᴀꜱᴇꜱ ᴛᴏ ᴏʟᴅ ғᴀᴠᴏᴜʀɪᴛᴇꜱ — ˹ʟᴏsᴛ ꭙ ᴍᴏᴠɪᴇs˼ ᴋᴇᴇᴘꜱ ᴇᴠᴇʀʏᴛʜɪɴɢ ꜱɪᴍᴘʟᴇ, ғᴀꜱᴛ &amp; ᴏʀɢᴀɴɪᴢᴇᴅ.

๏ ᴛᴀᴘ ʜᴇʟᴘ ᴛᴏ ᴠɪᴇᴡ ᴀʟʟ ᴄᴏᴍᴍᴀɴᴅꜱ, ꜰᴇᴀᴛᴜʀᴇꜱ &amp; ꜱᴇᴀʀᴄʜ ᴏᴘᴛɪᴏɴꜱ."""

HELP = """<b>˹ʟᴏsᴛ ꭙ ᴍᴏᴠɪᴇs˼ — Help</b>

/search name — Search enabled websites
/search name 1080p — Filter by quality
/search name series — Filter by type
/search "Movie" — Search a literal name
Or just send a title, such as dear.
/anime name — Kuroiru watch links
/favorites — Saved titles
/recent — Recently selected titles
/continue — Resume your last menu
/cancel — Stop your current jobs

Choose a title, season and available version. Single-choice stages are skipped. Back restores your previous menu. Refresh regenerates a failed or expired download link.

The bot returns links; it does not upload whole movies to Telegram. Availability depends on the source."""

ADMIN_HELP = """

<b>Staff commands</b>
/allow ID | /revoke ID | /allowed
/domain SITE_ID URL | /domains | /domainrollback ID
/providerdomain PROVIDER_ID OLD_HOST NEW_HOST
/admin add|remove ID — owner only
/sites — Site status, enable/disable and tests
/reload — Validate and reload all YAML
/maintenance on|off
/stats — Operational statistics
/version — App, parser and configuration versions
/log [errors|job_id|user ID|site ID|provider ID]
/log live — Recent log feed for 2 minutes
/log stop — Stop the feed
/inspect job_id — Sanitized parser output
/backup — Download a logical data/config backup
"""


def button(label, data): return {"text": label, "callback_data": data}


from .runtime_config import Administration, load_runtime

class BotApp(Administration):
    def __init__(self, registry: Registry, store, telegram):
        self.registry, self.store, self.tg = registry, store, telegram
        self.network = Network(registry.settings.requests_concurrency)
        self.catalog = Catalog(registry, self.network, store)
        self.resolver = Resolver(registry, self.network, store)
        self.jobs = {}
        self.user_jobs = defaultdict(set)
        self.search_times = defaultdict(deque)
        self.locks = defaultdict(asyncio.Lock)
        self.live_feeds = {}
        self.update_tasks = set()
        self.update_locks = defaultdict(asyncio.Lock)
        self.maintenance = registry.settings.maintenance
        self.polling_ok = False
        self.last_poll = 0
        self.username = ""
        self.management_lock = asyncio.Lock()

    def owner(self, user): return user in self.registry.settings.owner_ids

    async def authorize(self, user, chat):
        if not await self.permitted(user):
            await self.tg.text(chat, "This bot is not available to this account.")
            return False
        if self.maintenance and not await self.staff(user):
            await self.tg.text(chat, "Bot is temporarily under maintenance.")
            return False
        return True

    async def new_session(self, user, chat, query=""):
        session = {"id": secrets.token_hex(5), "user": user, "chat": chat, "revision": 0, "query": query, "screen": "results", "page": 0, "results": [], "filters": {}, "history": [], "message": None, "media_message": False, "retries": 0}
        await self.save(session)
        await self.store.put("last_session", user, session["id"], self.registry.settings.session_ttl)
        return session

    async def save(self, s): await self.store.put("sessions", s["id"], s, self.registry.settings.session_ttl)

    def cb(self, s, action, arg=""):
        return f"s:{s['id']}:{s['revision']}:{action}:{arg}"

    def push(self, s):
        s["history"].append({k: copy.deepcopy(s.get(k)) for k in ["screen", "page", "filters", "media", "stage", "options", "title_index", "selected_source", "anime_group"]})
        s["history"] = s["history"][-30:]

    async def display(self, s, text, keyboard, poster=None, new=False):
        if not await self.permitted(s["user"]): return
        if s.get("message") and not new and not poster:
            try:
                if s.get("media_message") and len(text) <= 950:
                    await self.tg.call("editMessageCaption", chat_id=s["chat"], message_id=s["message"], caption=text, parse_mode="HTML", reply_markup={"inline_keyboard": keyboard})
                    return
                if not s.get("media_message") and not poster and len(text) <= 3900:
                    await self.tg.text(s["chat"], text, keyboard, s["message"])
                    return
            except FlowError:
                pass
        if poster and len(text) <= 950:
            cache_key = uid(poster)
            file_id = await self.store.get("poster", cache_key)
            for photo in [file_id, poster] if file_id else [poster]:
                try:
                    msg = await self.tg.call("sendPhoto", chat_id=s["chat"], photo=photo, caption=text, parse_mode="HTML", reply_markup={"inline_keyboard": keyboard})
                    s["message"], s["media_message"] = msg["message_id"], True
                    if msg.get("photo"): await self.store.put("poster", cache_key, msg["photo"][-1]["file_id"])
                    return
                except FlowError: continue
        if poster and len(text) > 950:
            # Keep the poster associated with the title even for long descriptions.
            try:
                msg = await self.tg.call("sendPhoto", chat_id=s["chat"], photo=poster, caption=escape(s.get("media", {}).get("title", "Details")))
                if msg.get("photo"): await self.store.put("poster", uid(poster), msg["photo"][-1]["file_id"])
            except FlowError: pass
        if len(text) > 3900:
            # Split only at line boundaries, preserving complete HTML entities/tags.
            chunks, chunk = [], ""
            for line in text.splitlines():
                if len(chunk) + len(line) > 3900:
                    if chunk: chunks.append(chunk)
                    chunk = ""
                if len(line) > 3900:
                    plain = __import__('re').sub(r"<[^>]*>", "", __import__('html').unescape(line))
                    for i in range(0, len(plain), 2000): chunks.append(escape(plain[i:i+2000]))
                else: chunk += line + "\n"
            if chunk: chunks.append(chunk)
            for part in chunks[:-1]: await self.tg.text(s["chat"], part)
            text = chunks[-1]
        msg = await self.tg.text(s["chat"], text, keyboard)
        s["message"], s["media_message"] = msg["message_id"], False

    async def render(self, s, poster=False):
        s["revision"] += 1
        keyboard = []
        screen = s["screen"]
        if screen == "results":
            results = [SearchResult.model_validate(x) for x in s["results"]]
            count = self.registry.settings.results_per_page
            pages = max(1, math.ceil(len(results) / count)); s["page"] = min(s["page"], pages - 1)
            names = Counter(x.title.casefold() for x in results)
            items = []
            for index in range(s["page"] * count, min(len(results), (s["page"] + 1) * count)):
                result = results[index]
                label = result.title
                if names[label.casefold()] > 1: label += f" ({result.year or '?'}, {result.kind})"
                items.append(button(label[:100], self.cb(s, "title", index)))
            keyboard += rows(items)
            if pages > 1: keyboard += self.page_buttons(s, pages)
            text = f"<b>Search: {escape(s['query'])}</b>\n{len(results)} titles" if results else "No matching titles found. Try another spelling or fewer filters."
            if s.get("partial"): text += "\nSome websites were unavailable: " + escape(", ".join(s["partial"]))
            if self.registry.settings.mock_mode: text += "\n🧪 Saved-page demonstration"
        elif screen == "choice":
            media = Media.model_validate(s["media"])
            site = self.registry.sites.get(media.refs[0].site)
            field, options, skipped = selection(media, s["filters"], site.features if site else {})
            s["stage"], s["options"] = field, options
            for skipped_field in skipped: await self.store.log(s.get("job", ""), s["user"], "AUTO_SKIP_" + skipped_field.upper())
            if field is None: raise FlowError("UNAVAILABLE", "This selection is no longer available; go Back")
            text = details_text(media, site.features if site else {}) if poster else f"<b>{escape(media.title)}</b>\n" + " → ".join(escape(str(v)) for k, v in s["filters"].items() if k not in {"file", "episode_key"})
            if field == "ready":
                variant = matching(media, s["filters"])[0]
                text += "\n\n" + escape(variant.filename) + "\n📦 " + escape(variant.size)
                keyboard.append([button("Choose episode" if variant.collection_url else "Download 💯", self.cb(s, "collection" if variant.collection_url else "resolve"))])
            else:
                field_names = {"season": "season", "mode": "pack or individual episode", "resolution": "quality", "source": "source", "languages": "audio languages", "version": "format", "episode_key": "episode", "file": "file version"}
                text += "\n\nChoose " + field_names[field] + ":"
                count = self.registry.settings.episodes_per_page
                pages = max(1, math.ceil(len(options) / count)); s["page"] = min(s["page"], pages - 1)
                keyboard += rows([button(options[i]["label"][:100], self.cb(s, "choose", i)) for i in range(s["page"] * count, min(len(options), (s["page"] + 1) * count))])
                if pages > 1: keyboard += self.page_buttons(s, pages)
            text += "\nSource: " + escape(site.name if site else media.refs[0].site)
            if media.screenshots: keyboard.append([button("📷 Screenshots",self.cb(s,"screenshots"))])
            saved = await self.store.get("favorites_" + str(s["user"]), media.id)
            keyboard.append([button("★ Saved — remove" if saved else "⭐ Save", self.cb(s, "favorite"))])
        elif screen == "sources":
            result=SearchResult.model_validate(s["results"][s["title_index"]])
            text=f"<b>{escape(result.title)}</b>\nChoose a website:"
            keyboard += [[button(self.registry.sites[ref.site].name,self.cb(s,"source",ref.site))] for ref in result.refs if ref.site in self.registry.sites]
        elif screen == "screenshots":
            media=Media.model_validate(s["media"]); count=6; pages=max(1,math.ceil(len(media.screenshots)/count));s["page"]=min(s["page"],pages-1)
            text=f"<b>{escape(media.title)}</b>\nSource-provided quality samples. These may not represent every encode."
            for i,item in enumerate(media.screenshots[s["page"]*count:(s["page"]+1)*count],s["page"]*count+1):
                keyboard.append([{"text":f"Screenshot {i}","url":item.url},{"text":"Original / image page","url":item.original or item.url}])
            if pages>1:keyboard+=self.page_buttons(s,pages)
        elif screen in {"anime","animeall","animegroup"}:
            media=Media.model_validate(s["media"])
            text=f"<b>{escape(media.title)}</b>\n{escape(media.description)}\n\nSource: Kuroiru • Links open the provider website. Episode links start at episode 1."
            if screen=="animeall":
                keyboard=[[button(group,self.cb(s,"animegroup",group))] for group in ["Free","Free (Scrapers)"]]
            else:
                links=media.watch_links
                if screen=="anime":
                    preferred=["Miruro","AniKoto","AniDB","animepahe","AniSnatch","MKissa"]
                    links=sorted(links,key=lambda x:preferred.index(x.label) if x.label in preferred else 99)[:6]
                    buttons=[{"text":x.label[:80],"url":x.url} for x in links]
                    keyboard=[buttons[i:i+3] for i in range(0,len(buttons),3)]
                    keyboard.append([button("View all",self.cb(s,"animeall"))])
                else:
                    links=[x for x in links if x.group==s.get("anime_group")]
                    text+="\n"+escape(s.get("anime_group",""))
                    count=18;pages=max(1,math.ceil(len(links)/count));s["page"]=min(s["page"],pages-1)
                    buttons=[{"text":x.label[:80],"url":x.url} for x in links[s["page"]*count:(s["page"]+1)*count]]
                    keyboard=[buttons[i:i+2] for i in range(0,len(buttons),2)]
                    if pages>1:keyboard+=self.page_buttons(s,pages)
                if not links:text+="\nNo watch links found for this selection."
        elif screen == "loading":
            text = s.get("loading", "Loading…")
        elif screen == "done":
            text = "✅ Link prepared.\n" + escape(s.get("result_note", ""))
            # URLs only exist in memory during this send. Never persist final signed URLs.
            if s.get("final_url"): keyboard.append([{"text": "Download 💯", "url": s["final_url"]}])
            keyboard.append([button("🔄 Link failed / Refresh", self.cb(s, "refresh"))])
        elif screen == "error":
            text = escape(s.get("error", "Something went wrong")) + f"\nError ID: <code>{s.get('job', '')}</code>"
            if s.get("retry") and s.get("retries", 0) < 3: keyboard.append([button("Retry", self.cb(s, "retry"))])
        else: text = "Cancelled. Use /search to search again."
        if screen != "cancelled":
            controls = []
            if s["history"] and screen != "loading": controls.append(button("⬅️ Back", self.cb(s, "back")))
            controls.append(button("❌ Cancel", self.cb(s, "cancel")))
            keyboard.append(controls)
        await self.display(s, text, keyboard, poster=s.get("media", {}).get("poster") if poster else None, new=poster)
        s.pop("final_url", None)
        await self.save(s)

    def page_buttons(self, s, pages):
        row = []
        if s["page"]: row.append(button("◀ Previous", self.cb(s, "page", s["page"] - 1)))
        row.append(button(f"{s['page'] + 1}/{pages}", "noop"))
        if s["page"] + 1 < pages: row.append(button("Next ▶", self.cb(s, "page", s["page"] + 1)))
        return [row]

    async def start_job(self, s, operation, index=None, retry=False):
        if s["id"] in self.jobs:
            await self.tg.text(s["chat"], "This menu already has an operation running."); return
        settings = self.registry.settings
        if len(self.user_jobs[s["user"]]) >= settings.active_jobs_per_user or len(self.jobs) >= settings.max_active_jobs:
            await self.tg.text(s["chat"], "The bot is busy. Wait for an active job or cancel it first."); return
        if not retry: s["retries"] = 0
        catalog, resolver, job_registry = self.catalog, self.resolver, self.registry
        s["retry"] = {"operation": operation, "index": index}
        s["screen"], s["job"] = "loading", secrets.token_hex(4).upper()
        s["loading"] = {"search": "Searching…", "details": "Loading details…", "resolve": "Preparing link…", "collection": "Loading episode choices…"}[operation]
        await self.render(s)
        await self.store.put("job_session", s["job"], {"session": s["id"], "user": s["user"]}, settings.session_ttl)
        async def run():
            try:
                async with asyncio.timeout(settings.max_job_seconds):
                    if operation == "search":
                        if s.get("search_mode") == "anime":
                            results, partial = await catalog.anime_search(s["query"], s["job"], s["user"])
                        else: results, partial = await catalog.search(s["query"], s["job"], s["user"])
                        s.update(screen="results", results=[r.model_dump() for r in results], partial=partial, page=0)
                    elif operation == "details":
                        result = SearchResult.model_validate(s["results"][index])
                        if s.get("selected_source"):
                            result.refs = [ref for ref in result.refs if ref.site == s["selected_source"]]
                        media = await catalog.details(result, s["job"], s["user"])
                        s.update(screen="anime" if media.watch_links or media.refs[0].site == "kuroiru" else "choice", media=media.model_dump(), filters={}, page=0)
                        recent = await self.store.get("recent", s["user"], [])
                        item = {k: media.model_dump()[k] for k in SearchResult.model_fields}
                        if not recent or recent[0]["id"] != item["id"]: recent.insert(0, item)
                        await self.store.put("recent", s["user"], recent[:20])
                        await self.store.log(s["job"], s["user"], "TITLE_SELECTED", title=media.title, year=media.year)
                    elif operation == "collection":
                        media = Media.model_validate(s["media"])
                        parent = matching(media,s["filters"])[0]
                        variants = await catalog.expand_collection(media,parent)
                        media.variants = variants
                        s.update(screen="choice",media=media.model_dump(),filters={},page=0)
                    else:
                        media = Media.model_validate(s["media"])
                        variants = matching(media, s["filters"])
                        if len(variants) != 1: raise FlowError("INPUT", "Select a single file first")
                        site = job_registry.sites.get(media.refs[0].site)
                        result = await resolver.resolve(variants[0], site.providers if site else [], s["job"], s["user"])
                        s.update(screen="done", final_url=result.url, result_note=("This link may expire.\n" if result.temporary == "likely" else "") + result.note)
                    await self.render(s, poster=operation == "details")
                    await self.store.log(s["job"], s["user"], "JOB_SUCCESS", operation=operation)
            except asyncio.CancelledError:
                await self.store.log(s["job"], s["user"], "JOB_CANCELLED", operation=operation)
                raise
            except Exception as error:
                if isinstance(error, FlowError): code, message = error.code, error.message
                elif isinstance(error, TimeoutError): code, message = "TIMEOUT", "This operation exceeded its time limit."
                else: code, message = "INTERNAL", "Something went wrong. The owner can inspect this error ID."
                s.update(screen="error", error=message)
                if code == "DRY_RUN": s["retry"] = None
                await self.store.log(s["job"], s["user"], "JOB_FAILED", reason=code, operation=operation, error_type=type(error).__name__, diagnostic=str(error))
                try: await self.render(s)
                except FlowError: pass
                if code in {"BLOCKED", "UNSUPPORTED", "BROWSER_REQUIRED", "PARSER_CHANGED", "INTERNAL", "INVALID_RESPONSE", "UNAVAILABLE"}:
                    # One notification per cause per minute prevents public users flooding the owner.
                    notice_key = uid(code, message)
                    notify = not await self.store.get("owner_notice", notice_key)
                    if notify: await self.store.put("owner_notice", notice_key, True, 60)
                    for owner in settings.owner_ids:
                        if not notify: break
                        try: await self.tg.text(owner, f"⚠️ Owner attention needed\nJob <code>{s['job']}</code> • {escape(operation)}\n{escape(message)}\nUse /log {s['job']}")
                        except FlowError: pass
            finally:
                self.jobs.pop(s["id"], None)
                self.user_jobs[s["user"]].discard(s["id"])
        task = asyncio.create_task(run())
        self.jobs[s["id"]] = task; self.user_jobs[s["user"]].add(s["id"])

    async def callback(self, callback):
        user = callback["from"]["id"]
        message = callback.get("message")
        if not message: return
        chat, data = message["chat"]["id"], callback.get("data", "")
        await self.tg.call("answerCallbackQuery", callback_query_id=callback["id"])
        if not await self.authorize(user, chat): return
        if data == "noop": return
        if data == "help": await self.tg.text(chat, HELP + (ADMIN_HELP if await self.staff(user) else "")); return
        if data.startswith("admin:"):
            if not await self.staff(user) or chat != user: return
            await self.admin_callback(user, chat, data); return
        parts = data.split(":", 4)
        if len(parts) != 5 or parts[0] != "s": return
        _, sid, revision, action, arg = parts
        s = await self.store.get("sessions", sid)
        if not s or s["user"] != user or s["chat"] != chat:
            await self.tg.text(chat, "This menu expired or belongs to another person. Use /search or /continue."); return
        if action == "cancel":
            task = self.jobs.get(sid)
            if task: task.cancel(); await asyncio.gather(task, return_exceptions=True)
            s["screen"] = "cancelled"; await self.render(s); return
        async with self.locks[sid]:
            s = await self.store.get("sessions", sid)
            if str(s["revision"]) != revision:
                await self.tg.text(chat, "That button is from an older menu. Use the latest menu or /continue."); return
            if sid in self.jobs: return
            if action == "page":
                s["page"] = max(0, min(1000, int(arg))); await self.render(s)
                if s["screen"] == "screenshots": await self.sample_album(s)
            elif action == "title":
                index = int(arg)
                if not 0 <= index < len(s["results"]): return
                self.push(s)
                s["title_index"], s["selected_source"] = index, None
                result = SearchResult.model_validate(s["results"][index])
                if len(result.refs)>1:
                    s.update(screen="sources",page=0); await self.render(s)
                else: await self.start_job(s, "details", index)
            elif action == "source":
                result = SearchResult.model_validate(s["results"][s["title_index"]])
                if arg not in [r.site for r in result.refs]: return
                self.push(s);s["selected_source"] = arg
                await self.start_job(s,"details",s["title_index"])
            elif action == "collection":
                self.push(s); await self.start_job(s,"collection")
            elif action == "screenshots":
                media = Media.model_validate(s["media"])
                self.push(s);s.update(screen="screenshots",page=0);await self.render(s)
                await self.sample_album(s)
            elif action in {"animeall", "animegroup"}:
                self.push(s);s.update(screen="animeall" if action=="animeall" else "animegroup",anime_group=arg,page=0);await self.render(s)
            elif action == "choose":
                index = int(arg)
                if not 0 <= index < len(s["options"]): return
                self.push(s)
                s["filters"][s["stage"]] = s["options"][index]["value"]
                s["page"] = 0; await self.render(s)
            elif action in {"resolve", "refresh"}:
                if action == "resolve": self.push(s)
                await self.start_job(s, "resolve")
            elif action == "back" and s["history"]:
                s.update(s["history"].pop()); await self.render(s)
            elif action == "retry" and s.get("retry") and s.get("retries", 0) < 3:
                s["retries"] += 1
                await self.start_job(s, **s["retry"], retry=True)
            elif action == "favorite":
                media = Media.model_validate(s["media"])
                space = "favorites_" + str(user)
                if await self.store.get(space, media.id): await self.store.remove(space, media.id)
                else:
                    item = {k: media.model_dump()[k] for k in SearchResult.model_fields}
                    await self.store.put(space, media.id, item)
                await self.render(s)

    async def sample_album(self,s):
        if not await self.permitted(s['user']):return
        media=Media.model_validate(s['media']);samples=media.screenshots[s['page']*6:(s['page']+1)*6]
        if not samples:return
        caption=f'{media.title} • {media.refs[0].site} • Source-provided samples'
        try:
            if len(samples)==1:await self.tg.call('sendPhoto',chat_id=s['chat'],photo=samples[0].url,caption=caption[:900])
            else:await self.tg.call('sendMediaGroup',chat_id=s['chat'],media=[{'type':'photo','media':x.url,**({'caption':caption[:900]} if i==0 else {})} for i,x in enumerate(samples)])
        except FlowError:
            await self.tg.text(s['chat'],'The images could not be loaded into Telegram. Use the screenshot links above.')

    async def command(self, message):
        if not message.get("from") or not message.get("text"): return
        user, chat = message["from"]["id"], message["chat"]["id"]
        text = message["text"].strip()
        if not text.startswith("/"): text = "/search " + text
        cmd, _, arg = text.partition(" ")
        if "@" in cmd and cmd.split("@", 1)[1].lower() != self.username.lower(): return
        cmd, arg = cmd.split("@")[0].lower(), arg.strip()
        if not await self.authorize(user, chat): return
        if await self.management(user,chat,cmd,arg): return
        if cmd == "/start":
            cfg = self.registry.settings
            keyboard = [[{"text": "Channel", "url": cfg.channel_url}, {"text": "💬 Support", "url": cfg.support_url}], [button("Help & Commands ❗❓", "help")]]
            caption = WELCOME.format(bot_url=escape(cfg.bot_url, quote=True))
            video = self.registry.root / cfg.welcome_video
            if video.exists() and video.is_file() and video.stat().st_size < 49_000_000:
                try: await self.tg.upload(chat, video, caption, keyboard, "sendVideo", "video"); return
                except FlowError: pass
            await self.tg.text(chat, caption, keyboard)
        elif cmd == "/help": await self.tg.text(chat, HELP + (ADMIN_HELP if await self.staff(user) else ""))
        elif cmd in {"/search", "/s", "/anime"}:
            if not arg: await self.tg.text(chat, "Use /search followed by a title, for example /search dear"); return
            timestamps = self.search_times[user]; now = time.monotonic()
            while timestamps and now - timestamps[0] >= 60: timestamps.popleft()
            if len(timestamps) >= self.registry.settings.searches_per_minute:
                await self.tg.text(chat, "Please wait a minute before searching again."); return
            timestamps.append(now)
            s = await self.new_session(user, chat, arg)
            s["search_mode"] = "anime" if cmd == "/anime" else "movies"
            await self.start_job(s, "search")
        elif cmd in {"/favorites", "/recent"}:
            values = [v for k, v in await self.store.list("favorites_" + str(user), 1000)] if cmd == "/favorites" else await self.store.get("recent", user, [])
            s = await self.new_session(user, chat, "Favorites" if cmd == "/favorites" else "Recent titles")
            s["results"] = values; await self.render(s)
        elif cmd == "/continue":
            sid = await self.store.get("last_session", user)
            s = await self.store.get("sessions", sid) if sid else None
            if not s: await self.tg.text(chat, "No saved session. Start with /search."); return
            if sid in self.jobs: await self.tg.text(chat, "Your last operation is still running. Use /cancel to stop it."); return
            s["chat"], s["message"], s["media_message"] = chat, None, False
            if s["screen"] == "loading": s.update(screen="error", error="The previous operation was interrupted. Retry to resume.")
            if s["screen"] in {"done", "cancelled"}: s["screen"] = ("anime" if s.get("media", {}).get("watch_links") else "choice") if s.get("media") else "results"
            await self.render(s, poster=s["screen"] == "choice")
        elif cmd == "/cancel":
            ids = list(self.user_jobs[user])
            tasks = [self.jobs[sid] for sid in ids if sid in self.jobs]
            for task in tasks: task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for sid in ids:
                previous = await self.store.get("sessions", sid)
                if previous: previous["screen"] = "cancelled"; await self.save(previous)
            await self.tg.text(chat, "Your active jobs were cancelled.")
        elif await self.staff(user) and chat == user: await self.admin_command(user, chat, cmd, arg)
        else: await self.tg.text(chat, "Use /help to see available commands.")

    async def show_sites(self, chat):
        lines, keyboard = ["<b>Websites</b>"], []
        for site in self.registry.sites.values():
            enabled = await self.catalog.enabled(site); health = await self.catalog.health(site.id)
            state = "Disabled" if not enabled else "Cooldown" if health["cooldown_until"] > time.time() else "Degraded" if health["consecutive"] else "Ready" if health["success"] else "Not yet checked"
            lines.append(f"{escape(site.name)} — {state} • {health['success']} successful / {health['failed']} failed")
            keyboard.append([button("Disable" if enabled else "Enable", f"admin:toggle:{site.id}"), button("Test " + site.name, f"admin:test:{site.id}")])
        if self.registry.errors: lines.append("Configuration errors: " + escape(str(self.registry.errors))[:1000])
        await self.tg.text(chat, "\n".join(lines), keyboard)

    async def admin_callback(self, user, chat, data):
        _, action, value = data.split(":", 2)
        if action == "toggle" and value in self.registry.sites:
            site = self.registry.sites[value]
            current = await self.catalog.enabled(site)
            await self.store.put("site_enabled", value, not current)
            if not site.enabled and not current:
                await self.tg.text(chat, "This site is disabled in YAML. Set enabled: true and /reload first.")
            await self.show_sites(chat)
        elif action == "test" and value in self.registry.sites:
            job = secrets.token_hex(4).upper()
            await self.tg.text(chat, "Testing configured search…")
            try:
                async with asyncio.timeout(self.registry.settings.max_job_seconds):
                    items = await self.catalog.search_one(self.registry.sites[value], "dear", job, user)
                await self.tg.text(chat, f"Search parser: {len(items)} titles.\nJob: <code>{job}</code>")
            except Exception as e: await self.tg.text(chat, "Test failed: " + escape(e.message if isinstance(e, FlowError) else type(e).__name__))
        elif action == "inspect": await self.admin_command(user, chat, "/inspect", value)
        elif action == "retry":
            mapping = await self.store.get("job_session", value)
            original = await self.store.get("sessions", mapping["session"]) if mapping else None
            if not original or not original.get("retry"):
                await self.tg.text(chat, "That job can no longer be retried."); return
            if original["id"] in self.jobs:
                await self.tg.text(chat, "That job is still running."); return
            s = copy.deepcopy(original)
            s.update(id=secrets.token_hex(5), user=user, chat=chat, message=None, media_message=False, revision=0)
            await self.save(s)
            await self.start_job(s, **s["retry"])

    async def admin_command(self, user, chat, cmd, arg):
        if cmd == "/sites": await self.show_sites(chat)
        elif cmd == "/reload":
            try:
                candidate = await load_runtime(self.registry.root,self.store)
                # Existing jobs retain references to the old immutable registry.
                self.registry = candidate
                self.network = Network(candidate.settings.requests_concurrency)
                self.catalog = Catalog(candidate, self.network, self.store)
                self.resolver = Resolver(candidate, self.network, self.store)
                await self.tg.text(chat, "Configuration validated and reloaded. Active settings are now updated.")
            except Exception as e: await self.tg.text(chat, "Reload rejected; previous configuration retained.\n" + escape(str(e))[:2500])
        elif cmd == "/maintenance" and arg in {"on", "off"}:
            self.maintenance = arg == "on"; await self.store.put("settings", "maintenance", self.maintenance)
            await self.tg.text(chat, "Maintenance " + arg)
        elif cmd == "/version":
            import platform
            lines = [f"App {__version__}", "Database schema 1", "HTML parser 1", "Python " + platform.python_version()]
            lines += [f"Site {s.id}: {s.version}" for s in self.registry.sites.values()]
            lines += [f"Provider {p.id}: {p.version}" for p in self.registry.providers.values()]
            await self.tg.text(chat, escape("\n".join(lines)))
        elif cmd == "/stats":
            logs = await self.store.logs(limit=10000, since=time.time() - 7 * 86400)
            codes = Counter(x["code"] for x in logs)
            today = sum(x["code"] == "SEARCH_COMPLETE" and x["timestamp"] > time.time() - 86400 for x in logs)
            active = len({x["user_id"] for x in logs})
            selections = Counter(json.loads(x['payload']).get('title','Unknown') for x in logs if x['code']=='TITLE_SELECTED')
            latencies = [json.loads(x['payload']).get('latency',0) for x in logs if x['code']=='SEARCH_SITE_SUCCESS']
            cache_total = codes['CACHE_HIT'] + codes['SEARCH_SITE_SUCCESS'] + codes['DETAIL_SUCCESS']
            cache_rate = round(codes['CACHE_HIT'] / cache_total * 100,1) if cache_total else 0
            summary = f"<b>Last 7 days</b> (up to 10,000 events)\nSearches in last 24h: {today}\nSearches this week: {codes['SEARCH_COMPLETE']}\nActive users: {active}\nSuccessful jobs: {codes['JOB_SUCCESS']}\nFailed jobs: {codes['JOB_FAILED']}\nCache hit rate: {cache_rate}%\nAverage source-search time: {sum(latencies)/len(latencies) if latencies else 0:.2f}s\nProvider successes: {codes['PROVIDER_SUCCESS']}\nRunning jobs: {len(self.jobs)}\nPersistent database: {'PostgreSQL' if self.store.durable else 'Local SQLite'}"
            if selections: summary += "\n\nMost selected titles:\n" + "\n".join(f"{escape(title)}: {count}" for title,count in selections.most_common(5))
            for site in self.registry.sites.values():
                health=await self.catalog.health(site.id)
                summary += f"\n{escape(site.name)}: {health['success']} OK / {health['failed']} failed"
            for provider, health in await self.store.list("provider_health"):
                attempts = health['success'] + health['failed']
                summary += f"\nProvider {escape(provider)}: {health['success'] / attempts * 100 if attempts else 0:.1f}% success, {health['latency']:.2f}s"
            await self.tg.text(chat, summary)
        elif cmd == "/inspect":
            data = await self.store.get("inspect", arg)
            if data is None: await self.tg.text(chat, "No retained parser output for that job."); return
            content = json.dumps(data, ensure_ascii=False, indent=2).encode()
            await self.tg.call("sendDocument", chat_id=chat, upload=("document", f"inspect-{arg}.json", content, "application/json"))
        elif cmd == "/log":
            if arg == "stop":
                task = self.live_feeds.pop(chat, None)
                if task: task.cancel()
                await self.tg.text(chat, "Log feed stopped."); return
            if arg == "live":
                old = self.live_feeds.pop(chat, None)
                if old: old.cancel()
                async def feed():
                    try:
                        for _ in range(12):
                            await self.show_logs(chat, {}, limit=6)
                            await asyncio.sleep(10)
                    except (FlowError, asyncio.CancelledError): pass
                    finally: self.live_feeds.pop(chat, None)
                self.live_feeds[chat] = asyncio.create_task(feed()); return
            parts = arg.split()
            filters = {}
            if arg == "errors": filters["errors"] = True
            elif len(parts) == 2 and parts[0] in {"user", "site", "provider"}: filters[{"user": "user_id"}.get(parts[0], parts[0])] = parts[1]
            elif arg: filters["job"] = arg
            await self.show_logs(chat, filters)
        elif cmd == "/backup":
            from .backup import backup_bytes
            data = await backup_bytes(self.store, self.registry.root)
            await self.tg.call("sendDocument", chat_id=chat, upload=("document", f"lost-movies-backup-{int(time.time())}.zip", data, "application/zip"))
        else: await self.tg.text(chat, HELP + ADMIN_HELP)

    async def show_logs(self, chat, filters, limit=18):
        events = await self.store.logs(limit=limit, **filters)
        lines = []
        for event in reversed(events):
            stamp = time.strftime("%H:%M:%S UTC", time.gmtime(event["timestamp"]))
            lines.append(f"{stamp} {event['job']} {event['code']}\n{event['site'] or event['provider']} {event['payload'][:220]}")
        keyboard = []
        if filters.get("job"):
            job = filters["job"]
            keyboard = [[button("View Parser", "admin:inspect:" + job), button("Retry Job", "admin:retry:" + job)]]
        text = "\n\n".join(lines) or "No matching events."
        if len(text) > 3000:
            await self.tg.call("sendDocument", chat_id=chat, upload=("document", "diagnostics.txt", text.encode(), "text/plain"))
            text = "Diagnostic events attached."
        await self.tg.text(chat, "<pre>" + escape(text) + "</pre>", keyboard)

    async def handle(self, update):
        try:
            if "callback_query" in update: await self.callback(update["callback_query"])
            elif "message" in update: await self.command(update["message"])
        except Exception as e:
            await self.store.log("UPDATE", 0, "JOB_FAILED", diagnostic=str(e), error_type=type(e).__name__)

    async def poll(self):
        me = await self.tg.call("getMe"); self.username = me.get("username", "")
        webhook = await self.tg.call("getWebhookInfo")
        if webhook.get("url"):
            raise FlowError("TELEGRAM_WEBHOOK", "A webhook is already configured. Remove it explicitly before using polling.")
        await self.tg.call("setMyCommands", commands=[{"command": c, "description": d} for c, d in [("start", "Welcome"), ("search", "Search movies and series"), ("anime", "Find anime watch links"), ("help", "Help and commands"), ("favorites", "Saved titles"), ("recent", "Recent titles"), ("continue", "Resume last session"), ("cancel", "Cancel active jobs")]])
        self.maintenance = await self.store.get("settings", "maintenance", self.maintenance)
        offset = await self.store.get("system", "telegram_offset", 0)
        while True:
            try:
                updates = await self.tg.call("getUpdates", offset=offset, timeout=25, allowed_updates=["message", "callback_query"])
                self.polling_ok = True; self.last_poll = time.time()
                for update in updates:
                    # Dispatch different users independently; bound pending update handlers.
                    while len(self.update_tasks) >= 40:
                        await asyncio.wait(self.update_tasks, return_when=asyncio.FIRST_COMPLETED)
                    sender = update.get("message", update.get("callback_query", {})).get("from", {}).get("id", 0)
                    async def dispatch(item=update, sender_id=sender):
                        async with self.update_locks[sender_id]: await self.handle(item)
                    task = asyncio.create_task(dispatch())
                    self.update_tasks.add(task)
                    task.add_done_callback(self.update_tasks.discard)
                    offset = update["update_id"] + 1
                    await self.store.put("system", "telegram_offset", offset)
            except FlowError as e:
                self.polling_ok = False
                if e.code in {"TELEGRAM_CONFLICT", "TELEGRAM_AUTH"}: raise
                await self.store.log("POLL", 0, "TELEGRAM_RETRY", reason=e.code)
                await asyncio.sleep(3)

    async def close(self):
        tasks = [*self.jobs.values(), *self.live_feeds.values(), *self.update_tasks]
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
