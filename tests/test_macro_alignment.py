"""Mixed-frequency alignment: backward as-of joins, staleness, no future data."""

from __future__ import annotations

import datetime as dt

import pytest

from app.services.macro_conditions.alignment import (
    LOOKUP_MISSING,
    LOOKUP_OK,
    LOOKUP_STALE,
    AsOfSeries,
    build_grid,
    combine_history_basis,
    earliest_data_through,
    etf_from_rows,
    series_from_rows,
)
from app.services.macro_conditions.market_proxy import last_completed_trading_day
from app.services.macro_conditions.registry import (
    DAILY_MAX_STALE_CALENDAR_DAYS,
    ETF_MAX_STALE_TRADING_DAYS,
    WEEKLY_MAX_STALE_CALENDAR_DAYS,
)


def _rows(pairs, *, available="2026-07-24T00:00:00Z", basis="latest_revised_backfill"):
    return [
        {
            "observation_date": day.isoformat(),
            "value": value,
            "first_seen_at": available,
            "history_basis": basis,
        }
        for day, value in pairs
    ]


# ---------------------------------------------------------------------------
# 20 daily backward as-of
# ---------------------------------------------------------------------------


def test_daily_series_reads_backward_and_never_forward() -> None:
    series = series_from_rows(
        "DGS10",
        _rows([(dt.date(2026, 7, 20), 4.2), (dt.date(2026, 7, 22), 4.3)]),
        max_stale_calendar_days=DAILY_MAX_STALE_CALENDAR_DAYS,
    )
    # On the 21st only the 20th is visible — the 22nd is the future.
    assert series.at(dt.date(2026, 7, 21)).value.value == pytest.approx(4.2)
    assert series.at(dt.date(2026, 7, 22)).value.value == pytest.approx(4.3)
    # Before any observation there is nothing to carry.
    assert series.at(dt.date(2026, 7, 19)).reason == LOOKUP_MISSING


def test_a_future_observation_date_is_never_used() -> None:
    series = series_from_rows(
        "DGS10",
        _rows([(dt.date(2026, 8, 1), 9.9)]),
        max_stale_calendar_days=DAILY_MAX_STALE_CALENDAR_DAYS,
    )
    for day in (dt.date(2026, 7, 24), dt.date(2026, 7, 31)):
        assert series.at(day).reason == LOOKUP_MISSING
    assert series.at(dt.date(2026, 8, 1)).value.value == pytest.approx(9.9)


# ---------------------------------------------------------------------------
# 21 weekly carry-forward
# ---------------------------------------------------------------------------


def test_weekly_series_carries_forward_inside_its_threshold() -> None:
    wednesday = dt.date(2026, 7, 15)
    series = series_from_rows(
        "WALCL",
        _rows([(wednesday, 6800.0)]),
        max_stale_calendar_days=WEEKLY_MAX_STALE_CALENDAR_DAYS,
    )
    for offset in range(0, WEEKLY_MAX_STALE_CALENDAR_DAYS + 1):
        lookup = series.at(wednesday + dt.timedelta(days=offset))
        assert lookup.reason == LOOKUP_OK
        assert lookup.value.value == pytest.approx(6800.0)
        assert lookup.value.source_age_days == offset


# ---------------------------------------------------------------------------
# 23 stale threshold stops the carry
# ---------------------------------------------------------------------------


def test_carrying_stops_once_the_stale_threshold_is_passed() -> None:
    day = dt.date(2026, 7, 1)
    series = series_from_rows(
        "SOFR",
        _rows([(day, 4.3)]),
        max_stale_calendar_days=DAILY_MAX_STALE_CALENDAR_DAYS,
    )
    edge = day + dt.timedelta(days=DAILY_MAX_STALE_CALENDAR_DAYS)
    assert series.at(edge).reason == LOOKUP_OK
    beyond = edge + dt.timedelta(days=1)
    lookup = series.at(beyond)
    assert lookup.reason == LOOKUP_STALE
    # Stale is reported as stale, not silently carried and not turned into a value.
    assert lookup.value is None


def test_etf_staleness_is_measured_in_trading_days() -> None:
    grid = build_grid(dt.date(2026, 6, 1), dt.date(2026, 7, 31))
    anchor = grid[10]
    series = etf_from_rows(
        "SPY",
        [
            {
                "observation_date": anchor.isoformat(),
                "adjusted_close": 600.0,
                "available_at": "2026-07-24T00:00:00Z",
                "history_basis": "latest_revised_backfill",
            }
        ],
        grid=grid,
    )
    assert series.at(grid[10 + ETF_MAX_STALE_TRADING_DAYS]).reason == LOOKUP_OK
    assert series.at(grid[11 + ETF_MAX_STALE_TRADING_DAYS]).reason == LOOKUP_STALE


