"""Поиск уровней: swing-экстремумы, кластеры, зона пробоя, пулы ликвидности."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import atr
from .models import Pool, Side, Zone


def find_swings(df: pd.DataFrame, left: int = 5, right: int = 5) -> tuple[list[int], list[int]]:
    """Индексы swing-high и swing-low (экстремум в окне left/right баров)."""
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    sh: list[int] = []
    sl: list[int] = []
    for i in range(left, len(df) - right):
        wh = highs[i - left : i + right + 1]
        wl = lows[i - left : i + right + 1]
        if highs[i] == wh.max() and (wh == highs[i]).sum() == 1:
            sh.append(i)
        if lows[i] == wl.min() and (wl == lows[i]).sum() == 1:
            sl.append(i)
    return sh, sl


def cluster(points: list[tuple[int, float]], tol: float) -> list[list[tuple[int, float]]]:
    """Группирует (idx, price) по близости цены (относительный допуск tol)."""
    groups: list[list[tuple[int, float]]] = []
    for p in sorted(points, key=lambda x: x[1]):
        if groups and p[1] <= groups[-1][-1][1] * (1 + tol):
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def find_zone(
    df: pd.DataFrame,
    side: Side,
    *,
    tol_atr: float = 1.0,
    max_distance_pct: float = 10.0,
    min_touches: int = 2,
    min_height_pct: float = 8.0,
    lookback: int = 365,
) -> Zone | None:
    """Ближайшая зона сопротивления над ценой (LONG) или поддержки под ценой (SHORT)."""
    d = df.iloc[-lookback:].reset_index(drop=True)
    a = float(atr(d).iloc[-1])
    price = float(d["close"].iloc[-1])
    tol = tol_atr * a / price
    sh, sl = find_swings(d)
    long = side is Side.LONG
    pts = [(i, float(d["high"].iloc[i])) for i in sh] if long else [(i, float(d["low"].iloc[i])) for i in sl]
    best: Zone | None = None
    best_dist = float("inf")
    for g in cluster(pts, tol):
        if len(g) < min_touches:
            continue
        lo, hi = min(p for _, p in g), max(p for _, p in g)
        # зона включает тело свечей около уровня: расширяем на 0.3 ATR
        lo, hi = lo - 0.3 * a, hi + 0.3 * a
        # зона не тоньше min_height_pct: для LONG растёт вниз от верха кластера, для SHORT вверх
        h = price * min_height_pct / 100
        if hi - lo < h:
            lo, hi = (hi - h, hi) if long else (lo, lo + h)
        # расстояние от цены до ближней границы зоны; цена внутри зоны даёт dist <= 0
        near = lo if long else hi
        dist = (near - price) / price * 100 if long else (price - near) / price * 100
        if dist < -(hi - lo) / price * 100 or dist > max_distance_pct:
            continue
        if abs(dist) < best_dist:
            best_dist = abs(dist)
            best = Zone(low=lo, high=hi, touches=len(g), first_idx=min(i for i, _ in g) + (len(df) - len(d)))
    return best


def find_pools(
    df: pd.DataFrame,
    zone: Zone,
    side: Side,
    *,
    n: int = 2,
    tol_atr: float = 0.5,
    min_gap_atr: float = 1.0,
    lookback: int = 365,
) -> list[Pool]:
    """Ближайшие пулы ликвидности за зоной по направлению сделки."""
    off = len(df) - min(lookback, len(df))
    d = df.iloc[off:].reset_index(drop=True)
    a = float(atr(d).iloc[-1])
    price = float(d["close"].iloc[-1])
    tol = tol_atr * a / price
    sh, sl = find_swings(d)
    long = side is Side.LONG
    pts = [(i, float(d["high"].iloc[i])) for i in sh] if long else [(i, float(d["low"].iloc[i])) for i in sl]
    gap = min_gap_atr * a
    if long:
        pts = [p for p in pts if p[1] > zone.high + gap]
    else:
        pts = [p for p in pts if p[1] < zone.low - gap]
    pools: list[Pool] = []
    for g in cluster(pts, tol):
        # equal highs/lows: уровень = экстремум кластера, линия от самого раннего касания
        px = max(p for _, p in g) if long else min(p for _, p in g)
        pools.append(Pool(price=px, idx=min(i for i, _ in g) + off, touches=len(g)))
    pools.sort(key=lambda p: abs(p.price - zone.mid))
    # не берём пул, который ближе предыдущего менее чем на 1 ATR
    out: list[Pool] = []
    for p in pools:
        if out and abs(p.price - out[-1].price) < gap:
            continue
        out.append(p)
        if len(out) == n:
            break
    return out
