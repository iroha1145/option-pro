"""Behavioral acceptance tests for the read-cache overhaul.

These pin the properties the caching work must not lose, as behaviors (not
source-text greps): fingerprint-gated single parse, atomic-replace
invalidation, singleflight cold reads, identity-bounded negative caching,
owner GETs that never start provider work, ETag/304 correctness, principal
scoping of every response cache, and byte-budgeted eviction.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import public_home_snapshot as phs
from app.access import request_owner_access_context
from app.api import earnings, market
from app.personal_config import get_personal_config
from app.services import http_read_cache
from app.services.cache import TTLCache
from app.services.snapshot_read_cache import FingerprintedFileCache
from app.public_home_snapshot import (
    PUBLIC_HOME_INDEX_SYMBOLS,
    create_public_home_entry,
    public_home_resource_parameters,
    read_public_home_entries,
    write_public_home_snapshot,
)
from tests.http_response_support import anonymous_get_request, response_payload


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _indices_payload(now: float, *, price: float = 100.0) -> dict:
    return {
        "indices": [
            {"symbol": symbol, "price": price + index, "change_percent": 0.5}
            for index, symbol in enumerate(PUBLIC_HOME_INDEX_SYMBOLS)
        ],
        "attempted": len(PUBLIC_HOME_INDEX_SYMBOLS),
        "succeeded": len(PUBLIC_HOME_INDEX_SYMBOLS),
        "data_limited": False,
        "source_status": "active",
        "as_of": _iso(now),
    }


def _indices_snapshot(path: Path, now: float, *, price: float = 100.0) -> None:
    entry = create_public_home_entry(
        "indices",
        _indices_payload(now, price=price),
        saved_at=now,
        parameters=public_home_resource_parameters("indices", now=now),
    )
    write_public_home_snapshot(path, {"indices": entry}, now=now)


# ─── 1. Same fingerprint parses and validates exactly once ──────────────────


def test_same_fingerprint_parses_and_validates_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)

    calls = {"validate": 0}
    original = phs._validate_entry

    def counting_validate(resource, value, *, now):
        calls["validate"] += 1
        return original(resource, value, now=now)

    monkeypatch.setattr(phs, "_validate_entry", counting_validate)

    first = read_public_home_entries(path, now=now)
    second = read_public_home_entries(path, now=now + 5)
    third = read_public_home_entries(path, now=now + 10)

    assert first["indices"]["payload"]["indices"][0]["price"] == 100.0
    assert second["indices"]["saved_at"] == first["indices"]["saved_at"]
    assert third["indices"]["saved_at"] == first["indices"]["saved_at"]
    assert calls["validate"] == 1


# ─── 2. Atomic replace invalidates immediately ───────────────────────────────


def test_atomic_replace_invalidates_immediately(tmp_path: Path) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 60, price=100.0)
    first = read_public_home_entries(path, now=now)
    assert first["indices"]["payload"]["indices"][0]["price"] == 100.0

    # write_public_home_snapshot publishes via mkstemp + os.replace — exactly
    # the worker's path. The very next read must see the new generation.
    _indices_snapshot(path, now - 5, price=222.0)
    second = read_public_home_entries(path, now=now)
    assert second["indices"]["payload"]["indices"][0]["price"] == 222.0


# ─── 4. Concurrent cold reads share one parse ────────────────────────────────


def test_concurrent_cold_reads_share_one_parse(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text(json.dumps({"value": 41}))
    cache = FingerprintedFileCache("test_singleflight", max_paths=4)

    parses = []
    barrier = threading.Barrier(6)

    def loader(raw: bytes):
        parses.append(1)
        time.sleep(0.05)
        return json.loads(raw)

    results = []

    def reader():
        barrier.wait()
        results.append(cache.read(target, loader))

    threads = [threading.Thread(target=reader) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(parses) == 1
    assert all(result == {"value": 41} for result in results)


# ─── 5. Unavailability is bounded by the file identity, not by time ──────────


def test_corrupt_file_negative_cache_clears_on_publish(tmp_path: Path) -> None:
    target = tmp_path / "doc.json"
    target.write_text("{not json")
    cache = FingerprintedFileCache("test_negative", max_paths=4)

    parses = []

    def loader(raw: bytes):
        parses.append(1)
        return json.loads(raw)

    assert cache.read(target, loader) is None
    assert cache.read(target, loader) is None
    # The corrupt bytes were parsed once; the second miss came from the
    # identity-scoped negative entry, not another parse.
    assert len(parses) == 1

    # A successful publish (new identity via os.replace semantics) must be
    # visible on the very next read — unavailability never outlives its cause.
    healthy = tmp_path / "doc.json.tmp"
    healthy.write_text(json.dumps({"value": 7}))
    import os

    os.replace(healthy, target)
    assert cache.read(target, loader) == {"value": 7}


# ─── 6. Owner ordinary GETs never start provider work (password mode) ────────


def _password_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    private_config = get_personal_config()
    password_config = private_config.model_copy(
        update={
            "access": private_config.access.model_copy(update={"mode": "password"})
        }
    )
    for module in (market, earnings):
        monkeypatch.setattr(module, "get_personal_config", lambda: password_config)


def test_owner_get_serves_snapshot_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )
    _password_mode(monkeypatch)
    monkeypatch.setattr(
        market, "_build_indices", lambda: pytest.fail("owner GET called the provider")
    )
    market._shared_cache.clear()

    async def scenario():
        with request_owner_access_context(True):
            return await market.market_indices(anonymous_get_request())

    payload = response_payload(asyncio.run(scenario()))
    assert payload["indices"][0]["price"] == 100.0


def test_owner_get_stays_unavailable_instead_of_cold_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    missing = tmp_path / "missing.json"
    monkeypatch.setattr(
        phs,
        "get_data_paths",
        lambda: type("P", (), {"public_home_snapshot": missing})(),
    )
    _password_mode(monkeypatch)
    monkeypatch.setattr(
        earnings,
        "_build_upcoming_earnings",
        lambda *_a: pytest.fail("owner GET started a provider scan"),
    )
    earnings.cache.clear()

    async def scenario():
        with request_owner_access_context(True):
            await earnings.upcoming_earnings(anonymous_get_request())

    with pytest.raises(HTTPException) as caught:
        asyncio.run(scenario())
    assert caught.value.status_code == 503


# ─── 8. Same ETag returns 304 with no body ───────────────────────────────────


def test_matching_etag_returns_304(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )

    async def scenario():
        with request_owner_access_context(False):
            first = await market.market_indices(anonymous_get_request())
            etag = first.headers["etag"]
            assert first.status_code == 200
            assert "max-age" in first.headers["cache-control"]
            assert "Cookie" in first.headers["vary"]
            second = await market.market_indices(
                anonymous_get_request(headers={"If-None-Match": etag})
            )
            assert second.status_code == 304
            assert not second.body
            assert second.headers["etag"] == etag
            # A different validator still gets the full body.
            third = await market.market_indices(
                anonymous_get_request(headers={"If-None-Match": '"nope"'})
            )
            assert third.status_code == 200

    asyncio.run(scenario())


# ─── 9. Principal scope: owner and visitor never share cached bytes ──────────


def test_owner_and_visitor_bodies_and_etags_stay_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )
    _password_mode(monkeypatch)
    market._shared_cache.clear()

    async def scenario():
        with request_owner_access_context(False):
            anon = await market.market_indices(anonymous_get_request())
        with request_owner_access_context(True):
            owner = await market.market_indices(anonymous_get_request())
        return anon, owner

    anon, owner = asyncio.run(scenario())
    anon_payload = response_payload(anon)
    owner_payload = response_payload(owner)
    # The visitor body carries the public-snapshot stale decoration; the owner
    # fresh body does not — and the ETags must differ accordingly.
    assert anon_payload["stale_reason"] == "public_snapshot_only"
    assert "stale_reason" not in owner_payload
    assert anon.headers["etag"] != owner.headers["etag"]


def test_visitor_etag_never_matches_owner_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A visitor replaying the owner's ETag must still get the visitor body."""

    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )
    _password_mode(monkeypatch)
    market._shared_cache.clear()

    async def scenario():
        with request_owner_access_context(True):
            owner = await market.market_indices(anonymous_get_request())
        with request_owner_access_context(False):
            replay = await market.market_indices(
                anonymous_get_request(headers={"If-None-Match": owner.headers["etag"]})
            )
        return replay

    replay = asyncio.run(scenario())
    assert replay.status_code == 200
    assert response_payload(replay)["stale_reason"] == "public_snapshot_only"


