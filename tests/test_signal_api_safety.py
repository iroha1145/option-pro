from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.access import request_owner_access_context
from app.api import signals
from tests.http_response_support import anonymous_get_request as _areq


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
        asyncio.run(signals.market_signals(_areq()))
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
    assert first["evidence_as_of"] == first["as_of"]
    assert first["evidence_source"] == "live"
    assert first["evidence_stale"] is False


async def _empty_signal_context(_symbol: str) -> dict:
    return {
        "blocks": {},
        "status": {key: "unavailable" for key in signals.CONTEXT_BLOCK_KEYS},
        "context_tickers": [],
    }


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
    monkeypatch.setattr(signals, "build_signal_context", _empty_signal_context)

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

        @staticmethod
        def active_for_ticker(_job_type, _ticker):
            return None

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


@pytest.mark.parametrize(
    ("fresh", "expected_live_calls"),
    [(True, 0), (False, 1)],
)
def test_signal_analysis_uses_manual_snapshot_and_falls_back_when_live_fails(
    monkeypatch,
    fresh,
    expected_live_calls,
):
    captured = {}
    live_calls = 0
    evidence = {
        "rsi14": {"value": 55.0},
        "return_20d": {"value": 4.0},
        "macd_hist": {"value": 0.2},
    }
    monkeypatch.setattr(signals, "_require_manual_analysis_enabled", lambda: None)
    monkeypatch.setattr(signals, "_require_runtime_capability", lambda: None)
    monkeypatch.setattr(
        signals,
        "read_stock_pull_resource",
        lambda _symbol, _resource: {
            "payload": evidence,
            "saved_at": 1_700_000_000.0,
            "fresh": fresh,
        },
    )

    def failed_live(_symbol: str):
        nonlocal live_calls
        live_calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(signals, "compute_stock_signals", failed_live)
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})
    monkeypatch.setattr(signals, "build_signal_context", _empty_signal_context)

    def create_job(job_type, payload, *, force_retry=False):
        captured.update(
            job_type=job_type,
            payload=payload,
            force_retry=force_retry,
        )
        return (
            {
                "job_id": "aij_manual_snapshot",
                "job_type": "signal_analysis",
                "status": "pending",
            },
            True,
        )

    class Repository:
        @staticmethod
        def public(row, *, cached=False):
            return {**row, "cached": cached}

        @staticmethod
        def active_for_ticker(_job_type, _ticker):
            return None

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(signals, "_job_repository", Repository)

    if not fresh:
        with pytest.raises(HTTPException) as captured_error:
            asyncio.run(
                signals.stock_ai_analysis(
                    "AAOI",
                    signals.SignalAnalysisJobCreateRequest(),
                )
            )
        assert captured_error.value.status_code == 409
        assert captured_error.value.detail["code"] == "stale_signal_evidence"
        assert captured == {}
        assert live_calls == expected_live_calls
        return

    response = asyncio.run(
        signals.stock_ai_analysis(
            "AAOI",
            signals.SignalAnalysisJobCreateRequest(),
        )
    )

    assert response.status_code == 202
    assert live_calls == expected_live_calls
    assert captured["payload"]["signals"] == evidence
    assert captured["payload"]["evidence_source"] == "manual_pull"
    assert captured["payload"]["evidence_as_of"] == "2023-11-14T22:13:20+00:00"
    assert captured["payload"]["evidence_stale"] is False


