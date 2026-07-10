from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime
from threading import Lock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api import market, strength as strength_api
from app.services import scoring, signals, yahoo
from app.services.strength import market_regime, relative_spreads, scanner, yahoo_options


def _history(size: int = 260) -> pd.DataFrame:
    close = pd.Series([100 + index * 0.5 for index in range(size)], dtype=float)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": pd.Series([1_000_000 + index * 1_000 for index in range(size)], dtype=float),
        }
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (list(range(1, 31)), 100.0),
        (list(range(30, 0, -1)), 0.0),
        ([10.0] * 30, 50.0),
    ],
)
def test_rsi_handles_one_sided_and_flat_series(values: list[float], expected: float) -> None:
    close = pd.Series(values, dtype=float)
    assert signals.compute_rsi(close, 14) == expected
    assert scanner._rsi(close, 14) == expected


def test_n_day_returns_use_exact_number_of_intervals() -> None:
    close = pd.Series(range(1, 22), dtype=float)
    expected = 20.0

    assert signals.compute_period_return(close, 20) == expected
    assert scanner._ret(close, 20) == expected
    assert market_regime._ret(close, 20) == expected
    assert relative_spreads._ret(close, 20) == expected

    too_short = close.iloc[1:]
    assert signals.compute_period_return(too_short, 20) is None
    assert scanner._ret(too_short, 20) is None


def test_relative_spread_20d_feature_uses_21_observations() -> None:
    numerator = pd.Series([100.0] * 65)
    denominator = pd.Series([100.0] * 65)
    numerator.iloc[-21:] = pd.Series(range(100, 121), dtype=float).to_numpy()

    result = relative_spreads._ratio_features(
        key="test",
        name="TEST",
        numerator="AAA",
        denominator="BBB",
        numerator_close=numerator,
        denominator_close=denominator,
        label_positive="positive",
        label_negative="negative",
    )

    assert result["ratio_return_20d"] == 20.0
    assert result["numerator_20d"] == 20.0


def test_market_regime_reports_insufficient_data_instead_of_bearish_score() -> None:
    result = market_regime.compute_market_regime({})

    assert result["status"] == "insufficient_data"
    assert result["score"] is None
    assert result["label"] == "数据不足"
    assert result["market_context"]["score"] is None
    assert result["missing_requirements"]


def test_data_quality_excludes_metadata_dictionaries() -> None:
    payload = {
        "real": {"value": 1, "top_score": 10, "bottom_score": 20},
        "missing": {"value": None, "top_score": 0, "bottom_score": 0},
        "_volume_today": {"value": 123, "label": "metadata"},
        "_volume_avg20": {"value": 100, "label": "metadata"},
        "_volume_ratio": {"value": 1.23, "label": "metadata"},
    }

    assert scoring._quality(payload, 2) == 50


def test_sector_periods_map_to_20_63_and_126_day_returns() -> None:
    rows = [
        {
            "sector_id": "alpha",
            "ticker": "AAA",
            "return_20d": 0.10,
            "return_63d": 0.20,
            "return_126d": -0.30,
            "final_score": 70.0,
        },
        {
            "sector_id": "alpha",
            "ticker": "AAB",
            "return_20d": 0.20,
            "return_63d": 0.40,
            "return_126d": -0.10,
            "final_score": 60.0,
        },
    ]

    one_month = scanner._sector_strength(rows, "1mo")[0]
    three_month = scanner._sector_strength(rows, "3mo")[0]
    six_month = scanner._sector_strength(rows, "6mo")[0]

    assert (one_month["period_days"], one_month["avg_return"]) == (20, 15.0)
    assert (three_month["period_days"], three_month["avg_return"]) == (63, 30.0)
    assert (six_month["period_days"], six_month["avg_return"]) == (126, -20.0)
    assert one_month["avg_return_3m"] == 30.0


