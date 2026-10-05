"""Рендер графика-идеи: тёмный «звёздный» стиль с янтарными свечами, EMA, уровнями Entry/SL/TP."""
from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from matplotlib.ticker import FuncFormatter, LogLocator, MaxNLocator, NullLocator  # noqa: E402

from .levels import find_swings  # noqa: E402
from .models import Idea, Side  # noqa: E402

BG = "#050505"
UP = "#f1e6cc"        # растущие свечи: кремовые
DOWN = "#f5a524"      # падающие: янтарные
AMBER = "#f5a524"
ORANGE = "#e8803a"
SL_COLOR = "#ff6b3d"
ENTRY_COLOR = "#f4f4f4"
CREAM = "#eadfc8"
DIM = "#b8a98a"
FONT = "DejaVu Serif"

MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июль", "Авг", "Сен", "Окт", "Ноя", "Дек"]
EXCHANGE_NAMES = {"binance": "Binance", "bybit": "Bybit", "okx": "OKX"}
DEFAULT_MASCOT = Path(__file__).resolve().parents[1] / "assets" / "mascot.png"


@dataclass
class ChartStyle:
    width: int = 1280
    height: int = 700
    dpi: int = 100
    watermark_text: str = ""
    watermark_image: str | None = None   # маленький логотип слева внизу
    mascot_image: str | None = None      # талисман слева сверху (None = выкл.)
    structure_labels: bool = True        # HH / HL / LH / LL
    future_bars: int = 45                # место справа под стрелку и уровни
    candle_width: float = 0.62
    log_ratio: float = 3.5               # логарифмическая шкала, если max/min цены больше


def price_decimals(v: float) -> int:
    v = abs(v)
    return 0 if v >= 10000 else 1 if v >= 1000 else 2 if v >= 100 else 3 if v >= 10 else 4 if v >= 1 else 5 if v >= 0.1 else 6


def fmt_num(v: float, decimals: int | None = None) -> str:
    """20748.1 -> '20 748', 249.382 -> '249.38', 0.01234 -> '0.012340' (точка — десятичный разделитель)."""
    d = price_decimals(v) if decimals is None else decimals
    return f"{v:,.{d}f}".replace(",", " ")


def _title(idea: Idea, tf_label: str) -> str:
    ex = EXCHANGE_NAMES.get(idea.exchange, idea.exchange.title())
    return f"{idea.ticker} / TetherUS PERPETUAL CONTRACT · {tf_label} · {ex}"


def _scenario_path(idea: Idea, n: int, last_px: float, style: ChartStyle) -> tuple[list[float], list[float]]:
    """Стрелка: подход к зоне -> откат в неё (ретест) -> импульс к ближайшему пулу."""
    long = idea.side is Side.LONG
    z = idea.zone
    edge = z.high if long else z.low
    target = idea.pools[0].price if idea.pools else edge * (1.15 if long else 0.85)
    x0, fb = n - 1, style.future_bars
    xs = [x0, x0 + fb * 0.22, x0 + fb * 0.38, x0 + fb * 0.80]
    breakout = edge + (edge - z.mid) * 0.35 if long else edge - (z.mid - edge) * 0.35
    ys = [last_px, breakout, z.mid + (edge - z.mid) * 0.15, target]
    return xs, ys


def _structure_marks(df: pd.DataFrame, last: int = 7) -> list[tuple[int, float, str, bool]]:
    """Последние swing-точки с метками HH/LH (максимумы) и HL/LL (минимумы)."""
    sh, sl = find_swings(df)
    pts = sorted([(i, True) for i in sh] + [(i, False) for i in sl])
    prev_h = prev_l = None
    marks: list[tuple[int, float, str, bool]] = []
    for i, is_high in pts:
        if is_high:
            p = float(df["high"].iloc[i])
            if prev_h is not None:
                marks.append((i, p, "HH" if p > prev_h else "LH", True))
            prev_h = p
        else:
            p = float(df["low"].iloc[i])
            if prev_l is not None:
                marks.append((i, p, "HL" if p > prev_l else "LL", False))
            prev_l = p
    return marks[-last:]


