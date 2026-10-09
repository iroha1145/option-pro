"""Durable stock reads skip payload copies and keep hot documents cached."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from app import stock_pull_snapshot as pull_snapshot
from app.api import stocks
from app.public_stock_data import public_stock_snapshot_path
from app.stock_pull_snapshot import read_stock_pull_resource, read_stock_pull_summary
from tests.test_http_audit_2026_09_25 import (
    _count_pull_snapshot_copies,
    _daily_chart,
    _overview,
    _write_pull_document,
)


def _publish(symbol: str, resource: str, payload: dict, saved_at: float) -> None:
    """Write the way the worker process does: nothing lands in this process's caches."""
    _write_pull_document(public_stock_snapshot_path(symbol), {symbol: {resource: (payload, saved_at)}})


def _hydrate(symbol: str, resource: str, key: str):
    return asyncio.run(stocks._hydrate_stock_pull_resource(symbol, resource, key))


def _count_parses(monkeypatch) -> list[int]:
    parses: list[int] = []

    def counting_loads(*args, **kwargs):
        parses.append(1)
        return json.loads(*args, **kwargs)

    monkeypatch.setattr(
        pull_snapshot,
        "json",
        SimpleNamespace(loads=counting_loads, dumps=json.dumps, JSONDecodeError=json.JSONDecodeError),
    )
    return parses


@pytest.fixture(autouse=True)
def _empty_process_cache(monkeypatch):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})


def test_repeated_hydrates_of_one_version_copy_the_payload_once(monkeypatch):
    now = time.time()
    _publish("AAOI", "daily_chart", _daily_chart("AAOI", bars=120), now - 120)
    copies = _count_pull_snapshot_copies(monkeypatch)

    first = _hydrate("AAOI", "daily_chart", "chart:AAOI:1d:raw")
    for _ in range(10):
        assert _hydrate("AAOI", "daily_chart", "chart:AAOI:1d:raw").value is first.value
    assert len(copies) == 1

    _publish("AAOI", "daily_chart", _daily_chart("AAOI", bars=121), now - 60)
    copies.clear()
    newer = _hydrate("AAOI", "daily_chart", "chart:AAOI:1d:raw")

    assert len(copies) == 1
    assert newer.fetched_at == pytest.approx(now - 60)
    assert len(newer.value["bars"]) == 122


def test_the_same_version_moves_its_freshness_window_without_a_copy(monkeypatch):
    now = time.time()
    saved = now - 600
    _publish("AAOI", "overview", _overview("AAOI"), saved)
    cached_value = _overview("AAOI")
    stocks._endpoint_cache["stock:AAOI"] = stocks._EndpointCacheEntry(
        expires_at=saved + 60, stale_until=saved + 1800, fetched_at=saved, value=cached_value,
    )
    copies = _count_pull_snapshot_copies(monkeypatch)

    monkeypatch.setattr("app.public_stock_data._market_phase", lambda _now: "closed")
    closed = _hydrate("AAOI", "overview", "stock:AAOI")
    monkeypatch.setattr("app.public_stock_data._market_phase", lambda _now: "active")
    active = _hydrate("AAOI", "overview", "stock:AAOI")

    assert copies == []
    assert closed.expires_at == saved + 6 * 3600
    assert active.expires_at == saved + 30 * 60
    assert active.value is cached_value


def test_an_older_durable_version_never_replaces_a_newer_entry(monkeypatch):
    now = time.time()
    _publish("AAOI", "overview", _overview("AAOI"), now - 600)
    live = stocks._EndpointCacheEntry(
        expires_at=now + 60, stale_until=now + 1800, fetched_at=now - 5, value=_overview("AAOI", 101.0),
    )
    stocks._endpoint_cache["stock:AAOI"] = live
    copies = _count_pull_snapshot_copies(monkeypatch)

    assert _hydrate("AAOI", "overview", "stock:AAOI") is live
    assert copies == []


def test_a_wide_summary_scan_keeps_full_read_documents_cached(monkeypatch):
    now = time.time()
    symbols = [f"S{index:02d}" for index in range(pull_snapshot._SNAPSHOT_DOCUMENT_CACHE_MAX_PATHS + 4)]
    for symbol in ["NVDA", *symbols]:
        _publish(symbol, "daily_chart", _daily_chart(symbol), now - 60)
    nvda = public_stock_snapshot_path("NVDA")
    assert read_stock_pull_resource("NVDA", "daily_chart", path=nvda) is not None
    parses = _count_parses(monkeypatch)

    for symbol in symbols:
        assert read_stock_pull_summary(symbol, path=public_stock_snapshot_path(symbol))
    assert len(parses) == len(symbols)
    assert read_stock_pull_resource("NVDA", "daily_chart", path=nvda) is not None

    assert len(parses) == len(symbols)
    assert all(
        pull_snapshot._snapshot_cache_key(public_stock_snapshot_path(symbol))
        not in pull_snapshot._snapshot_document_cache
        for symbol in symbols
    )


def test_a_summary_read_refreshes_a_cached_document_in_place(monkeypatch):
    now = time.time()
    nvda = public_stock_snapshot_path("NVDA")
    _publish("NVDA", "daily_chart", _daily_chart("NVDA"), now - 120)
    assert read_stock_pull_resource("NVDA", "daily_chart", path=nvda) is not None
    _publish("NVDA", "daily_chart", _daily_chart("NVDA", bars=61), now - 60)
    parses = _count_parses(monkeypatch)

    assert read_stock_pull_summary("NVDA", path=nvda)["daily_chart"]["saved_at"] == pytest.approx(now - 60)
    refreshed = read_stock_pull_resource("NVDA", "daily_chart", path=nvda)

    assert len(parses) == 1
    assert refreshed["saved_at"] == pytest.approx(now - 60)
