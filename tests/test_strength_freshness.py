from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.services.market_calendar import last_completed_trading_day, previous_trading_day
from app.services.strength.freshness import (
    evaluate_strength_snapshot_freshness,
    expected_complete_session,
    parse_aware_datetime,
    should_replace_published_snapshot,
    strength_payload_is_publishable,
)

ET = ZoneInfo("America/New_York")
TTL = 26 * 60 * 60


def _ts(value: str) -> float:
    return datetime.fromisoformat(value).timestamp()


def test_date_only_timestamp_is_nyse_close_not_utc_midnight() -> None:
    parsed = parse_aware_datetime("2026-07-02")
    assert parsed is not None
    local = parsed.astimezone(ET)
    assert local.date().isoformat() == "2026-07-02"
    assert local.hour == 16
    assert local.minute == 0


def test_independence_day_observed_and_weekend_use_completed_sessions() -> None:
    # Saturday 2026-07-04; Friday 2026-07-03 is Independence Day observed.
    saturday = datetime(2026, 7, 4, 16, 0, tzinfo=ET)
    assert last_completed_trading_day(saturday).isoformat() == "2026-07-02"
    monday_morning = datetime(2026, 7, 6, 9, 0, tzinfo=ET)
    assert last_completed_trading_day(monday_morning).isoformat() == "2026-07-02"
    # Just after Monday close, vendor buffer still accepts Friday 2026-07-02.
    monday_close = datetime(2026, 7, 6, 16, 5, tzinfo=ET)
    assert expected_complete_session(monday_close).isoformat() == "2026-07-02"


def test_early_close_and_dst_boundaries() -> None:
    # 2026-11-27 is the day after Thanksgiving (early close 13:00 ET).
    before = datetime(2026, 11, 27, 12, 59, tzinfo=ET)
    assert last_completed_trading_day(before).isoformat() == "2026-11-25"
    after = datetime(2026, 11, 27, 13, 0, tzinfo=ET)
    assert last_completed_trading_day(after).isoformat() == "2026-11-27"
    # First Monday after the 2026 US DST fall-back.
    november = datetime(2026, 11, 2, 16, 0, tzinfo=ET)
    assert last_completed_trading_day(november).isoformat() == "2026-11-02"


def test_recent_save_cannot_hide_old_daily_input() -> None:
    now = datetime(2026, 9, 4, 16, 30, tzinfo=ET)
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=now.timestamp(),
        payload={
            "score_data_through": "2026-07-02T20:00:00+00:00",
            "rows": [{"daily_data_through": "2026-07-02T20:00:00+00:00"}],
        },
        now=now.timestamp(),
        ttl_seconds=TTL,
    )
    assert verdict.stale is True
    assert verdict.source_status == "historical"
    assert verdict.stale_reason == "score_data_too_old"
    assert verdict.score_data_through is not None


def test_ttl_expiry_keeps_body_semantics_without_inventing_input_time() -> None:
    now = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    saved_at = now.timestamp() - TTL - 10
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=saved_at,
        payload={"as_of": "2026-09-04T11:00:00+00:00", "rows": []},
        now=now.timestamp(),
        ttl_seconds=TTL,
    )
    assert verdict.stale is True
    assert verdict.unknown_input_time is True
    assert verdict.stale_reason == "worker_snapshot_expired"
    assert verdict.score_data_through is None


def test_closed_market_with_matching_session_is_fresh() -> None:
    now = datetime(2026, 9, 5, 11, 0, tzinfo=ET)  # Saturday
    expected = expected_complete_session(now)
    through = datetime(expected.year, expected.month, expected.day, 16, 0, tzinfo=ET)
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=now.timestamp() - 60,
        payload={"score_data_through": through.isoformat(), "rows": []},
        now=now.timestamp(),
        ttl_seconds=TTL,
    )
    assert verdict.stale is False
    assert verdict.source_status == "active"
    assert verdict.stale_reason is None


def test_missing_timestamps_stay_unknown_and_are_not_filled() -> None:
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=now.timestamp() - 10,
        payload={"as_of": "not-a-time", "rows": [{"daily_data_through": None}]},
        now=now.timestamp(),
        ttl_seconds=TTL,
    )
    assert verdict.stale is False
    assert verdict.source_status == "unknown"
    assert verdict.stale_reason == "missing_score_data_through"
    assert verdict.score_data_through is None


def test_future_input_timestamp_is_unverified() -> None:
    now = datetime(2026, 9, 4, 15, 0, tzinfo=timezone.utc)
    verdict = evaluate_strength_snapshot_freshness(
        saved_at=now.timestamp() - 10,
        payload={"score_data_through": "2026-12-01T20:00:00+00:00", "rows": []},
        now=now.timestamp(),
        ttl_seconds=TTL,
    )
    assert verdict.source_status == "unknown"
    assert verdict.score_data_through is None


def test_total_provider_failure_is_not_publishable() -> None:
    ok, reason = strength_payload_is_publishable(
        {
            "rows": [],
            "results": [],
            "universe_count": 12,
            "skipped": {"data_error": 12},
            "data_sources": {"prices": {"status": "failed"}},
        }
    )
    assert ok is False
    assert reason == "price_source_failed"


def test_empty_filter_result_with_active_prices_is_publishable() -> None:
    ok, reason = strength_payload_is_publishable(
        {
            "rows": [],
            "results": [],
            "universe_count": 12,
            "skipped": {"low_price": 12, "data_error": 0},
            "data_sources": {"prices": {"status": "active"}},
        }
    )
    assert ok is True
    assert reason is None


def test_late_publish_cannot_replace_a_newer_snapshot() -> None:
    assert should_replace_published_snapshot(
        existing_saved_at=100.0,
        incoming_saved_at=99.0,
    ) is False
    assert should_replace_published_snapshot(
        existing_saved_at=None,
        incoming_saved_at=1.0,
    ) is True


def test_previous_session_helper_skips_weekend() -> None:
    assert previous_trading_day(datetime(2026, 9, 7, tzinfo=ET).date()).isoformat() == "2026-09-04"
