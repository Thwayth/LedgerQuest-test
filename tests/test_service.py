import asyncio
import time

import pandas as pd

from signalbot.config import Config
from signalbot.exchanges import Market
from signalbot.service import DAY_MS, Service
from signalbot.storage import Storage
from signalbot.synthetic import make_trb_like

PNG = b"\x89PNG"


class FakePub:
    def __init__(self):
        self.posts, self.replies, self.sent = [], [], []

    async def post_photo(self, png, caption, name):
        self.posts.append((png, caption, name))
        return 111, len(self.posts)

    async def reply(self, chat_id, message_id, text):
        self.replies.append((chat_id, message_id, text))

    async def send(self, chat_id, text):
        self.sent.append((chat_id, text))


class FakeEx:
    """Одна монета с синтетической историей; 1ч-свечи после публикации задаются тестом."""

    def __init__(self):
        self.df = make_trb_like()
        self.intraday = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
        self.daily_override = None

    async def auniverse(self, *a, **k):
        return [Market("binance", "TRB/USDT:USDT", "TRB", 50e6)]

    async def aohlcv(self, exchange, symbol, tf, bars, since_ms=None):
        if tf == "1h":
            return self.intraday
        return self.daily_override if self.daily_override is not None else self.df


def make_service():
    cfg = Config(owner_id=1, dry_run=True, watermark_text="T")
    cfg.strategy.min_score = 0
    ex, pub, db = FakeEx(), FakePub(), Storage()
    return Service(cfg, ex, db, pub), ex, pub, db


def test_scan_publishes_once_and_respects_cooldown_and_daily_limit():
    svc, ex, pub, db = make_service()
    first = asyncio.run(svc.scan_once())
    assert len(first) == 1 and len(pub.posts) == 1
    png, caption, name = pub.posts[0]
    assert png.startswith(PNG) and "<b>TRB</b>" in caption and name == "TRB"
    saved = db.get(1)
    assert saved.chat_id == 111 and saved.message_id == 1 and saved.status == "WATCH"
    assert asyncio.run(svc.scan_once()) == []  # монета заблокирована (активная идея)
    assert len(pub.posts) == 1


def test_daily_limit_blocks_scan():
    svc, ex, pub, db = make_service()
    svc.cfg.max_ideas_per_day = 0
    assert asyncio.run(svc.scan_once()) == []
    assert pub.posts == []


def test_track_sends_breakout_and_tp_replies_once():
    svc, ex, pub, db = make_service()
    asyncio.run(svc.scan_once())
    t = db.get(1)
    now = int(time.time() * 1000)
    day0 = now - now % DAY_MS
    # вчерашняя дневная свеча закрылась выше зоны -> пробой; затем 1ч-свеча касается цели 1
    daily = pd.DataFrame([{"ts": day0 - DAY_MS, "open": t.zone_high, "high": t.zone_high * 1.03, "low": t.zone_high * 0.99,
                           "close": t.zone_high * 1.02, "volume": 1.0}])
    ex.daily_override = daily
    ex.intraday = pd.DataFrame([{"ts": day0, "open": t.zone_high * 1.02, "high": t.pools[0] * 1.001,
                                 "low": t.zone_high * 1.0, "close": t.pools[0], "volume": 1.0}])
    # created_ts должен быть раньше закрытия «вчерашней» свечи
    t.created_ts = day0 - DAY_MS - 3_600_000
    db.save(t)
    asyncio.run(svc.track_once())
    texts = [r[2] for r in pub.replies]
    assert any("пробой произошёл" in x for x in texts)
    assert any("цель 1 из 2" in x for x in texts)
    assert all(r[0] == 111 and r[1] == 1 for r in pub.replies)  # ответами на исходный пост
    n = len(pub.replies)
    asyncio.run(svc.track_once())
    assert len(pub.replies) == n  # события не дублируются
    assert db.get(1).tp_hit == 1


def test_stats_and_weekly_report_text():
    svc, ex, pub, db = make_service()
    assert "Закрытых сделок пока нет" in svc.stats_text()
    asyncio.run(svc.weekly_report())
    assert pub.sent and pub.sent[0][0] == 1  # DRY_RUN: владельцу
