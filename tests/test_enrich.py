import asyncio
from types import SimpleNamespace as NS

import pandas as pd

from signalbot.config import Config
from signalbot.enrich import FNG_URL, GlobalContext, MarketData, SymbolContext
from signalbot.models import Side
from signalbot.scoring import context_points
from tests.test_service import FakeEx, FakePub, Storage

from signalbot.service import Service


def test_context_points_funding_crowding():
    crowded_long = context_points(Side.LONG, GlobalContext(), SymbolContext(funding_pct=0.08))[0]
    crowded_short_side = context_points(Side.SHORT, GlobalContext(), SymbolContext(funding_pct=0.08))[0]
    assert crowded_long == -4 and crowded_short_side == 3  # толпа в лонгах: минус лонгу, плюс шорту
    assert context_points(Side.LONG, GlobalContext(), SymbolContext(funding_pct=-0.03))[0] == 3


def test_context_points_btc_oi_fng_and_clamp():
    long_bull = context_points(Side.LONG, GlobalContext(btc_trend=1, btc_7d_pct=4, fear_greed=20), SymbolContext(oi_change_7d_pct=15))[0]
    assert long_bull == 3 + 3 + 2  # BTC по идее, OI растёт, страх у лонга
    short_vs_bull = context_points(Side.SHORT, GlobalContext(btc_trend=1, btc_7d_pct=4), SymbolContext())[0]
    assert short_vs_bull == -4
    worst = context_points(Side.LONG, GlobalContext(btc_trend=-1, btc_7d_pct=-9, fear_greed=90),
                           SymbolContext(funding_pct=0.2, oi_change_7d_pct=-30))[0]
    assert worst == -10  # зажато границей


def test_context_points_all_unknown_is_neutral():
    assert context_points(Side.LONG, GlobalContext(), SymbolContext()) == (0.0, [])


class FakeClient:
    has = {"fetchOpenInterestHistory": True}

    def fetch_funding_rate(self, symbol):
        return {"fundingRate": 0.0003}

    def fetch_open_interest_history(self, symbol, tf, limit):
        return [{"openInterestValue": 100.0}, {"openInterestValue": 112.0}]


class FakeExchanges:
    def client(self, name):
        return FakeClient()

    def ohlcv(self, exchange, symbol, tf, bars):
        close = [100 + i for i in range(120)]  # BTC растёт
        return pd.DataFrame({"close": close})


def test_market_data_collects_from_open_sources():
    md = MarketData(FakeExchanges(), http_get=lambda url: {"data": [{"value": "31"}]} if url == FNG_URL else {})
    g = md.global_context("okx")
    assert g.btc_trend == 1 and g.fear_greed == 31 and g.btc_7d_pct > 0
    s = md.symbol_context("okx", "X/USDT:USDT")
    assert s.funding_pct == 0.03 and s.oi_change_7d_pct == 12.0


def test_market_data_survives_source_failures():
    class Broken(FakeExchanges):
        def ohlcv(self, *a):
            raise RuntimeError("down")

        def client(self, name):
            raise RuntimeError("down")

    def boom(url):
        raise RuntimeError("no net")

    md = MarketData(Broken(), http_get=boom)
    g = md.global_context("okx")
    assert (g.btc_trend, g.fear_greed) == (None, None)
    try:
        md.symbol_context("okx", "X")
    except RuntimeError:
        pass  # клиент недоступен — сервис ловит это и берёт нейтральный контекст


def test_service_applies_context_and_preview_sends_without_db():
    cfg = Config(owner_id=1, dry_run=True)
    cfg.strategy.min_score = 0
    md = MarketData(FakeExchanges(), http_get=lambda url: {"data": [{"value": "50"}]})
    svc = Service(cfg, FakeEx(), Storage(), FakePub(), market=md)
    found = asyncio.run(svc._ranked(set(), keep=3))
    assert found and "context" in found[0][0].extra
    pub = FakePub()
    n = asyncio.run(svc.preview(pub, n=1))
    assert n == 1 and len(pub.posts) == 1 and svc.db.all() == []  # в базу превью не пишется