def test_signal_analysis_reuses_the_active_job_instead_of_double_paying(
    monkeypatch,
):
    """上下文块让同票重复提交的哈希几乎必然不同——单飞守卫顶上。"""

    active_row = {
        "job_id": "aij_active_one",
        "job_type": "signal_analysis",
        "status": "in_progress",
    }
    created = []
    monkeypatch.setattr(signals, "_require_manual_analysis_enabled", lambda: None)
    monkeypatch.setattr(signals, "_require_runtime_capability", lambda: None)
    monkeypatch.setattr(signals, "build_signal_context", _empty_signal_context)
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {"rsi14": {"value": 55.0}},
    )
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})
    monkeypatch.setattr(
        signals, "read_stock_pull_resource", lambda _s, _r: None
    )

    def create_job(job_type, payload, *, force_retry=False):
        created.append(payload)
        return (
            {
                "job_id": "aij_new_two",
                "job_type": job_type,
                "status": "pending",
            },
            True,
        )

    class Repository:
        @staticmethod
        def public(row, *, cached=False):
            return {**row, "cached": cached}

        @staticmethod
        def active_for_ticker(job_type, ticker):
            assert job_type == "signal_analysis"
            assert ticker == "AMD"
            return active_row

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(signals, "_job_repository", Repository)

    reused = asyncio.run(
        signals.stock_ai_analysis(
            "AMD",
            signals.SignalAnalysisJobCreateRequest(),
        )
    )
    assert reused.status_code == 202
    assert reused.headers["Location"].endswith("aij_active_one")
    assert created == []

    forced = asyncio.run(
        signals.stock_ai_analysis(
            "AMD",
            signals.SignalAnalysisJobCreateRequest(force=True),
        )
    )
    assert forced.status_code == 202
    assert forced.headers["Location"].endswith("aij_new_two")
    assert len(created) == 1
    assert created[0]["context_status"] == {
        key: "unavailable" for key in signals.CONTEXT_BLOCK_KEYS
    }


def test_signal_analysis_drops_context_when_the_payload_is_too_large(
    monkeypatch,
):
    attempts = []
    monkeypatch.setattr(signals, "_require_manual_analysis_enabled", lambda: None)
    monkeypatch.setattr(signals, "_require_runtime_capability", lambda: None)
    monkeypatch.setattr(
        signals,
        "compute_stock_signals",
        lambda _symbol: {"rsi14": {"value": 55.0}},
    )
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})
    monkeypatch.setattr(
        signals, "read_stock_pull_resource", lambda _s, _r: None
    )

    async def context_with_blocks(_symbol: str) -> dict:
        return {
            "blocks": {"macro_conditions": {"composite_score": 55}},
            "status": {"macro_conditions": "ok"},
            "context_tickers": [],
        }

    monkeypatch.setattr(signals, "build_signal_context", context_with_blocks)

    def create_job(job_type, payload, *, force_retry=False):
        attempts.append(payload)
        if len(attempts) == 1:
            raise ValueError("ai_job_payload_too_large")
        return (
            {
                "job_id": "aij_bare_retry",
                "job_type": job_type,
                "status": "pending",
            },
            True,
        )

    class Repository:
        @staticmethod
        def public(row, *, cached=False):
            return {**row, "cached": cached}

        @staticmethod
        def active_for_ticker(_job_type, _ticker):
            return None

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(signals, "_job_repository", Repository)

    response = asyncio.run(
        signals.stock_ai_analysis(
            "AMD",
            signals.SignalAnalysisJobCreateRequest(),
        )
    )

    assert response.status_code == 202
    assert len(attempts) == 2
    assert "macro_conditions" in attempts[0]
    assert "macro_conditions" not in attempts[1]
    assert "context_status" not in attempts[1]


def test_stock_signal_snapshot_uses_saved_time_instead_of_current_time(
    monkeypatch,
):
    saved_at = 1_700_000_000.0
    evidence = {
        "rsi14": {"value": 55.0},
        "return_20d": {"value": 4.0},
        "macd_hist": {"value": 0.2},
    }
    monkeypatch.setattr(
        signals,
        "read_stock_pull_resource",
        lambda _symbol, _resource: {
            "payload": evidence,
            "saved_at": saved_at,
            "fresh": False,
        },
    )
    monkeypatch.setattr(signals, "cached_stock_signals", lambda _symbol: None)

    async def scenario():
        with request_owner_access_context(False):
            return await signals.stock_signals("AAOI")

    payload = asyncio.run(scenario())

    assert payload["snapshot_source"] == "manual_pull"
    assert payload["snapshot_saved_at"] == "2023-11-14T22:13:20+00:00"
    assert payload["as_of"] == payload["snapshot_saved_at"]
    assert payload["_stale"] is True
