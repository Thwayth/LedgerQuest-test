"""Оркестрация: скан рынка -> публикация идей; трекинг -> апдейты; отчёты."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .caption import build_caption, update_text
from .enrich import Context, MarketData
from .chart import ChartStyle, render_idea_chart
from .config import Config
from .exchanges import TF_LABEL_RU, TF_MS, Exchanges
from .lifecycle import Tracked, evaluate
from .models import Idea
from .publisher import Publisher
from .scanner import analyze
from .scoring import context_points
from .stats import compute_stats, format_stats
from .storage import Storage

log = logging.getLogger(__name__)
DAY_MS = 86_400_000
TRACK_TF = "1h"


def utc_day_start_ms(now_ms: int) -> int:
    return now_ms - now_ms % DAY_MS


class Service:
    def __init__(
        self, cfg: Config, ex: Exchanges, db: Storage, pub: Publisher, concurrency: int = 4, market: MarketData | None = None
    ):
        self.cfg, self.ex, self.db, self.pub = cfg, ex, db, pub
        self.market = market if market is not None else (MarketData(ex) if cfg.use_context else None)
        self._sem = asyncio.Semaphore(concurrency)
        self._scan_lock = asyncio.Lock()
        self._track_lock = asyncio.Lock()

    # --- скан ---------------------------------------------------------------
    async def _analyze_market(self, m) -> tuple[Idea, object] | None:
        async with self._sem:
            try:
                df = await self.ex.aohlcv(m.exchange, m.symbol, self.cfg.timeframe, self.cfg.history_bars)
            except Exception as e:
                log.warning("candles %s %s: %s", m.exchange, m.symbol, e)
                return None
        idea = analyze(df, m.symbol, m.exchange, self.cfg.timeframe, self.cfg.strategy)
        return (idea, df) if idea else None

    async def scan_once(self) -> list[Tracked]:
        """Один проход по рынку. Возвращает опубликованные идеи."""
        if self._scan_lock.locked():
            log.info("scan уже идёт, пропускаю")
            return []
        async with self._scan_lock:
            now = int(time.time() * 1000)
            left = self.cfg.max_ideas_per_day - self.db.count_created_since(utc_day_start_ms(now))
            if left <= 0:
                log.info("дневной лимит идей исчерпан")
                return []
            blocked = self.db.blocked_symbols(now - self.cfg.cooldown_days * DAY_MS)
            found = await self._ranked(blocked, keep=left)
            published: list[Tracked] = []
            for idea, df in found[:left]:
                try:
                    published.append(await self.publish_idea(idea, df))
                except Exception:
                    log.exception("не удалось опубликовать %s", idea.symbol)
            return published

    async def _ranked(self, blocked: set[str], keep: int) -> list[tuple[Idea, object]]:
        """Вселенная -> анализ -> поправка по контексту -> лучшие идеи по убыванию скоринга."""
        markets = await self.ex.auniverse(self.cfg.min_quote_volume_usd, self.cfg.max_symbols, self.cfg.exclude_bases)
        markets = [m for m in markets if m.symbol not in blocked]
        log.info("вселенная: %d монет", len(markets))
        results = await asyncio.gather(*(self._analyze_market(m) for m in markets))
        found = sorted((r for r in results if r), key=lambda r: r[0].score, reverse=True)
        log.info("найдено идей: %d", len(found))
        if self.market is not None and found:
            found = await self._apply_context(found, top=max(6, keep * 2))
        return found[:keep]

    async def _apply_context(self, found, top: int):
        head, tail = found[:top], found[top:]
        try:
            glob = await asyncio.to_thread(self.market.global_context, head[0][0].exchange)
        except Exception as e:
            log.warning("глобальный контекст: %s", e)
            return found

        async def one(item):
            idea, df = item
            try:
                sym = await asyncio.to_thread(self.market.symbol_context, idea.exchange, idea.symbol)
            except Exception as e:
                log.warning("контекст %s: %s", idea.symbol, e)
                return item
            pts, notes = context_points(idea.side, glob, sym)
            ctx = Context(glob=glob, sym=sym, points=pts, notes=notes)
            idea.extra["context"] = ctx
            idea.score = round(min(100.0, max(0.0, idea.score + pts)), 1)
            log.info("контекст %s %s: %+.0f → score %.1f | %s", idea.side.value, idea.symbol, pts, idea.score, "; ".join(notes) or "нейтрально")
            return item

        head = list(await asyncio.gather(*(one(i) for i in head)))
        head = [(i, d) for i, d in head if i.score >= self.cfg.strategy.min_score]
        return sorted(head + tail, key=lambda r: r[0].score, reverse=True)

    async def preview(self, pub: Publisher, n: int = 2) -> int:
        """Примеры постов только владельцу: без записи в базу, без дневного лимита и пауз по монетам."""
        found = await self._ranked(set(), keep=n)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for idea, df in found:
            png = await asyncio.to_thread(render_idea_chart, df, idea, self._style(), TF_LABEL_RU.get(idea.timeframe, idea.timeframe))
            await pub.post_photo(png, build_caption(idea, day, self.cfg.disclaimer), idea.ticker)
        return len(found)

    def _style(self) -> ChartStyle:
        return ChartStyle(
            watermark_text=self.cfg.watermark_text, watermark_image=self.cfg.watermark_image, mascot_image=self.cfg.mascot_image
        )

    async def publish_idea(self, idea: Idea, df) -> Tracked:
        label = TF_LABEL_RU.get(idea.timeframe, idea.timeframe)
        png = await asyncio.to_thread(render_idea_chart, df, idea, self._style(), label)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        caption = build_caption(idea, day, self.cfg.disclaimer)
        chat_id, message_id = await self.pub.post_photo(png, caption, idea.ticker)
        t = Tracked(
            symbol=idea.symbol, exchange=idea.exchange, timeframe=idea.timeframe, side=idea.side,
            zone_low=idea.zone.low, zone_high=idea.zone.high, pools=[p.price for p in idea.pools],
            entry_cons=idea.entry_conservative, entry_aggr=idea.entry_aggressive,
            invalidation=idea.invalidation, created_ts=int(time.time() * 1000), score=idea.score,
            chat_id=chat_id, message_id=message_id,
        )
        self.db.add(t)
        log.info("опубликовано %s %s score=%.1f", idea.side.value, idea.symbol, idea.score)
        return t

    # --- трекинг -------------------------------------------------------------
    async def track_once(self) -> int:
        if self._track_lock.locked():
            return 0
        async with self._track_lock:
            n_events = 0
            for t in self.db.active():
                try:
                    n_events += await self._track_one(t)
                except Exception:
                    log.exception("трекинг %s", t.symbol)
            return n_events

    async def _track_one(self, t: Tracked) -> int:
        now = int(time.time() * 1000)
        since = t.created_ts - TF_MS[TRACK_TF]
        intraday = await self.ex.aohlcv(t.exchange, t.symbol, TRACK_TF, 0, since_ms=since)
        daily = await self.ex.aohlcv(t.exchange, t.symbol, t.timeframe, 30)
        events = evaluate(
            t, intraday, daily, now, TF_MS[TRACK_TF], TF_MS[t.timeframe], self.cfg.max_wait_days * DAY_MS
        )
        self.db.save(t)  # состояние сохраняем до отправки: события не дублируются при сбое сети
        for ev in events:
            if t.chat_id and t.message_id:
                try:
                    await self.pub.reply(t.chat_id, t.message_id, update_text(t, ev))
                except Exception:
                    log.exception("не удалось отправить апдейт %s %s", t.symbol, ev.kind)
        return len(events)

    # --- отчёты ----------------------------------------------------------------
    def stats_text(self, days: int | None = None) -> str:
        if days is None:
            return format_stats(compute_stats(self.db.all()), "📊 Статистика за всё время")
        since = int(time.time() * 1000) - days * DAY_MS
        items = [t for t in self.db.all() if (t.closed_ts or 0) >= since or t.status in ("WATCH", "BROKEN")]
        return format_stats(compute_stats(items), f"📊 Итоги за {days} дн.")

    async def weekly_report(self) -> None:
        await self.pub.send(self.cfg.publish_target, self.stats_text(7))
