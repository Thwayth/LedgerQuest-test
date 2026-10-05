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
CREATE TABLE IF NOT EXISTS sticker_sets (name TEXT PRIMARY KEY, title TEXT, synced INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS stickers (unique_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, set_name TEXT, emoji TEXT,
    tags TEXT NOT NULL DEFAULT '[]', description TEXT, described INTEGER NOT NULL DEFAULT 0, thumb_id TEXT);
CREATE TABLE IF NOT EXISTS emoji (id TEXT PRIMARY KEY, char TEXT NOT NULL, set_name TEXT);
CREATE TABLE IF NOT EXISTS emoji_sets (name TEXT PRIMARY KEY, title TEXT, synced INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS reactions (key TEXT PRIMARY KEY, kind TEXT NOT NULL, char TEXT NOT NULL, set_name TEXT, thumb_id TEXT,
    description TEXT, described INTEGER NOT NULL DEFAULT 0, ord INTEGER NOT NULL DEFAULT 0);
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
            try:  # база из прошлых версий без колонки set_name
                self._db.execute("ALTER TABLE emoji ADD COLUMN set_name TEXT")
            except sqlite3.OperationalError:
                pass

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

    # --- стикеры -------------------------------------------------------------------
    def add_sticker_set(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO sticker_sets (name) VALUES (?)", (name,))
            self._db.commit()
            return cur.rowcount > 0

    def sticker_sets(self, only_unsynced: bool = False) -> list[str]:
        with self._lock:
            q = "SELECT name FROM sticker_sets" + (" WHERE synced=0" if only_unsynced else "") + " ORDER BY rowid"
            return [r[0] for r in self._db.execute(q).fetchall()]

    def mark_set_synced(self, name: str, title: str | None) -> None:
        with self._lock:
            self._db.execute("UPDATE sticker_sets SET synced=1, title=? WHERE name=?", (title, name))
            self._db.commit()

    def upsert_sticker(self, unique_id: str, file_id: str, set_name: str | None, emoji: str | None, tags: list[str], thumb_id: str | None) -> bool:
        """Новый стикер вставляется с тегами по эмодзи; у известного обновляются только file_id и превью (разметка Claude сохраняется)."""
        with self._lock:
            exists = self._db.execute("SELECT 1 FROM stickers WHERE unique_id=?", (unique_id,)).fetchone()
            if exists:
                self._db.execute("UPDATE stickers SET file_id=?, thumb_id=COALESCE(?, thumb_id) WHERE unique_id=?", (file_id, thumb_id, unique_id))
            else:
                self._db.execute("INSERT INTO stickers (unique_id, file_id, set_name, emoji, tags, thumb_id) VALUES (?,?,?,?,?,?)",
                                 (unique_id, file_id, set_name, emoji, json.dumps(tags, ensure_ascii=False), thumb_id))
            self._db.commit()
            return not exists

    def pending_stickers(self, limit: int) -> list[tuple[str, str]]:
        """(unique_id, thumb_id) стикеров, которые Claude ещё не смотрел."""
        with self._lock:
            return [(r[0], r[1]) for r in self._db.execute(
                "SELECT unique_id, thumb_id FROM stickers WHERE described=0 AND thumb_id IS NOT NULL ORDER BY rowid LIMIT ?", (limit,)).fetchall()]

    def set_sticker_description(self, unique_id: str, description: str, tags: list[str]) -> None:
        with self._lock:
            self._db.execute("UPDATE stickers SET description=?, tags=?, described=1 WHERE unique_id=?",
                             (description, json.dumps(tags, ensure_ascii=False), unique_id))
            self._db.commit()

    def mark_sticker_unviewable(self, unique_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE stickers SET described=1 WHERE unique_id=?", (unique_id,))
            self._db.commit()

    def stickers(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT unique_id, file_id, set_name, emoji, tags, description, described FROM stickers ORDER BY rowid").fetchall()
        return [dict(unique_id=r[0], file_id=r[1], set_name=r[2], emoji=r[3], tags=json.loads(r[4]), description=r[5], described=bool(r[6])) for r in rows]

    def kv_get(self, key: str) -> str | None:
        with self._lock:
            r = self._db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return r[0] if r else None

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            self._db.commit()

    def add_emoji(self, emoji_id: str, char: str, set_name: str | None = None) -> bool:
        """Запоминает кастомное эмодзи. True, если оно новое."""
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO emoji (id, char, set_name) VALUES (?, ?, ?)", (emoji_id, char, set_name))
            if set_name and cur.rowcount == 0:
                self._db.execute("UPDATE emoji SET set_name=? WHERE id=? AND set_name IS NULL", (set_name, emoji_id))
            self._db.commit()
            return cur.rowcount > 0

    def emoji_map(self) -> dict[str, list[str]]:
        """Обычный символ эмодзи (без вариационного селектора) -> id кастомных эмодзи с таким значением."""
        out: dict[str, list[str]] = {}
        with self._lock:
            rows = self._db.execute("SELECT id, char FROM emoji ORDER BY rowid").fetchall()
        for i, ch in rows:
            out.setdefault(ch.replace("\ufe0f", ""), []).append(i)
        return out

    def unresolved_emoji_ids(self, limit: int = 200) -> list[str]:
        with self._lock:
            return [r[0] for r in self._db.execute("SELECT id FROM emoji WHERE set_name IS NULL LIMIT ?", (limit,)).fetchall()]

    def set_emoji_set(self, emoji_id: str, set_name: str) -> None:
        with self._lock:
            self._db.execute("UPDATE emoji SET set_name=? WHERE id=?", (set_name, emoji_id))
            self._db.commit()

    def add_emoji_set(self, name: str) -> bool:
        with self._lock:
            cur = self._db.execute("INSERT OR IGNORE INTO emoji_sets (name) VALUES (?)", (name,))
            self._db.commit()
            return cur.rowcount > 0

    def emoji_sets(self, only_unsynced: bool = False) -> list[str]:
        with self._lock:
            q = "SELECT name FROM emoji_sets" + (" WHERE synced=0" if only_unsynced else "") + " ORDER BY rowid"
            return [r[0] for r in self._db.execute(q).fetchall()]

    def mark_emoji_set_synced(self, name: str, title: str | None) -> None:
        with self._lock:
            self._db.execute("UPDATE emoji_sets SET synced=1, title=? WHERE name=?", (title, name))
            self._db.commit()

    # --- реакции канала ------------------------------------------------------------------
    def replace_reactions(self, items: list[dict]) -> None:
        """Список доступных реакций канала; описание уже разобранных сохраняется, исчезнувшие реакции удаляются."""
        with self._lock:
            keep = {i["key"] for i in items}
            for (k,) in self._db.execute("SELECT key FROM reactions").fetchall():
                if k not in keep:
                    self._db.execute("DELETE FROM reactions WHERE key=?", (k,))
            for n, i in enumerate(items):
                if self._db.execute("SELECT 1 FROM reactions WHERE key=?", (i["key"],)).fetchone():
                    self._db.execute("UPDATE reactions SET ord=?, thumb_id=COALESCE(?, thumb_id) WHERE key=?", (n, i.get("thumb_id"), i["key"]))
                else:
                    self._db.execute("INSERT INTO reactions (key, kind, char, set_name, thumb_id, description, described, ord) VALUES (?,?,?,?,?,?,?,?)",
                                     (i["key"], i["kind"], i["char"], i.get("set_name"), i.get("thumb_id"), i.get("description"), int(bool(i.get("described"))), n))
            self._db.commit()

    def reactions(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT key, kind, char, set_name, thumb_id, description, described FROM reactions ORDER BY ord").fetchall()
        return [dict(key=r[0], kind=r[1], char=r[2], set_name=r[3], thumb_id=r[4], description=r[5], described=bool(r[6])) for r in rows]

    def set_reaction_description(self, key: str, description: str) -> None:
        with self._lock:
            self._db.execute("UPDATE reactions SET description=?, described=1 WHERE key=?", (description, key))
            self._db.commit()

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
