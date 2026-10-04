from __future__ import annotations

import pandas as pd


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def bb_width(df: pd.DataFrame, period: int = 20, mult: float = 2.0) -> pd.Series:
    """Относительная ширина полос Боллинджера: (верх - низ) / середина."""
    ma = df["close"].rolling(period).mean()
    sd = df["close"].rolling(period).std(ddof=0)
    return (2 * mult * sd) / ma
