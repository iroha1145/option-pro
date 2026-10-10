"""首页市场综合研判的接口：访客只读、ETag/304、缺失也是 200、补发只给 Owner。

研判存储与槽判定属于后端库，这里注入假存储与假槽表；接口本身走真实的密码
模式网关、Owner 登录与 Worker 状态库。
"""

from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

import app.main as main
import app.services.market_brief as market_brief_package
from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
)
from app.api import access as access_api
from app.api import market_brief as market_brief_api
from app.data_paths import get_data_paths
from app.main import _GatewayMiddleware
from app.personal_config import AccessConfig, MarketBriefConfig
from app.services.market_brief import BriefSchedule
from app.worker.state import WorkerStateRepository


PASSWORD = "market-brief-api-test-password"
ORIGIN = "https://testserver"
SAMPLE = json.loads(
    (Path(__file__).resolve().parent / "fixtures" / "market_brief_sample.json").read_text(
        encoding="utf-8"
    )
)
MISSING = {
    "status": "missing",
    "schema_version": "market-brief-v1",
    "brief": None,
    "latest_attempt": None,
    "next_slot": {"slot": "pre_open", "at": "2026-10-09T12:40:00Z"},
    "snapshot_saved_at": None,
}
HISTORY = [
    {
        "run_id": "mb_20261008_post_close_5f1c3a9e",
        "trading_date": "2026-10-08",
        "slot": "post_close",
        "trigger": "scheduled",
        "status": "completed",
        "generated_at": "2026-10-09T03:41:12Z",
        "error_code": None,
    },
    {
        "run_id": "mb_20261008_pre_open_0a1b2c3d",
        "trading_date": "2026-10-08",
        "slot": "pre_open",
        "trigger": "scheduled",
        "status": "failed",
        "generated_at": None,
        "error_code": "provider_overloaded",
    },
    {
        "run_id": "mb_20261007_post_close_99887766",
        "trading_date": "2026-10-07",
        "slot": "post_close",
        "trigger": "manual",
        "status": "completed",
        "generated_at": "2026-10-08T03:12:00Z",
        "error_code": None,
    },
]


class FakeStore:
    def __init__(self, latest: dict, *, runs_today: int = 0, root: Path | None = None) -> None:
        # The default personal config tracks the shared model budget, which
        # reads the brief run directory.
        self.root = root
        self.latest = latest
        self.runs_today = runs_today
        self.latest_calls: list[dict] = []
        self.runs_on_days: list[date] = []

    def latest_public(self, *, now=None, owner=False, schedule=None):
        self.latest_calls.append({"now": now, "owner": owner, "schedule": schedule})
        payload = copy.deepcopy(self.latest)
        if owner and payload.get("brief"):
            payload["brief"]["cost_usd"] = 2.15
        return payload

    def history(self, *, limit: int = 10):
        return copy.deepcopy(HISTORY[:limit])

    def runs_on(self, day: date) -> int:
        self.runs_on_days.append(day)
        return self.runs_today


def _action_headers() -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Optix-Action": "1",
        "Sec-Fetch-Site": "same-origin",
    }


def _app() -> FastAPI:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password(PASSWORD),
    )
    app.include_router(access_api.router)
    app.include_router(
        market_brief_api.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )
    app.add_middleware(_GatewayMiddleware, access_runtime=app.state.access_runtime)
    return app


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/access/login",
        json={"password": PASSWORD},
        headers=_action_headers(),
    )
    assert response.status_code == 200


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> FakeStore:
    fake = FakeStore(SAMPLE, root=tmp_path / "market-brief")
    monkeypatch.setattr(market_brief_api, "_store", lambda: fake)
    monkeypatch.setattr(market_brief_api, "_key_configured", lambda: True)
    monkeypatch.setattr(
        market_brief_package,
        "next_slot_at",
        lambda now, schedule: (
            datetime(2026, 10, 9, 12, 40, tzinfo=timezone.utc),
            date(2026, 10, 9),
            "pre_open",
        ),
    )
    main._rl_buckets.clear()
    return fake


def _worker_ready(*, enabled: bool = True) -> WorkerStateRepository:
    repository = WorkerStateRepository(get_data_paths().worker_db)
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("market-brief-api", lease_seconds=300, now=observed)
    assert token is not None
    repository.record_task(
        "market-brief-api",
        token,
        "market_brief",
        enabled=enabled,
        status="idle",
        now=observed,
    )
    return repository


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------


def test_anonymous_read_serves_the_stored_brief_with_an_etag(store: FakeStore) -> None:
    with TestClient(_app(), base_url=ORIGIN) as client:
        response = client.get("/api/market-brief/latest")
        assert response.status_code == 200
        assert response.json() == SAMPLE
        assert response.headers["cache-control"] == (
            "private, max-age=60, stale-while-revalidate=300"
        )
        etag = response.headers.get("etag")
        assert etag
        replay = client.get("/api/market-brief/latest", headers={"If-None-Match": etag})
    assert replay.status_code == 304
    call = store.latest_calls[0]
    assert call["owner"] is False
    assert isinstance(call["schedule"], BriefSchedule)
    assert call["now"].tzinfo is not None


