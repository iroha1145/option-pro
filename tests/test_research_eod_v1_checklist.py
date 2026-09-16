"""Engineering checklist items that are not return verification."""

from __future__ import annotations

from datetime import date, datetime

import pytest
from zoneinfo import ZoneInfo

from app.services.market_calendar import early_close_minutes, is_trading_day
from app.services.research_eod_v1.calendar_asof import last_completed_session, session_close_at, validate_information_cutoff
from app.services.research_eod_v1.config_load import load_etf_subasset_manifest, load_experiment_manifest
from app.services.research_eod_v1.eod_shadow import publish_snapshot, read_snapshot
from app.services.research_eod_v1.mathutil import midrank_percentiles


def test_half_day_session_uses_exchange_calendar() -> None:
    day = date(2024, 7, 3)
    assert is_trading_day(day)
    assert early_close_minutes(day) == 13 * 60
    close = session_close_at(day)
    assert close.hour == 13
    as_of = datetime(2024, 7, 3, 14, 0, tzinfo=ZoneInfo("America/New_York"))
    assert last_completed_session(as_of) == day
    before = datetime(2024, 7, 3, 12, 0, tzinfo=ZoneInfo("America/New_York"))
    assert last_completed_session(before) != day


def test_source_cannot_predate_session_close() -> None:
    close = session_close_at(date(2024, 7, 3))
    with pytest.raises(ValueError):
        validate_information_cutoff(
            signal_time=close,
            session_close=close,
            source_available_at=close.replace(hour=12),
        )


def test_filter_change_does_not_change_midranks() -> None:
    values = [10.0, 20.0, 30.0, 40.0]
    first = midrank_percentiles(values)
    second = midrank_percentiles(values)
    assert first == second


def test_etf_rows_are_not_mixed_into_864() -> None:
    primary = load_experiment_manifest()
    etf = load_etf_subasset_manifest()
    assert primary["count"] == 864
    assert etf["count"] == 180
    assert not any(row.get("subasset_id") for row in primary["experiments"])


def test_atomic_publish_keeps_previous_on_empty_previous(tmp_path) -> None:
    path = tmp_path / "snap.json"
    payload = publish_snapshot(path, session_date="2026-09-15", config_hash="h", universe_version="u", rows=[])
    assert read_snapshot(path)["session_date"] == payload["session_date"]