# ─── 10. Byte budgets evict instead of growing forever ───────────────────────


def test_ttl_cache_byte_budget_evicts_stably(monkeypatch: pytest.MonkeyPatch) -> None:
    store = TTLCache()
    monkeypatch.setattr(store, "_MAX_TOTAL_BYTES", 50_000, raising=False)
    big = "x" * 9_000
    for index in range(20):
        store.set(f"key:{index}", big, 600)
    stats = store.stats()
    assert stats["bytes"] <= 50_000
    assert 0 < stats["entries"] < 20
    # Newest entries survive; the cache stays useful under pressure.
    assert store.get("key:19") is not None


def test_ttl_cache_oversized_item_is_served_but_not_retained() -> None:
    store = TTLCache()
    huge = "x" * (store._MAX_ITEM_BYTES + 1)
    returned = store.set("huge", huge, 600)
    assert returned is huge
    assert store.get("huge") is None


def test_serialized_response_store_respects_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http_read_cache, "_MAX_TOTAL_BYTES", 64 * 1024)
    monkeypatch.setattr(http_read_cache, "_STORE_MIN_BYTES", 1)
    http_read_cache.reset_serialized_response_cache()
    request = anonymous_get_request()
    for index in range(24):
        asyncio.run(http_read_cache.respond_with_snapshot(
            request,
            {"filler": "y" * 8_000, "index": index},
            version_key=f"budget-test-{index}",
            cache_control="private, max-age=30",
        ))
    store = http_read_cache._store
    with store._lock:
        total = store._total_bytes_locked()
        entries = len(store._entries)
    assert total <= 64 * 1024
    assert 0 < entries < 24


