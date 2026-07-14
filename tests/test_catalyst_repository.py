from __future__ import annotations

import base64
import copy
import json
import sqlite3
from datetime import timedelta

import pytest

from app.services.catalysts.models import (
    AffectedStockImpact,
    CalendarEvent,
    ComponentHealth,
    PublicTickerValidation,
    RemoteAnalysis,
)
from app.services.catalysts.repository import (
    DATABASE_VERSION,
    SCHEMA_CHECKSUM,
    CatalystRepository,
)
from app.services.catalysts.errors import CatalystRepositoryError, InvalidCursorError
from app.services.catalysts.shadow import compute_shadow, empty_shadow
from catalyst_support import catalyst_item, utc


DAILY_CACHE_AUDIT = {
    "strength_feature_version": "strength-feature-v1",
    "strength_score_version": "strength-score-v1",
    "normalization_version": "strength-normalization-v1",
    "range_persistence_version": "range-persistence-v1",
}


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


def validated_item(*, sequence: int, minute: int, status: str):
    item = catalyst_item(
        sequence=sequence,
        updated_at=utc(10, minute),
        analysis=True,
        ticker="XYZ",
    )
    assert item.analysis is not None
    analysis = item.analysis.model_copy(
        update={
            "stock_validations": [
                PublicTickerValidation(
                    ticker="XYZ",
                    validation_status=status,
                    validated_at=utc(10, minute),
                    focus_revision=sequence,
                    universe_version=f"fixture-{sequence}",
                )
            ]
        }
    )
    return item.model_copy(update={"source_tickers": [], "analysis": analysis})


def publish_custom_item(repository: CatalystRepository, item, *, now_minute: int) -> str:
    token = f"trusted-{item.change_sequence}-{now_minute}"
    run_id = repository.begin_sync_run("feed", snapshot_token=token, now=utc(10, now_minute))
    repository.stage_latest_page(run_id, [item])
    return repository.publish_latest(
        run_id,
        snapshot_token=token,
        data_through=item.updated_at,
        next_updated_after=item.updated_at,
        watermark_sequence=item.change_sequence,
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
        "catalyst_analysis_projections",
        "catalyst_stock_impact_projections",
        "catalyst_projection_migration_stats",
        "catalyst_calendar_event_revisions",
        "catalyst_source_health",
        "catalyst_analysis_jobs",
        "catalyst_refresh_outbox",
        "catalyst_worker_status",
        "catalyst_worker_lock",
        "focus_daily_strength_snapshots",
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
        contract_schema_version="macrolens-option-pro-v2",
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
            contract_schema_version="macrolens-option-pro-v2",
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


def test_trusted_ticker_projection_preserves_validation_lifecycle_by_item_revision(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))

    publish_custom_item(
        repository,
        validated_item(sequence=10, minute=6, status="unverified"),
        now_minute=6,
    )
    first_detail = repository.get_news(101, as_of=utc(10, 6))
    assert first_detail["analysis"]["affected_stocks"][0]["ticker"] == "XYZ"
    assert first_detail["analysis"]["stock_validations"][0]["validation_status"] == "unverified"
    assert first_detail["trusted_stock_impacts"] == []
    assert repository.ticker_feed("XYZ", as_of=utc(10, 6), window_hours=72)["items"] == []

    publish_custom_item(
        repository,
        validated_item(sequence=11, minute=7, status="canonical"),
        now_minute=7,
    )
    assert repository.ticker_feed("XYZ", as_of=utc(10, 7), window_hours=72)["items"]
    assert repository.ticker_feed("XYZ", as_of=utc(10, 6), window_hours=72)["items"] == []

    publish_custom_item(
        repository,
        validated_item(sequence=12, minute=8, status="unverified"),
        now_minute=8,
    )
    assert repository.ticker_feed("XYZ", as_of=utc(10, 8), window_hours=72)["items"] == []
    historical = repository.ticker_feed("XYZ", as_of=utc(10, 7), window_hours=72)
    assert historical["items"][0]["change_sequence"] == 11
    current = repository.get_news(101, as_of=utc(10, 8))
    assert current["analysis"]["affected_stocks"][0]["ticker"] == "XYZ"
    assert current["trusted_stock_impacts"] == []

    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM catalyst_analysis_projections"
        ).fetchone()[0] == 3
        assert connection.execute(
            "SELECT count(*) FROM catalyst_stock_impact_projections"
        ).fetchone()[0] == 1


