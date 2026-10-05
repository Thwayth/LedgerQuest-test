from signalbot.chart import DEFAULT_MASCOT, ChartStyle, fmt_num, render_idea_chart
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
    assert fmt_num(249.382) == "249.38"
    assert fmt_num(1.5) == "1.5000"
    assert fmt_num(0.01234) == "0.012340"


def test_render_with_mascot_levels_and_structure_labels():
    from signalbot.config import StrategyParams
    from signalbot.scanner import analyze

    df = make_trb_like()
    idea = analyze(df, "TRB/USDT:USDT", "okx", "1d", StrategyParams(min_score=0))
    assert idea and idea.entry_conservative and idea.invalidation
    png = render_idea_chart(df, idea, ChartStyle(mascot_image=str(DEFAULT_MASCOT), structure_labels=True))
    assert png.startswith(PNG_MAGIC) and len(png) > 50_000  # звёзды + талисман делают картинку тяжелее


def test_missing_mascot_file_is_ignored():
    df = make_trb_like()
    assert render_idea_chart(df, _idea(df), ChartStyle(mascot_image="/no/such.png")).startswith(PNG_MAGIC)


def test_structure_marks_labels():
    from signalbot.chart import _structure_marks

    marks = _structure_marks(make_trb_like())
    assert marks and {m[2] for m in marks} <= {"HH", "LH", "HL", "LL"}


def test_log_scale_for_wide_price_range_and_no_label_crash():
    from signalbot.config import StrategyParams
    from signalbot.scanner import analyze

    df = make_trb_like()
    for col in ("open", "high", "low", "close"):
        df.loc[df.index < 60, col] = df.loc[df.index < 60, col] * 2.5  # ранний пик в 4+ раза выше
    idea = analyze(df, "AR/USDT:USDT", "okx", "1d", StrategyParams(min_score=0, max_distance_pct=14))
    assert idea is not None
    png = render_idea_chart(df, idea, ChartStyle(mascot_image=str(DEFAULT_MASCOT)))
    assert png.startswith(PNG_MAGIC) and len(png) > 50_000
    png_lin = render_idea_chart(df, idea, ChartStyle(log_ratio=0))  # выключение лог-шкалы тоже работает
    assert png_lin.startswith(PNG_MAGIC)
