from __future__ import annotations

import asyncio
import json
from pathlib import Path
import aiohttp
from .models import FlowError


class Telegram:
    def __init__(self, token):
        self.token = token
        self.session = None
        self.limiter = asyncio.Lock()
        self.next_call = 0.0

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45))
        return self

    async def __aexit__(self, *args): await self.session.close()

    async def call(self, method, upload=None, **payload):
        for attempt in range(3):
            if method != "getUpdates":
                async with self.limiter:
                    now = asyncio.get_running_loop().time()
                    await asyncio.sleep(max(0, self.next_call - now))
                    self.next_call = asyncio.get_running_loop().time() + .06
            try:
                if upload:
                    key, filename, body, content_type = upload
                    form = aiohttp.FormData()
                    for k, v in payload.items():
                        form.add_field(k, json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v))
                    form.add_field(key, body, filename=filename, content_type=content_type)
                    kwargs = {"data": form}
                else: kwargs = {"json": payload}
                async with self.session.post(f"https://api.telegram.org/bot{self.token}/{method}", **kwargs) as response:
                    data = await response.json(content_type=None)
                if data.get("ok"): return data["result"]
                code = data.get("error_code")
                description = data.get("description", "Telegram request failed")
                if "message is not modified" in description: return None
                if code == 429:
                    await asyncio.sleep(min(data.get("parameters", {}).get("retry_after", 2), 60))
                    continue
                if code == 409: raise FlowError("TELEGRAM_CONFLICT", "Another instance is polling this bot. Stop the workspace Run process before publishing.")
                if code == 401: raise FlowError("TELEGRAM_AUTH", "Telegram rejected BOT_TOKEN. Check Replit Secrets.")
                raise FlowError("TELEGRAM", description)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                if attempt == 2: raise FlowError("TELEGRAM_NETWORK", "Telegram could not be reached")
                await asyncio.sleep(2 ** attempt)
        raise FlowError("TELEGRAM_LIMIT", "Telegram rate limit; please retry shortly")

    async def text(self, chat, text, keyboard=None, message=None):
        args = dict(chat_id=chat, text=text, parse_mode="HTML", link_preview_options={"is_disabled": True})
        if keyboard is not None: args["reply_markup"] = {"inline_keyboard": keyboard}
        if message: return await self.call("editMessageText", message_id=message, **args)
        return await self.call("sendMessage", **args)

    async def upload(self, chat, path, caption="", keyboard=None, method="sendDocument", field="document"):
        data = await asyncio.to_thread(Path(path).read_bytes)
        kwargs = dict(chat_id=chat, caption=caption, parse_mode="HTML")
        if keyboard: kwargs["reply_markup"] = {"inline_keyboard": keyboard}
        return await self.call(method, upload=(field, Path(path).name, data, "video/mp4" if field == "video" else "application/octet-stream"), **kwargs)