# ─── 11. A restarted process recovers from snapshots without providers ───────


def test_cold_process_recovers_from_snapshot_without_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 120)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )
    _password_mode(monkeypatch)
    monkeypatch.setattr(
        market, "_build_indices", lambda: pytest.fail("restart recovery hit a provider")
    )
    # Simulate the restart: every in-process cache is empty.
    market._shared_cache.clear()
    phs._parsed_documents.invalidate()
    http_read_cache.reset_serialized_response_cache()

    async def scenario():
        results = []
        with request_owner_access_context(True):
            results.append(response_payload(await market.market_indices(anonymous_get_request())))
        with request_owner_access_context(False):
            results.append(response_payload(await market.market_indices(anonymous_get_request())))
        return results

    owner_payload, visitor_payload = asyncio.run(scenario())
    assert owner_payload["indices"][0]["price"] == 100.0
    assert visitor_payload["indices"][0]["price"] == 100.0


# ─── 8b. Same assertions through the real middleware stack (gateway + gzip) ──


def test_conditional_caching_through_full_middleware_stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    now = time.time()
    path = tmp_path / "public-home.json"
    _indices_snapshot(path, now - 30)
    monkeypatch.setattr(
        phs, "get_data_paths", lambda: type("P", (), {"public_home_snapshot": path})()
    )
    _password_mode(monkeypatch)
    market._shared_cache.clear()

    with TestClient(
        app, base_url="http://127.0.0.1", client=("127.0.0.1", 51000)
    ) as client:
        first = client.get(
            "/api/market/indices", headers={"Accept-Encoding": "gzip"}
        )
        assert first.status_code == 200
        # The gateway must not clobber the endpoint's cache policy…
        assert "max-age" in first.headers["cache-control"]
        assert "no-store" not in first.headers["cache-control"]
        etag = first.headers["etag"]
        assert etag.startswith('"')
        assert "Cookie" in first.headers["vary"]
        assert first.headers["x-optix-cache"] in {"bytes-miss", "bytes-hit"}
        assert "enc" in first.headers.get("server-timing", "")
        assert first.json()["indices"][0]["price"] == 100.0

        second = client.get(
            "/api/market/indices",
            headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert not second.content
        assert second.headers["etag"] == etag

        # …while everything that never opted in stays no-store.
        status = client.get("/api/market/status")
        assert status.headers["cache-control"] == "private, no-store"

        # Diagnostics stay behind the owner boundary for real requests too.
        diag = client.get("/api/diagnostics/cache")
        assert diag.status_code == 200  # private-network owner IP
        layers = diag.json()["layers"]
        assert "public_home_documents" in layers
