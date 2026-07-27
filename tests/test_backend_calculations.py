from __future__ import annotations

import asyncio
import logging
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
import httpx
from fastapi import HTTPException

from app.api import market, signals as signals_api, strength as strength_api
from app.public_home_snapshot import validate_public_home_payload
from app.services import scoring, signals, yahoo
from app.services.cache import TTLCache
from app.services.strength import (
    market_regime,
    marketdata,
    relative_spreads,
    scanner,
    yahoo_options,
)
from app.services.breakouts.config import BreakoutSettings


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


def _massive_bars(count: int, *, end: date) -> list[dict[str, float]]:
    start = end - timedelta(days=count - 1)
    return [
        {
            "t": float(
                pd.Timestamp(start + timedelta(days=index), tz="UTC").timestamp()
                * 1000
            ),
            "o": 100.0 + index,
            "h": 102.0 + index,
            "l": 99.0 + index,
            "c": 101.0 + index,
            "v": 1_000_000.0 + index,
        }
        for index in range(count)
    ]


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


def test_cross_sectional_percentiles_use_midranks_for_ties() -> None:
    all_tied = [
        {"ticker": "AAA", "value": 1.0},
        {"ticker": "BBB", "value": 1.0},
        {"ticker": "CCC", "value": 1.0},
    ]
    partially_tied = [
        {"ticker": "AAA", "value": 1.0},
        {"ticker": "BBB", "value": 1.0},
        {"ticker": "CCC", "value": 2.0},
    ]

    for ranker in (scanner._pct_rank, yahoo_options._pct_rank):
        assert ranker(all_tied, "value") == {"AAA": 50.0, "BBB": 50.0, "CCC": 50.0}
        assert ranker(partially_tied, "value") == {"AAA": 25.0, "BBB": 25.0, "CCC": 100.0}


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


def test_market_regime_degrades_partial_risk_without_erasing_core_shape() -> None:
    history = _history(260)
    result = market_regime.compute_market_regime(
        {symbol: history for symbol in ("SPY", "QQQ", "IWM", "RSP")}
    )
    assert result["status"] == "degraded"
    assert result["score"] is not None
    assert result["partial_score"] is not None
    assert result["market_shape"]["status"] == "degraded"
    assert result["market_shape"]["state"] is not None
    assert "volatility" in result["optional_missing"]
    assert "credit" in result["optional_missing"]
    assert "rates" in result["optional_missing"]


def test_empty_relative_spread_matrix_stays_unavailable() -> None:
    result = relative_spreads.compute_spread_matrix({})
    assert result["status"] == "unavailable"
    assert result["score"] is None
    assert result["label"] == "数据不足"
    assert result["spreads"]
    assert all(item["score"] is None for item in result["spreads"].values())


def test_partial_market_regime_does_not_publish_neutral_risk_dimensions() -> None:
    history = _history(260)
    result = market_regime.compute_market_regime(
        {symbol: history for symbol in ("SPY", "QQQ", "IWM", "RSP")}
    )

    assert result["status"] == "degraded"
    assert result["risk_appetite_score"] is None
    assert result["market_context"]["liquidity_credit_score"] is None
    assert result["market_context"]["sentiment_score"] is None
    assert result["market_context"]["sector_flow_score"] is None


def test_market_signal_breadth_ignores_missing_sector_funds_and_marks_degraded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals._cache.clear()
    monkeypatch.setattr(signals, "_bulk_history", lambda _symbols: {"SPY": _history()})

    result = signals.compute_market_signals()

    assert result["sectors_above_50dma"]["value"] is None
    assert result["_breadth_coverage"] == {
        "available": 0,
        "expected": len(signals.SECTOR_ETFS),
        "ratio": 0.0,
    }
    assert result["_source_status"]["value"] == "degraded"


def test_market_signal_breadth_uses_only_available_funds_in_denominator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals._cache.clear()
    history = _history()
    available = signals.SECTOR_ETFS[:7]
    monkeypatch.setattr(
        signals,
        "_bulk_history",
        lambda _symbols: {"SPY": history, **{symbol: history for symbol in available}},
    )

    result = signals.compute_market_signals()

    assert result["sectors_above_50dma"]["value"] == 100.0
    assert result["_breadth_coverage"]["available"] == 7
    assert result["_source_status"]["value"] == "degraded"