def test_duplicate_ticker_validations_parse_but_fail_closed_in_projection(
    tmp_path,
) -> None:
    item = validated_item(sequence=10, minute=6, status="canonical")
    assert item.analysis is not None
    payload = item.analysis.model_dump(mode="json")
    payload["stock_validations"].append(
        {
            **payload["stock_validations"][0],
            "validation_status": "unverified",
        }
    )

    analysis = RemoteAnalysis.model_validate(payload)
    assert len(analysis.stock_validations) == 2

    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    publish_custom_item(
        repository,
        item.model_copy(update={"analysis": analysis}),
        now_minute=6,
    )

    detail = repository.get_news(101, as_of=utc(10, 7))
    assert detail is not None
    assert detail["analysis"]["affected_stocks"][0]["ticker"] == "XYZ"
    assert len(detail["analysis"]["stock_validations"]) == 2
    assert detail["trusted_stock_impacts"] == []
    assert repository.ticker_feed("XYZ", as_of=utc(10, 7), window_hours=72)[
        "items"
    ] == []

    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM catalyst_analysis_projections"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM catalyst_stock_impact_projections"
        ).fetchone()[0] == 0


def test_same_item_projection_payload_conflict_keeps_published_snapshot(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first_item = validated_item(sequence=10, minute=6, status="canonical")
    snapshot = publish_custom_item(repository, first_item, now_minute=6)
    conflicting = validated_item(sequence=10, minute=6, status="unverified")
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="projection-conflict", now=utc(10, 7)
    )
    repository.stage_latest_page(run_id, [conflicting])

    with pytest.raises(CatalystRepositoryError) as captured:
        repository.publish_latest(
            run_id,
            snapshot_token="projection-conflict",
            data_through=conflicting.updated_at,
            next_updated_after=conflicting.updated_at,
            watermark_sequence=10,
            now=utc(10, 7),
        )

    assert captured.value.code == "projection_payload_conflict"
    state = repository.sync_state("feed")
    assert state["current_snapshot_id"] == snapshot
    assert state["watermark_sequence"] == 10
    current = repository.get_news(101, as_of=utc(10, 7))
    assert current["trusted_stock_impacts"][0]["ticker"] == "XYZ"


def test_same_item_projection_payload_replay_is_idempotent(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    item = validated_item(sequence=10, minute=6, status="canonical")
    publish_custom_item(repository, item, now_minute=6)

    with repository.open_read_connection() as connection:
        before = tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "catalyst_item_revisions",
                "catalyst_analysis_projections",
                "catalyst_stock_impact_projections",
            )
        )

    publish_custom_item(repository, item, now_minute=7)

    with repository.open_read_connection() as connection:
        after = tuple(
            connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "catalyst_item_revisions",
                "catalyst_analysis_projections",
                "catalyst_stock_impact_projections",
            )
        )
    assert after == before == (1, 1, 1)
    current = repository.get_news(101, as_of=utc(10, 7))
    assert current["trusted_stock_impacts"][0]["ticker"] == "XYZ"