def test_scan_cache_key_and_metadata_use_requested_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    keys: list[tuple[str, int]] = []

    class FakeCache:
        async def get_or_set_with_meta(self, key, ttl, producer):
            keys.append((key, ttl))
            return await producer(), False, time.time() + ttl

    monkeypatch.setattr(scanner, "cache", FakeCache())
    monkeypatch.setattr(
        scanner,
        "_scan_sync",
        lambda **kwargs: {"as_of": "now", "sectors": [], "market_regime": {}, "rows": [], "results": []},
    )

    short = asyncio.run(scanner.scan_strength(ttl=300, include_options=False))
    long = asyncio.run(scanner.scan_strength(ttl=900, include_options=False))

    assert keys[0][0] != keys[1][0]
    assert ":ttl:300" in keys[0][0]
    assert ":ttl:900" in keys[1][0]
    assert short["cache_ttl_seconds"] == 300
    assert long["cache_ttl_seconds"] == 900


def test_expiry_clock_uses_new_york_1600_and_fractional_dte() -> None:
    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 7, 10, 10, 0, tzinfo=ny)

    expiry = yahoo.option_expiry_metrics("2026-07-10", now=now)
    parsed = yahoo_options._parse_expiration("2026-07-10", now=now)

    assert expiry["expiration_at"].endswith("16:00:00-04:00")
    assert expiry["dte"] == 0.25
    assert math.isclose(expiry["time_to_expiry_years"], 0.25 / 365.0)
    assert parsed == ("2026-07-10", 0.25)


def test_stock_signal_uses_atm_iv_name_without_fake_historical_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    hist = _history()
    monkeypatch.setattr(signals, "_history", lambda symbol, period="1y": hist)
    monkeypatch.setattr(yahoo, "get_stock_iv", lambda symbol: 0.325)
    signals._cache.clear()

    result = signals.compute_stock_signals("TEST")

    assert result["atm_iv_percent"]["value"] == 32.5
    assert "iv_rank" not in result


def test_bounded_fallback_fetch_limits_concurrency_and_breaks_on_failures() -> None:
    state = {"active": 0, "max_active": 0, "calls": 0}
    lock = Lock()

    def success(symbol: str) -> pd.DataFrame:
        with lock:
            state["active"] += 1
            state["calls"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.01)
        with lock:
            state["active"] -= 1
        return pd.DataFrame({"Close": [1.0]})

    results = scanner._bounded_history_fetch(
        [f"S{index}" for index in range(12)],
        success,
        max_workers=4,
        total_budget_seconds=1.0,
        request_timeout_seconds=0.05,
    )
    assert len(results) == 12
    assert 1 < state["max_active"] <= 4

    failed_calls: list[str] = []

    def fail(symbol: str) -> pd.DataFrame:
        failed_calls.append(symbol)
        return pd.DataFrame()

    failed = scanner._bounded_history_fetch(
        [f"F{index}" for index in range(100)],
        fail,
        max_workers=4,
        total_budget_seconds=1.0,
        request_timeout_seconds=0.01,
        failure_limit=3,
    )
    assert failed == {}
    assert len(failed_calls) <= 7


def test_price_action_dimension_survives_feature_and_scoring_pipeline() -> None:
    hist = _history()
    row = scanner._feature_row("TEST", hist, hist, {"sector_id": "test", "sector_name": "Test"})
    assert row is not None
    assert isinstance(row["price_action"], dict)

    scored = scanner._score_rows(
        [row],
        {"status": "insufficient_data", "score": None, "rules": {}, "risk_on_spread_score": None},
        "balanced",
        0,
    )[0]
    assert scored["price_action_score"] == scored["breakdown"]["price_action"]
    assert scored["market_regime_score"] is None
    assert "市场行情不足，市场维度按中性值处理" in scored["warnings"]


def test_strength_api_masks_upstream_errors_as_503(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail(**_kwargs):
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(strength_api, "scan_strength", fail)
    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            strength_api.scan(
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=30,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000,
                cache_ttl=300,
            )
        )
    assert captured.value.status_code == 503
    assert captured.value.detail == "Strength data is currently unavailable"
    assert "secret" not in captured.value.detail


def test_us_calendar_skips_observed_holiday_for_next_open() -> None:
    observed_independence_day = datetime(2026, 7, 3).date()
    assert market._is_trading_day(observed_independence_day) is False
    assert market._next_trading_day(observed_independence_day).isoformat() == "2026-07-06"
