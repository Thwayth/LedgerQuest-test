"""Рендер графика-идеи в тёмной теме в стиле TradingView."""
from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter, MaxNLocator  # noqa: E402

from .models import Idea, Side  # noqa: E402

BG = "#131722"
GRID = "#1e222d"
TEXT = "#b2b5be"
UP = "#26a69a"
DOWN = "#ef5350"
ZONE = "#9598a1"
LINE = "#e0e3eb"
ARROW = "#f0f3fa"
UP_PCT = "#26a69a"

MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июль", "Авг", "Сен", "Окт", "Ноя", "Дек"]
EXCHANGE_NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX"}


@dataclass
class ChartStyle:
    width: int = 1280
    height: int = 700
    dpi: int = 100
    watermark_text: str = ""
    watermark_image: str | None = None  # путь к PNG-логотипу
    future_bars: int = 45  # пустое место справа под стрелку
    candle_width: float = 0.62


def fmt_num(v: float, decimals: int | None = None) -> str:
    """20748.1 -> '20 748', 0.01234 -> '0,01234' (русский формат)."""
    if decimals is None:
        decimals = 0 if abs(v) >= 100 else 2 if abs(v) >= 1 else 4 if abs(v) >= 0.01 else 6
    s = f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s


def _fmt_price_tick(v: float, _pos=None) -> str:
    return fmt_num(v, 0 if abs(v) >= 100 else None)


def _title(idea: Idea, tf_label: str) -> str:
    ex = EXCHANGE_NAMES.get(idea.exchange, idea.exchange.title())
    return f"{idea.ticker} / TetherUS PERPETUAL CONTRACT · {tf_label} · {ex}"


def _scenario_path(idea: Idea, n: int, last_px: float, style: ChartStyle) -> tuple[list[float], list[float]]:
    """Стрелка: подход к зоне -> откат в неё (ретест) -> импульс к ближайшему пулу."""
    long = idea.side is Side.LONG
    z = idea.zone
    edge = z.high if long else z.low
    target = idea.pools[0].price if idea.pools else edge * (1.15 if long else 0.85)
    x0 = n - 1
    fb = style.future_bars
    xs = [x0, x0 + fb * 0.22, x0 + fb * 0.38, x0 + fb * 0.80]
    breakout = edge + (edge - z.mid) * 0.35 if long else edge - (z.mid - edge) * 0.35
    ys = [last_px, breakout, z.mid + (edge - z.mid) * 0.15, target]
    return xs, ys


