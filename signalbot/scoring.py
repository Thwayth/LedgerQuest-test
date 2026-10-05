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


def context_points(side: Side, glob, sym) -> tuple[float, list[str]]:
    """Поправка к скорингу по рыночному контексту, от -10 до +10 пунктов.

    funding: толпа в лонгах — минус лонгу и плюс шорту (и наоборот);
    OI: рост интереса при поджатии — топливо для пробоя, резкий спад — минус;
    BTC: идея по тренду BTC плюс, против тренда минус; Fear & Greed — лёгкий контртренд.
    """
    long = side is Side.LONG
    pts, notes = 0.0, []
    f = getattr(sym, "funding_pct", None)
    if f is not None:
        crowd = f if long else -f  # >0: толпа на нашей стороне
        if crowd >= 0.05:
            pts -= 4; notes.append(f"funding {f:+.3f}%: толпа на стороне идеи (−4)")
        elif crowd >= 0.02:
            pts -= 2; notes.append(f"funding {f:+.3f}%: перегрев (−2)")
        elif crowd <= -0.01:
            pts += 3; notes.append(f"funding {f:+.3f}%: толпа против идеи (+3)")
    oi = getattr(sym, "oi_change_7d_pct", None)
    if oi is not None:
        if oi >= 10:
            pts += 3; notes.append(f"OI {oi:+.0f}% за 7д (+3)")
        elif oi <= -15:
            pts -= 2; notes.append(f"OI {oi:+.0f}% за 7д (−2)")
    trend = getattr(glob, "btc_trend", None)
    if trend:
        aligned = trend == (1 if long else -1)
        pts += 3 if aligned else -4
        notes.append(f"BTC {'по' if aligned else 'против'} идеи (7д {glob.btc_7d_pct:+.1f}%) ({'+3' if aligned else '−4'})")
    fg = getattr(glob, "fear_greed", None)
    if fg is not None:
        if fg >= 75:
            d = -2 if long else 2
        elif fg <= 25:
            d = 2 if long else -2
        else:
            d = 0
        if d:
            pts += d; notes.append(f"Fear&Greed {fg} ({d:+d})")
    return max(-10.0, min(10.0, pts)), notes
