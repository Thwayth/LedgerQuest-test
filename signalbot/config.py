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
    disclaimer: bool = True
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
        disclaimer=bool(raw.get("caption", {}).get("disclaimer", True)),
        watermark_text=c.get("watermark_text") or "",
        watermark_image=c.get("watermark_image"),
        db_path=raw.get("db_path", "data/signalbot.sqlite3"),
    )
    return cfg