def _draw_stars(bg, seed_key: str) -> None:
    seed = int(hashlib.sha256(seed_key.encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    n = 420
    x, y = rng.random(n), rng.random(n)
    size = rng.uniform(0.15, 2.2, n) ** 1.6
    alpha = rng.uniform(0.15, 0.85, n)
    warm = rng.random(n) < 0.22
    colors = np.where(warm, "#f5a524", "#f3ead8")
    rgba = np.array([matplotlib.colors.to_rgba(c, a) for c, a in zip(colors, alpha)])
    bg.scatter(x, y, s=size, c=rgba, linewidths=0)


def _fmt_axis(ax) -> None:
    ticks = [t for t in ax.get_yticks() if ax.get_ylim()[0] <= t <= ax.get_ylim()[1]]
    step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else 1.0
    dec = 0 if step >= 1 else int(np.ceil(-np.log10(step)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt_num(v, dec)))


def _place_mascot(fig, path: str, df: pd.DataFrame, ax, ylim: tuple[float, float], xmax: int, use_log: bool = False) -> None:
    img = plt.imread(path)
    ih, iw = img.shape[:2]
    fw, fh = fig.get_size_inches() * fig.dpi
    w = 0.27
    h = w * fw * (ih / iw) / fh
    top = 0.885
    # ужимаем талисман, если свечи слева поднимаются в его зону
    x_hi = -2 + (w - 0.012) / 0.90 * (xmax + 2)
    covered = df["high"].to_numpy()[: max(1, int(max(x_hi, 0)) + 1)]
    cmax = float(covered.max())
    frac = (np.log(cmax / ylim[0]) / np.log(ylim[1] / ylim[0])) if use_log else (cmax - ylim[0]) / (ylim[1] - ylim[0])
    yf = 0.075 + frac * 0.80
    free = top - yf - 0.01
    s = float(np.clip(free / h, 0.82, 1.0))
    w2, h2 = w * s, h * s
    m = fig.add_axes([0.0, top - h2, w2, h2], zorder=1)
    m.imshow(img, interpolation="lanczos")
    m.axis("off")


def render_idea_chart(df: pd.DataFrame, idea: Idea, style: ChartStyle | None = None, tf_label: str = "1Д") -> bytes:
    style = style or ChartStyle()
    df = df.reset_index(drop=True)
    n = len(df)
    long = idea.side is Side.LONG
    c = df["close"].to_numpy()
    last_px = float(c[-1])

    with plt.rc_context({"font.family": FONT}):
        fig = plt.figure(figsize=(style.width / style.dpi, style.height / style.dpi), dpi=style.dpi, facecolor=BG)

        bg = fig.add_axes([0, 0, 1, 1], zorder=0, facecolor=BG)
        bg.set_xlim(0, 1)
        bg.set_ylim(0, 1)
        bg.axis("off")
        _draw_stars(bg, f"{idea.symbol}|{idea.timeframe}|{n}")

        ax = fig.add_axes([0.012, 0.075, 0.90, 0.80], zorder=3, facecolor="none")
        xmax = n - 1 + style.future_bars
        ax.set_xlim(-2, xmax)

        # --- диапазон по Y
        ymin, ymax = float(df["low"].min()), float(df["high"].max())
        extra = [p.price for p in idea.pools] + [v for v in (idea.invalidation, idea.entry_conservative) if v]
        ymin, ymax = min([ymin, *extra]), max([ymax, *extra])
        use_log = bool(style.log_ratio) and ymin > 0 and ymax / ymin > style.log_ratio
        if use_log:
            span = float(np.log(ymax / ymin))
            ylim = (ymin * float(np.exp(-0.05 * span)), ymax * float(np.exp(0.15 * span)))  # запас сверху под талисман
            ax.set_yscale("log")
        else:
            span = ymax - ymin
            ylim = (max(ymin - span * 0.05, 0.0), ymax + span * 0.15)
        ax.set_ylim(*ylim)

        def shift(price: float, frac: float) -> float:
            """Сдвиг цены на долю высоты графика (в лог-шкале — мультипликативно)."""
            return price * float(np.exp(frac * span)) if use_log else price + frac * span

        # --- зона пробоя
        z = idea.zone
        ax.add_patch(Rectangle((-2, z.low), xmax + 2, z.high - z.low, facecolor=AMBER, alpha=0.10, edgecolor="none", zorder=1))
        for edge in (z.low, z.high):
            ax.plot([-2, xmax], [edge, edge], color=AMBER, alpha=0.35, linewidth=0.8, linestyle=(0, (4, 4)), zorder=1)

        # --- EMA: сплошная / точками / пунктиром
        close = df["close"]
        for ema_span, color, ls, lw, skip in (
            (21, "#f0b454", "-", 0.9, 5),
            (50, "#e8903a", (0, (1, 2)), 1.3, 10),
            (200, "#d9731f", (0, (5, 3)), 1.2, 60),
        ):
            ema = close.ewm(span=ema_span, adjust=False).mean().to_numpy().copy()
            ema[:skip] = np.nan
            ax.plot(np.arange(n), ema, color=color, linestyle=ls, linewidth=lw, alpha=0.9, zorder=2)

        # --- свечи
        o, h, l = df["open"].to_numpy(), df["high"].to_numpy(), df["low"].to_numpy()
        up = c >= o
        xs = np.arange(n)
        colors = np.where(up, UP, DOWN)
        ax.vlines(xs, l, h, colors=colors, linewidth=1.0, zorder=4)
        body_lo, body_hi = np.minimum(o, c), np.maximum(o, c)
        min_body = body_lo * 0.002 if use_log else (ylim[1] - ylim[0]) * 0.0007
        ax.bar(xs, np.maximum(body_hi - body_lo, min_body), bottom=body_lo, width=style.candle_width, color=colors, zorder=5, linewidth=0)

        # --- структура рынка: HH / HL / LH / LL
        if style.structure_labels:
            span_x = max(8, n // 28)
            for i, p, label, is_high in _structure_marks(df):
                ax.plot([i - span_x / 2, i + span_x / 2], [p, p], color=AMBER, alpha=0.75, linewidth=1.0, linestyle=(0, (4, 3)), zorder=3)
                ax.text(i, shift(p, 0.012 if is_high else -0.012), label, color=AMBER, fontsize=10, ha="center",
                        va="bottom" if is_high else "top", alpha=0.95, zorder=6)

        # --- уровни Entry / SL / TP (подписи справа над линией)
        fut0 = n + 2

        def pos(price: float) -> float:
            """Положение цены на графике, 0..1 (для разведения подписей)."""
            return float(np.log(price / ylim[0]) / np.log(ylim[1] / ylim[0])) if use_log else (price - ylim[0]) / (ylim[1] - ylim[0])

        levels: list[tuple[float, str, str, int | None]] = [(p.price, f"TP{k}", AMBER, p.idx) for k, p in enumerate(idea.pools, 1)]
        if idea.entry_conservative:
            levels.append((idea.entry_conservative, "Entry", ENTRY_COLOR, None))
        if idea.invalidation:
            levels.append((idea.invalidation, "SL", SL_COLOR, None))
        last_pos = None
        for price, label, color, from_idx in sorted(levels, key=lambda t: -t[0]):
            if from_idx is not None:  # пул ликвидности: бледная линия от swing вправо
                ax.plot([from_idx, fut0], [price, price], color=color, alpha=0.30, linewidth=0.9, linestyle=(0, (1, 3)), zorder=2)
            ax.plot([fut0, xmax], [price, price], color=color, linewidth=1.3, linestyle=(0, (4, 3)), zorder=3)
            # подпись над линией; если выше уже стоит близкая — уводим под линию
            below = last_pos is not None and last_pos - pos(price) < 0.045
            ax.text(xmax - 0.6, shift(price, -0.006 if below else 0.006), f"{label} {fmt_num(price)}", color=color, fontsize=11,
                    ha="right", va="top" if below else "bottom", zorder=7)
            last_pos = pos(price) - (0.03 if below else 0.0)

        # --- стрелка-сценарий
        ax_x, ax_y = _scenario_path(idea, n, last_px, style)
        ax.plot(ax_x[:-1], ax_y[:-1], color=CREAM, linewidth=1.6, zorder=8, solid_joinstyle="miter")
        ax.annotate("", xy=(ax_x[-1], ax_y[-1]), xytext=(ax_x[-2], ax_y[-2]),
                    arrowprops=dict(arrowstyle="-|>", color=CREAM, linewidth=1.6, mutation_scale=16, shrinkA=0, shrinkB=0), zorder=8)

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
        ax.tick_params(axis="x", colors=DIM, labelsize=10, length=0, pad=8)

        # --- ось Y справа: янтарная линия и цифры
        ax.yaxis.tick_right()
        if use_log:
            ax.yaxis.set_major_locator(LogLocator(base=10, subs=(1, 2, 3, 5, 7), numticks=14))
            ax.yaxis.set_minor_locator(NullLocator())
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _p: fmt_num(v)))
        else:
            ax.yaxis.set_major_locator(MaxNLocator(nbins=9, steps=[1, 2, 4, 5, 10]))
        ax.set_ylim(*ylim)
        if not use_log:
            _fmt_axis(ax)
        ax.tick_params(axis="y", colors=AMBER, labelsize=10, length=4, width=0.8, pad=8)
        for name, s in ax.spines.items():
            s.set_visible(name == "right")
        ax.spines["right"].set_color(AMBER)
        ax.spines["right"].set_linewidth(0.9)
        ax.spines["right"].set_alpha(0.8)

        # --- плашка текущей цены
        ax.axhline(last_px, color=AMBER, linewidth=0.7, linestyle=(0, (1, 2)), alpha=0.8, zorder=2)
        ax.annotate(fmt_num(last_px), xy=(1.0, last_px), xycoords=("axes fraction", "data"), xytext=(6, 0),
                    textcoords="offset points", color="#111", fontsize=10, fontweight="bold", va="center", ha="left",
                    bbox=dict(boxstyle="square,pad=0.3", fc=AMBER, ec="none"), zorder=10, annotation_clip=False)

        # --- заголовок и цена
        fig.text(0.014, 0.945, _title(idea, tf_label), color=CREAM, fontsize=13, fontweight="bold", va="center", zorder=10)
        sign = "+" if idea.change_abs >= 0 else "−"
        chg_color = UP if idea.change_abs >= 0 else DOWN
        price_txt = fmt_num(last_px)
        fig.text(0.014, 0.905, price_txt, color=chg_color, fontsize=12, fontweight="bold", va="center", zorder=10)
        fig.text(0.014 + 0.012 + 0.0085 * len(price_txt), 0.905,
                 f"{sign}{fmt_num(abs(idea.change_abs))} ({sign}{abs(idea.change_pct):.2f}%)",
                 color=chg_color, fontsize=11, va="center", zorder=10)

        # --- талисман и водяной знак
        mascot = style.mascot_image
        if mascot and Path(mascot).exists():
            _place_mascot(fig, mascot, df, ax, ylim, xmax, use_log)
        if style.watermark_image and Path(style.watermark_image).exists():
            wm = fig.add_axes([0.012, 0.085, 0.05, 0.08], zorder=20)
            wm.imshow(plt.imread(style.watermark_image))
            wm.axis("off")
        elif style.watermark_text:
            ax.text(0.012, 0.045, style.watermark_text, transform=ax.transAxes, color=CREAM, alpha=0.7,
                    fontsize=16, fontweight="bold", zorder=20)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", facecolor=BG, dpi=style.dpi)
        plt.close(fig)
    return buf.getvalue()
