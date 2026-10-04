"""Жизненный цикл идеи: WATCH -> BROKEN -> TP_ALL | STOPPED, либо CANCELLED/EXPIRED.

Одна и та же логика используется трекером (живые 1ч-свечи) и бэктестом (дневные свечи).
Правила:
  * пробой = закрытие дневной свечи за границей зоны (LONG: выше high, SHORT: ниже low);
  * вход после пробоя = цена закрытия этой свечи;
  * в одной свече стоп приоритетнее целей (консервативно);
  * позиция делится на равные части по целям; остаток при стопе закрывается по стопу.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .models import Side

WATCH, BROKEN, TP_ALL, STOPPED, CANCELLED, EXPIRED = "WATCH", "BROKEN", "TP_ALL", "STOPPED", "CANCELLED", "EXPIRED"
ACTIVE = (WATCH, BROKEN)
FINISHED = (TP_ALL, STOPPED)  # идёт в статистику результатов


@dataclass
class Tracked:
    symbol: str
    exchange: str
    timeframe: str
    side: Side
    zone_low: float
    zone_high: float
    pools: list[float]
    entry_cons: float
    entry_aggr: float
    invalidation: float
    created_ts: int  # мс: момент публикации (в бэктесте: закрытие свечи сигнала)
    score: float = 0.0
    status: str = WATCH
    broken_ts: int | None = None
    entry_price: float | None = None
    tp_hit: int = 0
    closed_ts: int | None = None
    result_pct: float | None = None
    r_multiple: float | None = None
    chat_id: int | None = None
    message_id: int | None = None
    id: int | None = None


@dataclass
class Event:
    kind: str  # BREAKOUT | TP | STOP | DONE | CANCEL | EXPIRED
    ts: int
    price: float
    n: int = 0  # номер цели для TP


def _dir(t: Tracked) -> int:
    return 1 if t.side is Side.LONG else -1


def _finish(t: Tracked, status: str, ts: int, stop_price: float | None) -> None:
    d = _dir(t)
    entry = t.entry_price
    n = len(t.pools)
    total = 0.0
    for p in t.pools[: t.tp_hit]:
        total += (p - entry) / entry * d * 100 / n
    if stop_price is not None:
        total += (stop_price - entry) / entry * d * 100 * (n - t.tp_hit) / n
    risk_pct = abs(entry - t.invalidation) / entry * 100
    t.status, t.closed_ts = status, ts
    t.result_pct = round(total, 3)
    t.r_multiple = round(total / risk_pct, 3) if risk_pct > 0 else 0.0


def evaluate(
    t: Tracked,
    intraday: pd.DataFrame,
    daily: pd.DataFrame,
    now_ms: int,
    intraday_ms: int,
    daily_ms: int,
    max_wait_ms: int,
) -> list[Event]:
    """Двигает идею по свечам, мутирует `t`, возвращает новые события (по времени)."""
    events: list[Event] = []
    long = t.side is Side.LONG
    edge = t.zone_high if long else t.zone_low
    inv = t.invalidation

    def stopped(low: float, high: float) -> bool:
        return low <= inv if long else high >= inv

    if t.status == WATCH:
        bts = bprice = None
        for r in daily.itertuples(index=False):
            close_ts = r.ts + daily_ms
            if close_ts <= t.created_ts or close_ts > now_ms:
                continue
            if (r.close > edge) if long else (r.close < edge):
                bts, bprice = close_ts, float(r.close)
                break
        for r in intraday.itertuples(index=False):
            if r.ts + intraday_ms <= t.created_ts:
                continue
            if bts is not None and r.ts >= bts:
                break
            if stopped(r.low, r.high):
                t.status, t.closed_ts = CANCELLED, int(r.ts)
                return events + [Event("CANCEL", int(r.ts), inv)]
            if r.ts - t.created_ts > max_wait_ms:
                t.status, t.closed_ts = EXPIRED, int(r.ts)
                return events + [Event("EXPIRED", int(r.ts), float(r.close))]
        if bts is None:
            if now_ms - t.created_ts > max_wait_ms:
                t.status, t.closed_ts = EXPIRED, now_ms
                events.append(Event("EXPIRED", now_ms, float(intraday["close"].iloc[-1]) if len(intraday) else t.entry_aggr))
            return events
        t.status, t.broken_ts, t.entry_price = BROKEN, bts, bprice
        events.append(Event("BREAKOUT", bts, bprice))

    if t.status == BROKEN:
        for r in intraday.itertuples(index=False):
            if r.ts < t.broken_ts:
                continue
            if stopped(r.low, r.high):
                events.append(Event("STOP", int(r.ts), inv))
                _finish(t, STOPPED, int(r.ts), inv)
                return events
            while t.tp_hit < len(t.pools) and ((r.high >= t.pools[t.tp_hit]) if long else (r.low <= t.pools[t.tp_hit])):
                px = t.pools[t.tp_hit]
                t.tp_hit += 1
                events.append(Event("TP", int(r.ts), px, t.tp_hit))
            if t.tp_hit >= len(t.pools):
                events.append(Event("DONE", int(r.ts), t.pools[-1]))
                _finish(t, TP_ALL, int(r.ts), None)
                return events
    return events
