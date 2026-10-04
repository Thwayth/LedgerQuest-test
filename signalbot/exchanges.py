"""Публичные данные Binance / Bybit / OKX через ccxt (без API-ключей)."""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

log = logging.getLogger(__name__)

TF_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000}
TF_LABEL_RU = {"1h": "1ч", "4h": "4ч", "1d": "1Д"}
COLUMNS = ["ts", "open", "high", "low", "close", "volume"]


@dataclass(frozen=True)
class Market:
    exchange: str
    symbol: str  # "TRB/USDT:USDT"
    base: str
    quote_volume: float


def make_client(name: str) -> Any:
    import ccxt

    opts = {"enableRateLimit": True, "timeout": 20_000}
    if name == "binance":
        return ccxt.binanceusdm(opts)
    if name == "bybit":
        return ccxt.bybit({**opts, "options": {"defaultType": "swap"}})
    if name == "okx":
        return ccxt.okx({**opts, "options": {"defaultType": "swap"}})
    raise ValueError(f"Неподдерживаемая биржа: {name}")


def _retry(fn: Callable[[], Any], attempts: int = 4, base_delay: float = 1.0) -> Any:
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:  # сетевые ошибки/rate limit ccxt
            if i == attempts - 1:
                raise
            delay = base_delay * 2**i
            log.warning("retry %s/%s after %s: %s", i + 1, attempts, delay, e)
            time.sleep(delay)


class Exchanges:
    """Синхронная обёртка (вызывается из потоков через asyncio.to_thread)."""

    def __init__(self, names: list[str], factory: Callable[[str], Any] = make_client):
        self.names = names
        self._factory = factory
        self._clients: dict[str, Any] = {}

    def client(self, name: str) -> Any:
        if name not in self._clients:
            self._clients[name] = self._factory(name)
        return self._clients[name]

    # --- вселенная ---------------------------------------------------------
    def universe(self, min_quote_volume: float, max_symbols: int, exclude_bases: list[str]) -> list[Market]:
        seen: dict[str, Market] = {}
        for name in self.names:  # порядок = приоритет биржи
            try:
                found = self._universe_one(name, min_quote_volume, exclude_bases)
            except Exception as e:
                log.error("universe %s failed: %s", name, e)
                continue
            for m in found:
                seen.setdefault(m.base, m)
        out = sorted(seen.values(), key=lambda m: m.quote_volume, reverse=True)
        return out[:max_symbols]

    def _universe_one(self, name: str, min_quote_volume: float, exclude_bases: list[str]) -> list[Market]:
        ex = self.client(name)
        markets = _retry(ex.load_markets)
        swaps = {
            s: m
            for s, m in markets.items()
            if m.get("swap") and m.get("linear") and m.get("quote") == "USDT" and m.get("active", True)
            and not m.get("expiry") and m.get("base", "").upper() not in exclude_bases
        }
        tickers = _retry(lambda: ex.fetch_tickers(list(swaps)))
        out: list[Market] = []
        for s, t in tickers.items():
            if s not in swaps:
                continue
            qv = t.get("quoteVolume")
            if qv is None and t.get("baseVolume") and t.get("last"):
                qv = t["baseVolume"] * t["last"]
            if qv and qv >= min_quote_volume:
                out.append(Market(name, s, swaps[s]["base"], float(qv)))
        return out

    # --- свечи -------------------------------------------------------------
    def ohlcv(self, exchange: str, symbol: str, timeframe: str, bars: int, since_ms: int | None = None) -> pd.DataFrame:
        """Свечи с пагинацией (у OKX/Bybit лимит на запрос меньше 365)."""
        ex = self.client(exchange)
        step = TF_MS[timeframe]
        since = since_ms if since_ms is not None else int(time.time() * 1000) - bars * step
        limit = 1000 if since_ms is not None else max(1, min(bars, 1000))
        rows: list[list[float]] = []
        for _ in range(60):  # предохранитель от бесконечной пагинации
            chunk = _retry(lambda: ex.fetch_ohlcv(symbol, timeframe, since=since, limit=limit))
            if not chunk:
                break
            rows.extend(chunk)
            nxt = chunk[-1][0] + step
            if nxt <= since or len(chunk) < 2 or nxt > time.time() * 1000:
                break
            since = nxt
            if len(rows) >= bars and since_ms is None:
                break
        df = pd.DataFrame(rows, columns=COLUMNS).drop_duplicates("ts").sort_values("ts")
        if since_ms is None:
            df = df.iloc[-bars:]
        return df.reset_index(drop=True)

    async def aohlcv(self, *a, **kw) -> pd.DataFrame:
        return await asyncio.to_thread(self.ohlcv, *a, **kw)

    async def auniverse(self, *a, **kw) -> list[Market]:
        return await asyncio.to_thread(self.universe, *a, **kw)
