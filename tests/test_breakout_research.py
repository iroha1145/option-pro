from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.research import (
    build_shadow_correlations,
    export_events,
    export_shadow_correlations,
    export_shadow_summary,
    export_shadows,
    load_completed_events,
    load_completed_shadows,
    main,
    summarize_shadows,
)


NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def _event(event_id: str, ticker: str, at: datetime, priority: float) -> dict:
    return {
        "event_id": event_id,
        "trading_date": at.date(),
        "ticker": ticker,
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "previous_state": "WATCHING",
        "transition_reason": "pivot_crossed",
        "event_at": at,
        "first_seen_at": at,
        "last_seen_at": at,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": "fixture-snapshot",
        "scores": {
            "alert_priority_score": priority,
            "data_confidence_score": 90.0,
        },
    }


def _publish(
    repository: BreakoutRepository,
    *,
    at: datetime,
    event_id: str,
    ticker: str,
    production_score: float,
    hypothetical_score: float,
    production_rank: int,
    hypothetical_rank: int,
) -> str:
    scan_run_id = repository.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"database": "breakout-db-v1"},
    )
    repository.publish_scan(
        scan_run_id,
        {
            "provider_snapshot": {
                "provider": "fixture",
                "status": "active",
                "as_of": at,
                "session": "regular",
                "schema_version": "fixture-v1",
                "warnings": [],
                "candidates": [],
            },
            "events": [_event(event_id, ticker, at, production_score)],
            "range_persistence_shadow": [
                {
                    "event_id": event_id,
                    "ticker": ticker,
                    "production_score": production_score,
                    "hypothetical_score": hypothetical_score,
                    "production_rank": production_rank,
                    "hypothetical_rank": hypothetical_rank,
                    "rank_delta": production_rank - hypothetical_rank,
                    "version": "range-persistence-v1",
                    "production_unchanged": True,
                }
            ],
        },
        now=at,
    )
    return scan_run_id


@pytest.fixture
def research_database(tmp_path):
    path = tmp_path / "breakouts.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    completed = _publish(
        repository,
        at=NOW,
        event_id="event-completed",
        ticker="AAPL",
        production_score=70,
        hypothetical_score=74,
        production_rank=2,
        hypothetical_rank=1,
    )
    hidden = _publish(
        repository,
        at=NOW + timedelta(minutes=5),
        event_id="event-not-completed",
        ticker="MSFT",
        production_score=80,
        hypothetical_score=75,
        production_rank=1,
        hypothetical_rank=2,
    )
    # A failed/abandoned publication attachment must never enter research exports.
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE breakout_scan_runs SET status='failed' WHERE scan_run_id=?", (hidden,)
        )
    return path, completed


def test_loaders_read_completed_publications_only_and_do_not_mutate_database(
    research_database,
):
    path, completed_scan = research_database
    before = path.read_bytes()

    events = load_completed_events(path)
    shadows = load_completed_shadows(path)

    assert [row["scan_run_id"] for row in events] == [completed_scan]
    assert [row["ticker"] for row in events] == ["AAPL"]
    assert events[0]["event_snapshot"]["event_id"] == "event-completed"
    assert [row["scan_run_id"] for row in shadows] == [completed_scan]
    assert shadows[0]["shadow"]["production_unchanged"] is True
    assert path.read_bytes() == before

    reader = BreakoutRepository(path, read_only=True).open_read_connection()
    try:
        assert reader.execute("PRAGMA query_only").fetchone()[0] == 1
    finally:
        reader.close()


def test_csv_and_json_exports_have_bounded_completed_rows(research_database, tmp_path):
    path, completed_scan = research_database
    events_path = tmp_path / "events.csv"
    shadows_path = tmp_path / "shadows.json"

    assert export_events(path, events_path) == 1
    assert export_shadows(path, shadows_path) == 1

    with events_path.open(newline="", encoding="utf-8") as handle:
        events = list(csv.DictReader(handle))
    shadows = json.loads(shadows_path.read_text(encoding="utf-8"))
    assert events[0]["scan_run_id"] == completed_scan
    assert json.loads(events[0]["event_snapshot"])["ticker"] == "AAPL"
    assert shadows[0]["scan_run_id"] == completed_scan
    assert shadows[0]["shadow"]["production_unchanged"] is True


def test_summary_is_descriptive_and_handles_missing_or_constant_values():
    summary = summarize_shadows(
        [
            {
                "scan_run_id": "scan-1",
                "published_at": "2026-07-13T14:00:00Z",
                "event_id": "event-1",
                "ticker": "AAPL",
                "version": "range-persistence-v1",
                "production_score": 70,
                "hypothetical_score": 74,
                "rank_delta": 1,
            },
            {
                "scan_run_id": "scan-2",
                "published_at": "2026-07-13T14:05:00Z",
                "event_id": "event-1",
                "ticker": "AAPL",
                "version": "range-persistence-v1",
                "production_score": 70,
                "hypothetical_score": 74,
                "rank_delta": None,
            },
        ]
    )

    assert summary["analysis_type"] == "descriptive_shadow_only"
    assert summary["decision_status"] == "insufficient_for_production_decision"
    assert summary["observation_count"] == 2
    assert summary["unique_event_count"] == 1
    assert summary["score_comparison"]["mean_hypothetical_minus_production"] == 4
    assert summary["score_comparison"]["production_hypothetical_pearson"] is None
    assert summary["rank_comparison"]["changed_rate"] == 1
    assert any("chronological" in item for item in summary["limitations"])


def test_correlation_export_uses_available_pairs_and_reports_sample_count(tmp_path):
    records = [
        {
            "production_score": 60 + index,
            "hypothetical_score": 61 + index * 2,
            "shadow": {
                "feature": {
                    "range_persistence": 40 + index * 3,
                    "range_persistence_slope_5d": None if index == 0 else index,
                }
            },
        }
        for index in range(3)
    ]
    correlations = build_shadow_correlations(
        records,
        fields=("production_score", "range_persistence", "range_persistence_slope_5d"),
    )
    by_pair = {
        (row["left_field"], row["right_field"]): row
        for row in correlations["pairs"]
    }
    production_range = by_pair[("production_score", "range_persistence")]
    assert production_range["paired_count"] == 3
    assert production_range["pearson"] == 1
    sparse = by_pair[("range_persistence", "range_persistence_slope_5d")]
    assert sparse["paired_count"] == 2
    assert correlations["decision_status"] == "insufficient_for_production_decision"


def test_cli_exports_and_rejects_database_as_output(research_database, tmp_path, capsys):
    path, _ = research_database
    events_path = tmp_path / "events.json"
    shadows_path = tmp_path / "shadows.csv"
    correlations_path = tmp_path / "correlations.csv"
    summary_path = tmp_path / "summary.json"

    assert (
        main(
            [
                "--db",
                str(path),
                "--events-out",
                str(events_path),
                "--shadows-out",
                str(shadows_path),
                "--correlations-out",
                str(correlations_path),
                "--summary-out",
                str(summary_path),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["event_rows"] == 1
    assert result["shadow_rows"] == 1
    assert result["correlation_pairs"] > 1
    assert result["summary"]["observation_count"] == 1
    assert export_shadow_summary(path, tmp_path / "second-summary.json")[
        "observation_count"
    ] == 1
    assert export_shadow_correlations(path, tmp_path / "second-correlations.json")[
        "observation_count"
    ] == 1

    with pytest.raises(ValueError, match="production database"):
        export_events(path, path)
