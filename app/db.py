"""SQLite persistence: settings, tracked items, caches and activity log."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Iterator

CONFIG_DIR = os.environ.get("HLA_CONFIG_DIR", "/config")
DB_PATH = os.path.join(CONFIG_DIR, "hardlinkarr.db")

_lock = threading.RLock()
_conn: sqlite3.Connection | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path   TEXT NOT NULL UNIQUE,
    size          INTEGER,
    inode         INTEGER,
    dev           INTEGER,
    torrent_hash  TEXT,
    torrent_name  TEXT,
    category      TEXT,
    media_type    TEXT,           -- movie | episode
    tmdb_id       INTEGER,
    imdb_id       TEXT,
    title         TEXT,
    year          INTEGER,
    season        INTEGER,
    episodes      TEXT,           -- json list of ints
    episode_title TEXT,
    resolution    TEXT,
    edition       TEXT,
    dest_path     TEXT,
    status        TEXT NOT NULL,  -- pending|linked|review|duplicate|exists|error|ignored
    confidence    REAL,
    match_source  TEXT,
    message       TEXT,
    created       REAL,
    updated       REAL
);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(status);
CREATE INDEX IF NOT EXISTS idx_items_hash ON items(torrent_hash);
CREATE INDEX IF NOT EXISTS idx_items_inode ON items(dev, inode);

CREATE TABLE IF NOT EXISTS cache (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    ts    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS log (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ts    REAL NOT NULL,
    level TEXT NOT NULL,
    msg   TEXT NOT NULL
);
"""


def conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.executescript(SCHEMA)
    return _conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    with _lock:
        c = conn()
        c.execute("BEGIN")
        try:
            yield c
            c.execute("COMMIT")
        except Exception:
            c.execute("ROLLBACK")
            raise


def query(sql: str, args: tuple | list = ()) -> list[dict]:
    with _lock:
        return [dict(r) for r in conn().execute(sql, args).fetchall()]


def one(sql: str, args: tuple | list = ()) -> dict | None:
    with _lock:
        r = conn().execute(sql, args).fetchone()
        return dict(r) if r else None


def execute(sql: str, args: tuple | list = ()) -> int:
    with _lock:
        cur = conn().execute(sql, args)
        return cur.lastrowid or cur.rowcount


# ---------------------------------------------------------------- items

ITEM_FIELDS = {
    "source_path", "size", "inode", "dev", "torrent_hash", "torrent_name", "category",
    "media_type", "tmdb_id", "imdb_id", "title", "year", "season", "episodes",
    "episode_title", "resolution", "edition", "dest_path", "status", "confidence",
    "match_source", "message",
}


def _encode(d: dict) -> dict:
    out = {k: v for k, v in d.items() if k in ITEM_FIELDS}
    if "episodes" in out and isinstance(out["episodes"], list):
        out["episodes"] = json.dumps(out["episodes"])
    return out


def decode_item(r: dict | None) -> dict | None:
    if r and r.get("episodes"):
        try:
            r["episodes"] = json.loads(r["episodes"])
        except (TypeError, ValueError):
            r["episodes"] = []
    return r


def upsert_item(d: dict) -> int:
    d = _encode(d)
    now = time.time()
    with _lock:
        existing = one("SELECT id FROM items WHERE source_path=?", (d["source_path"],))
        if existing:
            sets = ", ".join(f"{k}=?" for k in d) + ", updated=?"
            execute(f"UPDATE items SET {sets} WHERE id=?", [*d.values(), now, existing["id"]])
            return existing["id"]
        cols = ", ".join(d) + ", created, updated"
        qs = ", ".join("?" for _ in d) + ", ?, ?"
        return execute(f"INSERT INTO items ({cols}) VALUES ({qs})", [*d.values(), now, now])


def update_item(item_id: int, **fields: Any) -> None:
    d = _encode(fields)
    if not d:
        return
    sets = ", ".join(f"{k}=?" for k in d) + ", updated=?"
    execute(f"UPDATE items SET {sets} WHERE id=?", [*d.values(), time.time(), item_id])


def get_item(item_id: int) -> dict | None:
    return decode_item(one("SELECT * FROM items WHERE id=?", (item_id,)))


# ---------------------------------------------------------------- cache

def cache_get(key: str, max_age: float) -> Any | None:
    r = one("SELECT value, ts FROM cache WHERE key=?", (key,))
    if r and time.time() - r["ts"] < max_age:
        return json.loads(r["value"])
    return None


def cache_set(key: str, value: Any) -> None:
    execute(
        "INSERT INTO cache(key, value, ts) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, ts=excluded.ts",
        (key, json.dumps(value), time.time()),
    )


# ---------------------------------------------------------------- log

def log(level: str, msg: str) -> None:
    print(f"[{level.upper()}] {msg}", flush=True)
    execute("INSERT INTO log(ts, level, msg) VALUES(?,?,?)", (time.time(), level, msg))
    # keep the log bounded
    execute("DELETE FROM log WHERE id < (SELECT MAX(id) - 5000 FROM log)")