def test_market_signal_bulk_fallback_does_not_retry_massive_per_symbol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    massive_calls: list[str] = []
    yahoo_calls: list[str] = []

    monkeypatch.setattr(signals.massive, "configured", lambda: True)
    monkeypatch.setattr(
        signals,
        "_massive_daily",
        lambda symbol, _period: (
            massive_calls.append(symbol) or pd.DataFrame()
        ),
    )
    monkeypatch.setattr(signals.yf, "download", lambda **_kwargs: pd.DataFrame())
    monkeypatch.setattr(
        signals,
        "_yahoo_history",
        lambda symbol, _period: (
            yahoo_calls.append(symbol) or pd.DataFrame()
        ),
    )

    result = signals._bulk_history(["AAA", "BBB"])

    assert set(result) == {"AAA", "BBB"}
    assert sorted(massive_calls) == ["AAA", "BBB"]
    assert sorted(yahoo_calls) == ["AAA", "BBB"]


def test_worker_market_signal_payload_matches_persisted_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signals._cache.clear()
    history = _history()
    monkeypatch.setattr(
        signals,
        "_bulk_history",
        lambda symbols: {symbol: history for symbol in symbols},
    )

    payload = asyncio.run(signals_api._build_market_signals_payload())

    assert validate_public_home_payload("market_signals", payload) == payload


def test_data_quality_excludes_metadata_dictionaries() -> None:
    payload = {
        "real": {"value": 1, "top_score": 10, "bottom_score": 20},
        "missing": {"value": None, "top_score": 0, "bottom_score": 0},
        "_volume_today": {"value": 123, "label": "metadata"},
        "_volume_avg20": {"value": 100, "label": "metadata"},
        "_volume_ratio": {"value": 1.23, "label": "metadata"},
    }

    assert scoring._quality(payload, 2) == 50


def test_market_top_bottom_scores_exclude_unwired_components_instead_of_filling_50() -> None:
    payload = {
        key: {"value": 1.0, "top_score": 20, "bottom_score": 10, "label": key}
        for key in (
            "sma20_distance",
            "sma50_distance",
            "sma200_distance",
            "rsi14",
            "return_20d",
            "rsp_spy_5d",
            "iwm_spy_5d",
            "sectors_above_50dma",
            "vix_percentile",
            "vix",
            "vix_5d_change",
            "yield_10y",
            "yield_10y_20d_change",
            "credit_risk",
        )
    }

    result = scoring.compute_market_scores(payload)

    assert result["top_breakdown"]["positioning"] is None
    assert result["bottom_breakdown"]["sentiment_pessimism"] is None
    assert result["coverage"]["top_active_weight"] == 0.9
    assert result["coverage"]["bottom_active_weight"] == 0.95
    assert result["data_quality"] < result["signal_data_quality"]


def test_market_top_bottom_scores_are_null_when_every_signal_is_missing() -> None:
    result = scoring.compute_market_scores({})
    assert result["top_score"] is None
    assert result["bottom_score"] is None
    assert result["top_label"] == "数据不足"
    assert result["bottom_label"] == "数据不足"


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


def test_scan_uses_server_fixed_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    keys: list[tuple[str, int]] = []
    values: dict[str, tuple[dict, float]] = {}
    scan_calls = 0

    class FakeCache:
        async def get_or_set_with_meta(self, key, ttl, producer):
            keys.append((key, ttl))
            if key in values:
                value, expires_at = values[key]
                return value, True, expires_at
            value = await producer()
            expires_at = time.time() + ttl
            values[key] = (value, expires_at)
            return value, False, expires_at

    def fake_scan(**kwargs):
        nonlocal scan_calls
        scan_calls += 1
        return {"as_of": "now", "sectors": [], "market_regime": {}, "rows": [], "results": []}

    monkeypatch.setattr(scanner, "cache", FakeCache())
    monkeypatch.setattr(scanner, "_scan_sync", fake_scan)
    monkeypatch.setattr(
        scanner,
        "_download_history",
        lambda _symbols, period="1y": _history(5),
    )

    first = asyncio.run(scanner.scan_strength(include_options=False))
    second = asyncio.run(scanner.scan_strength(include_options=False))

    view_keys = [item for item in keys if item[0].startswith("strength:themes:")]
    history_keys = [item for item in keys if item[0].startswith("strength-history:")]
    assert len(view_keys) == 2
    assert view_keys[0][0] == view_keys[1][0]
    assert ":ttl:" not in view_keys[0][0]
    assert all(ttl == scanner.STRENGTH_CACHE_TTL_SECONDS for _key, ttl in view_keys)
    assert len(history_keys) == 1
    assert history_keys[0][1] == scanner.STRENGTH_CACHE_TTL_SECONDS
    assert scan_calls == 1
    assert first["_cached"] is False
    assert second["_cached"] is True
    assert first["cache_ttl_seconds"] == scanner.STRENGTH_CACHE_TTL_SECONDS
    assert second["cache_ttl_seconds"] == scanner.STRENGTH_CACHE_TTL_SECONDS


