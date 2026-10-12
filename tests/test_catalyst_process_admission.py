from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from app.catalyst_read_admission import CatalystReadAdmission
from app.services.catalysts.hotspot_reads import current_hotspot_read_pool


def test_pool_splits_eight_hotspots_from_one_feed_and_cancel_retains_slots():
    async def run():
        pool = SimpleNamespace(available=True)
        app = SimpleNamespace(state=SimpleNamespace(hotspot_read_pool=pool))
        release = asyncio.Event()
        active = {"feed": 0, "hotspots": 0}
        peak = dict(active)
        completed = []
        async def inner(scope, receive, send):
            kind = scope["path"].rsplit("/", 1)[-1]
            active[kind] += 1
            peak[kind] = max(peak[kind], active[kind])
            assert current_hotspot_read_pool() is (pool if kind == "hotspots" else None)
            try:
                await release.wait()
                completed.append(scope["number"])
            finally:
                active[kind] -= 1
        admission = CatalystReadAdmission(inner)
        def request(kind, number):
            return asyncio.create_task(admission({"type": "http", "method": "GET",
                "path": f"/api/catalysts/{kind}", "app": app, "number": number}, None, None))
        workers = [request("hotspots", i) for i in range(8)] + [request("feed", 8)]
        for _ in range(100):
            if active == {"feed": 1, "hotspots": 8}:
                break
            await asyncio.sleep(.001)
        assert active == {"feed": 1, "hotspots": 8}
        queued = request("hotspots", 9)
        queued_feed = request("feed", 10)
        for _ in range(5):
            workers[0].cancel()
            await asyncio.sleep(.001)
        assert not workers[0].done()
        assert not queued.done() and not queued_feed.done()
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        assert active == {"feed": 1, "hotspots": 8}
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await workers[0]
        await asyncio.gather(*workers[1:], queued_feed)
        await request("hotspots", 11)
        assert 9 not in completed and 11 in completed
        assert peak == {"feed": 1, "hotspots": 8}
        assert active == {"feed": 0, "hotspots": 0}
        assert current_hotspot_read_pool() is None
    asyncio.run(run())


def test_stopped_pool_uses_shared_single_slot():
    async def run():
        app = SimpleNamespace(state=SimpleNamespace(hotspot_read_pool=SimpleNamespace(available=False)))
        active = peak = 0
        async def inner(scope, receive, send):
            nonlocal active, peak
            assert current_hotspot_read_pool() is None
            active += 1
            peak = max(active, peak)
            await asyncio.sleep(.005)
            active -= 1
        admission = CatalystReadAdmission(inner)
        await asyncio.gather(*(admission({"type": "http", "method": "GET", "app": app,
            "path": "/api/catalysts/" + ("feed" if i % 2 else "hotspots")}, None, None)
            for i in range(10)))
        assert peak == 1
    asyncio.run(run())


@pytest.mark.parametrize("mode,slots,waiting", [("hotspots", 8, 64), ("feed", 1, 96), ("fallback", 1, 96)])
def test_two_thousand_requests_have_bounded_waiters_and_cancellation_recovers(mode, slots, waiting):
    async def run():
        app = SimpleNamespace(state=SimpleNamespace(
            hotspot_read_pool=SimpleNamespace(available=mode != "fallback")))
        release = asyncio.Event()
        active = 0
        statuses = []
        async def inner(scope, receive, send):
            nonlocal active
            active += 1
            try:
                await release.wait()
                await JSONResponse({"ok": True})(scope, receive, send)
            finally:
                active -= 1
        admission = CatalystReadAdmission(inner)
        async def request(index):
            path = mode if mode != "fallback" else ("feed" if index % 2 else "hotspots")
            messages = []
            async def send(message):
                messages.append(message)
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
            await admission({"type": "http", "method": "GET", "app": app,
                "path": "/api/catalysts/" + path}, None, send)
            return messages
        tasks = [asyncio.create_task(request(i)) for i in range(2000)]
        accepted = slots + waiting
        for _ in range(1000):
            if len(statuses) == 2000 - accepted and active == slots:
                break
            await asyncio.sleep(.001)
        assert statuses == [429] * (2000 - accepted)
        assert active == slots
        gates = admission._hotspot_limiters if mode == "hotspots" else admission._limiters
        gate = gates[asyncio.get_running_loop()]
        assert gate.entered == accepted
        if mode == "fallback":
            assert len(admission._hotspot_limiters) == 0
        # Cancel ten queued requests and repeatedly cancel one active request.
        for task in tasks[slots:slots + 10]:
            task.cancel()
        await asyncio.gather(*tasks[slots:slots + 10], return_exceptions=True)
        assert gate.entered == accepted - 10
        for _ in range(3):
            tasks[0].cancel()
            await asyncio.sleep(.001)
        assert not tasks[0].done() and gate.entered == accepted - 10
        replacements = [asyncio.create_task(request(2000 + i)) for i in range(10)]
        await asyncio.sleep(.01)
        assert gate.entered == accepted
        release.set()
        result = await asyncio.gather(*tasks, *replacements, return_exceptions=True)
        assert sum(isinstance(value, asyncio.CancelledError) for value in result) == 11
        assert gate.entered == 0 and active == 0
        rejected = next(value for value in result if isinstance(value, list)
                        and value[0]["status"] == 429)
        assert (b"retry-after", b"1") in rejected[0]["headers"]
        assert b'"error":"rate_limited"' in rejected[1]["body"]
        statuses.clear()
        await asyncio.gather(*(request(i) for i in range(50)))
        assert statuses == [200] * 50
        assert gate.entered == 0
    asyncio.run(run())


