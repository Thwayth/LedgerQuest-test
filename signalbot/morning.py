"""Утренний пост: приветствие, новость или заход на реакции (на фактах), кастомные эмодзи."""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
from dataclasses import dataclass, field
from html import escape

from .reactions import PLACEHOLDER, fill_placeholders, placeholders_valid
from .writer import BANNED, DEFAULT_MODEL

log = logging.getLogger(__name__)
KIND_NEWS, KIND_MOOD, KIND_TEASER = "news", "mood", "teaser"
TEASER_MIN_CHANGE = 5.0  # монета «дня» берётся только при росте от +5% за сутки
MARK = "[утро] "
STABLES = {"USDC", "FDUSD", "TUSD", "USDP", "DAI", "BUSD"}
PROFANITY = re.compile(r"(хуй|хуе|пизд|ебан|ебат|ёб|блят|бляд|сука|мудак|залуп)", re.I)
STD_EMOJI = ["☀️", "🚀", "📈", "💎", "🔥", "🐂", "🪙", "⚡", "🦁", "🌅"]

GREETINGS = [
    "Доброе утро, {a}!", "{a}, всем доброго утра!", "Подъём, {a}!", "Утро доброе, {a}!", "Всем привет, {a}!",
    "{a}, с добрым утром!", "Проснулись, {a}? Утро на дворе.", "Доброе утро, {a}, кофе налит?", "Приветствую, {a}!",
    "{a}, утро начинается не с будильника, а с графиков.",
]

SYSTEM = """Ты пишешь утренний пост для закрытого крипто-канала от лица его автора: опытного трейдера-блогера. Аудиторию канала зовут по-разному: Прайд, львы, ребята, команда, народ.

Стиль:
- Живой разговорный русский, как в телеграм-канале: коротко, с характером, без канцелярита и шаблонов ИИ.
- Приветствие каждый раз новое: меняй обращение и подачу, не повторяй приветствия из блока RECENT.
- Лёгкая шутка или ирония, уместная к теме. Без мата, оскорблений, политики.
- В конце — заход на реакции: предложи подписчикам ответить реакциями (можно назвать 1-2 обычных эмодзи для голосования) или ответить в комментариях.

Жёсткие правила:
- Опирайся только на блок DATA. Не выдумывай факты, причины движения, детали новости сверх заголовка и описания. Не обещай рост, «памп», прибыль. Не давай торговых советов и не называй цены входа.
- Любые числа бери только из DATA, ничего не считай сам и не придумывай новых.
- Тип поста задан в DATA.kind:
  * news — выбери ОДНУ новость из DATA.headlines, перескажи своими словами по-русски (источник можно упомянуть), добавь короткое мнение и вопрос аудитории;
  * mood — коротко про настроение рынка утром по DATA.market (BTC, ETH, индекс страха и жадности, лидеры роста/падения можно назвать по тикеру), с шуткой и вопросом аудитории;
  * teaser — в DATA.hidden_mover сказано, что одна монета из топа сильно растёт за сутки, НО её тикер скрыт. НЕ называй её и не намекай на название. Заинтригуй: предложи угадать и накидать реакций, скажи, что назовёшь монету в DATA.reveal_time по МСК. Не обещай продолжения роста, подчеркни, что это просто факт движения за сутки.
- Эмодзи в greeting и body не используй (их добавит бот).
- В DATA.reactions перечислены реакции, которые реально включены в канале (slot и meaning). Зовя подписчиков ставить реакции, пиши плейсхолдеры {R1}, {R2} вместо эмодзи и подбирай реакцию по смыслу (meaning), например «ставьте {R1}, если ждёте рост». Других эмодзи для голосования не используй. Если DATA.reactions нет, зови писать в комментариях.

Верни JSON:
- greeting: приветствие, 1 короткая фраза (до 60 символов).
- body: основной текст, 2-4 предложения (до 480 символов).
- hook: заход на реакции, 1 предложение (до 200 символов)."""

SCHEMA = {
    "type": "object",
    "properties": {"greeting": {"type": "string"}, "body": {"type": "string"}, "hook": {"type": "string"}},
    "required": ["greeting", "body", "hook"],
    "additionalProperties": False,
}


@dataclass
class Snapshot:
    exchange: str
    btc: dict | None = None  # {"price", "change"}
    eth: dict | None = None
    gainers: list[tuple[str, float]] = field(default_factory=list)
    losers: list[tuple[str, float]] = field(default_factory=list)


@dataclass
class MorningText:
    greeting: str
    body: str
    hook: str