@pytest.mark.parametrize(
    "invalid",
    ["missing_ohl", "nan_ohl", "duplicate_session", "future_session", "bool_timestamp"],
)
def test_massive_history_rejects_incomplete_or_non_distinct_ohlcv(
    invalid: str,
) -> None:
    observed = date(2026, 7, 23)
    bars = _massive_bars(15, end=observed)
    if invalid == "missing_ohl":
        bars[-1].pop("o")
    elif invalid == "nan_ohl":
        bars[-1]["h"] = float("nan")
    elif invalid == "duplicate_session":
        bars[-1]["t"] = bars[0]["t"]
    elif invalid == "future_session":
        bars[-1]["t"] = float(
            pd.Timestamp(observed + timedelta(days=2), tz="UTC").timestamp()
            * 1000
        )
    else:
        bars[-1]["t"] = True

    assert not scanner._massive_history_is_complete(
        bars,
        period="1mo",
        end=observed,
    )


def test_massive_history_returns_sorted_coherent_distinct_rows() -> None:
    observed = date(2026, 7, 23)
    bars = list(reversed(_massive_bars(15, end=observed)))

    validated = scanner._validated_massive_history(
        bars,
        period="1mo",
        end=observed,
    )

    assert len(validated) == 15
    assert [row["t"] for row in validated] == sorted(row["t"] for row in validated)
    assert all(set(row) == {"t", "o", "h", "l", "c", "v"} for row in validated)


@pytest.mark.parametrize("initial_shape", ["empty", "all_nan"])
def test_history_download_retries_a_transient_unusable_yahoo_response(
    monkeypatch: pytest.MonkeyPatch,
    initial_shape: str,
) -> None:
    history = _history(300)
    history.columns = pd.MultiIndex.from_product([["AAA"], history.columns])
    unusable = pd.DataFrame()
    if initial_shape == "all_nan":
        unusable = pd.DataFrame(
            [[float("nan"), float("nan")]],
            columns=pd.MultiIndex.from_product([["AAA"], ["Close", "Volume"]]),
        )
    responses = iter([unusable, history])
    calls = 0

    def download(**_kwargs):
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(scanner.yf, "download", download)

    result = scanner._download_history(["AAA"], period="2y")

    assert calls == 2
    assert not result.empty
    assert result.attrs["price_source"]["status"] == "active"
    assert not scanner._slice_ticker(result, "AAA").empty


def test_unusable_strength_history_is_not_cached_as_a_valid_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    benchmark_only = pd.concat({"SPY": _history(300)}, axis=1)

    def unusable_history(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return benchmark_only.copy()

    monkeypatch.setattr(scanner, "cache", TTLCache())
    monkeypatch.setattr(scanner, "_download_history", unusable_history)
    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (
            ["AAA"],
            {"AAA": {"sector_id": "software", "sector_name": "软件"}},
        ),
    )

    for _ in range(2):
        with pytest.raises(RuntimeError, match="strength_price_history_unavailable"):
            asyncio.run(scanner.scan_strength(include_options=False))

    assert attempts == 2


