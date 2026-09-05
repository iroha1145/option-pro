from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import sqlite3

from app.services import accounts


def test_repeated_successful_logins_keep_latest_sessions_and_other_accounts(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "SESSIONS_PER_ACCOUNT_MAX", 3)
    # All issuance timestamps coincide: insertion order must still keep the
    # just-returned cookie rather than immediately evicting the new session.
    store = accounts.AccountStore(tmp_path / "accounts.db", clock=lambda: 1000.0)
    first = store.register("friend", "fixture-session-password")
    other = store.register("another", "fixture-session-password")
    sessions = [store.authenticate("friend", "fixture-session-password") for _ in range(5)]

    assert store.resolve_session(first.token) is None
    assert store.resolve_session(other.token) == other.account
    assert all(store.resolve_session(session.token) is None for session in sessions[:-3])
    assert all(store.resolve_session(session.token) == first.account for session in sessions[-3:])
    with sqlite3.connect(store.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_sessions").fetchone()[0] == 4


def test_concurrent_store_instances_cannot_bypass_session_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(accounts, "SESSIONS_PER_ACCOUNT_MAX", 3)
    path = tmp_path / "accounts.db"
    first_store = accounts.AccountStore(path)
    account = first_store.register("friend", "fixture-session-password").account
    stores = [accounts.AccountStore(path) for _ in range(6)]
    for store in stores:
        store.initialize()
    with ThreadPoolExecutor(max_workers=6) as pool:
        sessions = list(pool.map(lambda store: store._issue_session(account), stores))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM account_sessions").fetchone()[0] == 3
    assert sum(first_store.resolve_session(session.token) is not None for session in sessions) == 3
