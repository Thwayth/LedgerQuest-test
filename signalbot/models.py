from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class Zone:
    """Ключевая зона сопротивления/поддержки, которую нужно пробить."""

    low: float
    high: float
    touches: int
    first_idx: int

    @property
    def mid(self) -> float:
        return (self.low + self.high) / 2


@dataclass(frozen=True)
class Pool:
    """Пул ликвидности: swing-экстремум или кластер equal highs/lows."""

    price: float
    idx: int  # индекс бара, где уровень сформировался (линия идёт от него вправо)
    touches: int = 1


@dataclass
class Idea:
    symbol: str  # "TRB/USDT:USDT"
    exchange: str  # "binance"
    timeframe: str  # "1d"
    side: Side
    zone: Zone
    pools: list[Pool]
    price: float
    change_abs: float
    change_pct: float
    score: float = 0.0
    entry_conservative: float | None = None
    entry_aggressive: float | None = None
    invalidation: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def ticker(self) -> str:
        return self.symbol.split("/")[0]
