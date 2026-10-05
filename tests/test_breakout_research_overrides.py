"""Research switches default off: the production path and its config hash stay identical."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.breakout_detector import detect_breakout
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.feature_engine import compute_feature_snapshot
from app.services.breakouts.models import (
    BreakoutCandidate,
    BreakoutStructure,
    MarketSession,
    PriceZone,
    TemporalCutoff,
)
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.worker import _stable_hash


NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 10, 10, 35, tzinfo=NY)


def _settings() -> BreakoutSettings:
    return BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True)


def _candidate() -> BreakoutCandidate:
    return BreakoutCandidate(
        ticker="AAPL",
        price=101.0,
        provider_change_pct=4.0,
        provider_volume=1_000_000,
        provider_timestamp=AS_OF,
        source="test",
        session=MarketSession.REGULAR,
    )


def _structure() -> BreakoutStructure:
    return BreakoutStructure(
        ticker="AAPL",
        base_start=datetime(2026, 5, 1).date(),
        base_end=datetime(2026, 7, 9).date(),
        calculation_cutoff_at=AS_OF,
        base_duration_days=48,
        resistance_zone=PriceZone(low=99.5, high=100.0),
        pivot_price=99.8,
        pivot_id="p" * 24,
        pivot_touch_count=3,
        quality=0.8,
        status="active",
    )


def _features(rvol: float) -> dict:
    # Above resistance by 0.5 ATR with a strong close and no upper wick: only the
    # relative-volume leg decides whether the single bar confirms.
    return {
        "event_price": 101.0,
        "atr20": 2.0,
        "close_location_value": 0.9,
        "rvol_time_of_day": rvol,
        "upper_wick_ratio": 0.05,
        "hold_bars_above_pivot": 1,
        "opening_range_complete": False,
    }


def test_overrides_do_not_touch_model_dump_or_config_hash(tmp_path) -> None:
    db_path = tmp_path / "research-variant.sqlite"
    plain = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True, db_path=str(db_path))
    tuned = plain.with_research_overrides(
        strong_single_rvol_min=2.0, rvol_lookback_sessions=10
    )
    assert tuned.research_overrides == {
        "strong_single_rvol_min": 2.0,
        "rvol_lookback_sessions": 10,
    }
    assert plain.research_overrides == {}
    assert tuned.model_dump(mode="json") == plain.model_dump(mode="json")
    assert _stable_hash(tuned.model_dump(mode="json")) == _stable_hash(
        plain.model_dump(mode="json")
    )
    # A research variant must keep writing to its own database, not production's.
    assert tuned.db_path == plain.db_path == db_path


def test_default_detector_path_is_unchanged_and_override_raises_the_bar() -> None:
    cutoff = TemporalCutoff(event_at=AS_OF, session=MarketSession.REGULAR)
    plain = _settings()
    empty_overrides = plain.with_research_overrides()
    default = detect_breakout(_candidate(), _structure(), _features(1.6), cutoff, plain)
    assert default["triggered"] and default["confirmed"]
    assert default["strong_single_bar_confirmation"] is True
    assert (
        detect_breakout(_candidate(), _structure(), _features(1.6), cutoff, empty_overrides)
        == default
    )

    stricter = plain.with_research_overrides(strong_single_rvol_min=2.0)
    result = detect_breakout(_candidate(), _structure(), _features(1.6), cutoff, stricter)
    assert result["triggered"] and not result["confirmed"]
    assert result["strong_single_bar_confirmation"] is False
    assert detect_breakout(
        _candidate(), _structure(), _features(2.1), cutoff, stricter
    )["confirmed"]


def _intraday(days: int) -> pd.DataFrame:
    sessions = pd.bdate_range(end="2026-07-10", periods=days)
    index = pd.DatetimeIndex(
        [
            pd.Timestamp(day.date().isoformat(), tz=NY) + pd.Timedelta(minutes=570 + 5 * j)
            for day in sessions
            for j in range(78)
        ]
    )
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 0.05, len(index)))
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 0.1,
            "Low": close - 0.1,
            "Close": close,
            "Volume": rng.integers(10_000, 50_000, len(index)),
        },
        index=index.tz_convert("UTC"),
    )


def _daily() -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-09", periods=60)
    close = np.linspace(95, 100, len(index))
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1, "Close": close, "Volume": 1_000_000},
        index=index,
    )


def test_feature_snapshot_default_lookback_is_twenty_and_override_shortens_it() -> None:
    cutoff = TemporalCutoff(event_at=AS_OF, session=MarketSession.REGULAR)
    intraday = _intraday(30)
    default = compute_feature_snapshot(daily=_daily(), intraday=intraday, cutoff=cutoff)
    explicit = compute_feature_snapshot(
        daily=_daily(), intraday=intraday, cutoff=cutoff, rvol_lookback_sessions=20
    )
    assert default == explicit
    assert default["comparison_sessions"] == 20

    shorter = compute_feature_snapshot(
        daily=_daily(), intraday=intraday, cutoff=cutoff, rvol_lookback_sessions=10
    )
    assert shorter["comparison_sessions"] == 10
    assert shorter["rvol_time_of_day"] != default["rvol_time_of_day"]


def test_service_reads_the_lookback_override_and_keeps_production_defaults() -> None:
    plain = BreakoutRadarService(_settings())
    assert plain._feature_snapshot_kwargs() == {
        "opening_range_minutes": 30,
        "rvol_lookback_sessions": 20,
    }
    tuned = BreakoutRadarService(_settings().with_research_overrides(rvol_lookback_sessions=10))
    assert tuned._feature_snapshot_kwargs()["rvol_lookback_sessions"] == 10