@pytest.mark.parametrize("history_shape", ["empty", "all_nan"])
def test_unusable_market_history_is_not_cached_as_a_valid_regime(
    monkeypatch: pytest.MonkeyPatch,
    history_shape: str,
) -> None:
    attempts = 0
    unusable = pd.DataFrame()
    if history_shape == "all_nan":
        unusable = pd.DataFrame(
            [[float("nan") for _ in scanner.MARKET_BENCHMARKS]],
            columns=pd.MultiIndex.from_tuples(
                [(symbol, "Close") for symbol in scanner.MARKET_BENCHMARKS]
            ),
        )

    def unusable_history(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        return unusable.copy()

    monkeypatch.setattr(scanner, "cache", TTLCache())
    monkeypatch.setattr(scanner, "_download_history", unusable_history)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="market_price_history_unavailable"):
            asyncio.run(scanner.market_strength())

    assert attempts == 2


def test_yahoo_option_enrichment_caps_pool_and_uses_small_parallel_batches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = [{"ticker": f"T{index:02d}", "final_score": 100 - index} for index in range(30)]
    settings = SimpleNamespace(
        yahoo_options_enabled=True,
        yahoo_options_enrich_limit=90,
        yahoo_options_failure_limit=100,
        yahoo_option_target_dte=30,
        yahoo_option_strike_window_pct=0.16,
    )
    state = {"active": 0, "max_active": 0, "calls": 0}
    lock = Lock()

    def slow_empty(_row, _settings):
        with lock:
            state["active"] += 1
            state["calls"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.02)
        with lock:
            state["active"] -= 1
        return None

    monkeypatch.setattr(yahoo_options, "_load_raw_metrics", slow_empty)
    status = yahoo_options.enrich_rows_with_yahoo_options(rows, display_top=30, settings=settings)

    assert status["candidate_pool"] == 20
    assert state["calls"] == 20
    assert 2 <= state["max_active"] <= 4


def test_yahoo_option_heat_does_not_inject_a_fake_iv_value_when_missing() -> None:
    scored = yahoo_options._score_metrics(
        [
            {
                "ticker": "TEST",
                "total_volume": 100,
                "total_open_interest": 100,
                "premium_flow": 1_000,
                "call_volume": 50,
                "put_volume": 50,
                "call_open_interest": 50,
                "put_open_interest": 50,
                "iv_average": None,
                "unusual_count": 0,
                "source_status": "active",
            }
        ]
    )["TEST"]

    assert scored["atm_iv_percent"] is None
    assert scored["option_pool_iv_rank"] is None
    assert scored["iv_rank"] is None
    assert scored["iv_label"] == "隐波缺失"
    # 单票池没有可辩护的横截面分位：热度分如实缺失而不是伪造 50 中位
    # （与 scanner._pct_rank 的口径一致，审计 2.1.12）。
    assert scored["option_heat_score"] is None
    assert scored["source_status"] == "insufficient_data"
    assert "volume_rank" in scored["heat_missing_components"]


def test_marketdata_option_heat_reweights_missing_iv_and_preserves_real_iv() -> None:
    base_payload = {
        "optionSymbol": ["TEST-C", "TEST-P"],
        "side": ["call", "put"],
        "volume": [100, 200],
        "openInterest": [300, 400],
        "dte": [30, 30],
        "updated": [1_700_000_000, 1_700_000_000],
    }
    missing = marketdata._score_option_payload(
        {**base_payload, "iv": [None, None]}
    )
    assert missing is not None
    assert missing["iv_average"] is None
    assert missing["iv_rank"] is None
    assert missing["iv_label"] == "隐波缺失"
    assert missing["active_weight"] == 0.76
    assert missing["coverage"] == 0.76
    assert missing["missing_components"] == ["iv_average"]

    volume_score = marketdata._clamp(math.log10(301) * 20)
    oi_score = marketdata._clamp(math.log10(701) * 13)
    imbalance = abs(math.log(101 / 201))
    imbalance_score = marketdata._clamp(50 + imbalance * 12, 50, 85)
    expected_without_iv = round(
        (volume_score * .34 + oi_score * .30 + imbalance_score * .12) / .76,
        1,
    )
    assert missing["option_heat_score"] == expected_without_iv

    observed = marketdata._score_option_payload(
        {**base_payload, "iv": [0.35, 0.35]}
    )
    assert observed is not None
    assert observed["iv_average"] == 0.35
    assert observed["iv_label"] == "中性IV"
    assert observed["active_weight"] == 1.0
    assert observed["coverage"] == 1.0
    assert observed["missing_components"] == []


