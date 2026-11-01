"""SQLite: схема и простые помощники. На каждый вызов своё соединение, поэтому работает из любых потоков."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tg_id INTEGER,
  username TEXT,
  link TEXT,
  title TEXT NOT NULL,
  members INTEGER,
  enabled INTEGER NOT NULL DEFAULT 1,
  is_demo INTEGER NOT NULL DEFAULT 0,
  last_msg_id INTEGER NOT NULL DEFAULT 0,
  last_scan_at TEXT,
  last_error TEXT,
  added_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS chats_tg ON chats(tg_id) WHERE tg_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  tg_msg_id INTEGER NOT NULL,
  text TEXT NOT NULL,
  author_id INTEGER,
  author_username TEXT,
  author_name TEXT,
  author_flags TEXT,
  reply_to INTEGER,
  replies_count INTEGER NOT NULL DEFAULT 0,
  posted_at TEXT NOT NULL,
  fetched_at TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'new',
  UNIQUE(chat_id, tg_msg_id)
);
CREATE INDEX IF NOT EXISTS messages_posted ON messages(posted_at);
CREATE INDEX IF NOT EXISTS messages_author ON messages(author_id);

CREATE TABLE IF NOT EXISTS orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  summary TEXT,
  budget_min REAL,
  budget_max REAL,
  currency TEXT,
  period TEXT,
  budget_rub_min REAL,
  budget_rub_max REAL,
  deadline_days INTEGER,
  skills TEXT NOT NULL DEFAULT '[]',
  contact_type TEXT,
  contact_value TEXT,
  contact_url TEXT,
  score INTEGER NOT NULL DEFAULT 0,
  verdict TEXT NOT NULL DEFAULT 'skip',
  why TEXT,
  fit TEXT NOT NULL DEFAULT '[]',
  red_flags TEXT NOT NULL DEFAULT '[]',
  rec_min REAL,
  rec_max REAL,
  rec_note TEXT,
  bids INTEGER NOT NULL DEFAULT 0,
  dup_count INTEGER NOT NULL DEFAULT 0,
  posted_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'new',
  feedback TEXT,
  feedback_reason TEXT,
  feedback_at TEXT,
  stage TEXT,
  stage_at TEXT,
  offered_at TEXT,
  price_offered REAL,
  won_amount REAL,
  alert_state TEXT NOT NULL DEFAULT 'none',
  alerted_at TEXT,
  scored_by TEXT
);
CREATE INDEX IF NOT EXISTS orders_posted ON orders(posted_at);
CREATE INDEX IF NOT EXISTS orders_state ON orders(state, verdict);
CREATE UNIQUE INDEX IF NOT EXISTS orders_msg ON orders(message_id);

CREATE TABLE IF NOT EXISTS order_sources (
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  link TEXT,
  posted_at TEXT NOT NULL,
  PRIMARY KEY (order_id, message_id)
);

CREATE TABLE IF NOT EXISTS drafts (
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  tone TEXT NOT NULL,
  variant INTEGER NOT NULL DEFAULT 0,
  price REAL,
  text TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY (order_id, tone, variant)
);

CREATE TABLE IF NOT EXISTS feedback_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
  kind TEXT NOT NULL,
  reason TEXT,
  score_at INTEGER,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS insights (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  text TEXT NOT NULL,
  payload TEXT,
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scan_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  source TEXT,
  messages INTEGER NOT NULL DEFAULT 0,
  orders INTEGER NOT NULL DEFAULT 0,
  error TEXT
);
"""

MIGRATIONS: list[str] = [
    SCHEMA_V1,
    "ALTER TABLE messages ADD COLUMN note TEXT;",  # почему сообщение не стало заказом: ai:offer, rule:rules и т. п.
]

JSON_COLUMNS = {"skills", "fit", "red_flags", "author_flags", "payload"}


def _ulower(value):
    """Нижний регистр для любого языка: встроенный lower() в SQLite понимает только латиницу."""
    return value.lower() if isinstance(value, str) else value


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._memory_conn: sqlite3.Connection | None = None
        self.migrate()

    def backup_to(self, target: str | Path) -> None:
        """Цельная копия базы в файл. Зашифрованные ключи и токены в неё не попадают."""
        with self.conn() as src:
            dst = sqlite3.connect(str(target))
            try:
                src.backup(dst)
                dst.execute("DELETE FROM settings WHERE key LIKE 'secret:%'")
                dst.commit()
                dst.execute("VACUUM")
            finally:
                dst.close()

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        if self.path == ":memory:":
            if self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_conn.row_factory = sqlite3.Row
                self._memory_conn.execute("PRAGMA foreign_keys=ON")
                self._memory_conn.create_function("ulower", 1, _ulower, deterministic=True)
            yield self._memory_conn
            self._memory_conn.commit()
            return
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.create_function("ulower", 1, _ulower, deterministic=True)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=15000")
        try:
            yield c
            c.commit()
        except BaseException:
            c.rollback()
            raise
        finally:
            c.close()

    def migrate(self) -> None:
        with self.conn() as c:
            version = c.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS, start=1):
                if i > version:
                    c.executescript(script)
                    c.execute(f"PRAGMA user_version={i}")

    # --- запросы ---------------------------------------------------------
    def one(self, sql: str, args: Iterable[Any] = ()) -> dict | None:
        with self.conn() as c:
            row = c.execute(sql, tuple(args)).fetchone()
        return decode(row) if row else None

    def all(self, sql: str, args: Iterable[Any] = ()) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(sql, tuple(args)).fetchall()
        return [decode(r) for r in rows]

    def run(self, sql: str, args: Iterable[Any] = ()) -> int:
        with self.conn() as c:
            cur = c.execute(sql, tuple(args))
            return cur.lastrowid or cur.rowcount

    def scalar(self, sql: str, args: Iterable[Any] = (), default: Any = None) -> Any:
        with self.conn() as c:
            row = c.execute(sql, tuple(args)).fetchone()
        return row[0] if row and row[0] is not None else default

    # --- настройки (JSON) ------------------------------------------------
    def get_setting(self, key: str, default: Any = None) -> Any:
        raw = self.scalar("SELECT value FROM settings WHERE key=?", (key,))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return default

    def set_setting(self, key: str, value: Any) -> None:
        self.run(
            "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )

    def del_setting(self, key: str) -> None:
        self.run("DELETE FROM settings WHERE key=?", (key,))


def decode(row: sqlite3.Row) -> dict:
    out = dict(row)
    for k in JSON_COLUMNS:
        if k in out and isinstance(out[k], str):
            try:
                out[k] = json.loads(out[k])
            except json.JSONDecodeError:
                out[k] = [] if k in {"skills", "fit", "red_flags"} else None
    return out


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
