"""Стикеры по ситуации: запоминаем паки, которые прислал владелец, размечаем их (эмодзи + Claude по превью)
и подбираем подходящий стикер к событию, чередуя паки и не повторяясь."""
from __future__ import annotations

import base64
import io
import json
import logging
import random
from html import escape

from PIL import Image

log = logging.getLogger(__name__)

SITUATIONS = ["morning", "profit", "celebrate", "loss", "shrug", "hype", "laugh", "think", "bullish", "bearish"]
SITUATION_HELP = {
    "morning": "приветствие, доброе утро, привет, кофе", "profit": "прибыль, цель взята, деньги, довольство",
    "celebrate": "большая победа, праздник, триумф", "loss": "убыток, стоп, грусть, огорчение, слёзы",
    "shrug": "пожимание плечами, нейтральность, ну бывает", "hype": "азарт, волнение, ожидание движения, огонь",
    "laugh": "смех, шутка, ирония, подмигивание", "think": "размышление, интерес, вопрос, разбор",
    "bullish": "уверенный рост, сила, бык, оптимизм", "bearish": "давление вниз, хищник, медведь, скепсис",
}
DEFAULT_CHANCE = {"morning": 0.8, "profit": 0.8, "celebrate": 1.0, "loss": 0.5, "shrug": 0.3, "hype": 0.6,
                  "laugh": 0.5, "think": 0.4, "bullish": 0.25, "bearish": 0.25}

# Базовая разметка по эмодзи стикера (работает сразу и без Claude)
EMOJI_TAGS: dict[str, list[str]] = {
    "morning": list("☀🌞🌅🌄☕👋🤗🙂😊😃😄🥱"), "profit": list("💰🤑💵💸📈🚀💎🔥😎👍🤝"),
    "celebrate": list("🥳🎉🏆🍾🎊👑💪🎆🙌👏"), "loss": list("😭😢😞😔💔📉🤕😩😫🥲😿😓😰"),
    "shrug": list("🤷😐😶🫤🙃😑😬🤨"), "hype": list("🔥🚀⚡😮😲🤩👀😱🤯"),
    "laugh": list("😂🤣😆😹😁😏😈🤪😜😝"), "think": list("🤔🧐🤓🧠💡📚🤨"),
    "bullish": list("🐂📈💪😎🦁🚀🤑"), "bearish": list("🐻📉😈🔪🩸🦈😒🙄"),
}


def emoji_tags(emoji: str | None) -> list[str]:
    if not emoji:
        return []
    chars = [c for c in emoji if c not in "️‍"]
    return [s for s, es in EMOJI_TAGS.items() if any(c in es for c in chars)]


