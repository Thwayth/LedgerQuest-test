"""Скоринг идеи 0..100: чем выше, тем качественнее сетап."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .indicators import bb_width
from .levels import find_swings
from .models import Side


def _clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def structure_score(df: pd.DataFrame, side: Side, window: int = 80) -> float:
    """Доля растущих минимумов (LONG) / падающих максимумов (SHORT) среди последних swing-точек."""
    d = df.iloc[-window:].reset_index(drop=True)
    sh, sl = find_swings(d, left=3, right=3)
    idx = sl if side is Side.LONG else sh
    col = "low" if side is Side.LONG else "high"
    vals = [float(d[col].iloc[i]) for i in idx][-4:]
    if len(vals) < 2:
        return 0.0
    pairs = list(zip(vals[:-1], vals[1:]))
    good = sum(1 for a, b in pairs if (b > a if side is Side.LONG else b < a))
    return good / len(pairs)


def score_components(df: pd.DataFrame, side: Side, dist_pct: float, max_dist_pct: float, touches: int, rr: float) -> dict[str, float]:
    bw = bb_width(df).dropna().iloc[-120:]
    if len(bw) >= 30:
        pct_rank = float((bw < bw.iloc[-1]).mean())  # доля более узких значений в прошлом
    else:
        pct_rank = 0.5
    vol = df["volume"].to_numpy(dtype=float)
    recent, prev = vol[-5:].mean(), vol[-35:-5].mean() if len(vol) >= 35 else vol.mean()
    vol_ratio = recent / prev if prev > 0 else 1.0
    # цена внутри зоны (dist<=0) считается максимально близкой
    prox = 1.0 - _clip01(max(dist_pct, 0.0) / max_dist_pct)
    return {
        "proximity": 25 * prox,
        "squeeze": 20 * (1 - pct_rank),
        "volume": 15 * _clip01((vol_ratio - 0.8) / 1.5),
        "structure": 15 * structure_score(df, side),
        "touches": 10 * _clip01((min(touches, 4) - 1) / 3),
        "rr": 15 * _clip01(rr / 5),
    }


def total_score(components: dict[str, float]) -> float:
    return round(float(sum(components.values())), 1)
