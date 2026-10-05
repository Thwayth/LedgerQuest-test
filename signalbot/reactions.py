"""Реакции канала (в том числе закреплённые премиум-реакции, например симба): бот читает их из Telegram,
понимает по превью, что на них нарисовано, и зовёт подписчиков ставить именно их."""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time

from .emojipacks import tg_emoji
from .stickers import _to_png

log = logging.getLogger(__name__)
REFRESH_MS = 24 * 3_600_000
PLACEHOLDER = re.compile(r"\{R(\d+)\}")

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
        "index": {"type": "integer"}, "meaning": {"type": "string"}},
        "required": ["index", "meaning"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}


class ReactionDescriber:
    """Claude смотрит превью премиум-реакций и коротко говорит, что на них изображено и какое настроение."""

    def __init__(self, model: str = "claude-sonnet-5-5", effort: str = "low", client=None):
        self.model, self.effort, self._client = model, effort, client

    def _cl(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=120.0, max_retries=2)
        return self._client

    def describe(self, images: list[bytes]) -> dict[int, str] | None:
        content: list = [{"type": "text", "text": (
            "Это премиум-реакции крипто-канала (превью). Для каждой (индекс с нуля) коротко опиши, что изображено и какое настроение "
            "она выражает, чтобы подписчикам можно было сказать: «ставьте такую реакцию, если ...». До 50 символов, по-русски.")}]
        for i, raw in enumerate(images):
            content += [{"type": "text", "text": f"Реакция {i}:"},
                        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": base64.standard_b64encode(_to_png(raw)).decode()}}]
        try:
            resp = self._cl().messages.create(
                model=self.model, max_tokens=3000, messages=[{"role": "user", "content": content}],
                output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
            )
            if resp.stop_reason == "refusal":
                return None
            data = json.loads(next(b.text for b in resp.content if b.type == "text"))
            return {int(it["index"]): str(it["meaning"])[:60] for it in data["items"]}
        except Exception as e:
            log.warning("описание реакций Claude: %s", e)
            return None


async def refresh_reactions(bot, db, channel_id: str | int | None, describer: ReactionDescriber | None = None, force: bool = False) -> int:
    """Читает доступные реакции канала (раз в сутки). Возвращает число реакций."""
    if not channel_id or str(channel_id) in ("0", ""):
        return 0
    last = db.kv_get("reactions_ts")
    if not force and last and time.time() * 1000 - float(last) < REFRESH_MS and db.reactions():
        return len(db.reactions())
    cid = int(channel_id) if str(channel_id).lstrip("-").isdigit() else channel_id
    try:
        chat = await bot.get_chat(cid)
    except Exception as e:
        log.warning("не удалось прочитать реакции канала: %s", e)
        return 0
    avail = getattr(chat, "available_reactions", None)
    if not avail:
        log.info("в канале доступны все стандартные реакции (список не задан)")
        return 0
    customs = [r.custom_emoji_id for r in avail if getattr(r, "type", None) == "custom_emoji"]
    info = {}
    if customs:
        try:
            for s in await bot.get_custom_emoji_stickers(customs):
                info[s.custom_emoji_id] = s
        except Exception as e:
            log.warning("не удалось получить премиум-реакции: %s", e)
    items = []
    for r in avail:
        t = getattr(r, "type", None)
        if t == "emoji":
            items.append({"key": r.emoji, "kind": "emoji", "char": r.emoji, "description": f"реакция {r.emoji}", "described": True})
        elif t == "custom_emoji":
            s = info.get(r.custom_emoji_id)
            char = (s.emoji if s and s.emoji else "⭐")
            thumb = s.thumbnail.file_id if s is not None and getattr(s, "thumbnail", None) else None
            items.append({"key": r.custom_emoji_id, "kind": "custom", "char": char, "set_name": getattr(s, "set_name", None), "thumb_id": thumb})
            db.add_emoji(r.custom_emoji_id, char, getattr(s, "set_name", None))  # реакции канала тоже идут в пул эмодзи
            if getattr(s, "set_name", None):
                db.add_emoji_set(s.set_name)
    db.replace_reactions(items)
    db.kv_set("reactions_ts", str(int(time.time() * 1000)))
    pending = [x for x in db.reactions() if x["kind"] == "custom" and not x["described"]]
    if pending and describer is not None:
        images, keys = [], []
        for x in pending:
            if not x["thumb_id"]:
                continue
            try:
                images.append((await bot.download(x["thumb_id"])).read())
                keys.append(x["key"])
            except Exception as e:
                log.warning("превью реакции недоступно: %s", e)
        res = await asyncio.to_thread(describer.describe, images) if images else None
        for i, k in enumerate(keys):
            if res and i in res:
                db.set_reaction_description(k, res[i])
    for x in db.reactions():  # без Claude / без превью: описание по эмодзи
        if not x["described"] and not x["description"]:
            db.set_reaction_description(x["key"], f"реакция {x['char']}")
    log.info("реакции канала: %d (%d премиум)", len(items), len(customs))
    return len(items)


def reaction_slots(db, max_n: int = 4) -> list[dict]:
    """Слоты для текста: {R1}, {R2}... Премиум-реакции в приоритете. Каждый слот: meaning, html, plain."""
    rs = db.reactions()
    rs = [r for r in rs if r["kind"] == "custom"] + [r for r in rs if r["kind"] == "emoji"]
    out = []
    for n, r in enumerate(rs[:max_n], 1):
        html = tg_emoji(r["key"], r["char"]) if r["kind"] == "custom" else r["char"]
        out.append({"slot": n, "meaning": (r["description"] or f"реакция {r['char']}"), "html": html, "plain": r["char"]})
    return out


def fill_placeholders(text: str, slots: list[dict]) -> str:
    """{R1} -> эмодзи реакции (HTML). Незнакомые плейсхолдеры убираются."""
    by = {s["slot"]: s["html"] for s in slots}
    return PLACEHOLDER.sub(lambda m: by.get(int(m.group(1)), ""), text)


def placeholders_valid(text: str, slots: list[dict]) -> bool:
    return all(int(n) in {s["slot"] for s in slots} for n in PLACEHOLDER.findall(text))
