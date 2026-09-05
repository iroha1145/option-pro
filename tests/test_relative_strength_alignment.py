from __future__ import annotations

import math

import pandas as pd
import pytest

from app.services import signals
from app.services.strength.features import _feature_row


def _history(size: int = 100) -> pd.DataFrame:
    index = pd.bdate_range("2026-01-02", periods=size)
    close = pd.Series([100.0 + step for step in range(size)], index=index)
    return pd.DataFrame(
        {"Open": close, "High": close + 1, "Low": close - 1,
         "Close": close, "Volume": 1_000_000.0},
        index=index,
    )


@pytest.fixture(autouse=True)
def _offline_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.yahoo.get_stock_iv", lambda _symbol: None)


def _relative_strength(hist: pd.DataFrame, spy: pd.DataFrame, days: int) -> float | None:
    if days == 63:
        row = _feature_row("TEST", hist, spy, {})
        assert row is not None
        return row["rs_spy_63d"]
    result = signals.compute_stock_signals_from_history("TEST", hist, spy_history=spy)
    return result["relative_strength_spy"]["value"]


@pytest.mark.parametrize("days", [20, 63])
def test_relative_strength_compares_matching_dates_when_stock_has_missing_bars(days: int) -> None:
    spy = _history()
    hist = spy.drop(spy.index[-10])

    # Identical prices on every shared date must have zero excess return.
    assert _relative_strength(hist, spy, days) == 0.0


@pytest.mark.parametrize("days", [20, 63])
def test_relative_strength_does_not_compare_stale_stock_to_newer_market_move(days: int) -> None:
    spy = _history()
    hist = spy.iloc[:-1].copy()
    spy.loc[spy.index[-1], "Close"] = 400.0

    assert _relative_strength(hist, spy, days) == 0.0


@pytest.mark.parametrize("days", [20, 63])
@pytest.mark.parametrize("endpoint", ["start", "end"])
def test_relative_strength_is_missing_without_benchmark_endpoint(days: int, endpoint: str) -> None:
    hist = _history()
    missing_day = hist.index[-(days + 1)] if endpoint == "start" else hist.index[-1]
    spy = hist.drop(missing_day)

    assert _relative_strength(hist, spy, days) is None


@pytest.mark.parametrize("days", [20, 63])
def test_relative_strength_keeps_window_when_benchmark_interior_bar_is_missing(days: int) -> None:
    hist = _history()
    spy = hist.drop(hist.index[-10])

    assert _relative_strength(hist, spy, days) == 0.0


@pytest.mark.parametrize("days", [20, 63])
def test_relative_strength_matches_session_dates_across_provider_timezones(days: int) -> None:
    hist = _history()
    spy = hist.copy()
    spy.index = spy.index.tz_localize("America/New_York").tz_convert("UTC")

    assert _relative_strength(hist, spy, days) == 0.0


@pytest.mark.parametrize("invalid", [math.nan, math.inf, -math.inf])
def test_strength_features_skip_history_without_finite_closes(invalid: float) -> None:
    hist = _history()
    hist["Close"] = invalid

    assert _feature_row("TEST", hist, pd.DataFrame(), {}) is None


def test_strength_features_count_valid_closes_before_minimum_history() -> None:
    hist = _history()
    hist.loc[hist.index[:-10], "Close"] = math.nan

    assert _feature_row("TEST", hist, pd.DataFrame(), {}) is None
