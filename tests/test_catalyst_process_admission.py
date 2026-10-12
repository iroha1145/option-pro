"""Service-layer admission for the two expensive catalyst reads.

The ASGI CatalystReadAdmission middleware was removed (2026-10-12 audit B9):
/api/catalysts/feed concurrency is bounded by a non-blocking
threading.BoundedSemaphore(1) in the service layer, and /api/catalysts/hotspots
binds the read-process pool at the route entry, letting the pool itself reject
over-subscription.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import request_owner_access_context
from app.api import catalysts as catalyst_api
from app.services.catalysts.errors import CatalystError
from app.services.catalysts.hotspot_reads import current_hotspot_read_pool
from app.services.catalysts.personal_service import feed_read_slot


@pytest.fixture(autouse=True)
def _owner_request_context():
    with request_owner_access_context(True):
        yield


def test_feed_read_slot_is_non_blocking_and_reusable_after_release() -> None:
    with feed_read_slot():
        with pytest.raises(CatalystError) as caught:
            with feed_read_slot():
                pass
        assert caught.value.code == "feed_read_in_progress"
        assert caught.value.retryable is True
        assert caught.value.retry_after_seconds == 1
        assert caught.value.counts_for_circuit is False
    # 释放后立即可再次获取。
    with feed_read_slot():
        pass


def test_feed_route_returns_429_while_a_read_holds_the_slot() -> None:
    entered = threading.Event()
    release = threading.Event()

    class SlowService:
        def feed(self, **kwargs):
            entered.set()
            assert release.wait(2), "test did not release the slow read"
            return {"status": "ok", "items": []}

    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: SlowService()
    with TestClient(app, base_url="http://localhost") as client:
        with ThreadPoolExecutor(max_workers=1) as pool:
            slow = pool.submit(client.get, "/api/catalysts/feed")
            try:
                assert entered.wait(2), "first feed read did not start"
                busy = client.get("/api/catalysts/feed")
                assert busy.status_code == 429
                assert busy.headers["retry-after"] == "1"
                assert busy.json()["detail"]["code"] == "feed_read_in_progress"
            finally:
                release.set()
            assert slow.result().status_code == 200
    # 慢读结束后准入恢复。
    assert client.get("/api/catalysts/feed").status_code == 200


def _hotspots_app(observed: list) -> FastAPI:
    class StubService:
        def hotspots(self, *, limit):
            observed.append(current_hotspot_read_pool())
            return {"status": "ok", "items": []}

    app = FastAPI()
    app.include_router(catalyst_api.router)
    app.dependency_overrides[catalyst_api._service] = lambda: StubService()
    return app


def test_hotspots_route_binds_the_available_read_pool() -> None:
    observed = []
    app = _hotspots_app(observed)
    pool = SimpleNamespace(available=True)
    app.state.hotspot_read_pool = pool
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/catalysts/hotspots").status_code == 200
    assert observed == [pool]
    # 路由返回后上下文不泄漏。
    assert current_hotspot_read_pool() is None


def test_hotspots_route_falls_back_to_inline_read_without_a_pool() -> None:
    # 池不可用（已停止/未启动）时如实回退进程内读，与原中间件语义一致。
    observed = []
    app = _hotspots_app(observed)
    app.state.hotspot_read_pool = SimpleNamespace(available=False)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/catalysts/hotspots").status_code == 200
    assert observed == [None]

    observed.clear()
    app = _hotspots_app(observed)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/catalysts/hotspots").status_code == 200
    assert observed == [None]
