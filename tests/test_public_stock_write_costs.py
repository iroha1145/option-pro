"""Regenerated public stock bundles and scheduling hints are written without fsync."""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from app import public_stock_data as public
from app import stock_pull_snapshot as pull_snapshot
from app.api import stocks
from app.public_stock_data import public_stock_snapshot_path
from tests.test_http_audit_2026_09_25 import _daily_chart, _overview


@pytest.fixture
def fsyncs(monkeypatch) -> list[int]:
    calls: list[int] = []
    real = os.fsync

    def counting(descriptor: int) -> None:
        calls.append(descriptor)
        real(descriptor)

    monkeypatch.setattr(pull_snapshot.os, "fsync", counting)
    monkeypatch.setattr(public.os, "fsync", counting)
    return calls


def _pull(monkeypatch, **options):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})

    async def overview(symbol):
        return _overview(symbol)

    async def chart(symbol, range_key, adjustment="raw"):
        del range_key, adjustment
        return _daily_chart(symbol, extended_tail=False)

    async def adjusted_history_unavailable(*_args, **_kwargs):
        raise RuntimeError("adjusted history unavailable")

    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)
    monkeypatch.setattr(stocks, "_load_stock_chart", adjusted_history_unavailable)
    return asyncio.run(stocks._pull_stock_data_once("ABC", include_options=False, **options))


def test_worker_bundles_are_written_without_fsync(monkeypatch, fsyncs):
    snapshot_path = public_stock_snapshot_path("ABC")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    result = _pull(
        monkeypatch, snapshot_path=snapshot_path, publish_to_cache=False, durable_snapshot=False,
    )

    assert result["resources"]["daily_chart"]["persisted"] is True
    assert pull_snapshot.read_stock_pull_resource("ABC", "daily_chart", path=snapshot_path)
    assert fsyncs == []


def test_owner_manual_pulls_stay_durable(monkeypatch, fsyncs):
    result = _pull(monkeypatch)

    assert result["resources"]["daily_chart"]["persisted"] is True
    # The file and its directory.
    assert len(fsyncs) == 2


def test_scheduling_hints_are_replaced_atomically_without_fsync(tmp_path, fsyncs):
    path = tmp_path / "status" / "AAOI.json"
    public._write_metadata(path, {"ticker": "AAOI", "status": "running"})
    first = os.stat(path).st_ino
    public._write_metadata(path, {"ticker": "AAOI", "status": "ready"})

    assert public._read_metadata(path) == {"ticker": "AAOI", "status": "ready"}
    assert os.stat(path).st_ino != first
    assert [name for name in os.listdir(path.parent) if name.startswith(".pending-")] == []
    assert fsyncs == []


def test_the_worker_puller_opts_out_of_fsync(monkeypatch, tmp_path):
    now = time.time()
    calls: list[dict] = []

    async def fake_pull(symbol, **kwargs):
        calls.append(kwargs)
        return {"status": "partial"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", fake_pull)
    queue = public.PublicStockDataRefresh(
        root=tmp_path,
        clock=lambda: now,
        current_reader=lambda: [],
        default_reader=lambda: ["AAA"],
        phase_reader=lambda _now: "regular",
        start_interval_seconds=0,
    )

    async def scenario() -> None:
        await queue.poll({})
        await asyncio.gather(*queue._consumers)
        await queue.aclose()

    asyncio.run(scenario())

    assert calls and all(kwargs["durable_snapshot"] is False for kwargs in calls)