def test_admission_overflow_keeps_gateway_security_and_cache_headers(monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app import main

    monkeypatch.setattr(main, "_rl_buckets", {})
    monkeypatch.setattr(main, "_RL_MARKET_READ_LIMIT", 10000)
    async def run():
        inner = FastAPI()
        release = asyncio.Event()
        @inner.get("/api/catalysts/hotspots")
        async def read():
            await release.wait()
            return {"ok": True}
        runtime = SimpleNamespace(mode="private", visitor_ai_actions=False,
            visitor_live_pulls=False, request_is_owner=lambda request: True)
        gateway = main._GatewayMiddleware(inner, access_runtime=runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gateway),
                                      base_url="http://localhost") as client:
            tasks = [asyncio.create_task(client.get("/api/catalysts/hotspots")) for _ in range(97)]
            for _ in range(1000):
                gate = gateway.catalyst_read_admission._limiters.get(asyncio.get_running_loop())
                if gate is not None and gate.entered == 97:
                    break
                await asyncio.sleep(.001)
            assert gate.entered == 97
            response = await client.get("/api/catalysts/hotspots")
            assert response.status_code == 429
            assert response.headers["retry-after"] == "1"
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
            assert response.headers["cache-control"] == "private, no-store"
            release.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*tasks))
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["feed", "stopped_pool", "missing_pool"])
@pytest.mark.parametrize("burst", [80, 97, 98])
def test_shared_gate_accepts_eighty_and_rejects_only_above_ninety_seven(mode, burst):
    async def run():
        pool = None if mode == "missing_pool" else SimpleNamespace(available=mode == "feed")
        app = SimpleNamespace(state=SimpleNamespace(hotspot_read_pool=pool))
        release = asyncio.Event()
        active = 0
        statuses = []

        async def inner(scope, receive, send):
            nonlocal active
            active += 1
            try:
                await release.wait()
                await JSONResponse({"ok": True})(scope, receive, send)
            finally:
                active -= 1

        admission = CatalystReadAdmission(inner)

        async def request(index):
            # Pool fallback shares a single bound across both read paths.
            kind = "feed" if mode == "feed" or index % 2 else "hotspots"
            async def send(message):
                if message["type"] == "http.response.start":
                    statuses.append(message["status"])
            await admission({"type": "http", "method": "GET", "app": app,
                "path": "/api/catalysts/" + kind}, None, send)

        tasks = [asyncio.create_task(request(i)) for i in range(burst)]
        for _ in range(1000):
            gate = admission._limiters.get(asyncio.get_running_loop())
            if gate is not None and gate.entered == min(burst, 97) and active == 1 and len(statuses) == max(0, burst - 97):
                break
            await asyncio.sleep(.001)
        assert gate.entered == min(burst, 97)
        assert active == 1
        assert statuses == [429] * max(0, burst - 97)
        assert not admission._hotspot_limiters
        release.set()
        await asyncio.gather(*tasks)
        assert statuses.count(200) == min(burst, 97)
        assert gate.entered == active == 0
    asyncio.run(run())
