from __future__ import annotations

import base64
import copy
import json
import sqlite3
from datetime import timedelta

import pytest

from app.services.catalysts.models import CalendarEvent, ComponentHealth
from app.services.catalysts.repository import (
    DATABASE_VERSION,
    SCHEMA_CHECKSUM,
    CatalystRepository,
)
from app.services.catalysts.errors import InvalidCursorError
from app.services.catalysts.shadow import compute_shadow, empty_shadow
from catalyst_support import catalyst_item, utc


class CountingRepository(CatalystRepository):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.read_statements = []

    def open_read_connection(self):
        connection = super().open_read_connection()
        connection.set_trace_callback(
            lambda statement: self.read_statements.append(statement)
            if statement.lstrip().upper().startswith(("SELECT", "WITH"))
            else None
        )
        return connection


def publish_item(
    repository: CatalystRepository,
    *,
    sequence: int,
    analyzed: bool,
    updated_minute: int,
    now_minute: int,
) -> str:
    item = catalyst_item(
        sequence=sequence,
        updated_at=utc(10, updated_minute),
        analysis=analyzed,
    )
    run_id = repository.begin_sync_run(
        "feed", snapshot_token=f"snapshot-{sequence:04d}", now=utc(10, now_minute)
    )
    repository.stage_latest_page(run_id, [item])
    return repository.publish_latest(
        run_id,
        snapshot_token=f"snapshot-{sequence:04d}",
        data_through=utc(10, updated_minute),
        next_updated_after=utc(10, updated_minute),
        watermark_sequence=sequence,
        now=utc(10, now_minute),
    )


def test_cache_schema_uses_wal_checksum_and_query_only_readers(tmp_path) -> None:
    path = tmp_path / "catalyst-cache.db"
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    schema = repository.check_schema()
    assert schema["schema_version"] == DATABASE_VERSION
    assert schema["schema_checksum"] == SCHEMA_CHECKSUM
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "catalyst_sync_runs",
        "catalyst_sync_state",
        "catalyst_staging_items",
        "catalyst_item_revisions",
        "catalyst_analysis_revisions",
        "catalyst_stock_impacts",
        "catalyst_calendar_event_revisions",
        "catalyst_source_health",
        "catalyst_analysis_jobs",
        "catalyst_refresh_outbox",
        "catalyst_worker_status",
        "catalyst_worker_lock",
    }.issubset(tables)
    reader = CatalystRepository(path, read_only=True)
    connection = reader.open_read_connection()
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden(value INTEGER)")
    finally:
        connection.close()


def test_point_in_time_hides_future_analysis_but_keeps_raw_news(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    publish_item(repository, sequence=1, analyzed=False, updated_minute=4, now_minute=4)
    publish_item(repository, sequence=2, analyzed=True, updated_minute=6, now_minute=6)

    historical = repository.get_news(101, as_of=utc(10, 5))
    assert historical is not None
    assert historical["title"].startswith("NVIDIA")
    assert historical["analysis"] is None
    assert historical["classification"] is None
    assert historical["impact_score"] is None
    assert historical["confidence"] is None
    assert historical["analysis_status"] == "not_requested"

    current = repository.get_news(101, as_of=utc(10, 7))
    assert current is not None
    assert current["analysis"]["classification"] == "bullish"
    assert current["available_at"] == "2026-07-11T10:06:00Z"
    assert current["analysis"]["affected_stocks"][0]["ticker"] == "NVDA"


def test_point_in_time_job_projection_hides_future_lifecycle_events(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    job = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 6),
    )

    def visible_at_cutoff():
        return repository.latest_job_for_news(
            101,
            content_hash="content-hash-101",
            change_sequence=2,
            contract_schema_version="macrolens-option-pro-v1",
            model="gpt-5.6-terra",
            reasoning="max",
            as_of=utc(10, 7),
        )

    assert visible_at_cutoff()["job_id"] == job["job_id"]
    future = "2026-07-11T10:08:00Z"
    original = "2026-07-11T10:06:00Z"
    for column in (
        "created_at",
        "updated_at",
        "submitted_at",
        "completed_at",
        "cancel_requested_at",
    ):
        with sqlite3.connect(path) as connection:
            connection.execute(
                f"UPDATE catalyst_analysis_jobs SET {column}=? WHERE local_job_id=?",
                (future, job["job_id"]),
            )
            connection.commit()
        assert visible_at_cutoff() is None, column
        with sqlite3.connect(path) as connection:
            connection.execute(
                f"UPDATE catalyst_analysis_jobs SET {column}=? WHERE local_job_id=?",
                (original if column in {"created_at", "updated_at"} else None, job["job_id"]),
            )
            connection.commit()


