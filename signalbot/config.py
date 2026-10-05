from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from dotenv import load_dotenv


@dataclass
class StrategyParams:
    max_distance_pct: float = 10.0
    zone_tol_atr: float = 1.0
    zone_min_touches: int = 2
    zone_min_height_pct: float = 8.0
    pools_count: int = 2
    min_rr: float = 2.0
    min_score: float = 55.0


@dataclass
class Config:
    bot_token: str = ""
    channel_id: str = ""
    owner_id: int | None = None
    dry_run: bool = True

    exchanges: list[str] = field(default_factory=lambda: ["binance", "bybit", "okx"])
    timeframe: str = "1d"
    history_bars: int = 365

    min_quote_volume_usd: float = 20_000_000
    max_symbols: int = 150
    exclude_bases: list[str] = field(default_factory=list)

    strategy: StrategyParams = field(default_factory=StrategyParams)

    scan_interval_minutes: int = 240
    track_interval_minutes: int = 15
    max_ideas_per_day: int = 3
    cooldown_days: int = 7
    max_wait_days: int = 10
    weekly_report_weekday: str = "mon"
    weekly_report_hour_utc: int = 9

    risk_per_trade_pct: float = 1.0
    disclaimer: bool = False
    use_context: bool = True
    writer_enabled: bool = True
    stickers_enabled: bool = True
    sticker_chance: dict = field(default_factory=dict)
    fun_enabled: bool = True
    fun_model: str = "claude-sonnet-5-5"
    fun_slots: list[tuple[int, int, str]] = field(default_factory=lambda: [(10, 0, "meme"), (14, 30, "fact"), (18, 0, "mix")])
    memes_dir: str = "assets/memes"
    fun_trends: bool = True
    fun_topics: list[str] = field(default_factory=list)
    morning_enabled: bool = True
    morning_hour_utc: int = 6
    reveal_hour_utc: int = 9
    audience: list[str] = field(default_factory=lambda: ["Прайд", "львы", "ребята", "команда", "народ"])
    news_feeds: list[str] = field(default_factory=list)
    writer_model: str = "claude-opus-5-5"
    writer_effort: str = "low"
    mascot_image: str | None = None
    watermark_text: str = ""
    watermark_image: str | None = None
    db_path: str = "data/signalbot.sqlite3"

    @property
    def publish_target(self) -> str | int:
        """Куда публикуем: в DRY_RUN только владельцу в личку."""
        if self.dry_run:
            if self.owner_id is None:
                raise RuntimeError("DRY_RUN=true требует OWNER_ID в .env")
            return self.owner_id
        if not self.channel_id:
            raise RuntimeError("Для боевого режима задайте CHANNEL_ID в .env")
        return int(self.channel_id) if self.channel_id.lstrip("-").isdigit() else self.channel_id


def _bool(v: str | None, default: bool) -> bool:
    if v is None or v == "":
        return default
    return v.strip().lower() in {"1", "true", "yes", "y", "on"}


def _slots(raw) -> list[tuple[int, int, str]]:
    """[{time_msk: "13:00", kind: meme}] -> [(час UTC, минута, тип)]; МСК = UTC+3."""
    if not raw:
        return [(10, 0, "meme"), (14, 30, "fact"), (18, 0, "mix")]
    out = []
    for s in raw:
        h, m = (int(x) for x in str(s["time_msk"]).split(":"))
        kind = str(s.get("kind", "mix"))
        if kind not in ("meme", "fact", "joke", "mix"):
            raise ValueError(f"fun.slots: неизвестный тип {kind!r}")
        out.append(((h - 3) % 24, m, kind))
    return out


def load_config(path: str | Path = "config.yaml", env_file: str | Path | None = ".env") -> Config:
    if env_file:
        load_dotenv(env_file)
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    u, s, sc = raw.get("universe", {}), raw.get("strategy", {}), raw.get("schedule", {})
    c = raw.get("chart", {})
    owner = os.getenv("OWNER_ID", "").strip()
    cfg = Config(
        bot_token=os.getenv("BOT_TOKEN", ""),
        channel_id=os.getenv("CHANNEL_ID", "").strip(),
        owner_id=int(owner) if owner else None,
        dry_run=_bool(os.getenv("DRY_RUN"), True),
        exchanges=raw.get("exchanges", ["binance", "bybit", "okx"]),
        timeframe=raw.get("timeframe", "1d"),
        history_bars=int(raw.get("history_bars", 365)),
        min_quote_volume_usd=float(u.get("min_quote_volume_usd", 20_000_000)),
        max_symbols=int(u.get("max_symbols", 150)),
        exclude_bases=[x.upper() for x in u.get("exclude_bases", [])],
        strategy=StrategyParams(**{k: v for k, v in s.items() if k in StrategyParams.__dataclass_fields__}),
        scan_interval_minutes=int(sc.get("scan_interval_minutes", 240)),
        track_interval_minutes=int(sc.get("track_interval_minutes", 15)),
        max_ideas_per_day=int(sc.get("max_ideas_per_day", 3)),
        cooldown_days=int(sc.get("cooldown_days", 7)),
        max_wait_days=int(sc.get("max_wait_days", 10)),
        weekly_report_weekday=str(sc.get("weekly_report_weekday", "mon")),
        weekly_report_hour_utc=int(sc.get("weekly_report_hour_utc", 9)),
        risk_per_trade_pct=float(raw.get("risk", {}).get("risk_per_trade_pct", 1.0)),
        disclaimer=bool(raw.get("caption", {}).get("disclaimer", False)),
        use_context=bool(raw.get("context", {}).get("enabled", True)),
        writer_enabled=bool(raw.get("writer", {}).get("enabled", True)),
        stickers_enabled=bool(raw.get("stickers", {}).get("enabled", True)),
        sticker_chance={str(k): float(v) for k, v in (raw.get("stickers", {}).get("chance") or {}).items()},
        fun_enabled=bool(raw.get("fun", {}).get("enabled", True)),
        fun_model=str(raw.get("fun", {}).get("model", "claude-sonnet-5-5")),
        fun_slots=_slots(raw.get("fun", {}).get("slots")),
        memes_dir=str(raw.get("fun", {}).get("memes_dir", "assets/memes")),
        fun_trends=bool(raw.get("fun", {}).get("trends", True)),
        fun_topics=[str(t) for t in (raw.get("fun", {}).get("topics") or [])],
        morning_enabled=bool(raw.get("morning", {}).get("enabled", True)),
        morning_hour_utc=(int(raw.get("morning", {}).get("hour_msk", 9)) - 3) % 24,  # МСК = UTC+3, без перехода на летнее время
        reveal_hour_utc=(int(raw.get("morning", {}).get("reveal_hour_msk", 12)) - 3) % 24,
        audience=list(raw.get("morning", {}).get("audience") or ["Прайд", "львы", "ребята", "команда", "народ"]),
        news_feeds=list(raw.get("morning", {}).get("news_feeds") or []),
        writer_model=str(raw.get("writer", {}).get("model", "claude-opus-5-5")),
        writer_effort=str(raw.get("writer", {}).get("effort", "low")),
        mascot_image=c.get("mascot_image"),
        watermark_text=c.get("watermark_text") or "",
        watermark_image=c.get("watermark_image"),
        db_path=os.getenv("DB_PATH") or raw.get("db_path", "data/signalbot.sqlite3"),
    )
    return cfg