def _to_png(raw: bytes, size: int = 256) -> bytes:
    im = Image.open(io.BytesIO(raw))
    im.seek(0)
    im = im.convert("RGBA")
    bg = Image.new("RGBA", im.size, (40, 40, 40, 255))  # прозрачность -> тёмный фон, чтобы контур читался
    bg.alpha_composite(im)
    im = bg.convert("RGB")
    im.thumbnail((size, size))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "description": {"type": "string"},
        "tags": {"type": "array", "items": {"type": "string", "enum": SITUATIONS}}},
        "required": ["index", "description", "tags"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


class StickerDescriber:
    """Claude смотрит превью стикеров пачкой и размечает, в каких ситуациях стикер уместен."""

    def __init__(self, model: str = "claude-sonnet-5-5", effort: str = "low", client=None):
        self.model, self.effort, self._client = model, effort, client

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=120.0, max_retries=2)
        return self._client

    def describe(self, images: list[bytes]) -> dict[int, tuple[str, list[str]]] | None:
        sit = "\n".join(f"- {k}: {v}" for k, v in SITUATION_HELP.items())
        content: list = [{"type": "text", "text": (
            "Ниже стикеры из паков Telegram (превью). Для каждого стикера (индекс = номер по порядку, с нуля) "
            "кратко опиши, что на нём изображено и какое настроение, и выбери от 0 до 3 ситуаций, в которых он уместен в крипто-канале трейдера.\n"
            f"Ситуации:\n{sit}\nЕсли стикер не подходит ни к одной ситуации или на нём что-то неприемлемое, верни пустой список tags. "
            "Описание до 60 символов.")}]
        for i, raw in enumerate(images):
            content.append({"type": "text", "text": f"Стикер {i}:"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(_to_png(raw)).decode()}})
        try:
            resp = self._cl().messages.create(
                model=self.model, max_tokens=6000, messages=[{"role": "user", "content": content}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": DESCRIBE_SCHEMA}},
            )
            if resp.stop_reason == "refusal":
                return None
            data = json.loads(next(b.text for b in resp.content if b.type == "text"))
            out = {}
            for it in data["items"]:
                tags = [t for t in it["tags"] if t in SITUATIONS][:3]
                out[int(it["index"])] = (str(it["description"])[:80], tags)
            return out
        except Exception as e:
            log.warning("разметка стикеров Claude: %s", e)
            return None


class StickerCatalog:
    BATCH = 20

    def __init__(self, db, describer: StickerDescriber | None = None):
        self.db, self.describer = db, describer

    async def sync(self, bot, describe_limit: int = 40) -> dict:
        """Подтягивает новые паки из Telegram и размечает (по эмодзи сразу, Claude — порциями за запуск)."""
        import asyncio

        added = 0
        for name in self.db.sticker_sets(only_unsynced=True):
            try:
                st_set = await bot.get_sticker_set(name)
            except Exception as e:
                log.warning("пак %s недоступен: %s", name, e)
                continue
            if str(getattr(st_set.sticker_type, "value", st_set.sticker_type)) != "regular":
                if str(getattr(st_set.sticker_type, "value", st_set.sticker_type)) == "custom_emoji":
                    self.db.add_emoji_set(name)  # пак премиум-эмодзи: пойдёт в пул эмодзи для постов
                self.db.mark_set_synced(name, st_set.title)  # маски и кастомные эмодзи отправлять как стикеры нельзя
                continue
            for s in st_set.stickers:
                thumb = s.thumbnail.file_id if getattr(s, "thumbnail", None) else (s.file_id if not (s.is_animated or s.is_video) else None)
                if self.db.upsert_sticker(s.file_unique_id, s.file_id, name, s.emoji, emoji_tags(s.emoji), thumb):
                    added += 1
            self.db.mark_set_synced(name, st_set.title)
            log.info("пак %s: %d стикеров", name, len(st_set.stickers))
        described = 0
        if self.describer is not None and describe_limit > 0:
            pending = self.db.pending_stickers(describe_limit)
            for i in range(0, len(pending), self.BATCH):
                chunk = pending[i:i + self.BATCH]
                images, keep = [], []
                for uid, thumb in chunk:
                    try:
                        buf = await bot.download(thumb)
                        images.append(buf.read())
                        keep.append(uid)
                    except Exception as e:
                        log.warning("превью стикера недоступно: %s", e)
                        self.db.mark_sticker_unviewable(uid)
                if not images:
                    continue
                res = await asyncio.to_thread(self.describer.describe, images)
                if res is None:
                    break  # сбой Claude: оставляем на следующий запуск, теги по эмодзи уже работают
                for idx, uid in enumerate(keep):
                    desc, tags = res.get(idx, ("", None))
                    if tags is None:
                        continue
                    self.db.set_sticker_description(uid, desc, tags)
                    described += 1
        return {"added": added, "described": described}

    def pick(self, situation: str, rng: random.Random | None = None) -> str | None:
        """file_id подходящего стикера: чаще размеченные Claude, паки чередуются, недавние не повторяются."""
        rng = rng or random.Random()
        all_s = self.db.stickers()
        cands = [s for s in all_s if situation in s["tags"]]
        if not cands:
            return None
        try:
            recent = json.loads(self.db.kv_get("sticker_recent") or "[]")
        except ValueError:
            recent = []
        last_set = next((s["set_name"] for s in all_s if s["unique_id"] == recent[-1]), None) if recent else None
        weights = []
        for s in cands:
            w = 2.0 if s["described"] else 1.0
            if s["unique_id"] in recent[-8:]:
                w *= 0.05
            if last_set and s["set_name"] == last_set:
                w *= 0.35  # стараемся брать из другого пака
            weights.append(w)
        chosen = rng.choices(cands, weights=weights, k=1)[0]
        self.db.kv_set("sticker_recent", json.dumps((recent + [chosen["unique_id"]])[-20:]))
        return chosen["file_id"]

    def stats(self) -> str:
        s = self.db.stickers()
        sets = self.db.sticker_sets()
        by = {k: sum(1 for x in s if k in x["tags"]) for k in SITUATIONS}
        return (f"Паков: {len(sets)}, стикеров: {len(s)}, разобрано Claude: {sum(1 for x in s if x['described'])}\n"
                + ", ".join(f"{k} {v}" for k, v in by.items()))