def test_analysis_is_bound_to_the_exact_news_revision(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))

    analyzed_revision = catalyst_item(
        sequence=1, updated_at=utc(10, 6), analysis=True
    ).model_copy(update={"content_hash": "content-hash-101-revision-1"})
    first_run = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-analyzed-revision", now=utc(10, 6)
    )
    repository.stage_latest_page(first_run, [analyzed_revision])
    repository.publish_latest(
        first_run,
        snapshot_token="snapshot-analyzed-revision",
        data_through=utc(10, 6),
        next_updated_after=utc(10, 6),
        watermark_sequence=1,
        now=utc(10, 6),
    )

    unanalysed_revision = catalyst_item(
        sequence=2, updated_at=utc(10, 8), analysis=False
    ).model_copy(
        update={
            "content_hash": "content-hash-101-revision-2",
            "title": "NVIDIA publishes corrected product details",
        }
    )
    second_run = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-unanalysed-revision", now=utc(10, 8)
    )
    repository.stage_latest_page(second_run, [unanalysed_revision])
    repository.publish_latest(
        second_run,
        snapshot_token="snapshot-unanalysed-revision",
        data_through=utc(10, 8),
        next_updated_after=utc(10, 8),
        watermark_sequence=2,
        now=utc(10, 8),
    )

    historical = repository.get_news(101, as_of=utc(10, 7))
    current = repository.get_news(101, as_of=utc(10, 9))
    assert historical["content_hash"] == "content-hash-101-revision-1"
    assert historical["analysis"]["classification"] == "bullish"
    assert current["content_hash"] == "content-hash-101-revision-2"
    assert current["analysis"] is None
    assert current["analysis_status"] == "not_requested"


def test_cursor_rejects_wrong_types_in_snapshot_fields() -> None:
    valid = {
        "v": 1,
        "q": "a" * 24,
        "as_of": "2026-07-11T10:07:00Z",
        "cutoff": "2026-07-11T10:08:00Z",
        "last": ["2026-07-11T10:00:00Z", 101],
    }

    def encode(payload) -> str:
        return base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).decode().rstrip("=")

    malformed = []
    numeric_cutoff = dict(valid, cutoff=123)
    malformed.append(numeric_cutoff)
    object_last = dict(valid, last={"published_at": valid["last"][0], "news_id": 101})
    malformed.append(object_last)
    string_news_id = dict(valid, last=[valid["last"][0], "101"])
    malformed.append(string_news_id)

    for payload in malformed:
        with pytest.raises(InvalidCursorError):
            CatalystRepository._decode_cursor(encode(payload))


