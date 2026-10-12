from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sqlite3
import threading
import time

import pytest

from app.access import current_request_is_owner, request_owner_access_context
from app.services.catalysts import hotspot_reads as reads
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from test_hotspots_read_cache_20261010 import production_cycle
from test_personal_catalyst_service import _service


@pytest.fixture(autouse=True)
def owner():
    with request_owner_access_context(True):
        yield


def request_for(engine, now, owner=False, limit=20):
    return reads.HotspotRead(
        db_path=str(engine.db_path), ai_db_path=str(engine.ai_repository.path),
        mode=engine.mode, canonical_tickers=tuple(engine.canonical_tickers),
        model=engine.model, reasoning=engine.reasoning,
        focus_model=engine.focus_model, focus_reasoning=engine.focus_reasoning,
        max_queued=engine.max_queued,
        manual_refresh_cooldown_seconds=engine.manual_refresh_cooldown_seconds,
        scheduled_skip_template_commentary=engine.scheduled_skip_template_commentary,
        limit=limit, now=now, owner_context=owner, include_owner_state=owner,
    )


@pytest.fixture
def pool():
    result = reads.HotspotReadPool()
    result.start()
    processes = [w.process for w in result._workers]
    assert len({p.pid for p in processes}) == reads.HOTSPOT_READ_WORKERS
    assert all(p.is_alive() for p in processes)
    try:
        yield result
    finally:
        processes.extend(w.process for w in result._workers if w is not None)
        result.close()
        assert not any(p.is_alive() for p in processes)


def contents(path):
    with sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True) as connection:
        return tuple(connection.iterdump())


def test_spawn_matches_full_facade_roles_and_keeps_both_stores_unchanged(production_cycle, pool):
    ai, engine, cycle, clock = production_cycle
    service = _service("manual", engine=engine, repository=ai)
    before = contents(engine.db_path), contents(ai.path)
    references = {}
    for owner in (False, True):
        with request_owner_access_context(owner):
            references[owner] = service.hotspots(limit=20, now=clock[0])
        assert references[owner]["items"]
    for index in range(20):
        owner = bool(index % 2)
        assert pool.read(request_for(engine, clock[0], owner)) == references[owner]
    assert before == (contents(engine.db_path), contents(ai.path))
    with request_owner_access_context(False):
        assert reads._read_hotspots(request_for(engine, clock[0], True)) == references[True]
        assert current_request_is_owner() is False
    # Internal readonly enforcement applies even in an authenticated context.
    readonly = LocalCatalystIntelligence(
        engine.db_path, ai, mode="manual", canonical_tickers=(), _read_only=True)
    with readonly._connect() as connection:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden(value)")