# ---------------------------------------------------------------------------
# 22 no interpolation, missing values never occupy a window slot
# ---------------------------------------------------------------------------


def test_missing_observations_are_dropped_rather_than_interpolated() -> None:
    series = series_from_rows(
        "SOFR",
        _rows(
            [
                (dt.date(2026, 7, 20), 4.30),
                (dt.date(2026, 7, 21), None),
                (dt.date(2026, 7, 22), 4.40),
            ]
        ),
        max_stale_calendar_days=DAILY_MAX_STALE_CALENDAR_DAYS,
    )
    assert len(series) == 2
    # The 21st carries the 20th forward; nothing is invented in between.
    assert series.at(dt.date(2026, 7, 21)).value.value == pytest.approx(4.30)
    assert [value for _day, value in series.trailing(dt.date(2026, 7, 22), 5)] == [4.30, 4.40]


def test_out_of_order_or_duplicate_dates_are_refused() -> None:
    series = AsOfSeries(
        "X",
        [
            (dt.date(2026, 7, 20), 1.0, "a", "latest_revised_backfill"),
            (dt.date(2026, 7, 20), 2.0, "a", "latest_revised_backfill"),
            (dt.date(2026, 7, 19), 3.0, "a", "latest_revised_backfill"),
        ],
        max_stale_calendar_days=7,
    )
    assert series.dates == (dt.date(2026, 7, 20),)
    assert series.values == (1.0,)


# ---------------------------------------------------------------------------
# 24 incomplete ETF bars
# ---------------------------------------------------------------------------


def test_an_unfinished_session_is_not_a_completed_trading_day() -> None:
    from datetime import timezone

    from app.services.market_calendar import ET

    # 2026-07-24 is a Friday. Before the 16:00 ET close the day is unfinished.
    before_close = dt.datetime(2026, 7, 24, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    after_close = dt.datetime(2026, 7, 24, 17, 0, tzinfo=ET).astimezone(timezone.utc)
    assert last_completed_trading_day(before_close) == dt.date(2026, 7, 23)
    assert last_completed_trading_day(after_close) == dt.date(2026, 7, 24)
    # Saturday resolves back to Friday.
    saturday = dt.datetime(2026, 7, 25, 12, 0, tzinfo=ET).astimezone(timezone.utc)
    assert last_completed_trading_day(saturday) == dt.date(2026, 7, 24)


def test_the_grid_contains_only_trading_days() -> None:
    from app.services.market_calendar import is_trading_day

    grid = build_grid(dt.date(2026, 6, 1), dt.date(2026, 7, 31))
    assert grid
    assert all(is_trading_day(day) for day in grid)
    assert list(grid) == sorted(grid)
    assert build_grid(dt.date(2026, 7, 31), dt.date(2026, 6, 1)) == ()


# ---------------------------------------------------------------------------
# 25 mixed-frequency data_through and provenance
# ---------------------------------------------------------------------------


def test_mixed_source_data_through_is_the_oldest_valid_input() -> None:
    assert earliest_data_through(
        [dt.date(2026, 7, 22), dt.date(2026, 7, 15), None]
    ) == dt.date(2026, 7, 15)
    assert earliest_data_through([None, None]) is None


def test_history_basis_becomes_mixed_when_regimes_are_combined() -> None:
    assert combine_history_basis(["latest_revised_backfill"]) == "latest_revised_backfill"
    assert combine_history_basis(["local_point_in_time"]) == "local_point_in_time"
    assert (
        combine_history_basis(["latest_revised_backfill", "local_point_in_time"]) == "mixed"
    )
    assert combine_history_basis([]) is None


def test_a_window_reports_the_latest_visibility_among_the_rows_it_uses() -> None:
    series = AsOfSeries(
        "WALCL",
        [
            (dt.date(2026, 7, 1), 1.0, "2026-07-02T00:00:00Z", "latest_revised_backfill"),
            # An old observation revised late carries a newer first_seen_at than
            # its successor, so the window maximum is a real maximum.
            (dt.date(2026, 7, 8), 2.0, "2026-07-09T00:00:00Z", "local_point_in_time"),
        ],
        max_stale_calendar_days=14,
    )
    assert series.window_available_at(dt.date(2026, 7, 8), 2) == "2026-07-09T00:00:00Z"
    assert series.window_history_basis(dt.date(2026, 7, 8), 2) == "mixed"
    assert series.window_history_basis(dt.date(2026, 7, 1), 1) == "latest_revised_backfill"
