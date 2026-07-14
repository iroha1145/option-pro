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


def test_signal_api_accepts_index_symbol_and_preserves_leading_caret(monkeypatch):
    captured = []

    def provider(symbol: str):
        captured.append(symbol)
        return {}

    monkeypatch.setattr(signals, "compute_stock_signals", provider)
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _signals: {})

    payload = asyncio.run(signals.stock_signals("^gspc"))

    assert captured == ["^GSPC"]
    assert payload["ticker"] == "^GSPC"


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


def test_signal_analysis_identity_is_stable_for_the_same_evidence():
    evidence = {"rsi14": {"value": 62.0, "score": 70}}
    scores = {"trend": 70}

    first = signals._signal_analysis_payload("AAPL", evidence, scores)
    second = signals._signal_analysis_payload("AAPL", evidence, scores)
    changed = signals._signal_analysis_payload(
        "AAPL",
        {"rsi14": {"value": 63.0, "score": 72}},
        scores,
    )

    assert first == second
    assert len(first["evidence_hash"]) == 64
    assert first["evidence_hash"] != changed["evidence_hash"]
    assert len(first["as_of"]) == 10


def test_signal_analysis_explicit_retry_passes_force_to_job_repository(
    monkeypatch,
):
    captured = {}
    monkeypatch.setattr(signals, "_require_runtime_capability", lambda: None)
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {"rsi14": {"value": 55.0}},
    )
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})

    def create_job(job_type, payload, *, force_retry=False):
        captured.update(
            job_type=job_type,
            payload=payload,
            force_retry=force_retry,
        )
        return (
            {
                "job_id": "aij_signal_retry",
                "job_type": "signal_analysis",
                "status": "pending",
            },
            True,
        )

    class Repository:
        @staticmethod
        def public(row, *, cached=False):
            return {**row, "cached": cached}

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(signals, "_job_repository", Repository)

    response = asyncio.run(
        signals.stock_ai_analysis(
            "AAPL",
            signals.SignalAnalysisJobCreateRequest(force=True),
        )
    )

    assert response.status_code == 202
    assert captured["job_type"] == "signal_analysis"
    assert captured["force_retry"] is True