def test_filters_aggregates_and_batch_ignore_untrusted_model_tickers(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    item = validated_item(sequence=10, minute=6, status="canonical")
    assert item.analysis is not None
    untrusted = AffectedStockImpact(
        ticker="ABC",
        company="Ambiguous Corp",
        impact_score=90,
        confidence=99,
        horizon="weeks",
        mechanism="regulatory",
        reason="模型识别结果尚未通过代码验证。",
    )
    analysis = item.analysis.model_copy(
        update={
            "affected_stocks": [*item.analysis.affected_stocks, untrusted],
            "stock_validations": [
                *item.analysis.stock_validations,
                PublicTickerValidation(
                    ticker="ABC",
                    validation_status="unverified",
                    validated_at=utc(10, 6),
                    focus_revision=10,
                    universe_version="fixture-10",
                ),
            ],
        }
    )
    publish_custom_item(
        repository,
        item.model_copy(update={"analysis": analysis}),
        now_minute=6,
    )

    detail = repository.get_news(101, as_of=utc(10, 7))
    assert {row["ticker"] for row in detail["analysis"]["affected_stocks"]} == {
        "ABC",
        "XYZ",
    }
    assert [row["ticker"] for row in detail["trusted_stock_impacts"]] == ["XYZ"]
    assert repository.list_feed(
        as_of=utc(10, 7), window_hours=72, min_abs_impact=60
    )["items"] == []
    assert repository.list_feed(
        as_of=utc(10, 7), window_hours=72, min_abs_impact=50
    )["items"]
    assert repository.list_feed(
        as_of=utc(10, 7), window_hours=72, horizon="weeks"
    )["items"] == []
    assert repository.list_feed(
        as_of=utc(10, 7), window_hours=72, horizon="days"
    )["items"]
    assert repository.list_feed(
        as_of=utc(10, 7), window_hours=72, mechanism="regulatory"
    )["items"] == []
    trusted_feed = repository.list_feed(
        as_of=utc(10, 7), window_hours=72, mechanism="direct_company"
    )
    assert trusted_feed["items"]
    assert [row["ticker"] for row in trusted_feed["stock_impacts"]] == ["XYZ"]
    batch = repository.batch_tickers(
        ["ABC", "XYZ"],
        as_of=utc(10, 7),
        window_hours=72,
        limit=10,
        include_neutral=True,
    )
    assert batch["results"]["ABC"]["items"] == []
    assert batch["results"]["XYZ"]["items"]


@pytest.mark.parametrize(
    ("status", "trusted"),
    [
        ("canonical", True),
        ("valid_external", True),
        ("ambiguous", False),
        ("unverified", False),
        ("invalid", False),
    ],
)
def test_only_approved_validation_states_enter_trusted_projection(
    tmp_path,
    status,
    trusted,
) -> None:
    repository = CatalystRepository(tmp_path / f"{status}.db")
    repository.initialize(now=utc(9))
    publish_custom_item(
        repository,
        validated_item(sequence=10, minute=6, status=status),
        now_minute=6,
    )
    detail = repository.get_news(101, as_of=utc(10, 7))
    assert bool(detail["trusted_stock_impacts"]) is trusted


def test_missing_validation_fails_closed_but_keeps_raw_model_impact(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "missing.db")
    repository.initialize(now=utc(9))
    item = validated_item(sequence=10, minute=6, status="canonical")
    assert item.analysis is not None
    item = item.model_copy(
        update={
            "analysis": item.analysis.model_copy(update={"stock_validations": []})
        }
    )
    publish_custom_item(repository, item, now_minute=6)
    detail = repository.get_news(101, as_of=utc(10, 7))
    assert detail["analysis"]["affected_stocks"][0]["ticker"] == "XYZ"
    assert detail["trusted_stock_impacts"] == []
    assert repository.ticker_feed("XYZ", as_of=utc(10, 7), window_hours=72)["items"] == []


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
        schema_version="macrolens-option-pro-v2",
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
        schema_version="macrolens-option-pro-v2",
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
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    duplicate = repository.enqueue_analysis(
        101,
        content_hash="hash-revision-one",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )
    revised = repository.enqueue_analysis(
        101,
        content_hash="hash-revision-two",
        change_sequence=2,
        contract_schema_version="macrolens-option-pro-v2",
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
        ("hash-revision-one", 1, "macrolens-option-pro-v2"),
        ("hash-revision-two", 2, "macrolens-option-pro-v2"),
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
        schema_version="macrolens-option-pro-v2",
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
        schema_version="macrolens-option-pro-v2",
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


def test_daily_strength_cache_uses_trading_day_version_and_short_degraded_ttl(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    observed = utc(10)
    repository.initialize(now=observed)
    payload = {
        "universe_version": "themes-v1",
        "coverage": 1.0,
        "_focus_rows": [
            {"ticker": "AAPL", "daily_data_through": observed.isoformat()}
        ],
    }
    repository.cache_daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        universe_version="themes-v1",
        **DAILY_CACHE_AUDIT,
        coverage=1.0,
        status="active",
        payload=payload,
        data_through=observed,
        degraded_ttl_seconds=300,
        now=observed,
    )
    cached = repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        **DAILY_CACHE_AUDIT,
        now=observed + timedelta(hours=12),
    )
    assert cached is not None
    assert cached["status"] == "active"
    assert cached["payload"] == payload

    assert repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        **{
            **DAILY_CACHE_AUDIT,
            "strength_score_version": "strength-score-v-next",
        },
        now=observed + timedelta(hours=12),
    ) is None
    with repository.open_write_connection() as connection:
        connection.execute(
            "UPDATE focus_daily_strength_snapshots "
            "SET payload_json=? WHERE trading_day=? AND cache_version=?",
            (
                json.dumps({**payload, "coverage": 0.75}),
                "2026-07-10",
                "focus-cache-v1",
            ),
        )
        connection.commit()
    assert repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        **DAILY_CACHE_AUDIT,
        now=observed + timedelta(hours=12),
    ) is None

    repository.cache_daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-degraded-v1",
        universe_version="themes-v1",
        **DAILY_CACHE_AUDIT,
        coverage=1.0,
        status="degraded",
        payload=payload,
        data_through=observed,
        degraded_ttl_seconds=300,
        now=observed,
    )
    assert repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-degraded-v1",
        **DAILY_CACHE_AUDIT,
        now=observed + timedelta(seconds=299),
    ) is not None
    assert repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-degraded-v1",
        **DAILY_CACHE_AUDIT,
        now=observed + timedelta(seconds=300),
    ) is None

    with pytest.raises(ValueError, match="active or degraded"):
        repository.cache_daily_strength_snapshot(
            trading_day="2026-07-10",
            cache_version="focus-cache-unavailable-v1",
            universe_version="themes-v1",
            **DAILY_CACHE_AUDIT,
            coverage=1.0,
            status="unavailable",
            payload=payload,
            data_through=observed,
            degraded_ttl_seconds=300,
            now=observed,
        )