def test_a_missing_brief_is_still_a_200(store: FakeStore) -> None:
    store.latest = MISSING
    with TestClient(_app(), base_url=ORIGIN) as client:
        response = client.get("/api/market-brief/latest")
    assert response.status_code == 200
    assert response.json() == MISSING


def test_the_owner_reads_the_owner_projection(store: FakeStore) -> None:
    with TestClient(_app(), base_url=ORIGIN) as client:
        anonymous = client.get("/api/market-brief/latest").json()
        _login(client)
        owner = client.get("/api/market-brief/latest").json()
    assert "cost_usd" not in anonymous["brief"]
    assert owner["brief"]["cost_usd"] == 2.15
    assert [call["owner"] for call in store.latest_calls] == [False, True]


def test_the_byte_cache_key_follows_the_next_slot_and_latest_attempt() -> None:
    base = copy.deepcopy(SAMPLE)
    moved = copy.deepcopy(SAMPLE)
    moved["next_slot"] = {"slot": "post_close", "at": "2026-10-09T20:30:00Z"}
    attempted = copy.deepcopy(SAMPLE)
    attempted["latest_attempt"] = {"run_id": "mb_x", "status": "failed"}
    keys = {
        market_brief_api._latest_version_key(payload, owner=False)
        for payload in (base, moved, attempted)
    }
    assert len(keys) == 3
    assert market_brief_api._latest_version_key(base, owner=True) not in keys


def test_history_is_public_and_bounded(store: FakeStore) -> None:
    with TestClient(_app(), base_url=ORIGIN) as client:
        assert client.get("/api/market-brief/history?limit=0").status_code == 422
        assert client.get("/api/market-brief/history?limit=31").status_code == 422
        response = client.get("/api/market-brief/history?limit=2")
    assert response.status_code == 200
    assert response.json() == {"runs": HISTORY[:2]}


def test_turning_public_read_off_hides_reads_from_visitors(
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        market_brief_api,
        "_config",
        lambda: MarketBriefConfig(public_read=False),
    )
    with TestClient(_app(), base_url=ORIGIN) as client:
        assert client.get("/api/market-brief/latest").status_code == 401
        assert client.get("/api/market-brief/history").status_code == 401
        _login(client)
        assert client.get("/api/market-brief/latest").status_code == 200
        assert client.get("/api/market-brief/history").status_code == 200


# ---------------------------------------------------------------------------
# 状态与补发
# ---------------------------------------------------------------------------


def test_anonymous_status_and_runs_are_rejected(store: FakeStore) -> None:
    _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        status_response = client.get("/api/market-brief/status")
        run_response = client.post(
            "/api/market-brief/runs",
            json={},
            headers=_action_headers(),
        )
    assert status_response.status_code == 401
    assert run_response.status_code == 401
    assert WorkerStateRepository(get_data_paths().worker_db).action_requests(
        action_type="market_brief"
    ) == []


def test_owner_run_request_queues_one_worker_action(store: FakeStore) -> None:
    repository = _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        first = client.post(
            "/api/market-brief/runs",
            json={"slot": "pre_open"},
            headers=_action_headers(),
        )
        second = client.post(
            "/api/market-brief/runs",
            json={"slot": "post_close"},
            headers=_action_headers(),
        )
    assert first.status_code == 202, first.text
    payload = first.json()
    assert set(payload) == {
        "request_id",
        "action_type",
        "task_name",
        "status",
        "reason",
        "reused",
        "requested_at",
        "cooldown_until",
        "cooldown_seconds",
        "slot",
        "error_code",
    }
    assert payload["action_type"] == "market_brief"
    assert payload["task_name"] == "market_brief"
    assert payload["status"] == "queued"
    assert payload["reason"] == "queued"
    assert payload["reused"] is False
    assert payload["slot"] == "pre_open"
    assert payload["cooldown_seconds"] == 600.0
    assert payload["error_code"] is None
    # 同一时间只有一个排队中的补发：第二次复用它并说明原因。
    assert second.status_code == 200
    assert second.json()["reason"] == "already_running"
    assert second.json()["error_code"] == "market_brief_in_progress"
    assert second.json()["request_id"] == payload["request_id"]
    queued = repository.action_requests(action_type="market_brief")
    assert len(queued) == 1
    assert queued[0]["details"] == {"parameters": {"slot": "pre_open"}}
    assert queued[0]["cooldown_seconds"] == 600.0


