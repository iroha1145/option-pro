from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.breakouts.research import EVENT_FIELDS
from app.services.breakouts.research_validation import (
    PRICE_DATA_SCHEMA_VERSION,
    attach_forward_return_labels,
    merge_completed_research_observations,
)


def _dataset() -> dict:
    return {
        "schema_version": PRICE_DATA_SCHEMA_VERSION,
        "dataset_id": "trigger-anchor-fixture",
        "source": "fixture",
        "adjustment": "unadjusted",
        "timezone": "America/New_York",
        "calendar": "XNYS",
        "as_of": "2026-01-31T23:59:59Z",
        "content_sha256": "b" * 64,
        "prices": {
            "AAPL": [
                {"date": "2026-01-05", "close": 105.0},
                {"date": "2026-01-06", "close": 110.0},
                {"date": "2026-01-07", "close": 120.0},
            ]
        },
    }


def test_forward_labels_anchor_to_triggered_at_not_first_or_last_seen() -> None:
    first_seen_at = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    triggered_at = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)
    observation = {
        "observation_id": "trigger-anchor:AAPL",
        "ticker": "AAPL",
        # The stable identity started on Jan 2, but post-breakout returns start
        # only after the Jan 5 trigger.
        "trading_date": "2026-01-02",
        "lifecycle_state": "TRIGGERED",
        "first_seen_at": first_seen_at.isoformat(),
        "triggered_at": triggered_at.isoformat(),
        "state_changed_at": triggered_at.isoformat(),
        "last_seen_at": "2026-01-05T15:15:00+00:00",
        "event_at": triggered_at.isoformat(),
        "raw_as_of": triggered_at.isoformat(),
        "feature_cutoff_at": triggered_at.isoformat(),
        "published_at": "2026-01-05T15:01:00+00:00",
        "event_price": 100.0,
    }

    result = attach_forward_return_labels([observation], _dataset(), horizons=(1,))
    row = result["observations"][0]
    assert row["label_entry_at"] == triggered_at.isoformat()
    assert row["label_entry_type"] == "triggered_event_mark"
    assert row["trading_date"] == "2026-01-05"
    assert row["labels"]["1"]["end_date"] == "2026-01-06"
    assert row["labels"]["1"]["forward_return"] == pytest.approx(0.10)


def test_untriggered_watching_event_never_receives_post_breakout_label() -> None:
    first_seen_at = datetime(2026, 1, 2, 15, 0, tzinfo=timezone.utc)
    observation = {
        "observation_id": "watching:AAPL",
        "ticker": "AAPL",
        "trading_date": "2026-01-02",
        "lifecycle_state": "WATCHING",
        "first_seen_at": first_seen_at.isoformat(),
        "triggered_at": None,
        "state_changed_at": first_seen_at.isoformat(),
        "last_seen_at": first_seen_at.isoformat(),
        "event_at": first_seen_at.isoformat(),
        "raw_as_of": first_seen_at.isoformat(),
        "feature_cutoff_at": first_seen_at.isoformat(),
        "published_at": "2026-01-02T15:01:00+00:00",
        "event_price": 100.0,
    }

    result = attach_forward_return_labels([observation], _dataset(), horizons=(1,))
    row = result["observations"][0]
    assert result["status"] == "unavailable"
    assert row["label_entry_at"] is None
    assert row["label_status"] == "unavailable"
    assert row["labels"]["1"]["status"] == "unavailable"
    assert row["labels"]["1"]["reason"] == "missing_triggered_at"
    assert row["labels"]["1"]["forward_return"] is None


def test_research_event_export_declares_all_event_clocks() -> None:
    assert {
        "first_seen_at",
        "triggered_at",
        "state_changed_at",
        "last_seen_at",
    }.issubset(EVENT_FIELDS)


def test_research_merge_uses_first_triggered_snapshot_after_watching() -> None:
    first_seen_at = "2026-01-02T15:00:00+00:00"
    triggered_at = "2026-01-05T15:00:00+00:00"

    def event(scan_id: str, published_at: str, state: str, trigger) -> dict:
        snapshot = {
            "ticker": "AAPL",
            "trading_date": "2026-01-02",
            "lifecycle_state": state,
            "event_at": trigger or first_seen_at,
            "first_seen_at": first_seen_at,
            "triggered_at": trigger,
            "state_changed_at": trigger or first_seen_at,
            "last_seen_at": trigger or first_seen_at,
            "event_price": 100.0,
            "features": {
                "raw_as_of": trigger or first_seen_at,
                "feature_cutoff_at": trigger or first_seen_at,
            },
        }
        return {
            "scan_run_id": scan_id,
            "published_at": published_at,
            "event_id": "event-anchor-merge",
            "ticker": "AAPL",
            "lifecycle_state": state,
            "event_at": snapshot["event_at"],
            "first_seen_at": first_seen_at,
            "triggered_at": trigger,
            "state_changed_at": snapshot["state_changed_at"],
            "last_seen_at": snapshot["last_seen_at"],
            "event_snapshot": snapshot,
        }

    def shadow(scan_id: str) -> dict:
        return {
            "scan_run_id": scan_id,
            "event_id": "event-anchor-merge",
            "ticker": "AAPL",
            "production_score": 50.0,
            "hypothetical_score": 51.0,
            "version": "range-v1",
            "shadow": {"feature": {"version": "range-v1"}},
        }

    merged = merge_completed_research_observations(
        [
            event("scan-watch", "2026-01-02T15:01:00+00:00", "WATCHING", None),
            event(
                "scan-trigger",
                "2026-01-05T15:01:00+00:00",
                "TRIGGERED",
                triggered_at,
            ),
        ],
        [shadow("scan-watch"), shadow("scan-trigger")],
    )

    assert len(merged["observations"]) == 1
    assert merged["observations"][0]["scan_run_id"] == "scan-trigger"
    assert merged["observations"][0]["triggered_at"] == triggered_at
