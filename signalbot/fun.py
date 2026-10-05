"""Мемы, факты и шутки для ленты канала: Claude пишет подписи, банки — запасной вариант."""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass

from .funbank import FACTS, JOKES, MEMES
from .memes import TEMPLATES
from .morning import PROFANITY
from .trends import SENSITIVE
from .writer import BANNED

log = logging.getLogger(__name__)
MARK = "[фан] "
POLITICS = re.compile(r"(путин|зеленск|трамп|байден|навальн|украин|росси|выбор|партия|война|сво\b)", re.I)

SYSTEM = """Ты ведёшь ленту закрытого крипто-канала от лица автора: опытного трейдера-блогера с самоиронией. Аудитория — трейдеры и инвесторы.

Общие правила:
- Живой разговорный русский, коротко, без канцелярита и без шаблонов ИИ.
- Юмор современный, как в русскоязычном интернете и TikTok: коротко, абсурдно, самоиронично, с неожиданным поворотом. Никакого «бумерского» юмора и баянов вроде банального «купил на хаях, продал на лоях» без свежего угла. Если шутку надо объяснять, это плохая шутка.
- Без мата, оскорблений, политики, религии, трагедий и сексуальных тем. Над конкретными людьми не издевайся и не приписывай им слов и поступков: допустима только лёгкая отсылка к известному мем-образу, без выдуманных фактов и цитат.
- Блок TRENDS (если есть) — актуальные мемы и тренды. Используй формат или отсылку, только если это безобидно и по-настоящему сочетается с трейдингом. Блок TOPICS — любимые темы автора, берут как вдохновение, не обязательно в каждом посте.
- Не обещай прибыль и рост, не давай торговых советов, не придумывай новости и факты.
- Не пиши цифр (кроме случаев, где в задании прямо сказано использовать числа из факта).
- Не повторяй заходы и шутки из блока RECENT.
- Эмодзи не используй (добавит бот)."""

MEME_TASK = f"""Придумай мем про крипту и трейдинг. Выбери шаблон из списка и напиши подписи.
Шаблоны: {', '.join(TEMPLATES)}.
- lion и chart:*: top и bottom — две короткие подписи (до 60 символов каждая), как в классических мемах («Я: …» / «Рынок: …»).
- nobody: формат «Никто: / Абсолютно никто: / Я: …». top — первая строка (обычно «Никто:»), bottom — абсурдная панчлайн-строка («Я в три ночи: …»).
- expectation: top — «ожидание» (всё идёт к луне), bottom — «реальность» (график падает), обе до 60 символов.
- two_panel: top — как делать плохо, bottom — как делать хорошо (до 60 символов каждая).
- Для chart:pump_dump на картинке помечено «Я купил» на пике, для chart:bleed_then_pump — «Я продал» на дне, для chart:staircase — «Я здесь» перед обвалом; подпись должна с этим сочетаться.
Верни JSON: template, top, bottom, caption (необязательная подпись к посту: одна короткая фраза с юмором, до 100 символов, можно пустую строку)."""

FACT_TASK = """Тебе дан проверенный факт (DATA.fact). Подготовь пост:
- card: этот же факт короче и яснее, для крупной надписи на карточке (до 200 символов). Строго по тексту факта: ничего не добавляй, не меняй числа и даты.
- comment: одна короткая фраза-комментарий с лёгким юмором (до 150 символов), без цифр.
Верни JSON: card, comment."""

JOKE_TASK = """Придумай короткую крипто-шутку или забавное наблюдение про трейдинг (1-3 предложения, до 300 символов), в духе анекдота или самоиронии. Верни JSON: joke."""

SCHEMAS = {
    "meme": {"type": "object", "properties": {"template": {"type": "string", "enum": TEMPLATES}, "top": {"type": "string"}, "bottom": {"type": "string"}, "caption": {"type": "string"}},
             "required": ["template", "top", "bottom", "caption"], "additionalProperties": False},
    "fact": {"type": "object", "properties": {"card": {"type": "string"}, "comment": {"type": "string"}},
             "required": ["card", "comment"], "additionalProperties": False},
    "joke": {"type": "object", "properties": {"joke": {"type": "string"}}, "required": ["joke"], "additionalProperties": False},
    "image": {"type": "object", "properties": {"caption": {"type": "string"}}, "required": ["caption"], "additionalProperties": False},
}


@dataclass
class MemeSpec:
    template: str
    top: str
    bottom: str
    caption: str = ""


@dataclass
class FactPost:
    card: str
    comment: str


def _nums(s: str) -> set[str]:
    return {x.replace(",", ".") for x in re.findall(r"\d+(?:[.,]\d+)?", s)}


def reject_reason(s: str, cap: int, allow_empty: bool = False, allowed_numbers: set[str] | None = None) -> str | None:
    if not s.strip():
        return None if allow_empty else "пусто"
    if len(s) > cap:
        return f"длиннее {cap} символов ({len(s)})"
    if "<" in s or ">" in s:
        return "HTML-символы"
    if PROFANITY.search(s):
        return "мат"
    if POLITICS.search(s) or SENSITIVE.search(s):
        return "чувствительная тема"
    bad = next((b for b in BANNED + ("памп", "pump", "улетит") if b in s.lower()), None)
    if bad:
        return f"запрещённое слово «{bad}»"
    extra = _nums(s) - (allowed_numbers if allowed_numbers is not None else set())
    if extra:
        return f"числа вне данных {sorted(extra)}"
    return None


