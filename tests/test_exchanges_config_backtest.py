import pytest

from signalbot.backtest import backtest_symbol, run_backtest
from signalbot.config import Config, StrategyParams, load_config
from signalbot.exchanges import Exchanges
from signalbot.synthetic import make_random_walk


class FakeEx:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def load_markets(self):
        return {
            "AAA/USDT:USDT": dict(swap=True, linear=True, quote="USDT", base="AAA", active=True, expiry=None),
            "BBB/USDT:USDT": dict(swap=True, linear=True, quote="USDT", base="BBB", active=True, expiry=None),
            "USDC/USDT:USDT": dict(swap=True, linear=True, quote="USDT", base="USDC", active=True, expiry=None),
            "AAA/USDT": dict(swap=False, linear=None, quote="USDT", base="AAA", active=True, expiry=None),
        }

    def fetch_tickers(self, symbols=None):
        vol = {"binance": {"AAA/USDT:USDT": 50e6, "BBB/USDT:USDT": 5e6, "USDC/USDT:USDT": 900e6},
               "bybit": {"AAA/USDT:USDT": 80e6, "BBB/USDT:USDT": 30e6}}[self.name]
        return {s: {"quoteVolume": v} for s, v in vol.items() if not symbols or s in symbols}

    def fetch_ohlcv(self, symbol, tf, since=None, limit=None):
        self.calls.append((since, limit))
        step = 86_400_000
        n = 250  # биржа отдаёт максимум 100 за запрос
        all_rows = [[i * step, 1, 2, 0.5, 1.5, 10] for i in range(n)]
        rows = [r for r in all_rows if r[0] >= (since or 0)][:100]
        return rows


def test_universe_dedup_filters_and_priority():
    ex = Exchanges(["binance", "bybit"], factory=FakeEx)
    u = ex.universe(20e6, 10, ["USDC"])
    assert [(m.base, m.exchange) for m in u] == [("AAA", "binance"), ("BBB", "bybit")]  # AAA с приоритетной биржи


def test_ohlcv_paginates_over_exchange_limit():
    ex = Exchanges(["binance"], factory=FakeEx)
    df = ex.ohlcv("binance", "AAA/USDT:USDT", "1d", bars=1, since_ms=0)
    assert len(df) == 250 and df["ts"].is_monotonic_increasing and df["ts"].is_unique


def test_config_defaults_and_dry_run(tmp_path):
    y = tmp_path / "c.yaml"
    y.write_text("timeframe: 4h\nstrategy:\n  min_rr: 3\n  bogus: 1\n")
    cfg = load_config(y, env_file=None)
    assert cfg.timeframe == "4h" and cfg.strategy.min_rr == 3 and cfg.dry_run is True
    with pytest.raises(RuntimeError):
        _ = cfg.publish_target
    cfg.owner_id = 42
    assert cfg.publish_target == 42
    cfg.dry_run, cfg.channel_id = False, "@chan"
    assert cfg.publish_target == "@chan"
    cfg.channel_id = "-1001234"
    assert cfg.publish_target == -1001234


def test_backtest_no_lookahead_and_consistent_stats():
    df = make_random_walk(seed=3, bars=600)
    trades = backtest_symbol(df, "S", StrategyParams(min_score=40), max_wait_days=10)
    last_ts = int(df["ts"].iloc[-1]) + 86_400_000
    for t in trades:
        assert t.created_ts <= last_ts
        assert t.closed_ts is None or t.closed_ts >= t.created_ts
        if t.status == "STOPPED":
            assert t.entry_price is not None
    rep, _ = run_backtest({"S": df}, StrategyParams(min_score=40), months=8)
    assert rep.stats.total + rep.stats.cancelled + rep.stats.active == rep.n_ideas
