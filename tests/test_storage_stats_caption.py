from signalbot.caption import build_caption, update_text
from signalbot.lifecycle import STOPPED, TP_ALL, Event, Tracked
from signalbot.models import Idea, Side, Zone
from signalbot.stats import compute_stats, format_stats
from signalbot.storage import Storage

D = 86_400_000


def tr(**kw):
    base = dict(
        symbol="TRB/USDT:USDT", exchange="binance", timeframe="1d", side=Side.LONG, zone_low=95, zone_high=100,
        pools=[110.0, 120.0], entry_cons=100.5, entry_aggr=98, invalidation=90, created_ts=1000,
    )
    base.update(kw)
    return Tracked(**base)


def test_storage_roundtrip_and_filters():
    db = Storage()
    a = tr()
    db.add(a)
    assert a.id == 1 and db.get(1).pools == [110.0, 120.0]
    a.status, a.closed_ts, a.result_pct, a.r_multiple = TP_ALL, 5000, 12.0, 2.0
    db.save(a)
    assert db.active() == []
    b = tr(symbol="ETH/USDT:USDT", created_ts=9000)
    db.add(b)
    assert [x.symbol for x in db.active()] == ["ETH/USDT:USDT"]
    assert db.count_created_since(2000) == 1
    assert db.blocked_symbols(cooldown_since_ms=4000) == {"TRB/USDT:USDT", "ETH/USDT:USDT"}
    assert db.blocked_symbols(cooldown_since_ms=6000) == {"ETH/USDT:USDT"}


def test_stats_winrate_and_drawdown():
    items = [
        tr(status=TP_ALL, closed_ts=1, result_pct=10, r_multiple=2.0),
        tr(status=STOPPED, closed_ts=2, result_pct=-5, r_multiple=-1.0),
        tr(status=STOPPED, closed_ts=3, result_pct=-5, r_multiple=-1.0),
        tr(status="CANCELLED", closed_ts=4),
        tr(status="WATCH"),
    ]
    s = compute_stats(items)
    assert (s.total, s.wins, s.losses, s.cancelled, s.active) == (3, 1, 2, 1, 1)
    assert s.winrate == 33.3 and s.max_dd_r == 2.0 and s.sum_pct == 0.0
    assert "Винрейт: 33.3%" in format_stats(s)
    assert "пока нет" in format_stats(compute_stats([]))


def _idea(side=Side.LONG):
    return Idea("TRB/USDT:USDT", "binance", "1d", side, Zone(95, 100, 2, 0), [], 98, 1, 1)


def test_caption_template_long_and_deterministic():
    c = build_caption(_idea(), "2026-10-04")
    assert c.startswith("<b>TRB</b> 🔥")
    assert "<blockquote>" in c and "</blockquote>" in c
    assert "пул" in c and "Не финансовый совет" in c
    assert c == build_caption(_idea(), "2026-10-04")
    assert "Не финансовый совет" not in build_caption(_idea(), "2026-10-04", disclaimer=False)


def test_caption_short_and_variation():
    assert "вниз" in build_caption(_idea(Side.SHORT), "d") or "поддерж" in build_caption(_idea(Side.SHORT), "d")
    variants = {build_caption(_idea(), f"2026-10-{d:02d}") for d in range(1, 15)}
    assert len(variants) > 1


def test_update_texts():
    t = tr(entry_price=100.0, result_pct=7.5, r_multiple=1.5, status=TP_ALL)
    assert "пробой произошёл ✅" in update_text(t, Event("BREAKOUT", 0, 101))
    assert "цель 1 из 2 взята 🎯" in update_text(t, Event("TP", 0, 110, 1))
    assert "+10.0%" in update_text(t, Event("TP", 0, 110, 1))
    assert "стоп ❌" in update_text(t, Event("STOP", 0, 90))
    assert "отменена ❌" in update_text(t, Event("CANCEL", 0, 90))