def test_marketdata_error_status_never_returns_upstream_body() -> None:
    sentinel = "upstream-secret-response-sentinel"
    request = httpx.Request("GET", "https://example.invalid/options")
    response = httpx.Response(500, text=sentinel, request=request)
    error = httpx.HTTPStatusError("provider failure", request=request, response=response)

    message = marketdata._request_error_message(error)

    assert message == "HTTP 500"
    assert sentinel not in message


def test_expiry_clock_uses_new_york_1600_and_fractional_dte() -> None:
    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 7, 10, 10, 0, tzinfo=ny)

    expiry = yahoo.option_expiry_metrics("2026-07-10", now=now)
    parsed = yahoo_options._parse_expiration("2026-07-10", now=now)

    assert expiry["expiration_at"].endswith("16:00:00-04:00")
    assert expiry["dte"] == 0.25
    assert math.isclose(expiry["time_to_expiry_years"], 0.25 / 365.0)
    assert parsed == ("2026-07-10", 0.25)


def test_expiry_clock_uses_equity_option_early_close() -> None:
    ny = ZoneInfo("America/New_York")
    now = datetime(2026, 12, 24, 12, 0, tzinfo=ny)

    expiry = yahoo.option_expiry_metrics("2026-12-24", now=now)

    assert expiry["expiration_at"].endswith("13:15:00-05:00")
    assert expiry["dte"] == pytest.approx(75 / (24 * 60), abs=0.000001)


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


def test_finnhub_fallback_keeps_token_out_of_url_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = SimpleNamespace(
        finnhub_api_key="secret-token",
        finnhub_candle_fallback_enabled=True,
        finnhub_candle_fallback_limit=1,
        finnhub_base_url="https://finnhub.example/api/v1",
        request_timeout=2.0,
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"s": "ok", "t": [1], "o": [1], "h": [2], "l": [0.5], "c": [1.5], "v": [100]}

    class FakeClient:
        def __init__(self, *, timeout, headers):
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, *, params):
            captured["url"] = url
            captured["params"] = dict(params)
            return FakeResponse()

    monkeypatch.setattr(scanner, "get_settings", lambda: settings)
    monkeypatch.setattr(scanner.httpx, "Client", FakeClient)

    frame, loaded, missing = scanner._download_finnhub_history(["AAA"], "1mo")

    assert not frame.empty
    assert loaded == ["AAA"]
    assert missing == []
    assert captured["headers"] == {"X-Finnhub-Token": "secret-token"}
    assert "token" not in captured["params"]


def test_marketdata_fallback_keeps_token_out_of_url_query(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}
    settings = SimpleNamespace(
        marketdata_token="secret-token",
        marketdata_stock_candle_fallback_enabled=True,
        marketdata_stock_candle_fallback_limit=1,
        marketdata_base_url="https://marketdata.example",
        request_timeout=2.0,
    )

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"s": "ok", "t": [1], "o": [1], "h": [2], "l": [0.5], "c": [1.5], "v": [100]}

    class FakeClient:
        def __init__(self, *, timeout, headers):
            captured["timeout"] = timeout
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def get(self, url, *, params):
            captured["url"] = url
            captured["params"] = dict(params)
            return FakeResponse()

    monkeypatch.setattr(scanner, "get_settings", lambda: settings)
    monkeypatch.setattr(scanner.httpx, "Client", FakeClient)

    frame, loaded, missing = scanner._download_marketdata_history(["AAA"], "1mo")

    assert not frame.empty
    assert loaded == ["AAA"]
    assert missing == []
    assert captured["headers"] == {"Authorization": "Bearer secret-token"}
    assert "token" not in captured["params"]


