from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading
import time

from app.services import finnhub_budget as budget


def test_budget_is_shared_by_connections_and_observes_both_windows(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])
    path = tmp_path / "budget.sqlite"
    assert all(budget.reserve_finnhub_request("test-key", db_path=path) for _ in range(30))
    assert not budget.reserve_finnhub_request("test-key", db_path=path)
    now[0] += 1.1
    assert all(budget.reserve_finnhub_request("test-key", db_path=path) for _ in range(30))
    now[0] += 2
    assert not budget.reserve_finnhub_request("test-key", db_path=path)
    assert budget.reserve_finnhub_request("other-key", db_path=path)
    now[0] = 1060.0
    assert budget.reserve_finnhub_request("test-key", db_path=path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM finnhub_requests WHERE key_id = ?", (budget._key_id("test-key"),)).fetchone()[0] == 31
    assert b"test-key" not in path.read_bytes()


def test_parallel_reservations_cannot_overspend(tmp_path, monkeypatch):
    monkeypatch.setattr(budget.time, "time", lambda: 1000.0)
    path = tmp_path / "budget.sqlite"
    # Each call opens its own SQLite connection, as separate worker/API
    # processes do. The reservation has to be atomic across those connections.
    with ThreadPoolExecutor(max_workers=8) as pool:
        reserved = list(pool.map(lambda _: budget.reserve_finnhub_request("key", db_path=path), range(80)))
    assert sum(reserved) == 30


def test_rate_limit_cooldown_is_shared_and_does_not_shorten(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])
    path = tmp_path / "budget.sqlite"
    budget.mark_finnhub_rate_limited("key", retry_after="90", db_path=path)
    budget.mark_finnhub_rate_limited("key", retry_after="1", db_path=path)
    now[0] = 1061
    assert not budget.reserve_finnhub_request("key", db_path=path)
    now[0] = 1090
    assert budget.reserve_finnhub_request("key", db_path=path)


def test_async_budget_and_storage_failure_fail_closed(tmp_path):
    path = tmp_path / "budget.sqlite"
    assert asyncio.run(budget.async_reserve_finnhub_request("key", db_path=path))
    assert not asyncio.run(budget.async_reserve_finnhub_request("", db_path=path))
    invalid = tmp_path / "not-a-directory"
    invalid.write_text("file")
    assert not budget.reserve_finnhub_request("key", db_path=invalid / "db")
    budget.mark_finnhub_rate_limited("key", db_path=invalid / "db")


def test_waits_happen_after_transaction_closes(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])
    path = tmp_path / "budget.sqlite"
    budget.mark_finnhub_rate_limited("key", retry_after=1, db_path=path)

    def sleep(duration):
        # A second writer can commit during the reservation's wait.
        with sqlite3.connect(path, timeout=0) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("COMMIT")
        now[0] += duration

    monkeypatch.setattr(budget.time, "sleep", sleep)
    assert budget.reserve_finnhub_request("key", timeout=2, db_path=path)


def _hold_writer(path, locked, release):
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        locked.set()
        assert release.wait(timeout=5), "test writer was not released"
        connection.execute("COMMIT")


def test_short_write_contention_does_not_discard_an_available_reservation(tmp_path):
    path = tmp_path / "budget.sqlite"
    assert budget.reserve_finnhub_request("seed", db_path=path)
    locked, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(_hold_writer, path, locked, release)
        assert locked.wait(timeout=5)
        # Keep a real SQLite writer open beyond one connection's busy timeout.
        timer = threading.Timer(0.35, release.set)
        timer.start()
        try:
            assert budget.reserve_finnhub_request("key", timeout=0, db_path=path)
        finally:
            release.set()
            timer.cancel()
        writer.result(timeout=5)
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM finnhub_requests WHERE key_id = ?",
            (budget._key_id("key"),),
        ).fetchone()[0] == 1


def test_short_write_contention_does_not_lose_a_provider_cooldown(tmp_path, monkeypatch):
    path = tmp_path / "budget.sqlite"
    now = [1000.0]
    monkeypatch.setattr(budget.time, "time", lambda: now[0])
    assert budget.reserve_finnhub_request("seed", db_path=path)
    locked, release = threading.Event(), threading.Event()
    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(_hold_writer, path, locked, release)
        assert locked.wait(timeout=5)
        timer = threading.Timer(0.35, release.set)
        timer.start()
        try:
            budget.mark_finnhub_rate_limited("key", retry_after=10, db_path=path)
        finally:
            release.set()
            timer.cancel()
        writer.result(timeout=5)
    now[0] = 1009.0
    assert not budget.reserve_finnhub_request("key", db_path=path)
    now[0] = 1010.0
    assert budget.reserve_finnhub_request("key", db_path=path)


def test_persistent_write_lock_fails_closed_within_a_bounded_wait(tmp_path, monkeypatch):
    path = tmp_path / "budget.sqlite"
    assert budget.reserve_finnhub_request("seed", db_path=path)
    monkeypatch.setattr(budget, "_STORAGE_BUSY_RETRY_SECONDS", 0.15)
    with sqlite3.connect(path) as writer:
        writer.execute("BEGIN IMMEDIATE")
        started = time.perf_counter()
        assert not budget.reserve_finnhub_request("key", db_path=path)
        assert time.perf_counter() - started < 2
        writer.execute("COMMIT")
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM finnhub_requests WHERE key_id = ?",
            (budget._key_id("key"),),
        ).fetchone()[0] == 0