def collect_snapshot(ex, exchanges: list[str], min_quote_volume: float) -> Snapshot | None:
    """Срез рынка по перпетуалам: BTC, ETH, лидеры роста и падения среди ликвидных монет."""
    for name in exchanges:
        try:
            tickers = ex.client(name).fetch_tickers()
        except Exception as e:
            log.warning("тикеры %s недоступны: %s", name, e)
            continue
        snap = Snapshot(exchange=name)
        movers: list[tuple[str, float]] = []
        for sym, t in tickers.items():
            if "/USDT:" not in sym or not sym.endswith(":USDT"):
                continue
            base = sym.split("/")[0]
            pct = t.get("percentage")
            if pct is None and t.get("open") and t.get("last"):
                pct = (t["last"] / t["open"] - 1) * 100
            if pct is None or not t.get("last"):
                continue
            if base in ("BTC", "ETH"):
                setattr(snap, base.lower(), {"price": float(t["last"]), "change": round(float(pct), 2)})
                continue
            qv = t.get("quoteVolume") or 0
            if qv >= min_quote_volume and base not in STABLES:
                movers.append((base, round(float(pct), 1)))
        movers.sort(key=lambda m: m[1], reverse=True)
        snap.gainers, snap.losers = movers[:3], movers[::-1][:3]
        if snap.btc or movers:
            return snap
    return None


def choose_kind(day: str, last_kind: str | None, has_news: bool, has_mover: bool) -> str:
    kinds = [KIND_MOOD] + ([KIND_NEWS] if has_news else []) + ([KIND_TEASER] if has_mover else [])
    if last_kind in kinds and len(kinds) > 1:
        kinds.remove(last_kind)  # не два одинаковых подряд
    return random.Random(int(hashlib.sha256(day.encode()).hexdigest()[:8], 16)).choice(kinds)


def build_data(kind: str, snap: Snapshot | None, headlines: list[dict], fng: int | None, reveal_time_msk: str, slots: list[dict] | None = None) -> dict:
    d: dict = {"kind": kind}
    if slots:
        d["reactions"] = [{"slot": s["slot"], "meaning": s["meaning"]} for s in slots]
    if kind == KIND_NEWS:
        d["headlines"] = headlines
    market: dict = {}
    if snap:
        if snap.btc:
            market["btc_change_24h_pct"] = snap.btc["change"]
        if snap.eth:
            market["eth_change_24h_pct"] = snap.eth["change"]
        if kind == KIND_MOOD:
            market["top_gainers_24h"] = [{"ticker": t, "change_pct": c} for t, c in snap.gainers]
            market["top_losers_24h"] = [{"ticker": t, "change_pct": c} for t, c in snap.losers]
    if fng is not None:
        market["fear_greed_index_0_100"] = fng
    if market and kind != KIND_NEWS:
        d["market"] = market
    if kind == KIND_TEASER and snap and snap.gainers:
        d["hidden_mover"] = {"change_24h_pct": snap.gainers[0][1], "ticker": "СКРЫТ"}
        d["reveal_time"] = reveal_time_msk
    return d


def _nums(s: str) -> list[float]:
    return [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[.,]\d+)?", s)]


def reject_reason(t: MorningText, data: dict, hidden_ticker: str | None = None, slots: list[dict] | None = None) -> str | None:
    """Почему текст не подходит (None — подходит)."""
    allowed = _nums(json.dumps(data, ensure_ascii=False))
    for name, s, cap in (("greeting", t.greeting, 60), ("body", t.body, 480), ("hook", t.hook, 200)):
        if not placeholders_valid(s, slots or []):
            return f"{name}: плейсхолдер несуществующей реакции"
        s = PLACEHOLDER.sub("", s)
        if not s:
            return f"{name}: пусто"
        if len(s) > cap:
            return f"{name}: длиннее {cap} символов ({len(s)})"
        if "<" in s or ">" in s:
            return f"{name}: HTML-символы"
        if PROFANITY.search(s):
            return f"{name}: мат"
        bad = next((b for b in BANNED + ("памп", "pump", "улетит", "x2", "х2") if b in s.lower()), None)
        if bad:
            return f"{name}: запрещённое слово «{bad}»"
        foreign = [n for n in _nums(s) if not any(abs(n - a) < 0.51 for a in allowed)]
        if foreign:
            return f"{name}: числа вне данных {foreign}"
        if hidden_ticker and re.search(rf"\b{re.escape(hidden_ticker)}\b", s, re.I):
            return f"{name}: раскрыт скрытый тикер"
    return None


def valid_text(t: MorningText, data: dict, hidden_ticker: str | None = None, slots: list[dict] | None = None) -> bool:
    return reject_reason(t, data, hidden_ticker, slots) is None


class MorningWriter:
    def __init__(self, model: str = DEFAULT_MODEL, effort: str = "low", client=None):
        self.model, self.effort, self._client = model, effort, client

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=90.0, max_retries=2)
        return self._client

    def write(self, data: dict, recent: list[str], hidden_ticker: str | None = None, slots: list[dict] | None = None) -> MorningText | None:
        prompt = (
            f"DATA:\n{json.dumps(data, ensure_ascii=False, indent=1)}\n\n"
            "RECENT (прошлые утренние посты, не повторяйся):\n" + ("\n".join(f"- {r}" for r in recent) or "(пока нет)")
        )
        for attempt in (1, 2):
            try:
                resp = self._cl().messages.create(
                    model=self.model, max_tokens=4000, system=SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                    output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
                )
                if resp.stop_reason == "refusal":
                    return None
                raw = json.loads(next(b.text for b in resp.content if b.type == "text"))
                t = MorningText(str(raw["greeting"]).strip(), str(raw["body"]).strip(), str(raw["hook"]).strip())
                why = reject_reason(t, data, hidden_ticker, slots)
                if why is None:
                    return t
                log.info("утренний текст не прошёл проверку (попытка %d): %s | %r", attempt, why, f"{t.greeting} / {t.body} / {t.hook}"[:300])
            except Exception as e:
                log.warning("утренний текст от Claude: %s", e)
                return None
        return None