def test_marketdata_fallback_httpx_logs_omit_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    sentinel = "marketdata-log-sentinel"
    settings = SimpleNamespace(
        marketdata_token=sentinel,
        marketdata_stock_candle_fallback_enabled=True,
        marketdata_stock_candle_fallback_limit=1,
        marketdata_base_url="https://marketdata.example",
        request_timeout=2.0,
    )
    real_client = httpx.Client

    def handle_request(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {sentinel}"
        assert "token" not in request.url.params
        return httpx.Response(
            200,
            json={"s": "ok", "t": [1], "o": [1], "h": [2], "l": [0.5], "c": [1.5], "v": [100]},
        )

    def client_factory(*, timeout, headers):
        return real_client(
            timeout=timeout,
            headers=headers,
            transport=httpx.MockTransport(handle_request),
        )

    monkeypatch.setattr(scanner, "get_settings", lambda: settings)
    monkeypatch.setattr(scanner.httpx, "Client", client_factory)
    caplog.set_level(logging.INFO, logger="httpx")

    frame, loaded, missing = scanner._download_marketdata_history(["AAA"], "1mo")

    assert not frame.empty
    assert loaded == ["AAA"]
    assert missing == []
    assert "HTTP Request:" in caplog.text
    assert sentinel not in caplog.text


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
    assert "市场行情不足，市场维度暂不计入评分" in scored["warnings"]
    assert scored["breakdown"]["market_regime_scoring_value"] is None
    assert "market" not in scored["breakdown"]["market_rules"]


def test_public_strength_scan_exposes_range_persistence_shadow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _history(300)
    history.index = pd.date_range("2025-05-19", periods=len(history), freq="B")
    history.attrs["price_source"] = {
        "provider": "fixture",
        "status": "active",
        "message": "fixture",
    }
    monkeypatch.setattr(
        "app.services.breakouts.config.get_breakout_settings",
        lambda: BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
        ),
    )
    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (
            ["AAA"],
            {"AAA": {"sector_id": "software", "sector_name": "软件"}},
        ),
    )
    monkeypatch.setattr(scanner, "_download_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_yahoo_options",
        lambda rows, display_top: {"status": "skipped"},
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_finnhub",
        lambda rows: {"status": "skipped"},
    )
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_marketdata_options",
        lambda rows: {"status": "skipped"},
    )

    payload = scanner._scan_sync(
        universe="themes",
        timeframe="all",
        profile="balanced",
        top=1,
        sector_id=None,
        min_price=1,
        min_avg_dollar_volume=0,
        include_options=False,
    )

    assert payload["range_persistence_mode"] == "shadow"
    assert payload["results"][0]["range_persistence"]["status"] == "active"
    assert payload["results"][0]["range_persistence_shadow"]["mode"] == "shadow"
    assert payload["results"][0]["range_persistence_score_delta"] is not None


def test_range_shadow_failure_does_not_remove_legacy_strength_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _history(300).drop(columns=["High", "Low"])
    history.index = pd.date_range("2025-05-19", periods=len(history), freq="B")
    monkeypatch.setattr(
        "app.services.breakouts.config.get_breakout_settings",
        lambda: BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
        ),
    )
    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (
            ["AAA"],
            {"AAA": {"sector_id": "software", "sector_name": "软件"}},
        ),
    )
    monkeypatch.setattr(scanner, "_download_history", lambda *args, **kwargs: history)
    monkeypatch.setattr(
        scanner,
        "enrich_rows_with_finnhub",
        lambda rows: {"status": "skipped"},
    )

    payload = scanner._scan_sync(
        universe="themes",
        timeframe="all",
        profile="balanced",
        top=1,
        sector_id=None,
        min_price=1,
        min_avg_dollar_volume=0,
        include_options=False,
    )

    assert payload["count"] == 1
    assert payload["results"][0]["ticker"] == "AAA"
    assert payload["results"][0]["range_persistence"]["status"] == "unavailable"
    assert payload["skipped"]["range_persistence_error"] == 1


def test_strength_api_returns_typed_unavailable_without_running_provider_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail(**_kwargs):
        raise AssertionError("the read endpoint must not run a provider scan")

    monkeypatch.setattr(scanner, "scan_strength", fail)
    monkeypatch.setattr(
        strength_api,
        "_STRENGTH_SNAPSHOT_PATH",
        tmp_path / "missing-strength-snapshot.json",
    )
    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            strength_api.scan(
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000,
            )
        )
    assert captured.value.status_code == 503
    assert captured.value.detail == {
        "code": "strength_snapshot_unavailable",
        "status": "unavailable",
        "message": "强势雷达后台快照暂不可用",
    }


def test_us_calendar_skips_observed_holiday_for_next_open() -> None:
    observed_independence_day = datetime(2026, 7, 3).date()
    assert market._is_trading_day(observed_independence_day) is False
    assert market._next_trading_day(observed_independence_day).isoformat() == "2026-07-06"
