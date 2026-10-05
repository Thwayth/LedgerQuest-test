"""Тексты постов через Claude: каждый раз своё мнение и тон, с проверкой и откатом на шаблоны.

Числа (ТВХ/ТП/СЛ) в текст не попадают: их считает бот и добавляет отдельной строкой.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
from dataclasses import dataclass

from .models import Idea, Side

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_OPINION, MAX_RISK = 450, 180
BANNED = ("гарантир", "100%", "без риска", "точно вырастет", "х10", "x10", "сто процентов", "не может упасть")

TONES = [
    "спокойный и уверенный",
    "ироничный, с сухим юмором",
    "осторожный скептик, которому идея всё же нравится",
    "азартный, но с трезвой головой",
    "деловой и лаконичный",
    "расслабленный, как будто пишет знакомому",
    "слегка ворчливый опытный трейдер",
]

COMPONENT_PHRASES = {
    "proximity": "цена вплотную подошла к уровню",
    "squeeze": "волатильность сжата, рынок поджимается перед движением",
    "volume": "объём в последние дни растёт",
    "structure": "структура разворачивается в сторону идеи",
    "touches": "уровень уже не раз отрабатывался рынком",
    "rr": "соотношение риска и потенциала выглядит неплохо",
}
COMPONENT_WEIGHTS = {"proximity": 25, "squeeze": 20, "volume": 15, "structure": 15, "touches": 10, "rr": 15}

SYSTEM = """Ты пишешь посты для закрытого крипто-канала от лица его автора: опытного трейдера-блогера.

Стиль:
- Живой разговорный русский, как в телеграм-канале. Короткие фразы, без канцелярита и без шаблонов ИИ («стоит отметить», «важно понимать», «в заключение», «данный», списки через тире).
- Пост должен звучать как мнение конкретного человека по конкретной монете, а не как отчёт. Каждый раз другой заход, другая подача, другой порядок мыслей.
- Шутка уместна не в каждом посте и только по делу: к ситуации монеты (мем-коин, резкий рост, долгий боковик, толпа в лонгах и т.п.). Без мата, оскорблений, политики и шуток про деньги читателей.
- Эмодзи не используй (заголовок поста уже есть).

Жёсткие правила:
- Опирайся только на блок DATA. Не выдумывай новости, причины движения, объёмы, свои позиции, сделки и результаты. Не пиши, что ты уже в сделке.
- Не обещай прибыль, не давай гарантий.
- Не пиши никаких цифр вообще (ни цен, ни процентов, ни номеров целей): уровни бот добавит отдельной строкой. Слова «первая цель» и т.п. допустимы.
- Для SHORT всё зеркально: пробой вниз, цели ниже.
- Не повторяй формулировки и заходы из блока RECENT.

Верни JSON с двумя полями:
- opinion: мнение по монете, 2-4 предложения, не больше 450 символов. Что за ситуация на дневке, чего ждать от пробоя уровня, как входить с подтверждением, куда смотреть по целям (пулы ликвидности).
- risk_line: одно короткое предложение для цитаты: кто готов рискнуть, может зайти по рынку и набирать позицию сеткой; риск на нём. Своими словами, не больше 180 символов."""

SCHEMA = {
    "type": "object",
    "properties": {"opinion": {"type": "string"}, "risk_line": {"type": "string"}},
    "required": ["opinion", "risk_line"],
    "additionalProperties": False,
}


@dataclass
class PostText:
    opinion: str
    risk_line: str


def idea_facts(idea: Idea) -> dict:
    long = idea.side is Side.LONG
    p, z = idea.price, idea.zone
    dist = (z.low - p) / p * 100 if long else (p - z.high) / p * 100
    comps = idea.extra.get("components", {})
    strong = [COMPONENT_PHRASES[k] for k, v in sorted(comps.items(), key=lambda kv: -kv[1] / COMPONENT_WEIGHTS[kv[0]])
              if k in COMPONENT_WEIGHTS and v / COMPONENT_WEIGHTS[k] >= 0.6][:3]
    ctx = idea.extra.get("context")
    return {
        "ticker": idea.ticker,
        "direction": "LONG (пробой вверх)" if long else "SHORT (пробой вниз)",
        "timeframe": "дневной",
        "change_24h_pct": round(idea.change_pct, 1),
        "distance_to_level_pct": round(dist, 1) if dist > 0 else "цена уже внутри зоны",
        "level_touches": idea.zone.touches,
        "targets_count": len(idea.pools),
        "risk_reward": idea.extra.get("rr"),
        "strengths": strong,
        "market_context": list(ctx.notes) if ctx else [],
    }


def _valid(t: PostText) -> bool:
    for s, cap in ((t.opinion, MAX_OPINION), (t.risk_line, MAX_RISK)):
        if not s or len(s) > cap or re.search(r"\d", s) or "<" in s or ">" in s:
            return False
        if any(b in s.lower() for b in BANNED):
            return False
    return True


class PostWriter:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low", client=None):
        self.model, self.effort, self._client = model, effort, client

    @staticmethod
    def available() -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=90.0, max_retries=2)
        return self._client

    def write(self, idea: Idea, day: str, recent: list[str]) -> PostText | None:
        """Текст поста или None (тогда вызывающий код берёт шаблоны). Не бросает исключений."""
        seed = int(hashlib.sha256(f"{idea.symbol}|{day}|{len(recent)}".encode()).hexdigest()[:8], 16)
        tone = random.Random(seed).choice(TONES)
        prompt = (
            f"DATA:\n{json.dumps(idea_facts(idea), ensure_ascii=False, indent=1)}\n\n"
            f"Тон этого поста: {tone}.\n\n"
            "RECENT (последние посты канала — не повторяйся):\n" + ("\n".join(f"- {r}" for r in recent) or "(пока нет)")
        )
        for attempt in (1, 2):
            try:
                resp = self._cl().messages.create(
                    model=self.model,
                    max_tokens=4000,
                    system=SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                )
                if resp.stop_reason == "refusal":
                    log.warning("Claude отказался писать пост для %s", idea.symbol)
                    return None
                raw = next(b.text for b in resp.content if b.type == "text")
                data = json.loads(raw)
                t = PostText(str(data["opinion"]).strip(), str(data["risk_line"]).strip())
                if _valid(t):
                    return t
                log.info("текст для %s не прошёл проверку (попытка %d)", idea.symbol, attempt)
            except Exception as e:
                log.warning("не удалось получить текст от Claude для %s: %s", idea.symbol, e)
                return None
        return None
