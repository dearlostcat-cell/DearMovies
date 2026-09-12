import argparse
import asyncio
import os
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
from bot.config import ROOT
from bot.storage import Store
from bot.backup import backup_bytes, restore


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore"); parser.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    os.chdir(ROOT); load_dotenv(ROOT / ".env")
    store = Store()
    try:
        Path("backups").mkdir(exist_ok=True)
        path = Path("backups") / f"{time.strftime('%Y%m%d-%H%M%S')}.zip"
        path.write_bytes(await backup_bytes(store, ROOT))
        print("Backup created:", path)
        if args.restore:
            await restore(store, ROOT, args.restore, args.confirm)
            print("Restore finished. Run configuration tests before restarting the bot.")
    finally: await store.close()


if __name__ == "__main__": asyncio.run(main())