def clean_ok(s: str, cap: int, allow_empty: bool = False, allowed_numbers: set[str] | None = None) -> bool:
    return reject_reason(s, cap, allow_empty, allowed_numbers) is None


class FunWriter:
    def __init__(self, model: str = "claude-sonnet-5-5", effort: str = "low", client=None):
        self.model, self.effort, self._client = model, effort, client

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=90.0, max_retries=2)
        return self._client

    def _ask(self, kind: str, task: str, data: dict | None, recent: list[str], image_b64: tuple[str, str] | None = None) -> dict | None:
        prompt = task + (f"\n\nDATA:\n{json.dumps(data, ensure_ascii=False)}" if data else "")
        prompt += "\n\nRECENT (не повторяйся):\n" + ("\n".join(f"- {r}" for r in recent) or "(пока нет)")
        content: list | str = prompt
        if image_b64:
            content = [{"type": "image", "source": {"type": "base64", "media_type": image_b64[0], "data": image_b64[1]}}, {"type": "text", "text": prompt}]
        try:
            resp = self._cl().messages.create(
                model=self.model, max_tokens=3000, system=SYSTEM, messages=[{"role": "user", "content": content}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMAS[kind]}},
            )
            if resp.stop_reason == "refusal":
                return None
            return json.loads(next(b.text for b in resp.content if b.type == "text"))
        except Exception as e:
            log.warning("fun/%s: %s", kind, e)
            return None

    def meme(self, recent: list[str], trends: list[str] | None = None, topics: list[str] | None = None) -> MemeSpec | None:
        for _ in (1, 2):
            d = self._ask("meme", MEME_TASK, {"TRENDS": trends or [], "TOPICS": topics or []}, recent)
            if d is None:
                return None
            try:
                m = MemeSpec(d["template"], d["top"].strip(), d["bottom"].strip(), d["caption"].strip())
            except (KeyError, TypeError, AttributeError):
                continue  # в ответе нет нужных полей: считаем попытку неудачной
            why = ("шаблон" if m.template not in TEMPLATES else None) or next(
                (f"{n}: {r}" for n, v, c, e in (("top", m.top, 70, False), ("bottom", m.bottom, 70, False), ("caption", m.caption, 110, True))
                 if (r := reject_reason(v, c, allow_empty=e))), None)
            if why is None:
                return m
            log.info("мем не прошёл проверку: %s | %r", why, f"{m.template}: {m.top} / {m.bottom} / {m.caption}"[:300])
        return None

    def fact(self, fact_text: str, recent: list[str]) -> FactPost | None:
        nums = _nums(fact_text)
        for _ in (1, 2):
            d = self._ask("fact", FACT_TASK, {"fact": fact_text}, recent)
            if d is None:
                return None
            try:
                f = FactPost(d["card"].strip(), d["comment"].strip())
            except (KeyError, TypeError, AttributeError):
                continue
            why = next((f"{n}: {r}" for n, v, c, a in (("card", f.card, 210, nums), ("comment", f.comment, 160, None))
                        if (r := reject_reason(v, c, allowed_numbers=a))), None)
            if why is None:
                return f
            log.info("факт не прошёл проверку: %s | %r", why, f"{f.card} / {f.comment}"[:300])
        return None

    def joke(self, recent: list[str], trends: list[str] | None = None, topics: list[str] | None = None) -> str | None:
        for _ in (1, 2):
            d = self._ask("joke", JOKE_TASK, {"TRENDS": trends or [], "TOPICS": topics or []}, recent)
            if d is None:
                return None
            joke = d.get("joke") if isinstance(d.get("joke"), str) else ""
            why = reject_reason(joke.strip(), 320)
            if why is None:
                return joke.strip()
            log.info("шутка не прошла проверку: %s | %r", why, joke[:300])
        return None

    def image_caption(self, media_type: str, b64: str, recent: list[str]) -> str | None:
        task = "К этой картинке (мем про крипту/трейдинг) напиши короткую подпись к посту: одна фраза с юмором, до 100 символов. Если картинка не про крипту, трейдинг или рынок, верни пустую строку. Верни JSON: caption."
        d = self._ask("image", task, None, recent, image_b64=(media_type, b64))
        if d is None:
            return None
        c = str(d.get("caption", "")).strip()
        return c if clean_ok(c, 110, allow_empty=True) else None


# --- выбор без повторов ---------------------------------------------------------------------------
def pick_unused(n: int, used: list[int], rng: random.Random) -> tuple[int, list[int]]:
    """Индекс, которого не было недавно; когда всё использовано — круг начинается заново."""
    free = [i for i in range(n) if i not in used]
    if not free:
        used, free = [], list(range(n))
    i = rng.choice(free)
    return i, used + [i]


def fact_text(idx: int) -> str:
    return FACTS[idx][1]


FACT_COMMENTS = ["Вот с таких мелочей и начиналась вся эта история.", "Факт упрямая вещь, особенно в крипте.", "Теперь знаете, чем удивить друзей за обедом.", "Рынок меняется, а история остаётся."]
MEME_CAPTIONS = ["Узнали себя?", "Это не мы, это рынок.", "Скажите, что я не один такой.", "Классика жанра.", "Бывает у всех. Ставьте реакции, кто узнал себя."]
