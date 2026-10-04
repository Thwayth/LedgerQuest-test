"""Поиск идей по свечам одной монеты (без сети: чистая функция, годится и для бэктеста)."""
from __future__ import annotations

import pandas as pd

from .config import StrategyParams
from .indicators import atr
from .levels import find_pools, find_zone
from .models import Idea, Side
from .scoring import score_components, total_score

MIN_BARS = 120


def analyze(
    df: pd.DataFrame,
    symbol: str,
    exchange: str,
    timeframe: str,
    p: StrategyParams,
    sides: tuple[Side, ...] = (Side.LONG, Side.SHORT),
) -> Idea | None:
    """Возвращает лучшую идею (LONG или SHORT) либо None. Последняя свеча может быть живой."""
    if len(df) < MIN_BARS:
        return None
    a = float(atr(df).iloc[-1])
    price = float(df["close"].iloc[-1])
    if not a > 0 or price <= 0:
        return None
    prev = float(df["close"].iloc[-2])

    best: Idea | None = None
    for side in sides:
        long = side is Side.LONG
        zone = find_zone(
            df, side, tol_atr=p.zone_tol_atr, max_distance_pct=p.max_distance_pct,
            min_touches=p.zone_min_touches, min_height_pct=p.zone_min_height_pct,
        )
        if zone is None:
            continue
        pools = find_pools(df, zone, side, n=p.pools_count)
        if not pools:
            continue

        recent = df.iloc[-5:]
        if long:
            entry_cons = zone.high + 0.1 * a
            inv = min(zone.low, float(recent["low"].min())) - 0.5 * a
            risk, reward = entry_cons - inv, pools[-1].price - entry_cons
            dist = (zone.low - price) / price * 100
        else:
            entry_cons = zone.low - 0.1 * a
            inv = max(zone.high, float(recent["high"].max())) + 0.5 * a
            risk, reward = inv - entry_cons, entry_cons - pools[-1].price
            dist = (price - zone.high) / price * 100
        if risk <= 0 or reward <= 0:
            continue
        rr = reward / risk
        if rr < p.min_rr:
            continue

        comps = score_components(df, side, dist, p.max_distance_pct, zone.touches, rr)
        score = total_score(comps)
        if score < p.min_score:
            continue
        idea = Idea(
            symbol=symbol, exchange=exchange, timeframe=timeframe, side=side, zone=zone, pools=pools,
            price=price, change_abs=price - prev, change_pct=(price / prev - 1) * 100, score=score,
            entry_conservative=entry_cons, entry_aggressive=price, invalidation=inv,
            extra={"rr": round(rr, 2), "atr": a, "components": {k: round(v, 1) for k, v in comps.items()}},
        )
        if best is None or idea.score > best.score:
            best = idea
    return best
