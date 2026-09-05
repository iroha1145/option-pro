"""Bounded, best-effort logo persistence, separate from financial databases.

Call disk functions through asyncio.to_thread. Public readers never create a
file or query upstream. Losing this cache must not break a usable image.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3
from typing import Any

from app.data_paths import get_data_paths

FRESH_SECONDS = 3 * 24 * 60 * 60
STALE_SECONDS = 7 * 24 * 60 * 60
NEGATIVE_SECONDS = 60 * 60
MAX_IMAGE_BYTES = 512 * 1024
MAX_DISK_BYTES = 64 * 1024 * 1024
MAX_DISK_ENTRIES = 2048
MEDIA_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/svg+xml"})


def cache_path() -> Path:
    return get_data_paths().root / "company-logos.sqlite"


def read(symbol: str, now: float) -> dict[str, Any] | None:
    path = cache_path()
    if not path.is_file():
        return None
    try:
        with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute(
                "SELECT saved_at, expires_at, stale_until, media_type, source, content "
                "FROM logos WHERE symbol=? AND stale_until>?", (symbol, now),
            ).fetchone()
        if row is None:
            return None
        saved, expires, stale, media, source, content = row
        if not (0 < saved <= expires <= stale and saved <= now + 60):
            return None
        if content is None:
            value: dict[str, Any] = {"not_found": True}
        elif media in MEDIA_TYPES and isinstance(content, bytes) and 64 < len(content) <= MAX_IMAGE_BYTES:
            value = {"content": content, "media_type": media, "source": source}
        else:
            return None
        return {"fetched_at": saved, "expires_at": expires, "stale_until": stale, "value": value}
    except (OSError, sqlite3.Error, ValueError, TypeError):
        return None


def write(symbol: str, entry: dict[str, Any], now: float) -> bool:
    """SQLite transactions prevent partial images; quotas include negative rows.

    The byte cap bounds stored image payloads. SQLite pages/journals have small
    additional overhead; freed pages are reused and incrementally reclaimed.
    """
    path = cache_path()
    value = entry["value"]
    content = value.get("content")
    if content is not None and (not isinstance(content, bytes) or len(content) > MAX_IMAGE_BYTES):
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(path, timeout=2)) as db:
            # Effective on first creation; does not rewrite another database.
            db.execute("PRAGMA auto_vacuum=INCREMENTAL")
            with db:
                db.execute("""CREATE TABLE IF NOT EXISTS logos (
                    symbol TEXT PRIMARY KEY, saved_at REAL NOT NULL,
                    expires_at REAL NOT NULL, stale_until REAL NOT NULL,
                    media_type TEXT, source TEXT, content BLOB)""")
                db.execute("DELETE FROM logos WHERE stale_until<=?", (now,))
                db.execute("""INSERT INTO logos VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                    saved_at=excluded.saved_at, expires_at=excluded.expires_at,
                    stale_until=excluded.stale_until, media_type=excluded.media_type,
                    source=excluded.source, content=excluded.content""",
                    (symbol, entry["fetched_at"], entry["expires_at"], entry["stale_until"],
                     value.get("media_type"), value.get("source"), content))
                rows = db.execute(
                    "SELECT symbol, coalesce(length(content),0) FROM logos ORDER BY saved_at DESC, symbol"
                ).fetchall()
                used = 0
                evict = []
                for index, (key, size) in enumerate(rows):
                    used += size
                    if index >= MAX_DISK_ENTRIES or used > MAX_DISK_BYTES:
                        evict.append((key,))
                db.executemany("DELETE FROM logos WHERE symbol=?", evict)
            db.execute("PRAGMA incremental_vacuum(64)")
        return True
    except (OSError, sqlite3.Error):
        return False
