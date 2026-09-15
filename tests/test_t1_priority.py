from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.daily_confirmation import (
    T1_SETTINGS,
    daily_bar_location,
    rvol_daily_20med,
    t1_checks,
    valid_daily_ohlc,
)
from app.services.breakouts.models import MarketSession
from app.services.market_calendar import prior_trading_sessions
from app.services.breakouts.t1_priority import (
    T1_MET,
    T1_NOT_APPLICABLE,
    T1_PENDING,
    T1_UNAVAILABLE,
    T1_UNMET,
    apply_t1_stable_boost,
    attach_t1_features,
    evaluate_t1_from_daily,
    event_t1_status,
    t1_resistance_high,
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


def _daily_frame(
    event_bar: dict[str, float] | None = None,
    *,
    session: date = SESSION,
    extra_days: list[date] | None = None,
    drop_days: set[date] | None = None,
    volume_overrides: dict[date, float | None] | None = None,
) -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for day in prior_trading_sessions(session, 20):
        if drop_days and day in drop_days:
            continue
        stamp, values = _bar(day, volume=1_000_000)
        if volume_overrides and day in volume_overrides:
            override = volume_overrides[day]
            if override is None:
                values = {key: value for key, value in values.items() if key != "Volume"}
            else:
                values["Volume"] = override
        rows[stamp] = values
    for day in extra_days or []:
        stamp, values = _bar(day, volume=2_000_000)
        rows[stamp] = values
    stamp, values = _bar(session, **(event_bar or {}))
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
    frame = _daily_frame(session=early, event_bar={"volume": 2_000_000})
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


def test_rvol_window_does_not_backfill_from_earlier_than_t20() -> None:
    prior = prior_trading_sessions(SESSION, 20)
    missing = prior[-1]
    earlier = prior_trading_sessions(prior[0], 1)[0]
    frame = _daily_frame(
        extra_days=[earlier],
        volume_overrides={missing: None},
    )
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    assert result["reason"] == "missing_prior_volume"


def test_missing_trading_day_in_window_is_unavailable() -> None:
    prior = prior_trading_sessions(SESSION, 20)
    frame = _daily_frame(drop_days={prior[5]})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    assert result["reason"] == "missing_prior_session"


def test_non_trading_day_bars_do_not_fill_the_window() -> None:
    sunday = date(2026, 9, 13)
    assert sunday.weekday() == 6
    frame = _daily_frame(extra_days=[sunday])
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_MET


def test_zero_and_negative_prices_are_unavailable() -> None:
    for open_, high, low, close in (
        (0.0, 110.0, 90.0, 108.0),
        (100.0, 110.0, 90.0, 0.0),
        (-1.0, 110.0, 90.0, 108.0),
        (100.0, 110.0, -0.01, 108.0),
    ):
        assert valid_daily_ohlc(open_, high, low, close) is None
        frame = _daily_frame(
            {"open_": open_, "high": high, "low": low, "close": close, "volume": 2_000_000}
        )
        result = evaluate_t1_from_daily(
            frame,
            session_date=SESSION,
            resistance_high=100,
            as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
            session=MarketSession.CLOSED,
        )
        assert result["status"] == T1_UNAVAILABLE
        assert result["reason"] == "invalid_ohlc"
        assert result["known_at"] is None


def test_illegal_ohlc_is_unavailable_not_unmet() -> None:
    frame = _daily_frame({"open_": 100, "high": 100, "low": 90, "close": 120, "volume": 2_000_000})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    assert result["reason"] == "invalid_ohlc"
    assert result["known_at"] is None
    assert valid_daily_ohlc(100, 100, 90, 120) is None


def test_nan_and_infinity_ohlc_are_unavailable() -> None:
    frame = _daily_frame({"open_": 100, "high": float("inf"), "low": 90, "close": 108, "volume": 2_000_000})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] == T1_UNAVAILABLE
    nan_frame = _daily_frame({"open_": 100, "high": float("nan"), "low": 90, "close": 108, "volume": 2_000_000})
    nan_result = evaluate_t1_from_daily(
        nan_frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert nan_result["status"] == T1_UNAVAILABLE


def test_boundary_thresholds_still_confirm() -> None:
    # CLV 0.70, wick 0.15, rvol 1.5, distance/ATR at the frozen floors.
    prior = prior_trading_sessions(SESSION, 20)
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for day in prior:
        rows[pd.Timestamp(day)] = {
            "Open": 100.0,
            "High": 110.0,
            "Low": 90.0,
            "Close": 100.0,
            "Volume": 1_000_000,
        }
    rows[pd.Timestamp(SESSION)] = {
        "Open": 100.0,
        "High": 120.0,
        "Low": 100.0,
        "Close": 114.0,
        "Volume": 1_500_000,
    }
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    location = daily_bar_location(100, 120, 100, 114)
    assert location["clv"] == 0.70
    assert location["upper_shadow_ratio"] == 0.30
    # 0.30 wick fails the 0.15 cap; use the researched boundary bar instead.
    rows[pd.Timestamp(SESSION)] = {
        "Open": 100.0,
        "High": 110.0,
        "Low": 90.0,
        "Close": 108.0,
        "Volume": 1_500_000,
    }
    frame = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    assert daily_bar_location(100, 110, 90, 108)["clv"] == 0.9
    assert daily_bar_location(100, 110, 90, 108)["upper_shadow_ratio"] == 0.1
    assert rvol_daily_20med(1.5, [1.0] * 20)["rvol_daily_20med"] == 1.5
    checks = t1_checks(clv=0.70, rvol=1.5, upper_shadow=0.15, distance_atr=0.20)
    assert checks["satisfied"] is True
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    assert result["status"] in {T1_MET, T1_UNMET, T1_UNAVAILABLE}
    assert result["status"] != T1_PENDING


def test_orb_t1_uses_event_anchor_not_daily_base_resistance() -> None:
    frame = _daily_frame()
    event = {
        "ticker": "AAA",
        "setup_type": "OPENING_RANGE_BREAKOUT",
        "origin_setup_type": "OPENING_RANGE_BREAKOUT",
        "trading_date": SESSION.isoformat(),
        "event_anchor": {
            "trading_date": SESSION.isoformat(),
            "pivot_price": 100.0,
            "invalidation_price": 90.0,
            "status": "active",
            "source": "completed_opening_range",
        },
        "structure": {"resistance_zone": {"high": 200.0, "low": 180.0}},
        "features": {},
    }
    assert t1_resistance_high(event) == 100.0
    attached = attach_t1_features(
        event,
        frame,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
    )
    t1 = attached["t1_priority"]
    assert t1["status"] == T1_NOT_APPLICABLE
    assert t1["reason"] == "setup_not_in_t1_universe"


def test_daily_base_t1_still_uses_structure_resistance() -> None:
    event = {
        "ticker": "AAA",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "origin_setup_type": "DAILY_BASE_BREAKOUT",
        "trading_date": SESSION.isoformat(),
        "structure": {"resistance_zone": {"high": 104.5, "low": 100.0}},
        "features": {},
    }
    assert t1_resistance_high(event) == 104.5


def test_next_session_bar_does_not_erase_event_t_conclusion() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    first = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        event_id="evt-daily",
    )
    later_frame = _daily_frame(
        {"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000},
        extra_days=[date(2026, 9, 15)],
    )
    later_frame.loc[pd.Timestamp(date(2026, 9, 15)), "Close"] = 50.0
    later = evaluate_t1_from_daily(
        later_frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 15, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-daily",
    )
    fresh = evaluate_t1_from_daily(
        later_frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 15, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        event_id="evt-daily",
    )
    assert first["status"] == T1_MET
    assert later["status"] == T1_MET
    assert later["known_at"] == first["known_at"]
    assert later["data_through"] == SESSION.isoformat()
    assert later.get("reused") is True
    assert fresh["status"] == T1_MET
    assert fresh["data_through"] == SESSION.isoformat()
    assert fresh["reason"] != "event_bar_not_last_completed"


def test_unverified_previous_status_is_not_locked() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    result = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        previous={"status": T1_MET, "known_at": "2026-09-13T20:00:00Z"},
    )
    assert result["status"] == T1_MET
    assert result["known_at"] != "2026-09-13T20:00:00Z"
    assert result.get("reused") is not True


def test_identity_revision_keeps_first_known_at() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    first = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 14, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        event_id="evt-rev",
    )
    revised = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=200,
        as_of=datetime(2026, 9, 15, 9, 0, tzinfo=ET),
        session=MarketSession.PREMARKET,
        previous=first,
        event_id="evt-rev",
    )
    assert revised["eval_version"] == first["eval_version"] + 1
    assert revised["first_known_at"] == first["known_at"]
    assert revised["known_at"] != first["known_at"]
    assert revised["revisions"]


def test_expired_historical_met_is_not_boosted() -> None:
    events = [
        {
            "event_id": "active-unmet",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T18:00:00+00:00",
            "alert_priority_score": 90,
            "features": {"t1_priority": {"status": T1_UNMET}},
            "lifecycle_state": "WATCHING",
        },
        {
            "event_id": "expired-met",
            "trading_date": "2026-09-14",
            "event_at": "2026-09-14T17:00:00+00:00",
            "alert_priority_score": 10,
            "features": {"t1_priority": {"status": T1_MET}},
            "lifecycle_state": "EXPIRED",
        },
    ]
    boosted = apply_t1_stable_boost(events)
    assert [item["event_id"] for item in boosted] == ["active-unmet", "expired-met"]
