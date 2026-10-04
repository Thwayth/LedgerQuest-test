"""Подписи к постам: основной шаблон (LONG/SHORT) и апдейты. Telegram HTML."""
from __future__ import annotations

import hashlib
import random
from html import escape

from .chart import fmt_num
from .lifecycle import Event, Tracked
from .models import Idea, Side

DISCLAIMER = "⚠️ Не финансовый совет"

_INTRO = {
    Side.LONG: [
        "На дневном таймфрейме монета выглядит очень интересной для пробоя отмеченного уровня.",
        "На дневном графике монета подошла к ключевому уровню — выглядит интересно для пробоя.",
        "Монета на дневке аккуратно поджимается к отмеченному уровню. Интересный сетап на пробой.",
    ],
    Side.SHORT: [
        "На дневном таймфрейме монета выглядит очень интересной для пробоя отмеченного уровня вниз.",
        "На дневном графике цена подошла к ключевой поддержке — интересный сетап на пробой вниз.",
        "Монета на дневке давит на отмеченный уровень поддержки. Выглядит интересно для шорта на пробой.",
    ],
}
_CONFIRM = [
    "Как только произойдёт пробой, можете открыть сделку с подтверждением.",
    "После пробоя и закрепления за уровнем можно открывать сделку с подтверждением.",
    "Дождитесь пробоя и закрепления — тогда можно входить с подтверждением.",
]
_AGGR = [
    "А если хотите, как я, рисковать — можете открыть по рынку и сеткой набрать сделку.",
    "Если готовы рискнуть, как я, — можно открыть по рынку и набрать позицию сеткой.",
    "А кто любит рискнуть, как я, может открыть по рынку и набирать сделку сеткой.",
]
_TARGETS = {
    Side.LONG: ["Цели — выше, отмеченные пулы ликвидности. 🤝", "Цели — отмеченные выше пулы ликвидности. 🤝"],
    Side.SHORT: ["Цели — ниже, отмеченные пулы ликвидности. 🤝", "Цели — отмеченные ниже пулы ликвидности. 🤝"],
}


def _rng(*parts: object) -> random.Random:
    h = hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()
    return random.Random(int(h[:12], 16))


def build_caption(idea: Idea, day: str, disclaimer: bool = True) -> str:
    """Шаблон поста; варианты фраз детерминированы по (тикер, день)."""
    r = _rng(idea.ticker, day, idea.side.value)
    head = f"<b>{escape(idea.ticker)}</b> {'🔥' if idea.side is Side.LONG else '📉'}"
    parts = [
        head,
        r.choice(_INTRO[idea.side]),
        r.choice(_CONFIRM),
        f"<blockquote>{r.choice(_AGGR)}</blockquote>",
        r.choice(_TARGETS[idea.side]),
    ]
    if disclaimer:
        parts.append(f"<i>{DISCLAIMER}</i>")
    return "\n\n".join(parts)


# --- апдейты (ответом на исходный пост) -------------------------------------
def _pct(t: Tracked, price: float) -> float:
    d = 1 if t.side is Side.LONG else -1
    return (price - t.entry_price) / t.entry_price * 100 * d


def update_text(t: Tracked, ev: Event) -> str:
    tk = escape(t.symbol.split("/")[0])
    if ev.kind == "BREAKOUT":
        way = "выше" if t.side is Side.LONG else "ниже"
        return f"<b>{tk}</b>: пробой произошёл ✅\nДневная свеча закрылась {way} уровня ({fmt_num(ev.price)}). Сделка с подтверждением активна."
    if ev.kind == "TP":
        return f"<b>{tk}</b>: цель {ev.n} из {len(t.pools)} взята 🎯\nУровень {fmt_num(ev.price)} · {_pct(t, ev.price):+.1f}% от входа."
    if ev.kind == "DONE":
        return f"<b>{tk}</b>: все цели отработаны 🏆\nРезультат по идее: {t.result_pct:+.1f}% ({t.r_multiple:+.2f}R)."
    if ev.kind == "STOP":
        return f"<b>{tk}</b>: стоп ❌\nИдея не сработала, закрыта по {fmt_num(ev.price)}. Результат: {t.result_pct:+.1f}% ({t.r_multiple:+.2f}R)."
    if ev.kind == "CANCEL":
        return f"<b>{tk}</b>: идея отменена ❌\nСценарий сломан ещё до пробоя — уровень {fmt_num(ev.price)} потерян."
    if ev.kind == "EXPIRED":
        return f"<b>{tk}</b>: идея неактуальна ❌\nПробоя не было слишком долго — сценарий снимается."
    raise ValueError(ev.kind)
