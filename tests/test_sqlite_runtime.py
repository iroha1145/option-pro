from __future__ import annotations

import sqlite3

from app.services.sqlite_runtime import apply_sqlite_runtime_pragmas


def test_runtime_pragmas_set_memory_temp_and_large_cache() -> None:
    connection = sqlite3.connect(":memory:")
    apply_sqlite_runtime_pragmas(connection)
    assert connection.execute("PRAGMA temp_store").fetchone()[0] in {2, "memory", "MEMORY"}
    cache_size = connection.execute("PRAGMA cache_size").fetchone()[0]
    assert cache_size == -262144
    mmap_size = connection.execute("PRAGMA mmap_size").fetchone()[0]
    assert mmap_size >= 0
    connection.close()


def test_runtime_pragmas_do_not_change_journal_or_synchronous() -> None:
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA journal_mode=DELETE")
    connection.execute("PRAGMA synchronous=FULL")
    apply_sqlite_runtime_pragmas(connection)
    journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    assert journal == "delete"
    assert int(connection.execute("PRAGMA synchronous").fetchone()[0]) == 2
    connection.close()