def render_idea_chart(df: pd.DataFrame, idea: Idea, style: ChartStyle | None = None, tf_label: str = "1Д") -> bytes:
    style = style or ChartStyle()
    df = df.reset_index(drop=True)
    n = len(df)
    long = idea.side is Side.LONG

    fig = plt.figure(figsize=(style.width / style.dpi, style.height / style.dpi), dpi=style.dpi, facecolor=BG)
    ax = fig.add_axes([0.012, 0.075, 0.90, 0.80], facecolor=BG)

    xmax = n - 1 + style.future_bars
    ax.set_xlim(-2, xmax)

    # --- диапазон по Y: вся история + пулы + цель стрелки
    lows, highs = df["low"].to_numpy(), df["high"].to_numpy()
    ymin, ymax = float(lows.min()), float(highs.max())
    for p in idea.pools:
        ymax, ymin = max(ymax, p.price), min(ymin, p.price)
    pad = (ymax - ymin) * 0.06
    ax.set_ylim(max(ymin - pad, 0), ymax + pad)

    # --- сетка
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)

    # --- зона (серая полоса на всю ширину от первого касания)
    z = idea.zone
    ax.add_patch(
        Rectangle((-2, z.low), xmax + 2, z.high - z.low, facecolor=ZONE, alpha=0.28, edgecolor="none", zorder=1)
    )

    # --- пулы ликвидности: тонкие линии от swing вправо до края
    for p in idea.pools:
        ax.plot([p.idx, xmax], [p.price, p.price], color=LINE, linewidth=1.1, zorder=2, solid_capstyle="butt")
        ax.plot([p.idx, p.idx], [p.price - pad * 0.0, p.price], color=LINE, linewidth=1.1, zorder=2)

    # --- свечи
    o, h, l, c = (df[k].to_numpy() for k in ("open", "high", "low", "close"))
    up = c >= o
    xs = np.arange(n)
    colors = np.where(up, UP, DOWN)
    ax.vlines(xs, l, h, colors=colors, linewidth=1.0, zorder=3)
    body_lo, body_hi = np.minimum(o, c), np.maximum(o, c)
    min_body = (ax.get_ylim()[1] - ax.get_ylim()[0]) * 0.0007
    body_h = np.maximum(body_hi - body_lo, min_body)
    ax.bar(xs, body_h, bottom=body_lo, width=style.candle_width, color=colors, zorder=4, linewidth=0)

    # --- стрелка-сценарий
    last_px = float(c[-1])
    ax_x, ax_y = _scenario_path(idea, n, last_px, style)
    ax.plot(ax_x[:-1], ax_y[:-1], color=ARROW, linewidth=1.5, zorder=6, solid_joinstyle="miter")
    ax.annotate(
        "",
        xy=(ax_x[-1], ax_y[-1]),
        xytext=(ax_x[-2], ax_y[-2]),
        arrowprops=dict(arrowstyle="-|>", color=ARROW, linewidth=1.5, mutation_scale=16, shrinkA=0, shrinkB=0),
        zorder=6,
    )

    # --- ось X: русские месяцы, январь -> год
    ts = pd.to_datetime(df["ts"], unit="ms") if np.issubdtype(df["ts"].dtype, np.integer) else pd.to_datetime(df["ts"])
    tick_x, tick_l = [], []
    prev_m = None
    for i, t in enumerate(ts):
        key = (t.year, t.month)
        if key != prev_m and t.day <= 28:
            if prev_m is not None or t.day <= 3:
                tick_x.append(i)
                tick_l.append(str(t.year) if t.month == 1 else MONTHS_RU[t.month - 1])
            prev_m = key
    ax.set_xticks(tick_x)
    ax.set_xticklabels(tick_l)
    ax.tick_params(axis="x", colors=TEXT, labelsize=10, length=0, pad=8)

    # --- ось Y справа
    ax.yaxis.tick_right()
    ax.yaxis.set_major_locator(MaxNLocator(nbins=9, steps=[1, 2, 4, 5, 10]))
    ax.yaxis.set_major_formatter(FuncFormatter(_fmt_price_tick))
    ax.tick_params(axis="y", colors=TEXT, labelsize=10, length=0, pad=10)
    for s in ax.spines.values():
        s.set_visible(False)

    # --- плашка текущей цены
    badge_color = UP if up[-1] else DOWN
    ax.axhline(last_px, color=badge_color, linewidth=0.8, linestyle=(0, (1, 2)), zorder=2)
    ax.annotate(
        fmt_num(last_px),
        xy=(1.0, last_px),
        xycoords=("axes fraction", "data"),
        xytext=(4, 0),
        textcoords="offset points",
        color="white",
        fontsize=10,
        fontweight="bold",
        va="center",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.25", fc=badge_color, ec="none"),
        zorder=10,
        annotation_clip=False,
    )

    # --- заголовок и цена
    fig.text(0.014, 0.945, _title(idea, tf_label), color="#d1d4dc", fontsize=13, fontweight="bold", va="center")
    sign = "+" if idea.change_abs >= 0 else "−"
    chg_color = UP_PCT if idea.change_abs >= 0 else DOWN
    fig.text(0.014, 0.905, fmt_num(last_px), color=chg_color, fontsize=12, fontweight="bold", va="center")
    fig.text(
        0.014 + 0.012 + 0.0075 * len(fmt_num(last_px)),
        0.905,
        f"{sign}{fmt_num(abs(idea.change_abs))} ({sign}{fmt_num(abs(idea.change_pct), 2)}%)",
        color=chg_color,
        fontsize=11,
        va="center",
    )

    # --- водяной знак (слева внизу)
    if style.watermark_image and Path(style.watermark_image).exists():
        img = plt.imread(style.watermark_image)
        wm = fig.add_axes([0.012, 0.085, 0.05, 0.08], zorder=20)
        wm.imshow(img)
        wm.axis("off")
    elif style.watermark_text:
        ax.text(
            0.012, 0.045, style.watermark_text, transform=ax.transAxes, color="#d1d4dc", alpha=0.75,
            fontsize=16, fontweight="bold", zorder=20,
        )

    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=BG, dpi=style.dpi)
    plt.close(fig)
    return buf.getvalue()
