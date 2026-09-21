from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api import strength
from app.services.eod_limited.project import project_row, project_strength_payload
from tests.http_response_support import anonymous_get_request, response_payload


def _row(**changes):
    return {
        "security_id": "TEST", "algorithm_id": "A_trend_quality",
        "sector_context": "semiconductors", "stock_or_etf_track": "stock",
        "status": "watch", "score": 80.0, "price": 12.5,
        "adv20": 25_000_000.0, "factors": {"T": 80.0, "R": 80.0},
        "effective_weights": {"T": 0.75, "R": 0.25},
        "score_components": {"T": 60.0, "R": 20.0},
        "capability_flags": {"dollar_liquidity_verified": False, "volume_session_verified": False},
        "rejection_reasons": ["DOLLAR_LIQUIDITY_UNVERIFIED"],
        **changes,
    }


def test_proxy_availability_does_not_certify_eligibility():
    result = project_row(_row(), list_kind="observation")
    assert result["avg_dollar_volume_20d"] == 25_000_000.0
    assert result["dollar_volume_proxy_available"] is True
    assert result["dollar_volume_unknown"] is False
    assert result["dollar_liquidity_verified"] is False
    assert result["volume_session_verified"] is False
    assert result["status"] == "watch"
    assert result["score_aggregation"] == "best_family_theme_path"
    assert sum(result["score_components"].values()) == result["score"]
    assert next(item for item in result["factor_dims"] if item["key"] == "factor_R")["label"] == "稳定性"


@pytest.mark.parametrize("adv", [None, float("nan"), float("inf"), -1, True, "unknown"])
def test_invalid_proxy_remains_missing(adv):
    result = project_row(_row(adv20=adv), list_kind="observation")
    assert result["avg_dollar_volume_20d"] is None
    assert result["dollar_volume_proxy_available"] is False


def test_consensus_does_not_claim_single_path_contributions():
    result = project_row(_row(consensus_z=85.0), list_kind="composite")
    assert result["score"] == 85.0
    assert result["score_aggregation"] == "m1_consensus"
    assert result["score_components"] == {}
    assert result["effective_weights"] == {}


def test_family_summary_keeps_best_path_and_includes_strict_eligible():
    rows = [_row(score=70), _row(score=81, sector_context="ai_cloud")]
    eligible = _row(algorithm_id="D_residual_momentum", status="eligible", score=85)
    payload = project_strength_payload({
        "watch_list": rows, "family_results": [{"rows": [eligible]}],
        "served_session": "2026-09-18", "purpose": "live_eod_inference",
    }, parameters={})
    assert len(payload["observation_rows"]) == 1
    result = payload["observation_rows"][0]
    assert result["score"] == 85
    assert result["observation_family_count"] == 2
    assert result["observation_family_scores"] == {"A_trend_quality": 81, "D_residual_momentum": 85}


def test_diagnostics_uses_one_batch_for_data_and_publication(monkeypatch):
    from app.services.eod_limited import diagnostic_store, store
    batch = {
        "diagnostics": {"generation": "g1"}, "published_at": 1_800_000_000.0,
        "purpose": "live_eod_inference", "served_session": "2099-01-02",
    }
    calls = []
    def read_batch():
        calls.append("batch")
        return batch
    def read_security(received, ticker, profile, horizon):
        assert received is batch
        assert (ticker, profile, horizon) == ("REJECTED", "aggressive", "long")
        return {"ticker": ticker, "paths": [{"status": "rejected", "score": 79.0}]}
    monkeypatch.setattr(store, "read_batch", read_batch)
    monkeypatch.setattr(diagnostic_store, "read_security_diagnostics", read_security)
    request = anonymous_get_request("/api/strength/diagnostics/REJECTED")
    response = asyncio.run(strength.security_diagnostics("REJECTED", request, "aggressive", "long"))
    payload = response_payload(response)
    assert calls == ["batch"]
    assert payload["paths"][0]["status"] == "rejected"
    assert payload["published_at"] == batch["published_at"]
    assert payload["snapshot_saved_at"] == datetime.fromtimestamp(batch["published_at"], timezone.utc).isoformat()


def test_old_batch_diagnostic_absence_is_unavailable_not_unknown_symbol(monkeypatch):
    from app.services.eod_limited import store
    monkeypatch.setattr(store, "read_batch", lambda: {"served_session": "2026-09-18"})
    with pytest.raises(HTTPException) as caught:
        asyncio.run(strength.security_diagnostics("TEST", anonymous_get_request("/api/strength/diagnostics/TEST"), "balanced", "mid"))
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "eod_diagnostics_unavailable"


@pytest.mark.parametrize("value, status", [
    (None, 404),
    ({"data_status": "diagnostics_unavailable"}, 503),
    ({"data_status": "variant_diagnostics_unavailable"}, 503),
    ({"data_status": "ambiguous_symbol", "provider_tickers": ["test", "Test"]}, 409),
])
def test_diagnostic_read_failures_do_not_become_empty_scored_results(monkeypatch, value, status):
    from app.services.eod_limited import diagnostic_store, store
    monkeypatch.setattr(store, "read_batch", lambda: {"diagnostics": {"path": "fixture"}})
    monkeypatch.setattr(diagnostic_store, "read_security_diagnostics", lambda *args: value)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(strength.security_diagnostics("TEST", anonymous_get_request("/api/strength/diagnostics/TEST"), "balanced", "mid"))
    assert caught.value.status_code == status
