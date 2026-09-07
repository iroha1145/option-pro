"""Additional mandatory matrix cases for screener freshness (A/B/E)."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.worker.state import WorkerStateRepository

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


def test_c03_etag_304_does_not_invent_a_new_data_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    through = "2026-09-03T20:00:00+00:00"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(through=through),
        saved_at=NOW - 60,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    clock = {"now": NOW}
    monkeypatch.setattr(strength.time, "time", lambda: clock["now"])

    async def scenario() -> None:
        first = await strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
        assert first.status_code == 200
        etag = first.headers["etag"]
        body = _rp(first)
        assert body["score_data_through"]
        assert body["_stale"] is False
        first_through = body["score_data_through"]

        replay = await strength.scan(
            _areq(headers={"If-None-Match": etag}),
            **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        )
        assert replay.status_code == 304
        assert not replay.body
        assert replay.headers["etag"] == etag

        clock["now"] = NOW + 27 * 60 * 60
        stale = await strength.scan(_areq(), **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
        assert stale.status_code == 200
        stale_body = _rp(stale)
        assert stale.headers["etag"] != etag
        assert stale_body["_stale"] is True
        assert stale_body["score_data_through"] == first_through
        assert stale_body["scan_completed_at"] == body["scan_completed_at"]

    asyncio.run(scenario())


def _strength_action_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WorkerStateRepository, int]:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("test-worker", lease_seconds=300, now=observed)
    assert token is not None
    repository.record_task(
        "test-worker",
        token,
        "strength_refresh",
        enabled=True,
        status="idle",
        now=observed,
    )
    return repository, token


def test_a08_ten_identical_posts_share_one_strength_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strength_action_repo(tmp_path, monkeypatch)
    parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    app = FastAPI()
    app.include_router(worker_actions.router)
    request_ids: list[str] = []
    with TestClient(app) as client:
        for _ in range(10):
            response = client.post(
                "/api/worker/actions/strength_refresh",
                json={"parameters": parameters},
            )
            assert response.status_code in {200, 202}
            request_ids.append(response.json()["request_id"])
    assert len(set(request_ids)) == 1
    assert request_ids[0]


def test_e02_missing_action_is_not_found(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _strength_action_repo(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(worker_actions.router)
    with TestClient(app) as client:
        missing = client.get("/api/worker/actions/act_00000000000000000000000000000000")
        invalid = client.get("/api/worker/actions/not-a-valid-id")
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "action_not_found"
    assert invalid.status_code == 400
    assert invalid.json()["detail"]["code"] == "invalid_request_id"


def test_e05_visitor_get_storm_does_not_call_scanner_or_touch_mtime(
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

    def forbidden(*_args, **_kwargs):
        raise AssertionError("visitor GET must not start a live scan")

    monkeypatch.setattr("app.services.strength.scanner.scan_strength", forbidden)
    for _ in range(20):
        _rp(asyncio.run(strength.scan(_areq(), **params)))
    assert variant.stat().st_mtime_ns == before


def test_e02_failed_action_is_terminal_and_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, token = _strength_action_repo(tmp_path, monkeypatch)
    app = FastAPI()
    app.include_router(worker_actions.router)
    parameters = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    with TestClient(app) as client:
        queued = client.post("/api/worker/actions/strength_refresh", json={"parameters": parameters})
        assert queued.status_code in {200, 202}
        request_id = queued.json()["request_id"]
        claimed = repository.claim_actions("test-worker", token, "strength_refresh")
        assert claimed
        repository.finish_actions(
            "test-worker",
            token,
            [item["request_id"] for item in claimed],
            succeeded=False,
            error_code="strength_input_unavailable",
        )
        failed = client.get(f"/api/worker/actions/{request_id}")
    assert failed.status_code == 200
    body = failed.json()
    assert body["status"] == "failed"
    assert body["error_code"] == "strength_input_unavailable"
    assert body["status"] != "completed"


def test_e04_reader_keeps_complete_snapshot_until_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    params = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    strength._write_strength_snapshot(
        base,
        parameters=params,
        payload=_payload(params, ticker="AAPL", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 60,
    )
    seen_before_replace: list[str] = []
    real_replace = os.replace

    def wrapped_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        document = json.loads(Path(dst).read_text(encoding="utf-8"))
        seen_before_replace.append(document["payload"]["rows"][0]["ticker"])
        assert not str(src).endswith(".json") or Path(src).name.startswith(".")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", wrapped_replace)
    strength._write_strength_snapshot(
        base,
        parameters=params,
        payload=_payload(params, ticker="MSFT", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW,
    )
    assert seen_before_replace == ["AAPL"]
    published = json.loads(base.read_text(encoding="utf-8"))
    assert published["payload"]["rows"][0]["ticker"] == "MSFT"
    leftovers = list(tmp_path.glob(".strength-snapshot-v1.*.tmp"))
    assert leftovers == []


def test_e06_parameter_hash_links_variant_path_and_published_payload(
    tmp_path: Path,
) -> None:
    base = tmp_path / "strength-snapshot-v1.json"
    params = {**strength.DEFAULT_STRENGTH_SCAN_PARAMETERS, "sector_id": "semiconductors"}
    digest = strength.strength_scan_parameters_hash(params)
    variant = strength._strength_snapshot_path(params, base_path=base)
    assert digest in variant.name
    strength._write_strength_snapshot(
        variant,
        base_path=base,
        parameters=params,
        payload=_payload(params, ticker="NVDA", through="2026-09-03T20:00:00+00:00"),
        saved_at=NOW - 30,
    )
    document = json.loads(variant.read_text(encoding="utf-8"))
    assert document["parameters"]["sector_id"] == "semiconductors"
    assert strength.strength_scan_parameters_hash(document["parameters"]) == digest
