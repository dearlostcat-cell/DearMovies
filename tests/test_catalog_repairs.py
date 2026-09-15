from pathlib import Path
import pytest
from bot.config import Registry
from bot.models import SearchResult
from bot.parser import parse_media
from bot.navigation import episode_key
from bot.service import Catalog
from bot.storage import Store


def test_supplied_episode_index_preserves_regular_and_bonus():
    r=Registry();r.load()
    html=(Path(__file__).parent/'fixtures/hdhub_episode_index.html').read_text(encoding='utf-8')
    m=parse_media(html,'https://new5.hdhub4u.cl/test',r.sites['hdhub4u'],SearchResult(id='test',title='test',kind='series'))
    assert len(m.variants)==11
    regular=[v for v in m.variants if v.episode is not None]
    assert [v.episode for v in regular]==list(range(1,8))
    assert len({episode_key(v) for v in m.variants})==11
    assert all(v.resolution=='Unknown' for v in m.variants)
    assert all(v.mode=='episode' and v.season==2 for v in m.variants)
    assert not any('WATCH' in link.label for v in m.variants for link in v.links)


@pytest.mark.asyncio
async def test_article_failures_do_not_cool_down_entire_site(tmp_path):
    r=Registry();r.load();store=Store('sqlite:///'+str(tmp_path/'health.db'))
    catalog=Catalog(r,None,store)
    try:
        for _ in range(10):await catalog.record_health('hdhub4u',False,.1,'PARSER_CHANGED')
        health=await catalog.health('hdhub4u')
        assert health['failed']==10 and health['cooldown_until']==0 and health['consecutive']==0
        for _ in range(r.settings.cooldown_failures):await catalog.record_health('rogmovies',False,.1,'BLOCKED')
        assert (await catalog.health('rogmovies'))['cooldown_until']>0
    finally:await store.close()
