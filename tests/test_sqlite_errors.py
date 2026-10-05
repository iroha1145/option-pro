from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.services.sqlite_errors import is_sqlite_lock_contention


def _with(error: sqlite3.Error, **attributes: object) -> sqlite3.Error:
    for name, value in attributes.items():
        setattr(error, name, value)
    return error


def test_a_real_busy_database_is_lock_contention(tmp_path: Path) -> None:
    path = tmp_path / "busy.sqlite"
    holder = sqlite3.connect(path)
    holder.execute("CREATE TABLE t (x)")
    holder.commit()
    holder.execute("BEGIN EXCLUSIVE")
    waiter = sqlite3.connect(path, timeout=0)
    try:
        with pytest.raises(sqlite3.OperationalError) as captured:
            waiter.execute("INSERT INTO t VALUES (1)")
    finally:
        waiter.close()
        holder.rollback()
        holder.close()
    assert is_sqlite_lock_contention(captured.value)


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("database is locked"),
        sqlite3.OperationalError("Database Is Busy"),
        sqlite3.OperationalError("database table is locked: jobs"),
        _with(sqlite3.OperationalError("unrelated text"), sqlite_errorcode=517),  # SQLITE_BUSY_SNAPSHOT
        _with(sqlite3.DatabaseError("unrelated text"), sqlite_errorcode=6),  # SQLITE_LOCKED
        _with(sqlite3.OperationalError("unrelated text"), sqlite_errorname="SQLITE_LOCKED_SHAREDCACHE"),
    ],
)
def test_every_lock_spelling_the_callers_used_is_recognised(error: sqlite3.Error) -> None:
    assert is_sqlite_lock_contention(error)


@pytest.mark.parametrize(
    "error",
    [
        sqlite3.OperationalError("disk I/O error"),
        sqlite3.OperationalError("attempt to write a readonly database"),
        _with(sqlite3.OperationalError("disk I/O error"), sqlite_errorcode=10, sqlite_errorname="SQLITE_IOERR"),
        _with(sqlite3.OperationalError("odd"), sqlite_errorcode=None),
        RuntimeError("database is locked"),
    ],
)
def test_other_errors_are_not_lock_contention(error: BaseException) -> None:
    assert not is_sqlite_lock_contention(error)
