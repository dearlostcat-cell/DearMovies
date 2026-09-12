import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.config import Registry
from bot.models import SearchResult, Reference
from bot.parser import parse_search, parse_media
from bot.network import Network
from bot.service import Catalog
from bot.storage import Store


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("site"); parser.add_argument("--fixtures", action="store_true")
    parser.add_argument("--query", default="dear"); parser.add_argument("--detail-url")
    args = parser.parse_args()
    registry = Registry(); registry.load(atomic=True)
    site = registry.sites[args.site]
    print("Configuration: PASS")
    if args.fixtures:
        results, _ = parse_search(registry.fixture(site, "search"), site.base_url, site)
        expected_path = registry.root / "tests/expected" / (site.id + ".json")
        expected = json.loads(expected_path.read_text()) if expected_path.exists() else {}
        if "search_count" in expected: assert len(results) == expected["search_count"]
        print("Search fixtures:", len(results), "cards")
        for kind in ("movie", "series"):
            if kind not in site.fixtures: continue
            ref = Reference(site=site.id, url=site.base_url + "/sample-" + kind)
            media = parse_media(registry.fixture(site, kind), ref.url, site, SearchResult(id="fixture", title="Fixture", kind=kind, refs=[ref]))
            if kind in expected:
                assert media.title == expected[kind]["title"]
                assert len(media.variants) == expected[kind]["variants"]
                assert sum(v.mode == "pack" for v in media.variants) == expected[kind].get("packs", 0)
            print(kind, "PASS:", media.title, len(media.variants), "file entries")
        print("Fixture checks passed. This does not verify live providers.")
    else:
        store = Store("sqlite:///data/site-test.db")
        try:
            registry.settings.mock_mode = False
            catalog = Catalog(registry, Network(), store)
            async with asyncio.timeout(120):
                results = await catalog.search_one(site, args.query, "SITE_TEST", 0)
                print("Live search:", len(results), "titles")
                for result in results[:8]: print(" ", result.title, result.year)
                if args.detail_url:
                    result = SearchResult(id="live-test", title="Test", refs=[Reference(site=site.id, url=args.detail_url)])
                    media = await catalog.details(result, "DETAIL_TEST", 0)
                    print("Live details:", media.title, len(media.variants), "versions")
                    print("Provider hosts:", sorted({__import__('urllib.parse', fromlist=['urlsplit']).urlsplit(l.url).hostname for v in media.variants for l in v.links}))
        finally: await store.close()


if __name__ == "__main__": asyncio.run(main())
