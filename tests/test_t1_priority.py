from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.daily_confirmation import (
    T1_SETTINGS,
    daily_bar_location,
    rvol_daily_20med,
    t1_checks,
)
from app.services.breakouts.models import MarketSession
from app.services.breakouts.t1_priority import (
    T1_MET,
    T1_PENDING,
    T1_UNAVAILABLE,
    T1_UNMET,
    apply_t1_stable_boost,
    evaluate_t1_from_daily,
    event_t1_status,
)


ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 14)


def _bar(
    day: date,
    *,
    open_=100.0,
    high=110.0,
    low=90.0,
    close=108.0,
    volume=1_500_000,
) -> tuple[pd.Timestamp, dict[str, float]]:
    return pd.Timestamp(day), {
        "Open": open_,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": volume,
    }


def _daily_frame(event_bar: dict[str, float] | None = None) -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for offset in range(25, 0, -1):
        day = date.fromordinal(SESSION.toordinal() - offset)
        stamp, values = _bar(day, volume=1_000_000)
        rows[stamp] = values
    stamp, values = _bar(SESSION, **(event_bar or {}))
    rows[stamp] = values
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def test_frozen_thresholds_match_the_researched_candidate() -> None:
    assert T1_SETTINGS["clv_min"] == 0.70
    assert T1_SETTINGS["rvol_min"] == 1.5
    assert T1_SETTINGS["upper_shadow_max"] == 0.15
    assert T1_SETTINGS["distance_atr_min"] == 0.20
    location = daily_bar_location(100, 110, 90, 108)
    assert location["clv"] == 0.9
    assert location["upper_shadow_ratio"] == 0.1
    assert rvol_daily_20med(150, [100] * 20)["rvol_daily_20med"] == 1.5
    checks = t1_checks(clv=0.70, rvol=1.5, upper_shadow=0.15, distance_atr=0.20)
    assert checks["satisfied"] is True
    assert t1_checks(clv=0.69, rvol=1.5, upper_shadow=0.15, distance_atr=0.20)["satisfied"] is False


def test_incomplete_session_does_not_use_todays_full_bar() -> None:
    frame = _daily_frame({"open_": 100, "high": 130, "low": 90, "close": 128, "volume": 9_000_000})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 14, 0, tzinfo=ET),
        session=MarketSession.REGULAR,
    )
    assert result["status"] == T1_PENDING
    assert result["reason"] == "session_incomplete"
    assert result["session_complete"] is False
    assert result["clv"] is None


def test_completed_session_can_confirm_met_conditions() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_MET
    assert result["session_complete"] is True
    assert result["known_at"]


def test_known_at_is_preserved_across_later_recomputes() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    first = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    later = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 15, 10, 0, tzinfo=ET),
        session=MarketSession.REGULAR,
        previous=first,
    )
    assert later["known_at"] == first["known_at"]
    assert later["computed_at"] != first["computed_at"]


def test_stable_boost_keeps_production_order_inside_groups() -> None:
    events = [
        {
            "event_id": "old-met",
            "trading_date": "2026-09-13",
            "event_at": "2026-09-13T20:00:00+00:00",
            "alert_priority_score": 10,
            "features": {"t1_priority": {"status": T1_MET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "new-unmet-high",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T18:00:00+00:00",
            "alert_priority_score": 90,
            "features": {"t1_priority": {"status": T1_UNMET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "new-met-low",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T17:00:00+00:00",
            "alert_priority_score": 20,
            "features": {"t1_priority": {"status": T1_MET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "new-met-high",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T18:00:00+00:00",
            "alert_priority_score": 80,
            "features": {"t1_priority": {"status": T1_MET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "new-pending",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T19:00:00+00:00",
            "alert_priority_score": 99,
            "features": {"t1_priority": {"status": T1_PENDING}},
            "lifecycle_state": "TRIGGERED",
        },
    ]
    boosted = apply_t1_stable_boost(events)
    assert [item["event_id"] for item in boosted] == [
        "new-met-high",
        "new-met-low",
        "new-pending",
        "new-unmet-high",
        "old-met",
    ]
    assert all(item["lifecycle_state"] == events_by_id(events)[item["event_id"]]["lifecycle_state"] for item in boosted)
    assert event_t1_status(boosted[2]) == T1_PENDING


def events_by_id(events: list[dict]) -> dict[str, dict]:
    return {item["event_id"]: item for item in events}


def test_missing_inputs_stay_unavailable_not_weak() -> None:
    result = evaluate_t1_from_daily(
        None,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    assert result["reason"] == "daily_unavailable"


def test_holiday_session_is_not_treated_as_a_completed_daily_bar() -> None:
    holiday = date(2026, 9, 7)  # Labor Day
    result = evaluate_t1_from_daily(
        _daily_frame(),
        session_date=holiday,
        resistance_high=100,
        as_of=datetime(2026, 9, 8, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    assert result["reason"] == "not_a_trading_day"
    assert result["clv"] is None


def test_early_close_waits_until_the_shortened_session_is_complete() -> None:
    early = date(2026, 11, 27)  # day after Thanksgiving
    frame = _daily_frame()
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for offset in range(25, 0, -1):
        day = date.fromordinal(early.toordinal() - offset)
        stamp, values = _bar(day, volume=1_000_000)
        rows[stamp] = values
    stamp, values = _bar(early, volume=2_000_000)
    rows[stamp] = values
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    pending = evaluate_t1_from_daily(
        frame,
        session_date=early,
        resistance_high=100,
        as_of=datetime(2026, 11, 27, 12, 30, tzinfo=ET),
        session=MarketSession.REGULAR,
    )
    assert pending["status"] == T1_PENDING
    done = evaluate_t1_from_daily(
        frame,
        session_date=early,
        resistance_high=100,
        as_of=datetime(2026, 11, 27, 13, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert done["status"] in {T1_MET, T1_UNMET, T1_UNAVAILABLE}
    assert done["session_complete"] is True


def test_boost_does_not_drop_or_upgrade_events() -> None:
    events = [
        {
            "event_id": "keep-unmet",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T18:00:00+00:00",
            "alert_priority_score": 10,
            "features": {"t1_priority": {"status": T1_UNMET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "keep-met",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T17:00:00+00:00",
            "alert_priority_score": 9,
            "features": {"t1_priority": {"status": T1_MET}},
            "lifecycle_state": "TRIGGERED",
        },
    ]
    boosted = apply_t1_stable_boost(events)
    assert {item["event_id"] for item in boosted} == {"keep-unmet", "keep-met"}
    assert events_by_id(boosted)["keep-unmet"]["lifecycle_state"] == "WATCHING"
    assert events_by_id(boosted)["keep-met"]["lifecycle_state"] == "TRIGGERED"