def test_a_run_request_without_a_slot_lets_the_worker_choose(store: FakeStore) -> None:
    repository = _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        response = client.post(
            "/api/market-brief/runs",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 202
    assert response.json()["slot"] is None
    assert repository.action_requests(action_type="market_brief")[0]["details"] == {}


@pytest.mark.parametrize(
    "body",
    [{"slot": "midday"}, {"slot": "pre_open", "force": True}, {"slot": 1}],
)
def test_a_malformed_run_request_is_rejected(store: FakeStore, body: dict) -> None:
    _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        response = client.post(
            "/api/market-brief/runs",
            json=body,
            headers=_action_headers(),
        )
    assert response.status_code == 422


def test_run_requests_are_refused_without_a_key_or_past_the_daily_limit(
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        monkeypatch.setattr(market_brief_api, "_key_configured", lambda: False)
        missing_key = client.post(
            "/api/market-brief/runs", json={}, headers=_action_headers()
        )
        monkeypatch.setattr(market_brief_api, "_key_configured", lambda: True)
        store.runs_today = 6
        over_limit = client.post(
            "/api/market-brief/runs", json={}, headers=_action_headers()
        )
    assert missing_key.status_code == 409
    assert missing_key.json()["detail"]["code"] == "anthropic_api_key_missing"
    assert over_limit.status_code == 429
    assert over_limit.json()["detail"] == {
        "code": "daily_run_limit_reached",
        "daily_runs": 6,
        "daily_max_runs": 6,
    }
    assert repository.action_requests(action_type="market_brief") == []


def test_run_requests_need_a_healthy_worker_with_the_task(store: FakeStore) -> None:
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        response = client.post(
            "/api/market-brief/runs", json={}, headers=_action_headers()
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"


def test_owner_status_reports_schedule_queue_and_daily_runs(store: FakeStore) -> None:
    _worker_ready()
    store.runs_today = 2
    with TestClient(_app(), base_url=ORIGIN) as client:
        _login(client)
        queued = client.post(
            "/api/market-brief/runs",
            json={"slot": "post_close"},
            headers=_action_headers(),
        ).json()
        response = client.get("/api/market-brief/status")
    assert response.status_code == 200
    payload = response.json()
    # The repository default tracks the shared model budget without enforcing it.
    shared_budget = payload.pop("shared_budget")
    assert shared_budget["budget_mode"] == "tracking"
    assert shared_budget["budget_enforced"] is False
    assert payload == {
        "enabled": True,
        "configured": True,
        "scheduled_enabled": True,
        "next_slot": {
            "slot": "pre_open",
            "trading_date": "2026-10-09",
            "at": "2026-10-09T12:40:00Z",
        },
        "last_run": HISTORY[0],
        "pending_action": {
            "request_id": queued["request_id"],
            "status": "queued",
            "requested_at": queued["requested_at"],
            "started_at": None,
            "slot": "post_close",
        },
        "cooldown_until": None,
        "cooldown_seconds": 600.0,
        "daily_runs": 2,
        "daily_max_runs": 6,
    }
    # 次数口径是 UTC 日历日。
    assert store.runs_on_days[-1] == datetime.now(timezone.utc).date()


def test_the_real_store_and_scheduler_serve_an_empty_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """不替换存储与槽判定：还没有任何运行时，读接口与状态都能正常返回。"""

    main._rl_buckets.clear()
    monkeypatch.setattr(market_brief_api, "_key_configured", lambda: False)
    _worker_ready()
    with TestClient(_app(), base_url=ORIGIN) as client:
        latest = client.get("/api/market-brief/latest")
        history = client.get("/api/market-brief/history")
        _login(client)
        status_payload = client.get("/api/market-brief/status").json()
    assert latest.status_code == 200
    payload = latest.json()
    assert payload["status"] == "missing"
    assert payload["brief"] is None
    assert payload["next_slot"]["slot"] in {"pre_open", "post_close"}
    assert history.json() == {"runs": []}
    assert status_payload["configured"] is False
    assert status_payload["last_run"] is None
    assert status_payload["daily_runs"] == 0
    assert status_payload["next_slot"]["slot"] == payload["next_slot"]["slot"]
    assert status_payload["next_slot"]["at"] == payload["next_slot"]["at"]


def test_the_real_application_routes_and_gateway_agree() -> None:
    schema = main.app.openapi()
    assert {
        path: sorted(operations)
        for path, operations in schema["paths"].items()
        if path.startswith("/api/market-brief")
    } == {
        "/api/market-brief/latest": ["get"],
        "/api/market-brief/history": ["get"],
        "/api/market-brief/status": ["get"],
        "/api/market-brief/runs": ["post"],
    }
    assert main._is_public_read_request("/api/market-brief/latest", "GET")
    assert main._is_public_read_request("/api/market-brief/history", "GET")
    assert not main._is_public_read_request("/api/market-brief/status", "GET")
    assert not main._is_public_read_request("/api/market-brief/runs", "POST")
