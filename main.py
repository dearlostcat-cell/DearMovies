from __future__ import annotations
import asyncio
import logging
import os
from aiohttp import web
from dotenv import load_dotenv
from bot.config import ROOT
from bot.storage import Store
from bot.control import BotController, create_control_web


async def serve():
    load_dotenv(ROOT/'.env');os.chdir(ROOT)
    store=Store()
    controller=BotController(ROOT,store,os.getenv('BOT_TOKEN','').strip())
    application=create_control_web(controller,os.getenv('WEB_CONTROL_PASSWORD',''))
    runner=web.AppRunner(application,access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner,'0.0.0.0',int(os.getenv('PORT','8080'))).start()
        print('Lost Movies control page is ready.',flush=True)
        await controller.initialize()
        while True:
            settings=controller.app.registry.settings if controller.app else None
            try:
                await store.cleanup(settings.job_retention_days if settings else 7)
            except Exception:
                logging.warning('Scheduled cleanup failed; control page remains available.')
            await asyncio.sleep(3600)
    finally:
        await controller.close();await runner.cleanup();await store.close()


if __name__=='__main__':
    logging.basicConfig(level=logging.WARNING)
    try:asyncio.run(serve())
    except KeyboardInterrupt:pass
