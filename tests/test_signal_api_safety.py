from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.api import signals


def test_signal_api_rejects_invalid_ticker_before_provider_call(monkeypatch):
    called = False

    def provider(_symbol: str):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(signals, "compute_stock_signals", provider)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(signals.stock_signals("../../secret"))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid ticker symbol"
    assert called is False


def test_signal_api_does_not_expose_or_log_internal_provider_errors(monkeypatch, caplog):
    secret = "secret upstream URL, API key, and full response body"
    monkeypatch.setattr(
        signals,
        "compute_market_signals",
        lambda: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(signals.market_signals())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Market signals are currently unavailable"
    assert "secret" not in exc_info.value.detail
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text


def test_stock_trend_bias_stays_missing_when_signal_coverage_is_too_low(
    monkeypatch,
):
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {
            "relative_strength_spy": {"value": None},
            "macd_hist": {"value": None},
            "rsi14": {"value": None},
        },
    )

    payload = asyncio.run(signals.stock_signals("AAPL"))

    assert payload["trend_bias_score"] is None
    assert payload["trend_bias_label"] == "数据不足"
    assert payload["trend_bias_status"] == "insufficient_data"
    assert payload["trend_bias_coverage"] == 0


def test_stock_trend_bias_renormalizes_two_observed_components(monkeypatch):
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {
            "relative_strength_spy": {"value": 5.0},
            "macd_hist": {"value": None},
            "rsi14": {"value": 60.0},
        },
    )

    payload = asyncio.run(signals.stock_signals("AAPL"))

    assert payload["trend_bias_score"] == 71
    assert payload["trend_bias_label"] == "偏多"
    assert payload["trend_bias_status"] == "degraded"
    assert payload["trend_bias_coverage"] == pytest.approx(2 / 3, abs=0.0001)
    assert payload["trend_bias_missing_components"] == ["macd_hist"]


def test_stock_trend_bias_preserves_full_input_formula(monkeypatch):
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {
            "relative_strength_spy": {"value": 5.0},
            "macd_hist": {"value": 0.1},
            "rsi14": {"value": 60.0},
        },
    )

    payload = asyncio.run(signals.stock_signals("AAPL"))

    assert payload["trend_bias_score"] == 74
    assert payload["trend_bias_status"] == "active"
    assert payload["trend_bias_coverage"] == 1