# --- запасные шаблоны (без ключа Claude) --------------------------------------------------------
def _rng(*parts: object) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:12], 16))


def template_text(kind: str, data: dict, day: str, audience: list[str], slots: list[dict] | None = None) -> MorningText:
    r = _rng("morning", day, kind)
    greeting = r.choice(GREETINGS).format(a=r.choice(audience))
    m = data.get("market", {})
    btc = m.get("btc_change_24h_pct")
    if kind == KIND_TEASER:
        ch = data["hidden_mover"]["change_24h_pct"]
        body = r.choice([
            f"Пока вы спали, одна монета из ликвидного топа выросла на {ch:+.1f}% за сутки. Имя пока держу при себе.",
            f"За сутки одна из топовых монет прибавила {ch:+.1f}%. Кто это — скажу позже, а пока гадайте.",
        ])
        hook = r.choice([
            f"Накидайте реакций и пишите догадки, назову монету в {data['reveal_time']} по МСК.",
            f"Реакции в студию: угадаете — получите моё уважение, раскрою в {data['reveal_time']} по МСК.",
        ])
        return MorningText(greeting, body, hook)
    if btc is None:
        body = r.choice(["Рынок просыпается, смотрю уровни и пью кофе.", "Утро, график, кофе. Классика."])
    elif btc >= 1:
        body = r.choice([f"BTC за сутки {btc:+.1f}%, рынок бодрый, но я бы всё равно считал риск до входа.",
                         f"Биток прибавил {btc:+.1f}% за сутки, настроение приподнятое."])
    elif btc <= -1:
        body = r.choice([f"BTC за сутки {btc:+.1f}%, рынок хмурый. Самое время не делать глупостей.",
                         f"Биток просел на {abs(btc):.1f}% за сутки, нервные уже в чатах, спокойные — на уровнях."])
    else:
        body = r.choice([f"BTC за сутки {btc:+.1f}%, рынок топчется на месте, ждём импульс.",
                         f"Биток почти не двигается ({btc:+.1f}% за сутки), затишье перед чем-то."])
    hooks = ["Как настроение: ждёте рост или падение сегодня? Накидайте реакций.",
             "Реакциями покажите, кто сегодня в лонгах, а кто на диване.",
             "Расскажите в комментариях, что ждёте от рынка сегодня."]
    if slots and len(slots) >= 2:  # реальные реакции канала
        hooks += ["Ждёте рост — ставьте {R1}, ждёте падение — ставьте {R2}.", "{R1} если сегодня в лонге, {R2} если пока на диване."]
    hook = r.choice(hooks)
    return MorningText(greeting, body, hook)


# --- эмодзи и сборка ---------------------------------------------------------------------------
def pick_emojis(custom: list[tuple[str, str]], n: int, day: str) -> list[str]:
    """Кастомные (премиум) эмодзи из пула канала, а если их нет — обычные."""
    r = _rng("emoji", day)
    if custom:
        pool = r.sample(custom, min(n, len(custom)))
        out = [f'<tg-emoji emoji-id="{escape(i, quote=True)}">{escape(ch)}</tg-emoji>' for i, ch in pool]
        while len(out) < n:
            out.append(r.choice(out))
        return out
    return r.sample(STD_EMOJI, n)


def build_morning_html(t: MorningText, emojis: list[str], slots: list[dict] | None = None) -> str:
    e1, e2, e3 = emojis[:3]
    body = fill_placeholders(escape(t.body), slots or [])
    hook = fill_placeholders(escape(t.hook), slots or [])
    return f"{e1} <b>{escape(t.greeting)}</b>\n\n{body}\n\n{e2} {hook} {e3}"


def reveal_html(ticker: str, change: float, day: str, emoji: str) -> str:
    r = _rng("reveal", day, ticker)
    head = r.choice([
        f"{emoji} Раскрываю: это <b>{escape(ticker)}</b>, {change:+.1f}% за сутки на момент утреннего поста.",
        f"{emoji} Как и обещал: <b>{escape(ticker)}</b> ({change:+.1f}% за сутки к утру). Кто угадал — молодцы.",
        f"{emoji} Время разгадки: <b>{escape(ticker)}</b>, {change:+.1f}% за сутки к утру.",
    ])
    tail = r.choice([
        "Это не сигнал на вход, а просто факт движения. Сетапы будут отдельными постами.",
        "Гнаться за вчерашней свечой не призываю: вход только по уровням и с подтверждением.",
    ])
    return f"{head}\n\n{tail}"