def test_spawn_observes_parameters_receipt_changes_and_replaced_file(production_cycle, pool, tmp_path):
    ai, engine, cycle, clock = production_cycle
    request = request_for(engine, clock[0], True)
    original = pool.read(request)
    assert len(original["items"]) > 1
    limited = pool.read(replace(request, limit=1))
    assert limited["items"] == original["items"][:1]
    historical = pool.read(replace(request, now=clock[0] - timedelta(days=3)))
    assert historical["items"] == []
    changed_model = replace(request, focus_model="gpt-5.6-terra", focus_reasoning="max")
    assert pool.read(changed_model) == reads._read_hotspots(changed_model)
    assert pool.read(changed_model) != original
    backup = tmp_path / "original-ai.db"
    with sqlite3.connect(ai.path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
    source.close()
    target.close()
    # A cached checked receipt must not survive a changed underlying job.
    with sqlite3.connect(ai.path) as connection:
        connection.execute("UPDATE ai_jobs SET result_json='{}' WHERE job_id=?", (cycle["job_id"],))
    changed = pool.read(request)
    assert changed == reads._read_hotspots(request)
    assert changed != original
    with sqlite3.connect(ai.path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    os.replace(backup, ai.path)
    assert pool.read(request) == original
    before_real = datetime.now(timezone.utc).replace(microsecond=0)
    realtime = pool.read(replace(request, now=None))
    after_real = datetime.now(timezone.utc)
    assert before_real <= datetime.fromisoformat(realtime["as_of"].replace("Z", "+00:00")) <= after_real
    # Replace the same catalyst path after workers have cached old revisions.
    replacement = tmp_path / "replacement.db"
    with sqlite3.connect(engine.db_path) as source, sqlite3.connect(replacement) as target:
        source.backup(target)
    source.close()
    target.close()
    with sqlite3.connect(replacement) as connection:
        connection.execute("DELETE FROM catalyst_local_hotspot_items")
    connection.close()
    # Checkpoint and remove only these fixture WAL sidecars before replacement.
    with sqlite3.connect(engine.db_path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    os.replace(replacement, engine.db_path)
    replaced = pool.read(request)
    assert replaced == reads._read_hotspots(request)
    assert replaced["items"] == []


def test_owner_only_unpublished_groups_do_not_leak_on_worker_reuse(production_cycle, pool):
    ai, engine, cycle, clock = production_cycle
    # Keep unverified Chinese groups visible to the owner, while guests must
    # still have a published representative analysis before seeing the group.
    with sqlite3.connect(engine.db_path) as connection:
        connection.execute("""UPDATE catalyst_local_event_groups
            SET representative_title_zh='英伟达发布新一代芯片，数据中心需求继续增长',
                representative_summary_zh='该消息涉及芯片供应与企业资本开支，后续需要观察订单兑现情况。'""")
    connection.close()
    engine.focus_model = "gpt-5.6-terra"
    engine.focus_reasoning = "max"
    engine.focus_verification_enabled = False
    service = _service("manual", engine=engine, repository=ai)
    with request_owner_access_context(True):
        owner_result = service.hotspots(limit=20, now=clock[0])
    with request_owner_access_context(False):
        guest_result = service.hotspots(limit=20, now=clock[0])
    assert owner_result["items"] and guest_result["items"] == []
    for index in range(20):
        owner = bool(index % 2)
        assert pool.read(request_for(engine, clock[0], owner)) == (owner_result if owner else guest_result)


def test_available_never_blocks_the_event_loop_on_recovery_lock():
    pool = reads.HotspotReadPool()
    held = threading.Event()
    def hold():
        with pool._lock:
            held.set()
            time.sleep(.2)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(1)
    started = time.monotonic()
    assert not pool.available
    elapsed = time.monotonic() - started
    thread.join()
    assert elapsed < .05


def test_missing_store_does_not_create_files_or_prevent_start(pool, tmp_path):
    request = reads.HotspotRead(str(tmp_path / "missing.db"), str(tmp_path / "missing-ai.db"),
        "manual", (), "gpt-5.6-terra", "max", "gpt-5.6-terra", "max", 200, 30, True,
        20, None, True, True)
    with pytest.raises(sqlite3.OperationalError):
        pool.read(request)
    assert not list(tmp_path.iterdir())


def _controlled_worker(pipe):
    pipe.send({"ready": True})
    try:
        while True:
            request = pipe.recv()
            if request == "crash":
                os._exit(7)
            if request == "hang":
                time.sleep(30)
            if request == "brief":
                time.sleep(.4)
            pipe.send({"result": {"pid": os.getpid()}})
    except EOFError:
        pass


def test_crash_timeout_recovery_bounded_admission_and_shutdown(monkeypatch):
    monkeypatch.setattr(reads, "_worker_main", _controlled_worker)
    monkeypatch.setattr(reads, "_READ_TIMEOUT_SECONDS", .7)
    pool = reads.HotspotReadPool()
    pool.start()
    processes = [w.process for w in pool._workers]
    try:
        first = pool.read("ok")["pid"]
        with pytest.raises(sqlite3.OperationalError):
            pool.read("crash")
        second = pool.read("ok")["pid"]
        assert second != first
        processes.append(pool._workers[0].process)
        started = time.monotonic()
        with pytest.raises(sqlite3.OperationalError):
            pool.read("hang")
        assert time.monotonic() - started < 3
        third = pool.read("ok")["pid"]
        assert third != second
        processes.append(pool._workers[0].process)
        with ThreadPoolExecutor(max_workers=reads.HOTSPOT_READ_WORKERS) as threads:
            futures = [threads.submit(pool.read, "brief") for _ in range(reads.HOTSPOT_READ_WORKERS)]
            deadline = time.monotonic() + 2
            while not all(w.leased for w in pool._workers):
                assert time.monotonic() < deadline
                time.sleep(.001)
            with pytest.raises(sqlite3.OperationalError, match="busy"):
                pool.read("ok")
            assert len({f.result()["pid"] for f in futures}) == reads.HOTSPOT_READ_WORKERS
        # Close while requests are still active: their leases fail promptly.
        with ThreadPoolExecutor(max_workers=1) as threads:
            future = threads.submit(pool.read, "hang")
            deadline = time.monotonic() + 2
            while not any(w.leased for w in pool._workers):
                assert time.monotonic() < deadline
                time.sleep(.001)
            pool.close()
            with pytest.raises(sqlite3.OperationalError):
                future.result(timeout=3)
        assert not pool.available
    finally:
        pool.close()
    assert not any(p.is_alive() for p in processes)


def _probe_read(request):
    import socket
    results = []
    for path in (request.db_path, request.ai_db_path):
        try:
            sqlite3.connect(path)
        except sqlite3.OperationalError:
            results.append("ordinary_open_blocked")
        with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as connection:
            try:
                connection.execute("CREATE TABLE forbidden(value)")
            except sqlite3.OperationalError:
                results.append("write_blocked")
    with socket.socket() as sock:
        try:
            sock.connect(("127.0.0.1", 9))
        except RuntimeError:
            results.append("network_blocked")
    return results


def _probe_worker(pipe):
    reads._read_hotspots = _probe_read
    reads._worker_main(pipe)


def _error_read(request):
    from app.services.catalysts.errors import CatalystError
    if request.limit == 1:
        raise RuntimeError("ai_job_schema_checksum_mismatch")
    if request.limit == 2:
        raise RuntimeError("private filesystem/provider detail")
    if request.limit == 3:
        raise CatalystError("invalid_cursor", "safe error", retryable=True,
                            retry_after_seconds=7, counts_for_circuit=False)
    return {"ok": True}


def _error_worker(pipe):
    reads._read_hotspots = _error_read
    reads._worker_main(pipe)


def test_spawn_preserves_local_store_classification_and_safe_error_fields(monkeypatch, tmp_path):
    from app.services.catalysts.errors import CatalystError
    monkeypatch.setattr(reads, "_worker_main", _error_worker)
    pool = reads.HotspotReadPool()
    pool.start()
    request = reads.HotspotRead(str(tmp_path / "cache.db"), str(tmp_path / "ai.db"),
        "manual", (), "gpt-5.6-terra", "max", "gpt-5.6-terra", "max", 200, 30, True,
        1, None, True, True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="hotspot_read_store_unavailable"):
            pool.read(request)
        with pytest.raises(RuntimeError, match="^hotspot_read_failed$"):
            pool.read(replace(request, limit=2))
        with pytest.raises(CatalystError) as caught:
            pool.read(replace(request, limit=3))
        assert caught.value.code == "invalid_cursor"
        assert caught.value.retryable is True
        assert caught.value.retry_after_seconds == 7
        assert caught.value.counts_for_circuit is False
        assert pool.read(replace(request, limit=4)) == {"ok": True}
    finally:
        pool.close()


def test_spawn_readonly_and_network_guard(production_cycle, monkeypatch):
    ai, engine, cycle, clock = production_cycle
    monkeypatch.setattr(reads, "_worker_main", _probe_worker)
    pool = reads.HotspotReadPool()
    pool.start()
    try:
        assert pool.read(request_for(engine, clock[0], True)) == [
            "ordinary_open_blocked", "write_blocked", "ordinary_open_blocked",
            "write_blocked", "network_blocked"]
    finally:
        pool.close()


def test_injected_service_stays_inline_and_parent_status_precedes_pool(production_cycle, monkeypatch, pool):
    ai, engine, cycle, clock = production_cycle
    service = _service("manual", engine=engine, repository=ai, cache_path=engine.db_path)
    expected = service.hotspots(limit=20, now=clock[0])
    calls = []
    original = service.status
    def status(**kwargs):
        calls.append("parent_status")
        return original(**kwargs)
    monkeypatch.setattr(service, "status", status)
    with reads.hotspot_read_pool_context(pool):
        assert service.hotspots(limit=20, now=clock[0]) == expected
        assert calls == ["parent_status"]
        # This fixture already contains real repositories; only mark them as
        # ordinary to exercise facade dispatch without environment secrets.
        service._intelligence_injected = False
        service._ai_repository_injected = False
        assert service.hotspots(limit=20, now=clock[0]) == expected
        assert calls == ["parent_status", "parent_status"]
        pool.close()
        assert service.hotspots(limit=20, now=clock[0])["status"] == "unavailable"
        monkeypatch.setattr(service, "status", lambda **kw: {
            "status": "disabled", "as_of": "fixed", "warnings": []})
        monkeypatch.setattr(pool, "read", lambda request: pytest.fail("disabled dispatched"))
        assert service.hotspots(limit=20, now=clock[0])["status"] == "disabled"
    assert reads.current_hotspot_read_pool() is None
