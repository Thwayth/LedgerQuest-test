"""Бэктест правил: поиск зон -> пробой -> цели/стоп на исторических дневных свечах.

Запуск:
    python -m signalbot.backtest --exchange binance --months 12 --top 30
    python -m signalbot.backtest --synthetic            # без сети, на синтетике

Walk-forward: на каждом баре t видны только свечи [0..t]; сигнал генерируется по ним, затем идея
«проживается» на будущих свечах той же логикой, что и в боте (lifecycle.evaluate).
Допущения: дневные свечи, стоп приоритетнее цели внутри одного бара, комиссий и проскальзывания нет.
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass

import pandas as pd

from .config import StrategyParams, load_config
from .exchanges import TF_MS, Exchanges
from .lifecycle import ACTIVE, Tracked, evaluate
from .scanner import MIN_BARS, analyze
from .stats import Stats, compute_stats

log = logging.getLogger(__name__)
DAY = TF_MS["1d"]


def backtest_symbol(
    df: pd.DataFrame,
    symbol: str,
    p: StrategyParams,
    *,
    start_idx: int | None = None,
    max_wait_days: int = 10,
    exchange: str = "bt",
    window: int = 365,
) -> list[Tracked]:
    """Прогон одной монеты. Новые идеи по монете не открываются, пока предыдущая активна."""
    df = df.reset_index(drop=True)
    start = max(MIN_BARS, start_idx if start_idx is not None else MIN_BARS)
    out: list[Tracked] = []
    t_idx = start
    while t_idx < len(df) - 1:
        view = df.iloc[max(0, t_idx - window + 1) : t_idx + 1].reset_index(drop=True)
        idea = analyze(view, symbol, exchange, "1d", p)
        if idea is None:
            t_idx += 1
            continue
        created = int(df["ts"].iloc[t_idx]) + DAY  # сигнал по закрытой свече t
        tr = Tracked(
            symbol=symbol, exchange=exchange, timeframe="1d", side=idea.side, zone_low=idea.zone.low,
            zone_high=idea.zone.high, pools=[x.price for x in idea.pools], entry_cons=idea.entry_conservative,
            entry_aggr=idea.entry_aggressive, invalidation=idea.invalidation, created_ts=created, score=idea.score,
        )
        future = df.iloc[t_idx + 1 :]
        now = int(df["ts"].iloc[-1]) + DAY  # «сейчас» = конец истории
        evaluate(tr, future, future, now, DAY, DAY, max_wait_days * DAY)
        out.append(tr)
        if tr.status in ACTIVE:  # история закончилась, идея не завершена
            break
        # следующий сигнал по монете — только после закрытия этой идеи
        closed = tr.closed_ts or created
        nxt = int((closed - int(df["ts"].iloc[0])) // DAY) + 1
        t_idx = max(t_idx + 1, nxt)
    return out


@dataclass
class Report:
    stats: Stats
    n_symbols: int
    n_ideas: int

    def text(self) -> str:
        s = self.stats
        return (
            f"Монет: {self.n_symbols} · Идей: {self.n_ideas} (закрыто {s.total}, отменено {s.cancelled}, в работе {s.active})\n"
            f"Винрейт: {s.winrate}% (✓{s.wins}/✗{s.losses})\n"
            f"Средний результат: {s.avg_rr:+.2f}R · Итого: {s.sum_pct:+.1f}% по цене\n"
            f"Макс. просадка: {s.max_dd_r:.2f}R"
        )


def run_backtest(frames: dict[str, pd.DataFrame], p: StrategyParams, months: int, max_wait_days: int = 10) -> tuple[Report, list[Tracked]]:
    all_trades: list[Tracked] = []
    for sym, df in frames.items():
        start = max(MIN_BARS, len(df) - months * 30)
        all_trades += backtest_symbol(df, sym, p, start_idx=start, max_wait_days=max_wait_days)
    return Report(compute_stats(all_trades), len(frames), len(all_trades)), all_trades


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--months", type=int, default=12, help="период теста (6–12)")
    ap.add_argument("--top", type=int, default=30, help="сколько самых ликвидных монет брать")
    ap.add_argument("--symbols", nargs="*", help="явный список, напр. BTC/USDT:USDT")
    ap.add_argument("--synthetic", action="store_true", help="синтетические данные (без сети)")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = load_config(a.config, env_file=None)

    frames: dict[str, pd.DataFrame] = {}
    if a.synthetic:
        from .synthetic import make_random_walk

        for i in range(a.top):
            frames[f"SYN{i}/USDT:USDT"] = make_random_walk(seed=i, bars=a.months * 30 + 400)
    else:
        ex = Exchanges([a.exchange])
        syms = a.symbols or [m.symbol for m in ex.universe(cfg.min_quote_volume_usd, a.top, cfg.exclude_bases)]
        bars = a.months * 30 + 400
        for s in syms:
            try:
                frames[s] = ex.ohlcv(a.exchange, s, "1d", bars)
            except Exception as e:
                log.warning("пропуск %s: %s", s, e)
    if not frames:
        log.error("нет данных для бэктеста (проверьте доступ к бирже)")
        return 1
    rep, _ = run_backtest(frames, cfg.strategy, a.months, cfg.max_wait_days)
    print(f"=== Бэктест {a.months} мес. ===")
    print(rep.text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
