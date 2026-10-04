"""aiogram: команды владельца (/stats, /active, /scan) и сборка бота."""
from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message

from .config import Config
from .service import Service

log = logging.getLogger(__name__)


def build_dispatcher(cfg: Config, svc: Service) -> Dispatcher:
    dp = Dispatcher()
    owner_only = F.from_user.id == (cfg.owner_id or -1)

    @dp.message(Command("start"), owner_only)
    async def start(m: Message) -> None:
        mode = "DRY_RUN (посты приходят вам в личку)" if cfg.dry_run else "боевой режим (посты идут в канал)"
        await m.answer(f"Бот запущен: {mode}.\n/stats — статистика\n/active — идеи в работе\n/scan — скан сейчас")

    @dp.message(Command("stats"), owner_only)
    async def stats(m: Message) -> None:
        await m.answer(svc.stats_text())

    @dp.message(Command("active"), owner_only)
    async def active(m: Message) -> None:
        items = svc.db.active()
        if not items:
            await m.answer("Активных идей нет.")
            return
        lines = [f"{t.symbol.split('/')[0]} {t.side.value} · {t.status} · TP {t.tp_hit}/{len(t.pools)}" for t in items]
        await m.answer("\n".join(lines))

    @dp.message(Command("scan"), owner_only)
    async def scan(m: Message) -> None:
        await m.answer("Сканирую рынок…")
        published = await svc.scan_once()
        await m.answer(f"Опубликовано идей: {len(published)}")

    return dp


def build_bot(cfg: Config) -> Bot:
    if not cfg.bot_token:
        raise RuntimeError("BOT_TOKEN не задан в .env")
    return Bot(cfg.bot_token)
