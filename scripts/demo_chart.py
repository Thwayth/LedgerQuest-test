"""Рисует пример идеи на синтетических данных: python scripts/demo_chart.py [short]"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalbot.chart import DEFAULT_MASCOT, ChartStyle, render_idea_chart
from signalbot.config import StrategyParams
from signalbot.models import Side
from signalbot.scanner import analyze
from signalbot.synthetic import make_trb_like


def main() -> None:
    df = make_trb_like()
    if "short" in sys.argv:
        df = df.assign(**{k: df[k].max() * 1.05 - df[k] for k in ("open", "close")}).assign(
            high=lambda d: d[["open", "close"]].max(axis=1) * 1.01, low=lambda d: d[["open", "close"]].min(axis=1) * 0.99
        )
    idea = analyze(df, "TRB/USDT:USDT", "okx", "1d", StrategyParams(min_score=0))
    assert idea, "идея не найдена"
    print(idea.side.value, idea.zone, idea.pools, idea.entry_conservative, idea.invalidation)
    png = render_idea_chart(df, idea, ChartStyle(mascot_image=str(DEFAULT_MASCOT)))
    out = Path("out/demo_trb.png")
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(png)
    print("saved", out, len(png) // 1024, "KB")


if __name__ == "__main__":
    main()
