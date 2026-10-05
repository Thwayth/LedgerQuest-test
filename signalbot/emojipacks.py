"""Паки премиум-эмодзи (TON, picardia, симба и др.): подтягиваем целиком и вставляем в посты по смыслу."""
from __future__ import annotations

import logging
import random
import re

log = logging.getLogger(__name__)

_TAG = re.compile(r"(<[^>]*>)")
# обычные эмодзи: основные блоки Unicode + частые одиночные символы
_EMOJI = re.compile("([\U0001F300-\U0001FAFF☀-➿⭐⬆⬇⬛⬜✅❌✨❗❓]️?)")


def tg_emoji(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def decorate(html: str, emoji_map: dict[str, list[str]], rng: random.Random | None = None, p: float = 0.85) -> str:
    """Заменяет обычные эмодзи в тексте поста (вне тегов) на премиум-эмодзи из ваших паков.

    Сначала подбор по значению (🔥 -> премиум-🔥 из пака, если такой есть). Если ни одной замены не вышло, а эмодзи
    в тексте есть, первое из них заменяется случайным эмодзи из пака, чтобы паки были в каждом посте.
    Исходный символ остаётся запасным: его увидят пользователи без Premium.
    """
    if not emoji_map:
        return html
    rng = rng or random.Random()
    parts = _TAG.split(html)
    done = html.count("<tg-emoji")  # уже стоящие премиум-эмодзи считаются: паки в посте уже есть
    in_special = False  # внутри <tg-emoji>...</tg-emoji> ничего не заменяем
    for i, part in enumerate(parts):
        if part.startswith("<"):
            if part.startswith("<tg-emoji"):
                in_special = True
            elif part.startswith("</tg-emoji"):
                in_special = False
            continue
        if in_special:
            continue

        def sub(m: re.Match) -> str:
            nonlocal done
            ch = m.group(1)
            ids = emoji_map.get(ch.replace("️", ""))
            if ids and rng.random() < p:
                done += 1
                return tg_emoji(rng.choice(ids), ch)
            return ch

        parts[i] = _EMOJI.sub(sub, part)
    if done == 0:
        all_ids = [i for ids in emoji_map.values() for i in ids]
        in_special = False
        for i, part in enumerate(parts):
            if part.startswith("<"):
                in_special = part.startswith("<tg-emoji") or (in_special and not part.startswith("</tg-emoji"))
                continue
            if in_special:
                continue
            m = _EMOJI.search(part)
            if m and all_ids:
                parts[i] = part[: m.start()] + tg_emoji(rng.choice(all_ids), m.group(1)) + part[m.end():]
                break
    return "".join(parts)


async def sync_emoji(bot, db) -> dict:
    """Узнаёт паки по присланным эмодзи и скачивает паки целиком."""
    resolved = packs = 0
    ids = db.unresolved_emoji_ids(200)
    if ids:
        try:
            stickers = await bot.get_custom_emoji_stickers(ids)
        except Exception as e:
            log.warning("не удалось определить паки эмодзи: %s", e)
            stickers = []
        seen = set()
        for s in stickers:
            seen.add(s.custom_emoji_id)
            name = s.set_name or ""
            db.set_emoji_set(s.custom_emoji_id, name)
            if name:
                db.add_emoji_set(name)
            resolved += 1
        for i in ids:
            if i not in seen:
                db.set_emoji_set(i, "")  # не нашли: больше не спрашиваем
    for name in db.emoji_sets(only_unsynced=True):
        try:
            st = await bot.get_sticker_set(name)
        except Exception as e:
            log.warning("пак эмодзи %s недоступен: %s", name, e)
            continue
        n = 0
        for s in st.stickers:
            if getattr(s, "custom_emoji_id", None):
                db.add_emoji(s.custom_emoji_id, s.emoji or "", name)
                n += 1
        db.mark_emoji_set_synced(name, st.title)
        packs += 1
        log.info("пак премиум-эмодзи %s: %d шт.", name, n)
    return {"resolved": resolved, "packs": packs}
