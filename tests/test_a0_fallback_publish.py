from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.api import strength
from tests.legacy_strength_support import read_legacy_snapshot
from app.services.algorithm_modes import A0_ALGORITHM, A0_UNAVAILABLE, PRODUCTION_ALGORITHM
from app.services.strength.ranking_variants import a0_request_can_score, apply_a0_mid_long
from tests.legacy_strength_support import LegacySnapshotTask
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp
from tests.test_strength_worker_snapshot import NOW, _payload


def _fallback_payload(parameters: dict, *, ticker: str = "FALL") -> dict:
    body = _payload(parameters=parameters, ticker=ticker)
    body["requested_algorithm"] = A0_ALGORITHM
    body["effective_algorithm"] = PRODUCTION_ALGORITHM
    body["fallback_reason"] = A0_UNAVAILABLE
    body["algorithm_version"] = "strength-v3"
    body["score_basis"] = "ranking_score"
    return body


def test_a0_fallback_snapshot_writes_and_reads_requested_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_path = tmp_path / "strength-snapshot-v1.json"
    parameters = strength.a0_companion_scan_parameters()
    path = strength._strength_snapshot_path(parameters, base_path=default_path)
    outcome = strength._write_strength_snapshot(
        path,
        parameters=parameters,
        payload=_fallback_payload(parameters),
        saved_at=NOW - 10,
        base_path=default_path,
    )
    assert outcome == "written"
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    result = _rp(
        asyncio.run(
            read_legacy_snapshot(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    )
    assert result["requested_algorithm"] == A0_ALGORITHM
    assert result["effective_algorithm"] == PRODUCTION_ALGORITHM
    assert result["fallback_reason"] == A0_UNAVAILABLE
    assert result["rows"][0]["ticker"] == "FALL"
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    production = _rp(
        asyncio.run(
            read_legacy_snapshot(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=PRODUCTION_ALGORITHM,
            )
        )
    )
    assert production["rows"][0]["ticker"] == "PROD"
    assert production["effective_algorithm"] == PRODUCTION_ALGORITHM
    assert production.get("fallback_reason") in {None}


def test_partial_missing_a0_scores_keep_a0_order() -> None:
    rows = [
        {"ticker": "GAP", "score_mid": None, "score_long": None, "ranking_score": 99},
        {"ticker": "MID", "score_mid": 80, "score_long": 60, "ranking_score": 50},
        {"ticker": "LOW", "score_mid": 10, "score_long": 10, "ranking_score": 90},
    ]
    assert a0_request_can_score(rows) is True
    ranked = apply_a0_mid_long(rows)
    assert [item["ticker"] for item in ranked] == ["MID", "LOW", "GAP"]
    assert ranked[2]["a0_available"] is False


def test_all_missing_a0_scores_are_unavailable_for_a0() -> None:
    rows = [
        {"ticker": "AAA", "score_mid": None, "score_long": None, "ranking_score": 99},
        {"ticker": "BBB", "ranking_score": 80},
    ]
    assert a0_request_can_score(rows) is False


def test_fallback_payload_without_requested_identity_is_rejected(tmp_path: Path) -> None:
    parameters = strength.a0_companion_scan_parameters()
    payload = _fallback_payload(parameters)
    payload["params"] = {
        key: value for key, value in payload["params"].items() if key != "ranking_algorithm"
    }
    with pytest.raises(ValueError, match="strength snapshot payload is invalid"):
        strength._write_strength_snapshot(
            strength._strength_snapshot_path(parameters, base_path=tmp_path / "strength-snapshot-v1.json"),
            parameters=parameters,
            payload=payload,
            saved_at=NOW - 10,
            base_path=tmp_path / "strength-snapshot-v1.json",
        )


def test_scanner_fallback_payload_is_accepted_by_worker_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_path = tmp_path / "strength-snapshot-v1.json"
    parameters = strength.a0_companion_scan_parameters()

    async def fake_scanner(**kwargs):
        body = _fallback_payload(
            {key: value for key, value in kwargs.items() if key != "force_refresh"}
        )
        body["rows"] = []
        body["results"] = []
        body["count"] = 0
        body["universe_count"] = 0
        return body

    result = asyncio.run(
        LegacySnapshotTask(
            scanner=fake_scanner,
            snapshot_path=default_path,
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {
                    "request_id": "act_fallback",
                    "details": {
                        "parameters": parameters,
                        "parameters_hash": strength.strength_scan_parameters_hash(parameters),
                    },
                }
            ]
        )
    )
    assert result.status == "idle"
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    published = _rp(
        asyncio.run(
            read_legacy_snapshot(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    )
    assert published["requested_algorithm"] == A0_ALGORITHM
    assert published["effective_algorithm"] == PRODUCTION_ALGORITHM
    assert published["fallback_reason"] == A0_UNAVAILABLE
    assert published["rows"] == []


def test_empty_a0_fallback_is_publishable(
    tmp_path: Path,
) -> None:
    parameters = strength.a0_companion_scan_parameters()
    payload = _fallback_payload(parameters)
    payload["rows"] = []
    payload["results"] = []
    payload["count"] = 0
    payload["universe_count"] = 0
    path = strength._strength_snapshot_path(parameters, base_path=tmp_path / "strength-snapshot-v1.json")
    outcome = strength._write_strength_snapshot(
        path,
        parameters=parameters,
        payload=payload,
        saved_at=NOW - 10,
        base_path=tmp_path / "strength-snapshot-v1.json",
    )
    assert outcome == "written"
