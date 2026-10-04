"""Recognise SQLite lock contention, the one storage error worth waiting out."""

from __future__ import annotations

import sqlite3

_LOCK_MESSAGES = ("database is locked", "database is busy", "database table is locked")


def is_sqlite_lock_contention(error: BaseException) -> bool:
    """SQLITE_BUSY / SQLITE_LOCKED, extended codes included; disk, permission or schema errors are not.

    An error raised by hand carries no ``sqlite_errorcode`` or ``sqlite_errorname``,
    so the message is checked as well.
    """

    if not isinstance(error, sqlite3.Error):
        return False
    code = getattr(error, "sqlite_errorcode", None)
    if isinstance(code, int) and (code & 0xFF) in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
        return True
    name = str(getattr(error, "sqlite_errorname", "") or "")
    if name.startswith(("SQLITE_BUSY", "SQLITE_LOCKED")):
        return True
    message = str(error).casefold()
    return any(marker in message for marker in _LOCK_MESSAGES)
