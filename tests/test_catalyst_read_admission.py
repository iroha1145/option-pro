from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel

from app.access import current_request_is_owner
from app.main import _GatewayMiddleware
from app import main


class Reply(BaseModel):
    number: int
    owner: bool


def _gateway():
    inner = FastAPI()
    router = APIRouter(prefix="/api/catalysts")
    lock = threading.Lock()
    counts = {"active": 0, "peak": 0, "completed": 0}
    entered = threading.Event()
    release = threading.Event()

    def read(number: int):
        with lock:
            counts["active"] += 1
            counts["peak"] = max(counts["peak"], counts["active"])
        try:
            if number == 200:
                entered.set()
                assert release.wait(2), "test did not release blocked worker"
            else:
                time.sleep(.01)
            if number == -1:
                raise HTTPException(409, "expected")
            if number == -2:
                raise RuntimeError("uncaught endpoint failure")
            return {"number": number, "owner": current_request_is_owner(), "extra": "removed"}
        finally:
            with lock:
                counts["active"] -= 1
                counts["completed"] += 1
    router.get("/feed", response_model=Reply)(read)
    router.get("/hotspots", response_model=Reply)(read)
    inner.include_router(router)
    @inner.get("/health")
    @inner.get("/api/quotes/stream")
    @inner.post("/api/access/login")
    @inner.post("/api/catalysts/feed")
    async def bypass():
        return {"ok": True}
    runtime = SimpleNamespace(mode="private", visitor_ai_actions=False,
        visitor_live_pulls=False, request_is_owner=lambda request: request.headers.get("x-owner") == "yes")
    return _GatewayMiddleware(inner, access_runtime=runtime), counts, entered, release


@pytest.fixture(autouse=True)
def _buckets(monkeypatch):
    monkeypatch.setattr(main, "_rl_buckets", {})
    monkeypatch.setattr(main, "_RL_MARKET_READ_LIMIT", 10000)
    monkeypatch.setattr(main, "_RL_LIGHT_LIMIT", 10000)
    monkeypatch.setattr(main, "_RL_HEAVY_LIMIT", 10000)


async def _wait_entered(entered):
    for _ in range(1000):
        if entered.is_set():
            return
        await asyncio.sleep(.001)
    raise AssertionError("worker did not start")


async def _cancelled(task):
    with pytest.raises(asyncio.CancelledError):
        await task


def test_shared_sync_body_limit_context_errors_and_two_loops():
    app, counts, _, _ = _gateway()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"x-owner": "yes"}) as client:
            async def read(i):
                path = "/api/catalysts/feed" if i % 2 else "/api/catalysts/hotspots"
                response = await client.get(path, params={"number": i})
                assert response.status_code == 200
                assert response.json() == {"number": i, "owner": True}
            await asyncio.gather(*(read(i) for i in range(10)))
            assert (await client.get("/api/catalysts/feed", params={"number": -1})).status_code == 409
            with pytest.raises(RuntimeError, match="uncaught endpoint failure"):
                await client.get("/api/catalysts/feed", params={"number": -2})
            assert (await client.get("/api/catalysts/feed", params={"number": "bad"})).status_code == 422
            await read(100)
    asyncio.run(run())
    asyncio.run(run())
    app.access_runtime.mode = "password"
    async def guest():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.get("/api/catalysts/feed", params={"number": 101})
            assert response.json() == {"number": 101, "owner": False}
            hidden = await client.get("/__bench/admission", headers={"x-owner": "yes"})
            assert hidden.status_code == 404
    asyncio.run(guest())
    assert counts == {"active": 0, "peak": 1, "completed": 27}
    assert current_request_is_owner() is False


def test_gateway_rejections_body_checks_and_bypass_do_not_queue(monkeypatch):
    app, counts, entered, release = _gateway()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            blocker = asyncio.create_task(client.get("/api/catalysts/feed", params={"number": 200}, headers={"x-owner": "yes"}))
            await _wait_entered(entered)
            try:
                denied = await asyncio.wait_for(client.get("/api/catalysts/hotspots", params={"number": 1}), .3)
                assert denied.status_code == 403
                monkeypatch.setattr(main, "_RL_MARKET_READ_LIMIT", 1)
                limited = await asyncio.wait_for(client.get("/api/catalysts/hotspots", params={"number": 1}, headers={"x-owner": "yes"}), .3)
                assert limited.status_code == 429
                monkeypatch.setattr(main, "_RL_MARKET_READ_LIMIT", 10000)
                invalid_body = await asyncio.wait_for(client.request("GET", "/api/catalysts/feed", headers={"x-owner": "yes", "content-length": str(main._MAX_API_BODY_BYTES + 1)}), .3)
                assert invalid_body.status_code == 413
                for method, path in [("GET", "/health"), ("GET", "/api/quotes/stream"), ("POST", "/api/access/login"), ("POST", "/api/catalysts/feed")]:
                    response = await asyncio.wait_for(client.request(method, path, headers={"x-owner": "yes"}), .3)
                    assert response.status_code == 200
                assert counts["completed"] == 0 and counts["active"] == 1
            finally:
                release.set()
                assert (await blocker).status_code == 200
    asyncio.run(run())


def test_queued_cancel_never_executes_and_active_cancel_retains_slot():
    app, counts, entered, release = _gateway()
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost", headers={"x-owner": "yes"}) as client:
            active = asyncio.create_task(client.get("/api/catalysts/feed", params={"number": 200}))
            await _wait_entered(entered)
            queued = asyncio.create_task(client.get("/api/catalysts/hotspots", params={"number": 1}))
            await asyncio.sleep(.01)
            queued.cancel()
            await _cancelled(queued)
            active.cancel()
            # A second cancellation must still not free the active worker slot.
            await asyncio.sleep(.01)
            active.cancel()
            next_request = asyncio.create_task(client.get("/api/catalysts/hotspots", params={"number": 2}))
            await asyncio.sleep(.02)
            assert counts["active"] == 1 and counts["completed"] == 0
            assert not active.done() and not next_request.done()
            release.set()
            await _cancelled(active)
            assert (await next_request).status_code == 200
            assert counts == {"active": 0, "peak": 1, "completed": 2}
    asyncio.run(run())
