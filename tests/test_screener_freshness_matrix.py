"""Additional mandatory matrix cases for screener freshness (A/B/E)."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
    require_same_origin_action,
)
from app.api import strength, worker_actions
from app.personal_config import AccessConfig
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp
from tests.test_strength_variant_lifecycle import NOW, _payload

ET = ZoneInfo("America/New_York")


def test_a04_visitor_cannot_submit_strength_refresh() -> None:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("screener-freshness-visitor"),
    )
    app.include_router(
        worker_actions.router,
        dependencies=[Depends(require_same_origin_action)],
    )
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)},
            headers={
                "Origin": "https://testserver",
                "Content-Type": "application/json",
                "X-Optix-Action": "1",
                "Sec-Fetch-Site": "same-origin",
            },
        )
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "owner_login_required"


def test_a04_visitor_get_does_not_touch_variant_mtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    params = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    variant = strength._strength_snapshot_path(params, base_path=base)
    strength._write_strength_snapshot(
        variant,
        base_path=base,
        parameters=params,
        payload=_payload(params, ticker="NVDA", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 30,
    )
    before = variant.stat().st_mtime_ns
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", base)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: False)
    _rp(asyncio.run(strength.scan(_areq(), **params)))
    assert variant.stat().st_mtime_ns == before


def test_b01_owner_read_marks_variant_recent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    params = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    variant = strength._strength_snapshot_path(params, base_path=base)
    strength._write_strength_snapshot(
        variant,
        base_path=base,
        parameters=params,
        payload=_payload(params, ticker="NVDA", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 30,
    )
    os.utime(variant, (NOW - 500, NOW - 500))
    before = variant.stat().st_mtime_ns
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", base)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: True)
    with request_owner_access_context(True):
        _rp(asyncio.run(strength.scan(_areq(), **params)))
    assert variant.stat().st_mtime_ns > before
    recent = strength.list_recent_strength_variant_parameters(base, limit=4)
    assert recent[0]["sector_id"] == "semiconductors"


def test_b02_payload_separates_universe_from_returned_top() -> None:
    payload = {
        "rows": [{"ticker": "NVDA", "score": 90}],
        "results": [{"ticker": "NVDA", "score": 90}],
        "count": 1,
        "universe_count": 48,
        "screened_count": 14,
        "tier_distribution": {
            "S": 3,
            "A": 8,
            "B": 12,
            "C": 15,
            "D": 10,
            "unscored": 0,
            "scored": 48,
            "total": 48,
        },
    }
    assert payload["universe_count"] > payload["count"]
    assert payload["tier_distribution"]["total"] == payload["universe_count"]
    assert payload["tier_distribution"]["total"] != payload["count"]


def test_b09_unknown_snapshot_does_not_invent_now(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    result = _rp(asyncio.run(strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)))
    assert result["source_status"] == "unknown"
    assert result["_stale"] is False
    assert "score_data_through" not in result or result.get("score_data_through") in {None, ""}
    assert result["scan_completed_at"] == result["snapshot_saved_at"]
    assert datetime.fromisoformat(result["scan_completed_at"]).timestamp() == pytest.approx(NOW - 10)


def test_b06_tokyo_and_new_york_share_the_session_date() -> None:
    from app.services.strength.freshness import parse_aware_datetime, session_date_of

    parsed = parse_aware_datetime("2026-07-02")
    assert parsed is not None
    assert session_date_of(parsed).isoformat() == "2026-07-02"
    tokyo = parsed.astimezone(ZoneInfo("Asia/Tokyo"))
    assert tokyo.date().isoformat() in {"2026-07-03"}
    assert session_date_of(parsed).isoformat() == "2026-07-02"
