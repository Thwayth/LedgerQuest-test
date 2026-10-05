"""Одноразовый запуск для GitHub Actions (без постоянного процесса и polling).

    python -m signalbot.oneshot auto     # трекинг + скан/отчёт, если подошло время
    python -m signalbot.oneshot scan     # принудительный скан
    python -m signalbot.oneshot track | report
    python -m signalbot.oneshot getid    # показать id чатов/каналов, где бот видел сообщения
    python -m signalbot.oneshot preview  # 2 примера постов только владельцу (без базы и лимитов)
    python -m signalbot.oneshot reset    # очистить идеи в базе (перед боевым запуском после тестов)
    python -m signalbot.oneshot morning  # утренний пост сейчас (в канал / в личку при DRY_RUN)
    python -m signalbot.oneshot morning-preview  # пробный утренний пост только владельцу
    python -m signalbot.oneshot fun      # мем/шутка прямо сейчас
    python -m signalbot.oneshot fun-preview  # пробные мем, факт и шутка только владельцу

Расписание Actions гоняет `auto` каждые RUN_EVERY_MIN минут; скан и недельный отчёт
выбираются по времени UTC из config.yaml. Команды бота (/scan, /stats) в этом режиме не работают.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from .bot import build_bot
from .commands import handle_updates
from .config import Config, load_config
from .exchanges import Exchanges
from .publisher import TelegramPublisher
from .service import Service
from .storage import Storage

log = logging.getLogger("signalbot.oneshot")
RUN_EVERY_MIN = 30  # частота cron в workflow
MODES = ("auto", "scan", "track", "report", "getid", "preview", "reset", "morning", "morning-preview", "fun", "fun-preview")
_WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def scan_due(now: datetime, interval_min: int, window_min: int = RUN_EVERY_MIN) -> bool:
    """Скан раз в interval_min минут: срабатывает в первом окне каждого интервала (от полуночи UTC)."""
    minutes = now.hour * 60 + now.minute
    return minutes % max(interval_min, window_min) < window_min


def morning_due(now: datetime, hour_utc: int, last_day: str | None, window_h: int = 6) -> bool:
    """Утренний пост: с нужного часа и ещё window_h часов (на случай опоздания расписания GitHub), раз в сутки."""
    return hour_utc <= now.hour < hour_utc + window_h and last_day != now.strftime("%Y-%m-%d")


def fun_due(now: datetime, hour_utc: int, minute: int, last_day: str | None, window_h: int = 4) -> bool:
    """Слот ленты (мем/факт/шутка): с назначенного времени и ещё window_h часов, раз в сутки."""
    start = now.replace(hour=hour_utc, minute=minute, second=0, microsecond=0)
    return start <= now < start + timedelta(hours=window_h) and last_day != now.strftime("%Y-%m-%d")


def report_due(now: datetime, weekday: str, hour: int, window_min: int = RUN_EVERY_MIN) -> bool:
    return _WEEKDAYS[now.weekday()] == weekday.lower()[:3] and now.hour == hour and now.minute < window_min


async def _getid(cfg: Config) -> None:
    bot = build_bot(cfg)
    try:
        updates = await bot.get_updates(timeout=0)
        seen: dict[int, str] = {}
        for u in updates:
            m = u.channel_post or u.message or u.edited_channel_post or u.edited_message
            chat = m.chat if m else (u.my_chat_member.chat if u.my_chat_member else None)
            if chat:
                seen[chat.id] = f"{chat.type}: {chat.title or chat.username or chat.first_name}"
        if not seen:
            print("Бот пока не видел сообщений. Опубликуйте любой пост в канале (бот должен быть админом) и запустите снова.")
        for cid, desc in seen.items():
            print(f"CHAT_ID={cid}   ({desc})")
    finally:
        await bot.session.close()


async def run(mode: str, now: datetime | None = None) -> None:
    cfg = load_config(env_file=None)
    if mode == "getid":
        await _getid(cfg)
        return
    now = now or datetime.now(timezone.utc)
    bot = build_bot(cfg)
    try:
        svc = Service(cfg, Exchanges(cfg.exchanges), Storage(cfg.db_path), TelegramPublisher(bot, cfg.publish_target))
        if mode == "reset":
            log.info("reset: удалено идей %d", svc.db.clear_ideas())
            return
        # сообщения владельца (команды, премиум-эмодзи) читаем при каждом запуске, в том числе перед утренним постом
        force_scan = await handle_updates(bot, cfg, svc) if mode != "report" else False
        if mode == "morning-preview":
            await svc.morning_post(TelegramPublisher(bot, cfg.owner_id), remember=False)
            return
        if mode == "fun-preview":
            await svc.fun_preview(TelegramPublisher(bot, cfg.owner_id))
            return
        if mode == "fun":
            await svc.fun_post("mix")
            return
        if mode == "morning":
            await svc.morning_post()
            return
        if mode == "preview":
            n = await svc.preview(TelegramPublisher(bot, cfg.owner_id), n=2)
            log.info("preview: отправлено %d", n)
            return
        auto = mode == "auto"
        if auto:
            if cfg.morning_enabled and morning_due(now, cfg.morning_hour_utc, svc.db.kv_get("last_morning")):
                try:
                    await svc.morning_post()
                except Exception:
                    log.exception("утренний пост не удался")
            if cfg.fun_enabled:
                today = now.strftime("%Y-%m-%d")
                for h, m, kind in cfg.fun_slots:  # не больше одного поста ленты за запуск
                    key = f"fun:{h:02d}:{m:02d}"
                    if fun_due(now, h, m, svc.db.kv_get(key)):
                        try:
                            await svc.fun_post(kind)
                            svc.db.kv_set(key, today)
                        except Exception:
                            log.exception("пост ленты (%s) не удался", kind)
                        break
            n_rev = await svc.send_due_reveals()
            if n_rev:
                log.info("разгадок отправлено: %d", n_rev)
        if mode in ("auto", "track"):
            n = await svc.track_once()
            log.info("трекинг: событий %d", n)
        if mode == "scan" or force_scan or (auto and scan_due(now, cfg.scan_interval_minutes)):
            published = await svc.scan_once()
            log.info("скан: опубликовано %d", len(published))
        if mode == "report" or (auto and report_due(now, cfg.weekly_report_weekday, cfg.weekly_report_hour_utc)):
            await svc.weekly_report()
    finally:
        await bot.session.close()


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mode = (argv if argv is not None else sys.argv[1:] or ["auto"])[0]
    if mode not in MODES:
        print(f"Режим должен быть одним из: {', '.join(MODES)}")
        return 2
    try:
        asyncio.run(run(mode))
    except RuntimeError as e:
        log.error("%s", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