def test_daily_strength_cache_write_rejects_an_old_focus_fencing_token(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    observed = utc(10)
    later = observed + timedelta(seconds=31)
    repository.initialize(now=observed)
    old_token = repository.acquire_worker_lock(
        "focus-context-producer",
        "focus-old",
        lease_seconds=30,
        now=observed,
    )
    assert old_token is not None
    new_token = repository.acquire_worker_lock(
        "focus-context-producer",
        "focus-new",
        lease_seconds=30,
        now=later,
    )
    assert new_token is not None and new_token > old_token
    repository.cache_daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        universe_version="new-universe",
        **DAILY_CACHE_AUDIT,
        coverage=1.0,
        status="active",
        payload={
            "universe_version": "new-universe",
            "coverage": 1.0,
            "_focus_rows": [],
        },
        data_through=later,
        degraded_ttl_seconds=300,
        now=later,
        lock_name="focus-context-producer",
        owner_id="focus-new",
        fencing_token=new_token,
    )

    with pytest.raises(CatalystRepositoryError, match="lease was lost"):
        repository.cache_daily_strength_snapshot(
            trading_day="2026-07-10",
            cache_version="focus-cache-v1",
            universe_version="old-universe",
            **DAILY_CACHE_AUDIT,
            coverage=1.0,
            status="active",
            payload={
                "universe_version": "old-universe",
                "coverage": 1.0,
                "_focus_rows": [],
            },
            data_through=observed,
            degraded_ttl_seconds=300,
            now=later,
            lock_name="focus-context-producer",
            owner_id="focus-old",
            fencing_token=old_token,
        )

    cached = repository.daily_strength_snapshot(
        trading_day="2026-07-10",
        cache_version="focus-cache-v1",
        **DAILY_CACHE_AUDIT,
        now=later,
    )
    assert cached is not None
    assert cached["universe_version"] == "new-universe"
    assert cached["payload"]["universe_version"] == "new-universe"


