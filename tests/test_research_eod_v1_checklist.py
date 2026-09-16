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


def test_zero_volume_and_halt_are_not_tradable() -> None:
    from app.services.research_eod_v1.factors import extract_raw
    from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close

    days = trading_days(date(2019, 1, 2), 80)
    series = make_series("HALT", days, trending_close(80))
    series.volume[-1] = 0.0
    series.dollar_volume[-1] = 0.0
    raw = extract_raw(
        series,
        market=series,
        panel={"HALT": series},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.zero_volume is True
    assert raw.currently_tradable is False
    halted = make_series("STOP", days, trending_close(80))
    halted.halted = True
    raw_halt = extract_raw(
        halted,
        market=halted,
        panel={"STOP": halted},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw_halt.halted is True
    assert raw_halt.currently_tradable is False


def test_dividend_tri_is_not_the_split_adjusted_close() -> None:
    from app.services.research_eod_v1.factors import extract_raw
    from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close

    days = trading_days(date(2018, 1, 2), 80)
    close = trending_close(80, 40, 0.0)
    series = make_series("DIV", days, close)
    series.tri = close.copy()
    series.tri[-1] = close[-1] * 1.10
    raw = extract_raw(
        series,
        market=series,
        panel={"DIV": series},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.last_close == close[-1]
    assert raw.m63 is not None
    flat = make_series("FLAT", days, close)
    raw_flat = extract_raw(
        flat,
        market=flat,
        panel={"FLAT": flat},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.m63 != raw_flat.m63


def test_snapshot_is_repeatable_on_identical_inputs() -> None:
    from app.services.research_eod_v1.config_load import load_registry
    from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
    from app.services.research_eod_v1.snapshot import compute_snapshot

    days = trading_days(date(2018, 1, 2), 260)
    panel = {}
    for i in range(6):
        panel[f"S{i}"] = make_series(f"S{i}", days, trending_close(260, 40 + i, 0.1), industry_id="chips")
    panel["SPY"] = make_series("SPY", days, trending_close(260, 200, 0.08), asset_track="etf", security_type="ETF")
    as_of = as_of_after_close(days[-1])
    registry = load_registry()
    first = compute_snapshot(as_of, panel, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    second = compute_snapshot(as_of, panel, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    assert first["rows"] == second["rows"]
    assert first["network_calls"] == 0


def test_atomic_publish_keeps_previous_on_empty_previous(tmp_path) -> None:
    path = tmp_path / "snap.json"
    payload = publish_snapshot(path, session_date="2026-09-15", config_hash="h", universe_version="u", rows=[])
    assert read_snapshot(path)["session_date"] == payload["session_date"]
