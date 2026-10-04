import pandas as pd

from signalbot.lifecycle import BROKEN, CANCELLED, EXPIRED, STOPPED, TP_ALL, WATCH, Tracked, evaluate
from signalbot.models import Side

D = 86_400_000


def candles(rows, start=D * 100):
    return pd.DataFrame(
        [{"ts": start + i * D, "open": o, "high": h, "low": l, "close": c, "volume": 1.0} for i, (o, h, l, c) in enumerate(rows)]
    )


def long_idea(**kw):
    base = dict(
        symbol="X/USDT:USDT", exchange="bt", timeframe="1d", side=Side.LONG, zone_low=95, zone_high=100,
        pools=[110.0, 120.0], entry_cons=100.5, entry_aggr=98, invalidation=90, created_ts=D * 100,
    )
    base.update(kw)
    return Tracked(**base)


def run(t, rows, wait=10):
    df = candles(rows)
    return evaluate(t, df, df, df["ts"].iloc[-1] + D, D, D, wait * D)


def test_breakout_then_all_targets():
    t = long_idea()
    ev = run(t, [(98, 101, 97, 101), (101, 111, 100, 109), (109, 121, 108, 120)])
    assert [e.kind for e in ev] == ["BREAKOUT", "TP", "TP", "DONE"]
    assert t.status == TP_ALL and t.entry_price == 101
    assert t.result_pct > 0 and t.r_multiple > 0


def test_stop_after_breakout_is_loss():
    t = long_idea()
    ev = run(t, [(98, 101, 97, 101), (101, 102, 89, 91)])
    assert [e.kind for e in ev] == ["BREAKOUT", "STOP"]
    assert t.status == STOPPED and t.result_pct < 0 and t.r_multiple < 0


def test_partial_tp_then_stop_blends_result():
    t = long_idea()
    run(t, [(98, 101, 97, 101), (101, 111, 100, 109), (109, 110, 89, 90)])
    assert t.status == STOPPED and t.tp_hit == 1
    expected = ((110 - 101) / 101 + (90 - 101) / 101) / 2 * 100
    assert abs(t.result_pct - expected) < 0.01


def test_stop_wins_over_tp_in_same_candle():
    t = long_idea()
    run(t, [(98, 101, 97, 101), (101, 125, 85, 100)])
    assert t.status == STOPPED and t.tp_hit == 0


def test_cancel_before_breakout():
    t = long_idea()
    ev = run(t, [(98, 99, 88, 89)])
    assert [e.kind for e in ev] == ["CANCEL"] and t.status == CANCELLED


def test_expired_when_no_breakout():
    t = long_idea()
    ev = run(t, [(97, 99, 96, 98)] * 14, wait=5)
    assert ev[-1].kind == "EXPIRED" and t.status == EXPIRED


def test_wick_above_without_close_is_not_breakout():
    t = long_idea()
    run(t, [(98, 105, 97, 99)])
    assert t.status == WATCH


def test_short_side_mirrors():
    t = long_idea(side=Side.SHORT, zone_low=100, zone_high=105, pools=[90.0, 80.0], invalidation=110, entry_cons=99.5)
    ev = run(t, [(102, 103, 98, 99), (99, 100, 89, 91), (91, 92, 79, 80)])
    assert [e.kind for e in ev] == ["BREAKOUT", "TP", "TP", "DONE"]
    assert t.result_pct > 0


def test_idempotent_on_repeat_evaluation():
    t = long_idea()
    rows = [(98, 101, 97, 101), (101, 111, 100, 109)]
    run(t, rows)
    assert t.status == BROKEN and t.tp_hit == 1
    assert run(t, rows) == []
