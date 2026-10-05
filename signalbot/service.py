"""Оркестрация: скан рынка -> публикация идей; трекинг -> апдейты; отчёты."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from .caption import build_caption, plain_summary, update_text
from .enrich import Context, MarketData
from .chart import ChartStyle, render_idea_chart
from .config import Config
from .exchanges import TF_LABEL_RU, TF_MS, Exchanges
from .lifecycle import Tracked, evaluate
from .morning import (KIND_MOOD, KIND_NEWS, KIND_TEASER, MARK, TEASER_MIN_CHANGE, MorningWriter, build_data,
                      build_morning_html, choose_kind, collect_snapshot, pick_emojis, reveal_html, template_text)
from .news import fetch_headlines
from .models import Idea
from .publisher import Publisher
from .scanner import analyze
from .scoring import context_points
from .stats import compute_stats, format_stats
from .storage import Storage
from .writer import PostWriter

log = logging.getLogger(__name__)
DAY_MS = 86_400_000
TRACK_TF = "1h"


def utc_day_start_ms(now_ms: int) -> int:
    return now_ms - now_ms % DAY_MS


class Service:
    def __init__(
        self, cfg: Config, ex: Exchanges, db: Storage, pub: Publisher, concurrency: int = 4,
        market: MarketData | None = None, writer: PostWriter | None = None,
    ):
        self.cfg, self.ex, self.db, self.pub = cfg, ex, db, pub
        self.market = market if market is not None else (MarketData(ex) if cfg.use_context else None)
        if writer is None and cfg.writer_enabled and PostWriter.available():
            writer = PostWriter(cfg.writer_model, cfg.writer_effort)
        self.writer = writer
        self.morning_writer = MorningWriter(cfg.writer_model, cfg.writer_effort) if (cfg.writer_enabled and PostWriter.available()) else None
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
            await pub.post_photo(png, await self._caption(idea, day), idea.ticker)
        return len(found)

    async def _caption(self, idea: Idea, day: str) -> str:
        """Текст поста: Claude (если есть ключ) или шаблоны; числа ТВХ/ТП/СЛ всегда считает бот."""
        text = None
        if self.writer is not None:
            recent = [t[:160] for t in self.db.recent_posts(12) if not t.startswith(MARK)][-5:]
            text = await asyncio.to_thread(self.writer.write, idea, day, recent)
        return build_caption(idea, day, self.cfg.disclaimer, text)

    def _style(self) -> ChartStyle:
        return ChartStyle(
            watermark_text=self.cfg.watermark_text, watermark_image=self.cfg.watermark_image, mascot_image=self.cfg.mascot_image
        )

    async def publish_idea(self, idea: Idea, df) -> Tracked:
        label = TF_LABEL_RU.get(idea.timeframe, idea.timeframe)
        png = await asyncio.to_thread(render_idea_chart, df, idea, self._style(), label)
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        caption = await self._caption(idea, day)
        chat_id, message_id = await self.pub.post_photo(png, caption, idea.ticker)
        self.db.add_post(int(time.time() * 1000), plain_summary(caption))
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

    # --- утренний пост ---------------------------------------------------------------
    async def morning_post(self, pub: Publisher | None = None, remember: bool = True) -> bool:
        """Утреннее приветствие с новостью/заходом на реакции. remember=False — пробный показ без состояния."""
        pub = pub or self.pub
        now = datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        snap = await asyncio.to_thread(collect_snapshot, self.ex, self.cfg.exchanges, self.cfg.min_quote_volume_usd)
        heads = await asyncio.to_thread(fetch_headlines, self.cfg.news_feeds) if self.cfg.news_feeds else []
        market = self.market or MarketData(self.ex)
        fng = await asyncio.to_thread(market.fear_greed)
        mover = snap.gainers[0] if snap and snap.gainers and snap.gainers[0][1] >= TEASER_MIN_CHANGE else None
        kind = choose_kind(day, self.db.kv_get("last_morning_kind"), bool(heads), bool(mover))
        reveal_msk = f"{(self.cfg.reveal_hour_utc + 3) % 24:02d}:00"
        data = build_data(kind, snap, heads, fng, reveal_msk)
        hidden = mover[0] if (kind == KIND_TEASER and mover) else None
        recent = [t[len(MARK):][:200] for t in self.db.recent_posts(30) if t.startswith(MARK)][-4:]
        text = None
        if self.morning_writer is not None:
            text = await asyncio.to_thread(self.morning_writer.write, data, recent, hidden)
        if text is None:  # нет ключа / сбой Claude: шаблоны (новости без Claude не пересказать — тогда настроение рынка)
            if kind == KIND_NEWS:
                kind = KIND_MOOD
                data = build_data(kind, snap, heads, fng, reveal_msk)
            text = template_text(kind, data, day, self.cfg.audience)
        emojis = pick_emojis(self.db.emojis(), 3, day)
        chat_id, message_id = await pub.post_text(build_morning_html(text, emojis))
        log.info("утренний пост отправлен (%s)", kind)
        if remember:
            ts = int(now.timestamp() * 1000)
            self.db.kv_set("last_morning", day)
            self.db.kv_set("last_morning_kind", kind)
            self.db.add_post(ts, MARK + f"{text.greeting} {text.body} {text.hook}")
            if kind == KIND_TEASER and mover:
                due = ts + max(1, (self.cfg.reveal_hour_utc - self.cfg.morning_hour_utc) % 24) * 3_600_000
                self.db.add_reveal(chat_id, message_id, reveal_html(mover[0], mover[1], day, pick_emojis(self.db.emojis(), 1, day + "r")[0]), due)
        return True

    async def send_due_reveals(self) -> int:
        n = 0
        for rid, chat_id, message_id, text in self.db.due_reveals(int(time.time() * 1000)):
            try:
                await self.pub.reply(chat_id, message_id, text)
                self.db.mark_reveal_sent(rid)
                n += 1
            except Exception:
                log.exception("не удалось отправить разгадку %s", rid)
        return n

    # --- отчёты ----------------------------------------------------------------
    def stats_text(self, days: int | None = None) -> str:
        if days is None:
            return format_stats(compute_stats(self.db.all()), "📊 Статистика за всё время")
        since = int(time.time() * 1000) - days * DAY_MS
        items = [t for t in self.db.all() if (t.closed_ts or 0) >= since or t.status in ("WATCH", "BROKEN")]
        return format_stats(compute_stats(items), f"📊 Итоги за {days} дн.")

    async def weekly_report(self) -> None:
        await self.pub.send(self.cfg.publish_target, self.stats_text(7))
