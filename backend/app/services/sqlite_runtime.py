"""SQLite connection tuning for the dedicated 16-core / 32 GiB / SSD host.

Journal mode and synchronous level stay with the writer that owns durability.
This helper only applies per-connection read/write cache settings that do not
change on-disk semantics.
"""

from __future__ import annotations

import sqlite3

# Negative cache_size is kibibytes. 256 MiB fits comfortably in 32 GiB RAM
# even with several processes (backend + worker) sharing the same box.
_CACHE_KIB = -262144
_MMAP_BYTES = 256 * 1024 * 1024


def apply_sqlite_runtime_pragmas(connection: sqlite3.Connection) -> None:
    """Speed repeated news/status/radar reads without changing durability."""

    connection.execute("PRAGMA temp_store=MEMORY")
    connection.execute(f"PRAGMA cache_size={_CACHE_KIB}")
    try:
        connection.execute(f"PRAGMA mmap_size={_MMAP_BYTES}")
    except sqlite3.Error:
        # mmap is optional: query_only / some URI modes may reject it.
        pass
