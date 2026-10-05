"""Ответы на команды в режиме GitHub Actions: бот не висит постоянно, а читает накопившиеся
сообщения при каждом запуске (раз в 30 минут) и отвечает пачкой."""
from __future__ import annotations

import logging
from html import escape

from aiogram.exceptions import TelegramConflictError

from .config import Config

log = logging.getLogger(__name__)


async def handle_updates(bot, cfg: Config, svc) -> bool:
    """Обрабатывает ожидающие команды. Возвращает True, если владелец попросил /scan."""
    try:
        updates = await bot.get_updates(timeout=0, allowed_updates=["message"])
    except TelegramConflictError as e:
        log.warning("getUpdates недоступен (у токена настроен webhook или бот запущен ещё где-то): %s", e)
        return False
    want_scan = False
    for u in updates:
        m = u.message
        if not m or not m.text or not m.text.startswith("/") or m.chat.type != "private":
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
