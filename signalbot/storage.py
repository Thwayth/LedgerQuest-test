from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from .lifecycle import ACTIVE, Tracked
from .models import Side

SCHEMA = """
CREATE TABLE IF NOT EXISTS ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL, exchange TEXT NOT NULL, timeframe TEXT NOT NULL, side TEXT NOT NULL,
    zone_low REAL NOT NULL, zone_high REAL NOT NULL, pools TEXT NOT NULL,
    entry_cons REAL NOT NULL, entry_aggr REAL NOT NULL, invalidation REAL NOT NULL,
    created_ts INTEGER NOT NULL, score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL, broken_ts INTEGER, entry_price REAL, tp_hit INTEGER NOT NULL DEFAULT 0,
    closed_ts INTEGER, result_pct REAL, r_multiple REAL, chat_id INTEGER, message_id INTEGER
);
CREATE TABLE IF NOT EXISTS posts (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS emoji (id TEXT PRIMARY KEY, char TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS reveals (id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
    text TEXT NOT NULL, due_ts INTEGER NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ix_ideas_symbol ON ideas(symbol);
CREATE INDEX IF NOT EXISTS ix_ideas_status ON ideas(status);
"""

_COLS = [
    "symbol", "exchange", "timeframe", "side", "zone_low", "zone_high", "pools", "entry_cons", "entry_aggr",
    "invalidation", "created_ts", "score", "status", "broken_ts", "entry_price", "tp_hit", "closed_ts",
    "result_pct", "r_multiple", "chat_id", "message_id",
]


def _row_to_tracked(r: sqlite3.Row) -> Tracked:
    return Tracked(
        id=r["id"], symbol=r["symbol"], exchange=r["exchange"], timeframe=r["timeframe"], side=Side(r["side"]),
        zone_low=r["zone_low"], zone_high=r["zone_high"], pools=json.loads(r["pools"]),
        entry_cons=r["entry_cons"], entry_aggr=r["entry_aggr"], invalidation=r["invalidation"],
        created_ts=r["created_ts"], score=r["score"], status=r["status"], broken_ts=r["broken_ts"],
        entry_price=r["entry_price"], tp_hit=r["tp_hit"], closed_ts=r["closed_ts"], result_pct=r["result_pct"],
        r_multiple=r["r_multiple"], chat_id=r["chat_id"], message_id=r["message_id"],
    )


def _values(t: Tracked) -> list:
    return [
        t.symbol, t.exchange, t.timeframe, t.side.value, t.zone_low, t.zone_high, json.dumps(t.pools),
        t.entry_cons, t.entry_aggr, t.invalidation, t.created_ts, t.score, t.status, t.broken_ts,
        t.entry_price, t.tp_hit, t.closed_ts, t.result_pct, t.r_multiple, t.chat_id, t.message_id,
    ]


class Storage:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._db.executescript(SCHEMA)

    def add(self, t: Tracked) -> int:
        with self._lock:
            cur = self._db.execute(
                f"INSERT INTO ideas ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})", _values(t)
            )
            self._db.commit()
            t.id = cur.lastrowid
            return t.id

    def save(self, t: Tracked) -> None:
        sets = ",".join(f"{c}=?" for c in _COLS)
        with self._lock:
            self._db.execute(f"UPDATE ideas SET {sets} WHERE id=?", [*_values(t), t.id])
            self._db.commit()

    def get(self, idea_id: int) -> Tracked | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM ideas WHERE id=?", (idea_id,)).fetchone()
        return _row_to_tracked(r) if r else None

    def active(self) -> list[Tracked]:
        q = ",".join("?" * len(ACTIVE))
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM ideas WHERE status IN ({q}) ORDER BY id", ACTIVE).fetchall()
        return [_row_to_tracked(r) for r in rows]

    def since(self, ts_ms: int) -> list[Tracked]:
        """Идеи, созданные ИЛИ закрытые начиная с ts_ms (для периодных отчётов)."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM ideas WHERE created_ts>=? OR COALESCE(closed_ts,0)>=? ORDER BY id", (ts_ms, ts_ms)
            ).fetchall()
        return [_row_to_tracked(r) for r in rows]

    def all(self) -> list[Tracked]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM ideas ORDER BY id").fetchall()
        return [_row_to_tracked(r) for r in rows]

    def add_post(self, ts_ms: int, text: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO posts (ts, text) VALUES (?, ?)", (ts_ms, text))
            self._db.commit()

    def recent_posts(self, n: int = 5) -> list[str]:
        with self._lock:
            rows = self._db.execute("SELECT text FROM posts ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [r[0] for r in rows][::-1]

    def kv_get(self, key: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._db.commit()

    def add_emoji(self, emoji_id: str, char: str) -> bool:
        """Запоминает кастомное эмодзи. True, если оно новое."""
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO emoji (id, char) VALUES (?, ?)", (emoji_id, char))
            self._db.commit()
            return cur.rowcount > 0

    def emojis(self) -> list[tuple[str, str]]:
        with self._lock:
            return [(r[0], r[1]) for r in self._db.execute("SELECT id, char FROM emoji ORDER BY rowid").fetchall()]

    def add_reveal(self, chat_id: int, message_id: int, text: str, due_ts: int) -> None:
        with self._lock:
            self._db.execute("INSERT INTO reveals (chat_id, message_id, text, due_ts) VALUES (?, ?, ?, ?)", (chat_id, message_id, text, due_ts))
            self._db.commit()

    def due_reveals(self, now_ms: int) -> list[tuple[int, int, int, str]]:
        with self._lock:
            return [tuple(r) for r in self._db.execute(
                "SELECT id, chat_id, message_id, text FROM reveals WHERE sent=0 AND due_ts<=? ORDER BY id", (now_ms,)).fetchall()]

    def mark_reveal_sent(self, reveal_id: int) -> None:
        with self._lock:
            self._db.execute("UPDATE reveals SET sent=1 WHERE id=?", (reveal_id,))
            self._db.commit()

    def clear_ideas(self) -> int:
        """Сброс идей (после тестового периода, чтобы пробные идеи не блокировали боевые)."""
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM ideas").fetchone()[0]
            self._db.execute("DELETE FROM ideas")
            self._db.commit()
        return n

    def count_created_since(self, ts_ms: int) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM ideas WHERE created_ts>=?", (ts_ms,)).fetchone()[0]

    def blocked_symbols(self, cooldown_since_ms: int) -> set[str]:
        """Монеты с активной идеей или закрытой позже cooldown_since_ms."""
        q = ",".join("?" * len(ACTIVE))
        with self._lock:
            rows = self._db.execute(
                f"SELECT DISTINCT symbol FROM ideas WHERE status IN ({q}) OR COALESCE(closed_ts,created_ts)>=?",
                [*ACTIVE, cooldown_since_ms],
            ).fetchall()
        return {r[0] for r in rows}
