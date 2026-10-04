"""Синтетические свечи для демо/тестов (когда биржи недоступны)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def make_trb_like(seed: int = 7, end: str = "2026-10-04") -> pd.DataFrame:
    """~365 дневных свечей, похожих на референс: нисходящий тренд, база, подход к сопротивлению."""
    key = [
        ("2025-10-05", 27000), ("2025-10-24", 34200), ("2025-11-08", 30000), ("2025-11-20", 32300),
        ("2025-12-05", 25000), ("2025-12-20", 18500), ("2026-01-15", 22800), ("2026-02-08", 13200),
        ("2026-03-05", 14800), ("2026-04-08", 17500), ("2026-04-22", 24200), ("2026-05-12", 19200),
        ("2026-06-05", 16500), ("2026-06-25", 12300), ("2026-07-20", 14200), ("2026-08-08", 16200),
        ("2026-08-20", 12800), ("2026-09-02", 21400), ("2026-09-14", 17300), ("2026-09-25", 19800),
        ("2026-10-03", 19919), ("2026-10-04", 20748),
    ]
    kd = pd.to_datetime([k for k, _ in key])
    kp = np.array([p for _, p in key], dtype=float)
    days = pd.date_range(kd[0], end, freq="D")
    base = np.interp(days.astype("int64"), kd.astype("int64"), kp)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 0.012, len(days)).cumsum() * 0.35
    noise -= np.linspace(noise[0], noise[-1], len(days))  # шум не уводит от опорных точек
    close = base * np.exp(noise * 0.25 + rng.normal(0, 0.011, len(days)))
    close[-1] = 20748.0
    close[-2] = 19919.0
    open_ = np.r_[close[0], close[:-1]]
    spread = np.abs(rng.normal(0.012, 0.007, len(days))) * close
    high = np.maximum(open_, close) + spread * rng.uniform(0.3, 1.2, len(days))
    low = np.minimum(open_, close) - spread * rng.uniform(0.3, 1.2, len(days))
    # пики-«ликвидность»: явные фитили в опорных точках
    for d, wick in [("2025-10-24", 1300), ("2025-11-20", 700), ("2026-04-22", 800), ("2026-01-15", 600), ("2026-09-02", 1500)]:
        i = int(np.argmin(np.abs(days - pd.Timestamp(d))))
        high[i] = max(high[i], close[i] + wick)
    df = pd.DataFrame(
        {"ts": days.as_unit("ms").astype("int64"), "open": open_, "high": high, "low": low, "close": close, "volume": rng.uniform(1e5, 5e5, len(days))}
    )
    return df
