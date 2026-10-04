from signalbot.chart import ChartStyle, fmt_num, render_idea_chart
from signalbot.levels import find_pools, find_zone
from signalbot.models import Idea, Side
from signalbot.synthetic import make_trb_like

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _idea(df, side=Side.LONG):
    zone = find_zone(df, side)
    pools = find_pools(df, zone, side)
    last, prev = float(df["close"].iloc[-1]), float(df["close"].iloc[-2])
    return Idea("TRB/USDT:USDT", "binance", "1d", side, zone, pools, last, last - prev, (last / prev - 1) * 100)


def test_render_creates_png():
    df = make_trb_like()
    png = render_idea_chart(df, _idea(df), ChartStyle(watermark_text="X"))
    assert png.startswith(PNG_MAGIC) and len(png) > 10_000


def test_render_short_without_pools_does_not_crash():
    df = make_trb_like()
    idea = _idea(df)
    idea.pools = []
    assert render_idea_chart(df, idea).startswith(PNG_MAGIC)


def test_fmt_num_russian_style():
    assert fmt_num(20748.0) == "20 748"
    assert fmt_num(0.01234) == "0,0123"
    assert fmt_num(1.5) == "1,50"
