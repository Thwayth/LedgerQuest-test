"""Контекст рынка из открытых источников (без ключей): funding, открытый интерес, тренд BTC, Fear & Greed.

Любой сбой источника даёт None (нейтрально) и не ломает скан.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

log = logging.getLogger(__name__)
BTC_SYMBOL = "BTC/USDT:USDT"
FNG_URL = "https://api.alternative.me/fng/?limit=1"


@dataclass
class GlobalContext:
    btc_trend: int | None = None      # +1 выше EMA50 и растёт 7д, -1 ниже и падает, 0 — смешанно
    btc_7d_pct: float | None = None
    fear_greed: int | None = None     # 0..100


@dataclass
class SymbolContext:
    funding_pct: float | None = None  # ставка funding за интервал, %
    oi_change_7d_pct: float | None = None


@dataclass
class Context:
    glob: GlobalContext = field(default_factory=GlobalContext)
    sym: SymbolContext = field(default_factory=SymbolContext)
    points: float = 0.0
    notes: list[str] = field(default_factory=list)


def _default_http_get(url: str) -> Any:
    import requests

    r = requests.get(url, timeout=10)
    r.raise_for_status()
    return r.json()


class MarketData:
    def __init__(self, ex, http_get: Callable[[str], Any] = _default_http_get):
        self.ex, self._get = ex, http_get

    # --- глобальный контекст (один раз за скан) -------------------------------
    def global_context(self, exchange: str) -> GlobalContext:
        g = GlobalContext()
        try:
            df = self.ex.ohlcv(exchange, BTC_SYMBOL, "1d", 120)
            close = df["close"]
            ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])
            ret7 = float(close.iloc[-1] / close.iloc[-8] - 1) * 100
            above = float(close.iloc[-1]) > ema50
            g.btc_7d_pct = round(ret7, 2)
            g.btc_trend = 1 if above and ret7 > 0 else -1 if (not above) and ret7 < 0 else 0
        except Exception as e:
            log.warning("тренд BTC недоступен: %s", e)
        try:
            g.fear_greed = int(self._get(FNG_URL)["data"][0]["value"])
        except Exception as e:
            log.warning("Fear & Greed недоступен: %s", e)
        return g

    # --- по монете -------------------------------------------------------------
    def symbol_context(self, exchange: str, symbol: str) -> SymbolContext:
        s = SymbolContext()
        client = self.ex.client(exchange)
        try:
            fr = client.fetch_funding_rate(symbol).get("fundingRate")
            if fr is not None:
                s.funding_pct = round(float(fr) * 100, 4)
        except Exception as e:
            log.info("funding %s: %s", symbol, e)
        try:
            if client.has.get("fetchOpenInterestHistory"):
                hist = client.fetch_open_interest_history(symbol, "1d", limit=8)
                vals = [h.get("openInterestValue") or h.get("openInterestAmount") for h in hist]
                vals = [float(v) for v in vals if v]
                if len(vals) >= 2 and vals[0] > 0:
                    s.oi_change_7d_pct = round((vals[-1] / vals[0] - 1) * 100, 2)
        except Exception as e:
            log.info("open interest %s: %s", symbol, e)
        return s
