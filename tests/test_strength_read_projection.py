from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import gc
import json
import threading
import weakref

from app.api import strength
from app.services.eod_limited import store
from app.services.eod_limited.read_projection import ProjectionCache
from tests.test_eod_limited_product import _scored
from tests.http_response_support import anonymous_get_request, response_payload


def test_projection_single_flight_bounded_and_weak():
    cache = ProjectionCache(max_entries=2)
    batch = store.BatchDocument()
    calls = []
    barrier = threading.Barrier(8)

    def build():
        calls.append(1)
        return {"observation_rows": [{"ticker": "TAIL", "score": 5}]}

    def read(_):
        barrier.wait()
        return cache.read(batch, "same", build)

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(read, range(8)))
    assert len(calls) == 1
    assert all(v.ticker_rows["TAIL"]["score"] == 5 for v in results)
    results[0]["_stale"] = True
    assert "_stale" not in cache.read(batch, "same", build)
    cache.read(batch, "second", build)
    cache.read(batch, "third", build)
    assert len(cache._entries) == 2
    reference = weakref.ref(batch)
    del batch
    gc.collect()
    assert reference() is None
    assert not cache._entries
    tiny = ProjectionCache(max_bytes=1)
    batch = store.BatchDocument()
    tiny.read(batch, "a", build)
    assert not tiny._entries


def _publish(monkeypatch, tmp_path, score=71.2):
    monkeypatch.setattr(store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    scored = _scored(session="2026-09-18", purpose="live_eod_inference")
    scored["watch_list"][0]["score"] = score
    store.publish_batch({"purpose": "live_eod_inference", "served_session": "2026-09-18", "published_at": 1700000000,
                         "variants": {"balanced|mid": scored, "aggressive|mid": scored, "balanced|short": scored}})


def _read(**kwargs):
    params = dict(universe="all_market", timeframe="mid", profile="balanced", top=120,
                  sector_id=None, min_price=0, min_avg_dollar_volume=0, include_options=False,
                  ranking_algorithm="eod_limited_v1")
    params.update(kwargs)
    return strength._read_eod_limited_snapshot(parameters=params, list_kind="observation", resolution=None)[0]


def test_identity_publication_stale_and_parameter_isolation(monkeypatch, tmp_path):
    from app.services.research_eod_v1 import calendar_asof
    from app.services.eod_limited import read_projection
    monkeypatch.setattr(read_projection, "projections", ProjectionCache())
    _publish(monkeypatch, tmp_path)
    old_batch = weakref.ref(store.read_batch())
    calls = []
    original = strength._project_eod_selection

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(strength, "_project_eod_selection", counted)
    monkeypatch.setattr(calendar_asof, "settled_eod_session", lambda now: date(2026, 9, 18))
    first = _read()
    assert not first["_stale"]
    monkeypatch.setattr(calendar_asof, "settled_eod_session", lambda now: date(2026, 9, 21))
    second = _read()
    assert second["_stale"] and second["stale_reason"] == "newer_session_available"
    assert len(calls) == 1
    assert _read(min_price=1000)["rows"] == []
    assert _read(profile="aggressive")["params"]["profile"] == "aggressive"
    assert _read(timeframe="short")["params"]["timeframe"] == "short"
    _publish(monkeypatch, tmp_path, score=99)
    gc.collect()
    assert old_batch() is None
    assert not read_projection.projections._entries
    assert _read()["rows"][0]["score"] == 99
    # The prior request retains its own stable document across replacement.
    assert first["rows"][0]["score"] == 71.2


def test_compact_scan_preserves_default_and_all_list_metadata(monkeypatch, tmp_path):
    _publish(monkeypatch, tmp_path)
    async def scan(compact):
        return response_payload(await strength.scan(
            anonymous_get_request(), universe="all_market", timeframe="mid", profile="balanced", top=120,
            sector_id=None, min_price=0, min_avg_dollar_volume=0, include_options=False,
            ranking_algorithm="eod_limited_v1", compact=compact))
    full = asyncio.run(scan(False))
    compact = asyncio.run(scan(True))
    assert "observation_rows" in full and "composite_rows" in full
    assert compact == {k: v for k, v in full.items() if k not in {"observation_rows", "composite_rows"}}
    assert len(json.dumps(compact)) < len(json.dumps(full))


def test_stock_full_index_and_live_context(monkeypatch, tmp_path):
    from app.services.eod_limited import context_snapshot
    from fastapi import HTTPException
    import pytest
    _publish(monkeypatch, tmp_path)
    batch = dict(store.read_batch())
    scored = dict(batch["variants"]["balanced|mid"])
    base = scored["watch_list"][0]
    scored["watch_list"] = [{**base, "security_id": f"T{i:04d}", "score": 200-i} for i in range(150)]
    batch["variants"] = {"balanced|mid": scored}
    store.publish_batch(batch)
    context = {"market_regime": {"score": 12}, "as_of": "first", "source_status": "active"}
    monkeypatch.setattr(context_snapshot, "read_context_snapshot", lambda: context)
    first = asyncio.run(strength.stock("T0149", profile="balanced"))
    assert first["row"]["ticker"] == "T0149"  # Beyond the public top 120.
    context["market_regime"] = {"score": 99}
    context["as_of"] = "second"
    second = asyncio.run(strength.stock("T0149", profile="balanced"))
    assert second["context_as_of"] == "second"
    assert second["market_regime"]["score"] == 99
    assert first["market_regime"]["score"] == 12
    with pytest.raises(HTTPException) as exc:
        asyncio.run(strength.stock("ABSENT", profile="balanced"))
    assert exc.value.status_code == 404


def test_resolution_is_not_cached(monkeypatch, tmp_path):
    from types import SimpleNamespace
    _publish(monkeypatch, tmp_path)
    params = _read()["params"]
    def resolved(source, requested):
        resolution = SimpleNamespace(requested=requested, source=source, effective="eod_limited_v1",
                                     version="test-version", score_basis="test-basis", fallback_reason=None,
                                     resolved_timeframe="mid")
        return strength._read_eod_limited_snapshot(parameters=params, list_kind="observation", resolution=resolution)[0]
    first = resolved("owner_override", "eod_limited_v1")
    second = resolved("default", "follow_default")
    assert first["resolution_source"] == "owner_override"
    assert second["resolution_source"] == "default"
    assert second["requested_algorithm"] == "follow_default"
    assert _read()["resolution_source"] is None