def test_focus_retention_rolls_up_history_and_protects_cycle_inputs(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    observed = utc(10)
    old = observed - timedelta(days=100)
    middle_morning = observed - timedelta(days=60, hours=6)
    middle_close = observed - timedelta(days=60)
    recent = observed - timedelta(days=10)
    repository.initialize(now=old)
    with repository.open_write_connection() as connection:
        snapshots = (
            (1, old),  # protected by a completed cycle
            (2, old + timedelta(minutes=1)),  # protected by a task result
            (3, old + timedelta(minutes=2)),  # expired ordinary snapshot
            (4, middle_morning),  # compacted in favor of revision 5
            (5, middle_close),  # daily representative
            (6, recent),  # full-resolution window
            (7, observed),  # latest is always retained
        )
        for revision, timestamp in snapshots:
            connection.execute(
                """
                INSERT INTO focus_context_snapshots(
                    revision,as_of,data_through,market_session,universe_version,
                    content_hash,raw_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    revision,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    "closed",
                    "retention-v1",
                    f"hash-{revision}",
                    "{}",
                    timestamp.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO focus_context_symbols(
                    revision,ticker,dollar_volume_rank,reasons_json,raw_json
                ) VALUES(?,?,?,?,?)
                """,
                (revision, f"T{revision}", revision, "[]", "{}"),
            )
        connection.execute(
            """
            INSERT INTO catalyst_market_focus_cycles(
                remote_cycle_id,public_cycle_id,snapshot_id,prepared_revision,
                status,raw_json,cached_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "mfc_remote_retention",
                "mfc_public_retention",
                None,
                1,
                "completed",
                json.dumps({"focus_revision": 1}),
                observed.isoformat(),
            ),
        )
        connection.execute(
            """
            INSERT INTO catalyst_market_focus_jobs(
                local_cycle_id,request_key,expected_prepared_revision,
                last_consumed_revision_at_request,execution_number,status,
                result_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "mfc_job_retention",
                "batch:retention:1",
                1,
                0,
                1,
                "completed",
                json.dumps({"focus_revision": 2}),
                observed.isoformat(),
                observed.isoformat(),
            ),
        )
        connection.commit()
    repository.cache_daily_strength_snapshot(
        trading_day="2026-01-01",
        cache_version="retention-v1",
        universe_version="themes-v1",
        **DAILY_CACHE_AUDIT,
        coverage=1.0,
        status="active",
        payload={
            "universe_version": "themes-v1",
            "coverage": 1.0,
            "_focus_rows": [{"ticker": "AAPL"}],
        },
        data_through=old,
        degraded_ttl_seconds=300,
        now=old,
    )

    statements: list[str] = []
    open_write_connection = repository.open_write_connection

    def traced_write_connection():
        connection = open_write_connection()
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(repository, "open_write_connection", traced_write_connection)
    runs = [
        repository.prune_focus_retention(
            snapshot_days=90,
            snapshot_full_resolution_days=30,
            snapshot_daily_rollup_enabled=True,
            daily_strength_days=30,
            batch_size=1,
            now=observed,
        )
        for _ in range(3)
    ]

    assert [counts["deleted"] for counts in runs] == [1, 1, 0]
    assert [counts["daily_strength_snapshots"] for counts in runs] == [0, 0, 1]
    assert sum(counts["focus_snapshots"] for counts in runs) == 2
    assert sum(counts["rollup_created"] for counts in runs) == 1
    assert all(counts["retained"] >= 5 for counts in runs)
    assert all(counts["protected"] == 3 for counts in runs)
    assert all(counts["foreign_key_violations"] == 0 for counts in runs)
    assert all(counts["batches"] == 1 for counts in runs)
    assert all(counts["database_bytes"] >= counts["live_bytes"] > 0 for counts in runs)
    assert sum("BEGIN IMMEDIATE" in statement for statement in statements) == 3
    assert any(
        "DELETE FROM focus_context_snapshots" in statement
        and "LIMIT 1" in statement
        for statement in statements
    )
    assert any(
        "DELETE FROM focus_daily_strength_snapshots" in statement
        and "LIMIT 1" in statement
        for statement in statements
    )
    assert not any("json_tree" in statement.lower() for statement in statements)
    assert not any(
        "foreign_key_check" in statement.lower() for statement in statements
    )
    assert any(
        "focus_reference_generation" in statement for statement in statements
    )
    with repository.open_read_connection() as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT revision FROM focus_context_snapshots ORDER BY revision"
            )
        ] == [1, 2, 5, 6, 7]
        assert [
            row[0]
            for row in connection.execute(
                "SELECT revision FROM focus_context_symbols ORDER BY revision"
            )
        ] == [1, 2, 5, 6, 7]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_focus_retention_defers_when_reference_generation_changes(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    observed = utc(10)
    old = observed - timedelta(days=100)
    repository.initialize(now=old)
    with repository.open_write_connection() as connection:
        for revision, timestamp in (
            (1, old),
            (2, old + timedelta(minutes=1)),
            (3, observed),
        ):
            connection.execute(
                """
                INSERT INTO focus_context_snapshots(
                    revision,as_of,data_through,market_session,universe_version,
                    content_hash,raw_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    revision,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    "closed",
                    "retention-v1",
                    f"hash-{revision}",
                    "{}",
                    timestamp.isoformat(),
                ),
            )
        connection.commit()

    open_write_connection = repository.open_write_connection
    injected = False

    def inject_reference_before_writer_lock():
        nonlocal injected
        if not injected:
            injected = True
            with open_write_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO catalyst_market_focus_cycles(
                        remote_cycle_id,public_cycle_id,snapshot_id,
                        prepared_revision,status,raw_json,cached_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        "mfc_remote_race",
                        "mfc_public_race",
                        None,
                        1,
                        "completed",
                        json.dumps({"focus_revision": 1}),
                        observed.isoformat(),
                    ),
                )
                connection.commit()
        return open_write_connection()

    monkeypatch.setattr(
        repository,
        "open_write_connection",
        inject_reference_before_writer_lock,
    )
    counts = repository.prune_focus_retention(
        snapshot_days=90,
        snapshot_full_resolution_days=30,
        daily_strength_days=30,
        batch_size=2,
        now=observed,
    )

    assert counts["focus_snapshots"] == 0
    assert counts["deleted"] == 0
    with repository.open_read_connection() as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT revision FROM focus_context_snapshots ORDER BY revision"
            )
        ] == [1, 2, 3]
        assert connection.execute(
            "SELECT generation FROM focus_reference_generation WHERE singleton=1"
        ).fetchone()[0] >= 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
