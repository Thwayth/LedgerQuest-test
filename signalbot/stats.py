"""Статистика по завершённым идеям (общая для бота и бэктеста)."""
from __future__ import annotations

from dataclasses import dataclass

from .lifecycle import FINISHED, Tracked


@dataclass
class Stats:
    total: int
    wins: int
    losses: int
    winrate: float  # %
    avg_rr: float  # средний R на сделку
    sum_pct: float  # суммарный результат, %
    max_dd_r: float  # максимальная просадка по кумулятивному R
    cancelled: int = 0
    active: int = 0


def compute_stats(items: list[Tracked]) -> Stats:
    done = sorted((t for t in items if t.status in FINISHED and t.result_pct is not None), key=lambda t: t.closed_ts or 0)
    wins = sum(1 for t in done if t.result_pct > 0)
    cum = peak = dd = 0.0
    for t in done:
        cum += t.r_multiple or 0.0
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    n = len(done)
    return Stats(
        total=n,
        wins=wins,
        losses=n - wins,
        winrate=round(wins / n * 100, 1) if n else 0.0,
        avg_rr=round(sum(t.r_multiple or 0 for t in done) / n, 2) if n else 0.0,
        sum_pct=round(sum(t.result_pct for t in done), 2),
        max_dd_r=round(dd, 2),
        cancelled=sum(1 for t in items if t.status in ("CANCELLED", "EXPIRED")),
        active=sum(1 for t in items if t.status in ("WATCH", "BROKEN")),
    )


def format_stats(s: Stats, title: str = "📊 Статистика идей") -> str:
    if s.total == 0:
        body = "Закрытых сделок пока нет."
    else:
        body = (
            f"Закрыто: {s.total} (✅ {s.wins} / ❌ {s.losses})\n"
            f"Винрейт: {s.winrate}%\n"
            f"Средний результат: {s.avg_rr:+.2f}R\n"
            f"Итого: {s.sum_pct:+.2f}% (по цене, без плеча)\n"
            f"Макс. просадка: {s.max_dd_r:.2f}R"
        )
    return f"{title}\n\n{body}\n\nОтменено/не реализовано: {s.cancelled} · В работе: {s.active}"