def test_atomic_publish_failure_keeps_old_snapshot_and_watermark(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first_snapshot = publish_item(
        repository, sequence=1, analyzed=False, updated_minute=4, now_minute=4
    )
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-0002", now=utc(10, 6)
    )
    repository.stage_latest_page(
        run_id,
        [catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
    )

    def fail(phase, _connection):
        if phase == "before_complete":
            raise RuntimeError("injected atomic publish failure")

    repository._publish_hook = fail
    with pytest.raises(RuntimeError, match="injected atomic"):
        repository.publish_latest(
            run_id,
            snapshot_token="snapshot-0002",
            data_through=utc(10, 6),
            next_updated_after=utc(10, 6),
            watermark_sequence=2,
            now=utc(10, 6),
        )

    state = repository.sync_state("feed")
    assert state["current_snapshot_id"] == first_snapshot
    assert state["watermark_sequence"] == 1
    assert repository.get_news(101, as_of=utc(10, 7))["analysis"] is None


def test_calendar_revisions_preserve_late_actual_for_historical_as_of(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(8))
    forecast = CalendarEvent(
        event_id="event-0001",
        currency="USD",
        title="Consumer Price Index",
        impact="high",
        scheduled_at=utc(11),
        forecast="3.0%",
        previous="3.1%",
        actual=None,
        is_stale=False,
        source_fetched_at=utc(9),
        available_at=utc(9),
    )
    first = repository.begin_sync_run("calendar", now=utc(9))
    repository.stage_calendar(first, [forecast])
    repository.publish_calendar(first, data_through=utc(9), now=utc(9))

    actual = forecast.model_copy(
        update={
            "actual": "2.9%",
            "source_fetched_at": utc(11, 5),
            "available_at": utc(11, 5),
        }
    )
    second = repository.begin_sync_run("calendar", now=utc(11, 5))
    repository.stage_calendar(second, [actual])
    repository.publish_calendar(second, data_through=utc(11, 5), now=utc(11, 5))

    before = repository.list_calendar(
        date_from=utc(10), date_to=utc(12), as_of=utc(10), currencies=None
    )
    after = repository.list_calendar(
        date_from=utc(10), date_to=utc(12), as_of=utc(11, 10), currencies=None
    )
    assert before["items"][0]["actual"] is None
    assert after["items"][0]["actual"] == "2.9%"


def test_worker_lock_uses_lease_and_monotonic_fencing_tokens(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first = repository.acquire_worker_lock(
        "catalyst-sync-worker", "worker-a", lease_seconds=30, now=utc(9)
    )
    assert first == 1
    assert repository.acquire_worker_lock(
        "catalyst-sync-worker", "worker-b", lease_seconds=30, now=utc(9, 0)
    ) is None
    second = repository.acquire_worker_lock(
        "catalyst-sync-worker", "worker-b", lease_seconds=30, now=utc(9, 1)
    )
    assert second == 2
    assert not repository.renew_worker_lock(
        "catalyst-sync-worker", "worker-a", first, lease_seconds=30, now=utc(9, 1)
    )


def test_source_health_persists_attempt_counters_and_null_disabled_semantics(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    repository.publish_health(
        status="degraded",
        data_through=utc(9),
        sources={
            "wire": ComponentHealth(
                status="degraded",
                last_attempt_at=utc(9),
                last_success_at=utc(8),
                data_through=utc(8),
                consecutive_failures=2,
                next_attempt_at=utc(10),
                raw_count=120,
                inserted_count=85,
                duplicates_count=35,
                detail="retry scheduled",
            ),
            "optional": ComponentHealth(status="disabled"),
        },
        observed_at=utc(9),
    )
    payload = repository.status_snapshot(
        stale_ttl_seconds=86_400,
        feed_interval_seconds=120,
        action_enabled=False,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v1",
        now=utc(9),
    )
    sources = {item["status"]: item for item in payload["sources"]}
    assert {item["source"] for item in payload["sources"]} == {"wire", "optional"}
    assert sources["degraded"]["last_attempt_at"] == "2026-07-11T09:00:00Z"
    assert sources["degraded"]["raw_count"] == 120
    assert sources["degraded"]["duplicates_count"] == 35
    assert sources["disabled"]["last_attempt_at"] is None
    assert sources["disabled"]["raw_count"] is None


def test_status_uses_remote_runtime_and_degrades_on_model_or_reasoning_drift(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    publish_item(repository, sequence=1, analyzed=False, updated_minute=4, now_minute=4)
    repository.publish_health(
        status="ok",
        data_through=utc(10, 4),
        sources={},
        model="gpt-4o-mini",
        reasoning="low",
        execution_mode="background",
        analysis_trigger_enabled=True,
        observed_at=utc(10, 4),
    )
    status = repository.status_snapshot(
        stale_ttl_seconds=86_400,
        feed_interval_seconds=120,
        action_enabled=True,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v1",
        now=utc(10, 5),
    )
    assert status["status"] == "degraded"
    assert status["model"] == "gpt-4o-mini"
    assert status["reasoning"] == "low"
    assert status["execution_mode"] == "background"
    assert status["expected_model"] == "gpt-5.6-terra"
    assert status["analysis_trigger_enabled"] is False
    assert "remote_model_mismatch" in status["warnings"]
    assert "remote_reasoning_mismatch" in status["warnings"]


def test_analysis_proxy_idempotency_is_bound_to_news_revision_and_contract(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first = repository.enqueue_analysis(
        101,
        content_hash="hash-revision-one",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    duplicate = repository.enqueue_analysis(
        101,
        content_hash="hash-revision-one",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )
    revised = repository.enqueue_analysis(
        101,
        content_hash="hash-revision-two",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v1",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 2),
    )
    assert duplicate["job_id"] == first["job_id"]
    assert revised["job_id"] != first["job_id"]
    with repository.open_read_connection() as connection:
        rows = connection.execute(
            "SELECT content_hash,change_sequence,contract_schema_version FROM catalyst_analysis_jobs ORDER BY created_at"
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        ("hash-revision-one", 1, "macrolens-option-pro-v1"),
        ("hash-revision-two", 2, "macrolens-option-pro-v1"),
    ]


def test_remote_and_source_health_cannot_be_reported_as_active(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    publish_item(repository, sequence=1, analyzed=False, updated_minute=4, now_minute=4)
    repository.publish_health(
        status="degraded",
        data_through=utc(10, 4),
        sources={"wire": ComponentHealth(status="degraded")},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        analysis_trigger_enabled=True,
        observed_at=utc(10, 4),
    )
    degraded = repository.status_snapshot(
        stale_ttl_seconds=86_400,
        feed_interval_seconds=120,
        action_enabled=True,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v1",
        now=utc(10, 5),
    )
    assert degraded["status"] == "degraded"
    assert "remote_health_degraded" in degraded["warnings"]
    assert "source_degraded" in degraded["warnings"]

    repository.publish_health(
        status="unavailable",
        data_through=utc(10, 4),
        sources={"wire": ComponentHealth(status="unavailable")},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        analysis_trigger_enabled=False,
        observed_at=utc(10, 6),
    )
    unavailable = repository.status_snapshot(
        stale_ttl_seconds=86_400,
        feed_interval_seconds=120,
        action_enabled=True,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v1",
        now=utc(10, 7),
    )
    assert unavailable["status"] == "stale"
    assert "remote_health_unavailable" in unavailable["warnings"]


def test_shadow_is_deduplicated_null_aware_and_never_mutates_scores() -> None:
    assert compute_shadow([], as_of=utc(12)) == empty_shadow()
    source = catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True).model_dump(mode="json")
    source["impact_score"] = 55
    source["confidence"] = 76
    production = {"intrinsic_score": 91.0, "ranking_score": 88.0}
    before = copy.deepcopy(production)
    shadow = compute_shadow([source, copy.deepcopy(source)], as_of=utc(12))
    assert shadow["catalyst_count_6h"] == 1
    assert shadow["catalyst_positive_count"] == 1
    assert shadow["catalyst_weighted_impact"] == pytest.approx(55)
    assert production == before

    future = copy.deepcopy(source)
    future["available_at"] = (utc(12) + timedelta(minutes=1)).isoformat()
    hidden = compute_shadow([future], as_of=utc(12))
    assert hidden["catalyst_weighted_impact"] is None
    assert hidden["catalyst_confidence"] is None


def test_fifty_ticker_batch_uses_one_visible_snapshot_not_fifty_full_scans(tmp_path) -> None:
    repository = CountingRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    tickers = [f"T{index:02d}" for index in range(50)]
    items = [
        catalyst_item(
            sequence=index,
            updated_at=utc(10, 6) if index == 1 else utc(10, 4),
            analysis=index == 1,
            news_id=10_000 + index,
            ticker=tickers[(index - 1) % len(tickers)],
        )
        for index in range(1, 1001)
    ]
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-large-batch", now=utc(10, 6)
    )
    repository.stage_latest_page(run_id, items)
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-large-batch",
        data_through=utc(10, 6),
        next_updated_after=utc(10, 6),
        watermark_sequence=1000,
        now=utc(10, 6),
    )

    repository.read_statements.clear()
    payload = repository.batch_tickers(
        tickers,
        as_of=utc(10, 7),
        window_hours=72,
        limit=20,
        min_confidence=None,
        include_neutral=False,
    )
    assert len(payload["results"]) == 50
    assert all(len(result["items"]) == 20 for result in payload["results"].values())
    # visible item+analysis snapshot, all visible impacts, and sync state.
    assert len(repository.read_statements) <= 3
    main_query = repository.read_statements[0]
    assert "COALESCE(published_at,fetched_at)>=" in main_query
    assert "catalyst_item_tickers" in main_query

    repository.read_statements.clear()
    single = repository.list_feed(
        as_of=utc(10, 7), window_hours=72, ticker="T00", limit=100
    )
    assert single["items"]
    assert all(
        "T00" in item["source_tickers"]
        or any(impact["ticker"] == "T00" for impact in (item.get("ticker_impacts") or []))
        for item in single["items"]
    )


def test_latest_revision_cannot_resurrect_an_older_window_or_source_match(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(0))
    first_window = catalyst_item(
        sequence=1, updated_at=utc(10, 4), analysis=False, news_id=201
    )
    first_source = catalyst_item(
        sequence=2, updated_at=utc(10, 4), analysis=False, news_id=202
    )
    first = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-before-revisions", now=utc(10, 4)
    )
    repository.stage_latest_page(first, [first_window, first_source])
    repository.publish_latest(
        first,
        snapshot_token="snapshot-before-revisions",
        data_through=utc(10, 4),
        next_updated_after=utc(10, 4),
        watermark_sequence=2,
        now=utc(10, 4),
    )

    moved_outside_window = first_window.model_copy(
        update={
            "change_sequence": 3,
            "updated_at": utc(11),
            "published_at": utc(1),
        }
    )
    moved_to_new_source = first_source.model_copy(
        update={
            "change_sequence": 4,
            "updated_at": utc(11),
            "source": "Other Wire",
        }
    )
    second = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-after-revisions", now=utc(11)
    )
    repository.stage_latest_page(second, [moved_outside_window, moved_to_new_source])
    repository.publish_latest(
        second,
        snapshot_token="snapshot-after-revisions",
        data_through=utc(11),
        next_updated_after=utc(11),
        watermark_sequence=4,
        now=utc(11),
    )

    recent = repository.list_feed(as_of=utc(12), window_hours=6, limit=20)
    assert 201 not in {item["news_id"] for item in recent["items"]}
    assert 202 in {item["news_id"] for item in recent["items"]}

    old_source = repository.list_feed(
        as_of=utc(12), window_hours=72, source="Fixture Wire", limit=20
    )
    assert 202 not in {item["news_id"] for item in old_source["items"]}
