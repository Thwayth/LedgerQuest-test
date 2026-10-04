from __future__ import annotations

import asyncio
import logging
import sys

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from .bot import build_bot, build_dispatcher
from .config import load_config
from .exchanges import Exchanges
from .publisher import TelegramPublisher
from .service import Service
from .storage import Storage

log = logging.getLogger("signalbot")


async def run() -> None:
    cfg = load_config()
    bot = build_bot(cfg)
    svc = Service(cfg, Exchanges(cfg.exchanges), Storage(cfg.db_path), TelegramPublisher(bot, cfg.publish_target))

    sched = AsyncIOScheduler(timezone="UTC")
    common = dict(max_instances=1, coalesce=True, misfire_grace_time=300)
    sched.add_job(svc.scan_once, "interval", minutes=cfg.scan_interval_minutes, id="scan", **common)
    sched.add_job(svc.track_once, "interval", minutes=cfg.track_interval_minutes, id="track", **common)
    sched.add_job(
        svc.weekly_report,
        CronTrigger(day_of_week=cfg.weekly_report_weekday, hour=cfg.weekly_report_hour_utc, minute=0),
        id="weekly", **common,
    )
    sched.start()
    log.info("старт: dry_run=%s, цель=%s", cfg.dry_run, cfg.publish_target)
    asyncio.create_task(svc.scan_once())  # первый проход сразу после старта

    dp = build_dispatcher(cfg, svc)
    try:
        await dp.start_polling(bot)
    finally:
        sched.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    while True:  # самовосстановление при неожиданных падениях (поверх restart-политики Docker)
        try:
            asyncio.run(run())
            return
        except KeyboardInterrupt:
            sys.exit(0)
        except RuntimeError as e:  # ошибки конфигурации не лечатся перезапуском
            log.error("%s", e)
            sys.exit(1)
        except Exception:
            log.exception("падение, перезапуск через 15с")
            import time

            time.sleep(15)


if __name__ == "__main__":
    main()
