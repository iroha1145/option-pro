"""Exact historical bundles retain chronology, permissions and paid identities."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import timedelta
import os
import sqlite3
import threading

import pytest

from app.access import request_owner_access_context
from app.services.catalysts import local_intelligence as local
from app.services.catalysts.etl_repository import CatalystEtlRepository
from test_catalyst_read_path_costs import _analyzed_store, NOW
from test_catalyst_local_intelligence import _apply_news, _news_change


@pytest.fixture(autouse=True)
def owner_context():
    local._reset_revision_cache()
    with request_owner_access_context(True):
        yield
    local._reset_revision_cache()
    assert not local._HISTORICAL_REVISION_BUILDS


def historical_store(tmp_path, monkeypatch):
    intelligence = _analyzed_store(tmp_path, monkeypatch)
    monkeypatch.setattr(local, "_utc_now", lambda: NOW + timedelta(days=2))
    return intelligence


def count_builds(intelligence, monkeypatch):
    counts = {"query": 0, "item": 0}
    guard = threading.Lock()
    for method, key in (("_active_revisions_query", "query"), ("_published_item", "item")):
        original = getattr(intelligence, method)
        def counted(*args, _original=original, _key=key, **kwargs):
            with guard:
                counts[_key] += 1
            return _original(*args, **kwargs)
        monkeypatch.setattr(intelligence, method, counted)
    return counts


@pytest.mark.parametrize("owner", [False, True])
@pytest.mark.parametrize("page_mode", [None, "visible"])
def test_exact_history_matches_uncached_response_and_pagination(tmp_path, monkeypatch, owner, page_mode):
    intelligence = historical_store(tmp_path, monkeypatch)
    kwargs = dict(as_of=NOW, window_hours=24, limit=4, page_mode=page_mode)
    with request_owner_access_context(owner):
        with monkeypatch.context() as uncached:
            uncached.setattr(local, "_HISTORICAL_REVISION_CACHE_MAX_BYTES", 0)
            expected = intelligence.feed(**kwargs)
            expected_second = intelligence.feed(**{**kwargs, "cursor": expected["next_cursor"]}) if expected["next_cursor"] else None
        counts = count_builds(intelligence, monkeypatch)
        cold = intelligence.feed(**kwargs)
        hot = intelligence.feed(**kwargs)
        assert cold == hot == expected
        if expected_second is not None:
            assert intelligence.feed(**{**kwargs, "cursor": cold["next_cursor"]}) == expected_second
    assert counts == {"query": 1, "item": 12}


def test_microsecond_as_of_boundary_never_reuses_a_different_window(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    earlier = NOW + timedelta(microseconds=1)
    future = NOW + timedelta(microseconds=2)
    _apply_news(CatalystEtlRepository(intelligence.db_path), [_news_change(13, 999, available_at=future)], as_of=future)
    intelligence.reconcile()
    counts = count_builds(intelligence, monkeypatch)
    first = intelligence.feed(as_of=earlier, window_hours=24, limit=100)
    later = intelligence.feed(as_of=future, window_hours=24, limit=100)
    assert 999 not in {item["news_id"] for item in first["items"]}
    assert 999 in {item["news_id"] for item in later["items"]}
    assert intelligence.feed(as_of=earlier, window_hours=24, limit=100) == first
    assert counts["query"] == 2


def test_owner_job_state_changes_without_crossing_into_guest_bundle(tmp_path, monkeypatch):
    intelligence = _analyzed_store(tmp_path, monkeypatch)
    job = intelligence.request_analysis(304, force=False)
    monkeypatch.setattr(local, "_utc_now", lambda: NOW + timedelta(days=2))
    counts = count_builds(intelligence, monkeypatch)
    def state(owner):
        with request_owner_access_context(owner):
            return next(item for item in intelligence.feed(as_of=NOW, window_hours=24, limit=100)["items"] if item["news_id"] == 304)["analysis_status"]
    assert state(False) == "not_requested"
    assert state(True) == "pending"
    with intelligence.ai_repository._connect() as connection:
        connection.execute("UPDATE ai_jobs SET status='queued',updated_at=? WHERE job_id=?", (local._iso(NOW - timedelta(seconds=1)), job["job_id"]))
        connection.commit()
    assert state(True) == "queued"
    assert state(False) == "not_requested"
    assert counts["query"] == 1


@pytest.mark.parametrize("statement", [
    "UPDATE catalyst_local_news_revisions SET raw_title='已修订的新闻标题' WHERE news_id=304",
    "DELETE FROM catalyst_local_news_revisions WHERE news_id=304",
    "UPDATE catalyst_local_analysis_links SET result_json='{\"bad\":true}' WHERE news_id=301",
    "DELETE FROM catalyst_local_analysis_result_audit WHERE job_id IN (SELECT job_id FROM catalyst_local_analysis_links WHERE news_id=301)",
])
def test_any_relevant_store_write_invalidates_historical_bundle(tmp_path, monkeypatch, statement):
    intelligence = historical_store(tmp_path, monkeypatch)
    counts = count_builds(intelligence, monkeypatch)
    intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    with intelligence._connect() as connection:
        connection.execute(statement)
        connection.commit()
    actual = intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    assert counts["query"] == 2
    with monkeypatch.context() as uncached:
        uncached.setattr(local, "_HISTORICAL_REVISION_CACHE_MAX_BYTES", 0)
        assert actual == intelligence.feed(as_of=NOW, window_hours=24, limit=100)


def test_response_mutation_cannot_change_cached_nested_context(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    original = intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    pristine = copy.deepcopy(original)
    for item in original["items"]:
        item["title"] = "tampered"
        item["source_tickers"].append("HACK")
        item["analysis_sources"].append({"url": "https://tampered.test"})
        if isinstance(item.get("analysis_input"), dict):
            item["analysis_input"]["basis"] = "tampered"
        if isinstance(item.get("analysis"), dict):
            item["analysis"]["affected_stocks"].clear()
    assert intelligence.feed(as_of=NOW, window_hours=24, limit=100) == pristine


def test_ten_historical_readers_share_one_successful_build(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    counts = count_builds(intelligence, monkeypatch)
    barrier = threading.Barrier(10)
    def read(_):
        with request_owner_access_context(False):
            barrier.wait(timeout=10)
            return intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(read, range(10)))
    assert all(result == results[0] for result in results)
    assert counts == {"query": 1, "item": 12}


def test_changed_cursor_does_not_wait_for_or_install_stale_builder(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    queried, resume = threading.Event(), threading.Event()
    original = intelligence._active_revisions_query
    delayed_thread = None
    def gated(*args, **kwargs):
        result = original(*args, **kwargs)
        if threading.current_thread() is delayed_thread:
            queried.set()
            assert resume.wait(timeout=5)
        return result
    monkeypatch.setattr(intelligence, "_active_revisions_query", gated)
    outputs, errors = [], []
    def delayed():
        try:
            with request_owner_access_context(False):
                outputs.append(intelligence.feed(as_of=NOW, window_hours=24, limit=100))
        except BaseException as error:
            errors.append(error)
    delayed_thread = threading.Thread(target=delayed)
    delayed_thread.start()
    try:
        assert queried.wait(timeout=5)
        with intelligence._connect() as connection:
            connection.execute("UPDATE catalyst_local_news_revisions SET raw_title='新版本的标题' WHERE news_id=304")
            connection.commit()
        newest = intelligence.feed(as_of=NOW, window_hours=24, limit=100)
        assert next(item for item in newest["items"] if item["news_id"] == 304)["title"] == "新版本的标题"
    finally:
        resume.set()
        delayed_thread.join(timeout=5)
    assert not errors and not delayed_thread.is_alive()
    assert intelligence.feed(as_of=NOW, window_hours=24, limit=100) == newest
    assert len(local._HISTORICAL_REVISION_CACHE) == 1


def test_failed_build_is_not_cached_and_releases_its_wait_record(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    original = intelligence._published_item
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("fixture read failed")
    monkeypatch.setattr(intelligence, "_published_item", broken)
    with pytest.raises(sqlite3.OperationalError, match="fixture read failed"):
        intelligence.feed(as_of=NOW, window_hours=24)
    assert not local._HISTORICAL_REVISION_BUILDS
    assert not local._HISTORICAL_REVISION_CACHE
    monkeypatch.setattr(intelligence, "_published_item", original)
    assert intelligence.feed(as_of=NOW, window_hours=24)["items"]


def test_history_cache_has_entry_byte_and_oversize_limits(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    for seconds in range(12):
        intelligence.feed(as_of=NOW + timedelta(seconds=seconds), window_hours=24)
    assert len(local._HISTORICAL_REVISION_CACHE) == 8
    assert local._HISTORICAL_REVISION_CACHE_BYTES <= local._HISTORICAL_REVISION_CACHE_MAX_BYTES
    one_size = max(entry[2] for entry in local._HISTORICAL_REVISION_CACHE.values())
    local._reset_revision_cache()
    monkeypatch.setattr(local, "_HISTORICAL_REVISION_CACHE_MAX_BYTES", one_size + 1024)
    for seconds in range(3):
        intelligence.feed(as_of=NOW + timedelta(seconds=seconds), window_hours=24)
    assert len(local._HISTORICAL_REVISION_CACHE) == 1
    assert local._HISTORICAL_REVISION_CACHE_BYTES <= one_size + 1024
    local._reset_revision_cache()
    monkeypatch.setattr(local, "_HISTORICAL_REVISION_CACHE_MAX_BYTES", 512)
    counts = count_builds(intelligence, monkeypatch)
    first = intelligence.feed(as_of=NOW, window_hours=24)
    assert intelligence.feed(as_of=NOW, window_hours=24) == first
    assert counts["query"] == 2
    assert not local._HISTORICAL_REVISION_CACHE
    assert local._HISTORICAL_REVISION_CACHE_BYTES == 0


def test_full_wait_registry_uses_uncached_reads_without_growing(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    placeholders = {("other", index): threading.Event() for index in range(8)}
    with local._HISTORICAL_REVISION_CACHE_LOCK:
        local._HISTORICAL_REVISION_BUILDS.update(placeholders)
    try:
        assert intelligence.feed(as_of=NOW, window_hours=24)["items"]
        assert local._HISTORICAL_REVISION_BUILDS == placeholders
        assert not local._HISTORICAL_REVISION_CACHE
    finally:
        with local._HISTORICAL_REVISION_CACHE_LOCK:
            local._HISTORICAL_REVISION_BUILDS.clear()


def test_same_path_replacement_with_equal_cursor_cannot_reuse_old_bundle(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    with intelligence._connect() as connection:
        connection.execute("PRAGMA journal_mode=DELETE")
    before = intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    replacement = tmp_path / "replacement.db"
    source = sqlite3.connect(intelligence.db_path)
    target = sqlite3.connect(replacement)
    try:
        source.backup(target)
        version = target.execute("SELECT version FROM catalyst_local_store_version WHERE id=1").fetchone()[0]
        target.execute("UPDATE catalyst_local_news_revisions SET raw_title='替换文件的新标题' WHERE news_id=304")
        target.execute("UPDATE catalyst_local_store_version SET version=? WHERE id=1", (version,))
        target.commit()
    finally:
        source.close()
        target.close()
    with intelligence._connect() as old_connection:
        original_cursor = local._revision_store_cursor(old_connection)
        os.replace(replacement, intelligence.db_path)
        assert intelligence._historical_revision_bundle(old_connection, as_of=NOW, window_hours=24) is None
    with intelligence._connect() as fresh:
        assert local._revision_store_cursor(fresh) == original_cursor
    after = intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    assert after != before
    assert next(item for item in after["items"] if item["news_id"] == 304)["title"] == "替换文件的新标题"


@pytest.mark.parametrize("owner", [False, True])
def test_cache_connection_preserves_read_only_timeout_and_close(tmp_path, monkeypatch, owner):
    intelligence = historical_store(tmp_path, monkeypatch)
    connection = None
    with request_owner_access_context(owner):
        with pytest.raises(RuntimeError, match="exit fixture"):
            with intelligence._connect() as connection:
                assert connection.row_factory is sqlite3.Row
                assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
                assert connection.execute("PRAGMA query_only").fetchone()[0] == int(not owner)
                assert connection._revision_file_identity == local._revision_file_stamp(intelligence.db_path)[:2]
                if owner:
                    connection.execute("CREATE TEMP TABLE cache_connection_write(value TEXT)")
                else:
                    with pytest.raises(sqlite3.OperationalError):
                        connection.execute("CREATE TABLE forbidden_write(value TEXT)")
                raise RuntimeError("exit fixture")
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")
    ordinary = sqlite3.connect(intelligence.db_path)
    try:
        assert intelligence._historical_revision_bundle(ordinary, as_of=NOW, window_hours=24) is None
    finally:
        ordinary.close()


def test_historical_skip_setting_is_part_of_shared_bundle_identity(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    with intelligence._connect() as connection:
        connection.execute("UPDATE catalyst_local_news_revisions SET source='Zacks',url='https://example.test/story?source=yseop_template' WHERE news_id=304")
        connection.commit()
    def state():
        with request_owner_access_context(False):
            return next(item for item in intelligence.feed(as_of=NOW, window_hours=24, limit=100)["items"] if item["news_id"] == 304)["analysis_status"]
    intelligence.scheduled_skip_template_commentary = True
    assert state() == "skipped"
    intelligence.scheduled_skip_template_commentary = False
    assert state() == "not_requested"
    intelligence.scheduled_skip_template_commentary = True
    assert state() == "skipped"


def test_unversioned_store_is_never_put_in_historical_cache(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    with intelligence._connect() as connection:
        connection.execute("DROP TABLE catalyst_local_store_version")
        connection.commit()
    counts = count_builds(intelligence, monkeypatch)
    first = intelligence.feed(as_of=NOW, window_hours=24)
    assert intelligence.feed(as_of=NOW, window_hours=24) == first
    assert counts["query"] == 2
    assert not local._HISTORICAL_REVISION_CACHE


def test_path_alias_uses_the_same_file_and_exact_historical_bundle(tmp_path, monkeypatch):
    intelligence = historical_store(tmp_path, monkeypatch)
    expected = intelligence.feed(as_of=NOW, window_hours=24)
    alias = tmp_path / "alias.db"
    alias.symlink_to(intelligence.db_path)
    alternate = local.LocalCatalystIntelligence(
        alias, intelligence.ai_repository, mode="manual", canonical_tickers=("NVDA",),
        scheduled_skip_template_commentary=intelligence.scheduled_skip_template_commentary,
    )
    def unexpected(*args, **kwargs):
        pytest.fail("same normalized file identity should reuse the exact bundle")
    monkeypatch.setattr(alternate, "_active_revisions_query", unexpected)
    assert alternate.feed(as_of=NOW, window_hours=24) == expected


def test_oversized_bundle_waiters_fall_back_concurrently_after_one_build(tmp_path, monkeypatch):
    import time
    intelligence = historical_store(tmp_path, monkeypatch)
    monkeypatch.setattr(local, "_HISTORICAL_REVISION_CACHE_MAX_BYTES", 1)
    original_query = intelligence._active_revisions_query
    original_cursor = local._revision_store_cursor
    barrier = threading.Barrier(6)
    all_fingerprints = threading.Event()
    guard = threading.Lock()
    measured = {"fingerprints": 0, "active": 0, "peak": 0, "queries": 0}
    def cursor(*args, **kwargs):
        value = original_cursor(*args, **kwargs)
        with guard:
            measured["fingerprints"] += 1
            if measured["fingerprints"] >= 6:
                all_fingerprints.set()
        return value
    def query(*args, **kwargs):
        with guard:
            measured["active"] += 1
            measured["queries"] += 1
            measured["peak"] = max(measured["peak"], measured["active"])
        try:
            assert all_fingerprints.wait(timeout=5)
            time.sleep(.04)
            return original_query(*args, **kwargs)
        finally:
            with guard:
                measured["active"] -= 1
    monkeypatch.setattr(local, "_revision_store_cursor", cursor)
    monkeypatch.setattr(intelligence, "_active_revisions_query", query)
    def read(_):
        with request_owner_access_context(False):
            barrier.wait(timeout=5)
            return intelligence.feed(as_of=NOW, window_hours=24, limit=100)
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(read, range(6)))
    assert all(result == results[0] for result in results)
    assert measured["queries"] == 6
    assert measured["peak"] > 1
    assert not local._HISTORICAL_REVISION_CACHE
    assert not local._HISTORICAL_REVISION_BUILDS
