import sys
from pathlib import Path
import yaml
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.config import ROOT, Site

identifier = input("Site ID (lowercase letters, numbers, dash): ").strip()
name = input("Display name: ").strip()
url = input("Base URL: ").strip()
data = {"id": identifier, "name": name, "base_url": url, "enabled": False,
        "search": {"path": input("Search path [/]: ").strip() or "/", "parameter": input("Search parameter [s]: ").strip() or "s"},
        "capabilities": {key: input(f"Supports {key}? [y/N]: ").lower() == "y" for key in ["movies", "series", "anime", "packs", "episodes", "languages"]}}
site = Site.model_validate(data)
path = ROOT / "config/sites" / f"{site.id}.yaml"
if path.exists(): raise SystemExit("A configuration with this filename already exists; nothing overwritten.")
path.write_text(yaml.safe_dump(site.model_dump(), sort_keys=False, allow_unicode=True), encoding="utf-8")
print("Created", path.name, "disabled. Adjust selectors and providers; run the tester before enabling.")
