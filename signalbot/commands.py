"""Ответы на команды в режиме GitHub Actions: бот не висит постоянно, а читает накопившиеся
сообщения при каждом запуске (раз в 30 минут) и отвечает пачкой."""
from __future__ import annotations

import logging
from html import escape

from aiogram.exceptions import TelegramConflictError
from aiogram.enums import MessageEntityType

from .config import Config
from .stickers import emoji_tags

log = logging.getLogger(__name__)


def _is_our_channel(chat, cfg: Config) -> bool:
    cid = (cfg.channel_id or "").strip()
    return bool(cid) and (str(chat.id) == cid or (getattr(chat, "username", None) and f"@{chat.username}".lower() == cid.lower()))


def _remember_sticker(svc, st) -> str:
    kind = str(getattr(st.type, "value", st.type))
    if kind == "custom_emoji":  # премиум-эмодзи: запоминаем пак целиком
        if getattr(st, "custom_emoji_id", None):
            svc.db.add_emoji(st.custom_emoji_id, st.emoji or "", getattr(st, "set_name", None))
        if getattr(st, "set_name", None) and svc.db.add_emoji_set(st.set_name):
            return f"Запомнил пак премиум-эмодзи «{st.set_name}». Подтяну все его эмодзи при ближайшем запуске и буду вставлять их в посты."
        return "Этот пак премиум-эмодзи уже в моей коллекции." if getattr(st, "set_name", None) else "Это эмодзи без пака, запомнил только его."
    if kind != "regular" or not getattr(st, "set_name", None):
        return "Это не обычный стикер из пака. Пришлите стикер из любого стикерпака или премиум-эмодзи."
    new_set = svc.db.add_sticker_set(st.set_name)
    thumb = st.thumbnail.file_id if getattr(st, "thumbnail", None) else None
    svc.db.upsert_sticker(st.file_unique_id, st.file_id, st.set_name, st.emoji, emoji_tags(st.emoji), thumb)
    if new_set:
        return f"Запомнил стикерпак «{st.set_name}». Разберу все его стикеры при ближайшем запуске и буду ставить подходящие по ситуации."
    return f"Пак «{st.set_name}» уже в моей коллекции."


def collect_custom_emoji(m) -> list[tuple[str, str]]:
    """Премиум-эмодзи из сообщения: [(id, обычный символ)]."""
    out = []
    for ents, text in ((getattr(m, "entities", None), getattr(m, "text", None)), (getattr(m, "caption_entities", None), getattr(m, "caption", None))):
        for e in ents or []:
            if e.type == MessageEntityType.CUSTOM_EMOJI and e.custom_emoji_id and text:
                out.append((e.custom_emoji_id, e.extract_from(text)))
    return out


async def handle_updates(bot, cfg: Config, svc) -> bool:
    """Обрабатывает ожидающие команды. Возвращает True, если владелец попросил /scan."""
    try:
        updates = await bot.get_updates(timeout=0, allowed_updates=["message", "channel_post"])
    except TelegramConflictError as e:
        log.warning("getUpdates недоступен (у токена настроен webhook или бот запущен ещё где-то): %s", e)
        return False
    want_scan = False
    for u in updates:
        post = getattr(u, "channel_post", None)
        if post is not None and _is_our_channel(post.chat, cfg):  # премиум-эмодзи из постов канала
            for eid, ch in collect_custom_emoji(post):
                if svc.db.add_emoji(eid, ch):
                    log.info("запомнил эмодзи %s из канала", ch)
            continue
        m = u.message
        if not m or m.chat.type != "private":
            continue
        is_owner_chat = cfg.owner_id is not None and m.chat.id == cfg.owner_id
        if is_owner_chat:  # владелец прислал сообщение с премиум-эмодзи: запоминаем для утренних постов
            new = [(eid, ch) for eid, ch in collect_custom_emoji(m) if svc.db.add_emoji(eid, ch)]
            if new:
                try:
                    await m.answer(f"Запомнил премиум-эмодзи: {' '.join(ch for _, ch in new)} (новых: {len(new)}). Буду использовать их в утренних постах.")
                except Exception:
                    log.exception("не удалось подтвердить эмодзи")
        st = getattr(m, "sticker", None)
        if is_owner_chat and st is not None:  # владелец прислал стикер: запоминаем весь пак
            try:
                await m.answer(_remember_sticker(svc, st))
            except Exception:
                log.exception("не удалось обработать стикер")
            continue
        if not m.text or not m.text.startswith("/"):
            continue
        cmd = m.text.split()[0].split("@")[0].lower()
        is_owner = cfg.owner_id is not None and m.chat.id == cfg.owner_id
        try:
            if cmd == "/start":
                note = (
                    "Вы владелец: идеи в тестовом режиме приходят сюда."
                    if is_owner
                    else "Этот chat id не совпадает с OWNER_ID в секретах GitHub. Если вы владелец, обновите OWNER_ID."
                )
                await m.answer(
                    f"Привет! Ваш chat id: <code>{m.chat.id}</code>\n{note}\n\n"
                    "Бот работает по расписанию (раз в 30 минут), поэтому отвечает с задержкой.\n"
                    "/stats · /active · /scan",
                    parse_mode="HTML",
                )
            elif not is_owner:
                continue
            elif cmd == "/stats":
                await m.answer(svc.stats_text())
            elif cmd == "/stickers":
                await m.answer(svc.stickers.stats())
            elif cmd == "/active":
                items = svc.db.active()
                text = "\n".join(
                    f"{escape(t.symbol.split('/')[0])} {t.side.value} · {t.status} · TP {t.tp_hit}/{len(t.pools)}" for t in items
                ) or "Активных идей нет."
                await m.answer(text)
            elif cmd == "/scan":
                want_scan = True
                await m.answer("Принял, сканирую рынок в этом запуске.")
        except Exception:
            log.exception("не удалось ответить на %s", cmd)
    if updates:  # подтверждаем обработанные, чтобы не отвечать повторно
        await bot.get_updates(offset=updates[-1].update_id + 1, limit=1, timeout=0, allowed_updates=["message"])
    return want_scan
