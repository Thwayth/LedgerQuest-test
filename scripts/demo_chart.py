"""Рисует пример идеи на синтетических данных: python scripts/demo_chart.py"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signalbot.chart import ChartStyle, render_idea_chart
from signalbot.levels import find_pools, find_zone
from signalbot.models import Idea, Side
from signalbot.synthetic import make_trb_like


def main() -> None:
    df = make_trb_like()
    side = Side.LONG
    zone = find_zone(df, side)
    assert zone, "зона не найдена"
    pools = find_pools(df, zone, side)
    prev, last = df["close"].iloc[-2], df["close"].iloc[-1]
    idea = Idea(
        symbol="TRB/USDT:USDT", exchange="binance", timeframe="1d", side=side, zone=zone, pools=pools,
        price=float(last), change_abs=float(last - prev), change_pct=float((last / prev - 1) * 100),
    )
    print("zone:", zone)
    print("pools:", pools)
    png = render_idea_chart(df, idea, ChartStyle(watermark_text="LedgerQuest"))
    out = Path("out/demo_trb.png")
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(png)
    print("saved", out, len(png) // 1024, "KB")


if __name__ == "__main__":
    main()
