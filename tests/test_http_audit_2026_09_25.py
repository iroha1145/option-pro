"""Regression tests for the 2026-09-25 audit of the HTTP layer (H-1 to H-14)."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import errno
import json
import os
import sqlite3
import stat
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import public_home_snapshot as phs
from app import public_stock_data as public
from app import stock_pull_snapshot as pull_snapshot
from app.access import (
    public_snapshot_unavailable,
    request_owner_access_context,
    require_owner_access,
    require_same_origin_action,
)
from app.api import earnings, macro_conditions, options, stocks, worker_actions
from app.data_paths import get_data_paths
from app.public_stock_data import public_stock_snapshot_path
from app.services import cache as cache_module
from app.services import massive
from app.services.accounts import AccountStore, verify_account_password
from app.services.watchlist_trend import daily_trend
from app.stock_data_reads import read_latest_stock_resource, read_latest_stock_summary
from tests.http_response_support import anonymous_get_request
from tests.test_public_home_snapshot import _entries as _public_home_entries
from tests.test_public_home_snapshot import _payload as _public_home_payload

NY = ZoneInfo("America/New_York")


def _overview(symbol: str, price: float = 100.0) -> dict:
    return {"ticker": symbol, "price": price, "price_provider": "fixture"}


def _signals() -> dict:
    return {
        "rsi14": {"value": 55.0},
        "return_20d": {"value": 2.0},
        "macd_hist": {"value": 0.5},
    }


def _daily_chart(symbol: str, *, bars: int = 60, extended_tail: bool = True) -> dict:
    start = datetime.now(NY).replace(hour=16, minute=0, second=0, microsecond=0)
    start -= timedelta(days=bars + 5)
    rows = []
    for index in range(bars):
        close = 100.0 + index + (index % 3)
        rows.append({
            "t": int((start + timedelta(days=index)).timestamp()),
            "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 1000,
        })
    if extended_tail:
        # Same session as the last regular bar: a trend that included it would
        # see a duplicate day and return None.
        rows.append({
            "t": rows[-1]["t"] + 3600,
            "o": 999.0, "h": 999.0, "l": 999.0, "c": 999.0, "v": 0, "ext": True,
        })
    return {
        "ticker": symbol,
        "range": "1d",
        "price_adjustment": "raw",
        "price_provider": "fixture",
        "bars": rows,
    }


def _write_pull_document(path: Path, entries: dict[str, dict[str, tuple[dict, float]]]) -> None:
    document = {
        "version": pull_snapshot.STOCK_PULL_SNAPSHOT_VERSION,
        "entries": {
            ticker: {
                resource: {"saved_at": saved_at, "payload": payload}
                for resource, (payload, saved_at) in resources.items()
            }
            for ticker, resources in entries.items()
        },
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _count_pull_snapshot_copies(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    copies: list[int] = []

    def counting_deepcopy(value, *args):
        copies.append(1)
        return copy.deepcopy(value, *args)

    monkeypatch.setattr(pull_snapshot, "copy", SimpleNamespace(deepcopy=counting_deepcopy))
    return copies


# ─── H-1: worker-state SQLite waits stay off the event loop ─────────────────


class _BlockingWorkerState:
    """Worker-state double whose chosen call parks like SQLite on a held lock."""

    def __init__(self, task_name: str, *, block_in: str) -> None:
        self.task_name = task_name
        self.block_in = block_in
        self.entered = threading.Event()
        self.release = threading.Event()
        self.released_by_test: bool | None = None

    def _park(self, method: str) -> None:
        if method == self.block_in:
            self.entered.set()
            self.released_by_test = self.release.wait(timeout=5)

    def health(self) -> dict:
        self._park("health")
        return {
            "healthy": True,
            "status": "ok",
            "tasks": [{"task_name": self.task_name, "enabled": True}],
            "actions": [],
        }

    def action_requests(self, *, action_type=None, limit=30) -> list[dict]:
        del action_type, limit
        self._park("action_requests")
        return []

    def action_request(self, request_id: str) -> dict:
        self._park("action_request")
        return {"request_id": request_id, "status": "queued"}

    def request_action(self, action_type, task_name, key, *, cooldown_seconds, details, now=None) -> dict:
        del key, cooldown_seconds, details, now
        self._park("request_action")
        return {
            "request_id": "req-1",
            "action_type": action_type,
            "task_name": task_name,
            "status": "queued",
            "reason": None,
        }


def _app_with_ping(router) -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    @app.get("/ping")
    async def ping() -> dict:
        return {"ok": True}

    return app


def _served_while_blocked(
    app: FastAPI,
    state: _BlockingWorkerState,
    method: str,
    path: str,
    **kwargs,
) -> httpx.Response:
    async def scenario() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            blocked = asyncio.create_task(client.request(method, path, **kwargs))
            assert await asyncio.to_thread(state.entered.wait, 5), "route never reached worker state"
            ping = await asyncio.wait_for(client.get("/ping"), timeout=5)
            assert ping.status_code == 200
            state.release.set()
            return await asyncio.wait_for(blocked, timeout=10)

    response = asyncio.run(scenario())
    # Only a loop that kept running could release the parked call before its
    # own timeout; a route blocking the loop would time out first.
    assert state.released_by_test is True
    return response


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "block_in", "expected_status"),
    [
        ("POST", "/api/worker/actions/retention", {"json": {}}, "request_action", 202),
        ("GET", "/api/worker/status", {}, "health", 200),
        ("GET", "/api/worker/actions", {}, "action_requests", 200),
        ("GET", "/api/worker/actions/req-1", {}, "action_request", 200),
    ],
)
def test_worker_action_routes_wait_on_worker_state_off_the_event_loop(
    monkeypatch, method, path, kwargs, block_in, expected_status,
):
    state = _BlockingWorkerState("retention", block_in=block_in)
    monkeypatch.setattr(worker_actions, "_repository", lambda: state)

    response = _served_while_blocked(
        _app_with_ping(worker_actions.router), state, method, path, **kwargs,
    )

    assert response.status_code == expected_status


def test_earnings_refresh_enqueues_off_the_event_loop(monkeypatch):
    state = _BlockingWorkerState("public_home", block_in="request_action")
    monkeypatch.setattr(worker_actions, "_repository", lambda: state)
    monkeypatch.setattr(
        earnings,
        "get_personal_config",
        lambda: SimpleNamespace(access=SimpleNamespace(mode="password")),
    )
    app = _app_with_ping(earnings.router)
    app.dependency_overrides[require_same_origin_action] = lambda: None

    response = _served_while_blocked(app, state, "POST", "/api/earnings/upcoming/refresh")

    assert response.status_code == 200
    assert response.json()["refresh_status"] == "queued"


def test_macro_refresh_enqueues_off_the_event_loop(monkeypatch):
    state = _BlockingWorkerState(macro_conditions.MACRO_TASK_NAME, block_in="request_action")
    monkeypatch.setattr(macro_conditions, "WorkerStateRepository", lambda _path: state)
    monkeypatch.setattr(
        macro_conditions,
        "_config",
        lambda: SimpleNamespace(enabled=True, manual_refresh_cooldown_seconds=300),
    )
    monkeypatch.setattr(macro_conditions, "_key_configured", lambda: True)
    app = _app_with_ping(macro_conditions.router)
    app.dependency_overrides[require_owner_access] = lambda: None
    app.dependency_overrides[require_same_origin_action] = lambda: None

    response = _served_while_blocked(app, state, "POST", "/api/macro/conditions/refresh", json={})

    assert response.status_code == 202
    assert response.json()["status"] == "queued"


# ─── H-2: the snapshot writer only closes descriptors it still owns ──────────


def _indices_entry(saved_at: float) -> dict:
    return phs.create_public_home_entry(
        "indices",
        _public_home_payload("indices", saved_at),
        saved_at=saved_at,
        parameters=phs.public_home_resource_parameters("indices", now=saved_at),
    )


def test_failed_publish_leaves_a_reused_descriptor_number_open(tmp_path, monkeypatch):
    now = time.time()
    target = tmp_path / "public-home.json"
    victim: dict[str, int] = {}

    def replace_then_fail(_source, _target):
        # Another thread's open() gets the lowest free number, which is the one
        # the writer's temporary file released when fdopen closed it.
        victim["fd"] = os.open(os.devnull, os.O_RDONLY)
        raise OSError(errno.EIO, "simulated replace failure")

    monkeypatch.setattr(phs.os, "replace", replace_then_fail)
    with pytest.raises(OSError, match="simulated replace failure"):
        phs.write_public_home_snapshot(target, {"indices": _indices_entry(now)}, now=now)

    try:
        os.fstat(victim["fd"])
    except OSError as exc:
        pytest.fail(f"the writer closed a descriptor it no longer owned: {exc}")
    finally:
        with contextlib.suppress(OSError):
            os.close(victim["fd"])
    assert not target.exists()
    assert not list(tmp_path.glob(".*.tmp"))


@pytest.mark.parametrize("failing_step", ["chmod", "dir_fsync"])
def test_hardening_failure_after_replace_does_not_fail_the_publish(
    tmp_path, monkeypatch, failing_step,
):
    now = time.time()
    target = tmp_path / "public-home.json"
    recorded: list[tuple[str, str]] = []
    monkeypatch.setattr(
        phs,
        "record_fallback_failure",
        lambda stage, error, **_kwargs: recorded.append((stage, type(error).__name__)),
    )
    if failing_step == "chmod":
        def refuse_chmod(*_args, **_kwargs):
            raise PermissionError(errno.EPERM, "chmod refused")

        monkeypatch.setattr(phs.os, "chmod", refuse_chmod)
        expected = [("public_home_snapshot_chmod", "PermissionError")]
    else:
        real_fsync = os.fsync

        def fail_directory_fsync(descriptor):
            if stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(errno.EIO, "directory fsync failed")
            real_fsync(descriptor)

        monkeypatch.setattr(phs.os, "fsync", fail_directory_fsync)
        expected = [("public_home_snapshot_dir_fsync", "OSError")]

    phs.write_public_home_snapshot(target, {"indices": _indices_entry(now)}, now=now)

    assert "indices" in phs.read_public_home_entries(target, now=now)
    assert recorded == expected


# ─── H-3: status and trend reads use payload-free summaries ─────────────────


def test_status_for_200_symbols_copies_no_payload_and_reparses_nothing(monkeypatch):
    now = time.time()
    symbols = [f"T{index:03d}" for index in range(200)]
    for symbol in symbols:
        _write_pull_document(public_stock_snapshot_path(symbol), {symbol: {
            "overview": (_overview(symbol), now - 60),
            "daily_chart": (_daily_chart(symbol), now - 60),
            "signals": (_signals(), now - 60),
        }})
    # A newer manual pull must still win for its resource.
    _write_pull_document(
        pull_snapshot.stock_pull_snapshot_path(),
        {symbols[0]: {"overview": (_overview(symbols[0], 101.0), now - 5)}},
    )
    copies = _count_pull_snapshot_copies(monkeypatch)
    parses: list[int] = []

    def counting_loads(*args, **kwargs):
        parses.append(1)
        return json.loads(*args, **kwargs)

    monkeypatch.setattr(
        pull_snapshot,
        "json",
        SimpleNamespace(loads=counting_loads, dumps=json.dumps, JSONDecodeError=json.JSONDecodeError),
    )

    first = asyncio.run(stocks.stock_data_status(tickers=",".join(symbols)))["items"]
    parsed_once = len(parses)
    second = asyncio.run(stocks.stock_data_status(tickers=",".join(symbols)))["items"]

    assert copies == []
    assert parsed_once == len(symbols) + 1
    assert len(parses) == parsed_once
    assert second == first
    assert [item["status"] for item in first] == ["ready"] * len(symbols)
    newest = datetime.fromisoformat(first[0]["resources"]["overview"]["as_of"]).timestamp()
    assert newest == pytest.approx(now - 5)
    assert all(resource["fresh"] for resource in first[1]["resources"].values())


def test_latest_summary_matches_the_full_resource_read(monkeypatch):
    now = time.time()
    _write_pull_document(public_stock_snapshot_path("AAOI"), {"AAOI": {
        "overview": (_overview("AAOI"), now - 600),
        "daily_chart": (_daily_chart("AAOI"), now - 30),
    }})
    _write_pull_document(pull_snapshot.stock_pull_snapshot_path(), {"AAOI": {
        "overview": (_overview("AAOI", 90.0), now - 20),
        "daily_chart": (_daily_chart("AAOI"), now - 400),
        "signals": (_signals(), now - 10),
    }})

    summary = read_latest_stock_summary("AAOI", now=now)

    for resource in ("overview", "daily_chart", "signals"):
        full = read_latest_stock_resource("AAOI", resource, now=now)
        assert summary[resource]["saved_at"] == full["saved_at"]
        assert summary[resource]["fresh"] == full["fresh"]
        assert summary[resource]["source"] == full["source"]
    assert "payload" not in summary["daily_chart"]


def test_watchlist_trends_read_summaries_and_match_the_full_chart(monkeypatch):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    now = time.time()
    symbols = [f"W{index:02d}" for index in range(40)]
    charts = {symbol: _daily_chart(symbol, bars=120) for symbol in symbols}
    for symbol in symbols:
        _write_pull_document(
            public_stock_snapshot_path(symbol),
            {symbol: {"daily_chart": (charts[symbol], now - 60)}},
        )
    copies = _count_pull_snapshot_copies(monkeypatch)
    payload = {"groups": [{"id": "g", "stocks": [{"ticker": symbol} for symbol in symbols]}]}

    result = asyncio.run(stocks._with_watchlist_daily_trends(payload))

    assert copies == []
    for row in result["groups"][0]["stocks"]:
        expected = daily_trend(charts[row["ticker"]], market_timezone=NY)
        assert expected is not None
        assert row["daily_trend"] == expected


def test_public_home_read_checks_only_the_requested_entry_clock(tmp_path, monkeypatch):
    now = time.time()
    path = tmp_path / "public-home.json"
    phs.write_public_home_snapshot(path, _public_home_entries(now), now=now)
    checked: list[str] = []
    real_check = phs._payload_timestamps_fit_entry

    def counting_check(resource, payload, *, not_after):
        checked.append(resource)
        return real_check(resource, payload, not_after=not_after)

    monkeypatch.setattr(phs, "_payload_timestamps_fit_entry", counting_check)

    indices = phs.read_public_home_resource(
        "indices",
        parameters=phs.public_home_resource_parameters("indices", now=now),
        path=path,
        now=now,
    )
    assert indices is not None
    assert checked == ["indices"]

    checked.clear()
    other_ticker = phs.read_public_home_resource(
        "focus_chart",
        parameters={"ticker": "AMD", "range": "1d", "adjustment": "raw"},
        path=path,
        now=now,
    )
    assert other_ticker is None
    assert checked == []


# ─── H-4: malformed symbols are a client error, not a 500 ───────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/api/stocks/FOO$/chart?range=5m",
        "/api/stocks/" + "A" * 40 + "/chart?range=15m",
        "/api/stocks/FOO$/chart?range=1d",
    ],
)
def test_chart_route_rejects_symbols_outside_the_snapshot_pattern(path):
    app = FastAPI()
    app.include_router(stocks.router)
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get(path)

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid ticker symbol"


# ─── H-5: worker pulls do not fill the HTTP process cache ───────────────────


def test_worker_pull_persists_without_filling_the_process_cache(monkeypatch):
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
    snapshot_path = public_stock_snapshot_path("ABC")
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)

    background = asyncio.run(stocks._pull_stock_data_once(
        "ABC", snapshot_path=snapshot_path, include_options=False, publish_to_cache=False,
    ))

    assert background["resources"]["overview"]["persisted"] is True
    assert background["resources"]["daily_chart"]["persisted"] is True
    assert stocks._endpoint_cache == {}
    assert pull_snapshot.read_stock_pull_resource("ABC", "daily_chart", path=snapshot_path)

    asyncio.run(stocks._pull_stock_data_once("ABC", include_options=False))
    assert {"stock:ABC", "chart:ABC:1d:raw"} <= set(stocks._endpoint_cache)


def test_worker_default_puller_opts_out_of_the_process_cache(monkeypatch, tmp_path):
    now = time.time()
    calls: list[tuple[str, dict]] = []

    async def fake_pull(symbol, **kwargs):
        calls.append((symbol, kwargs))
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

    assert {symbol for symbol, _kwargs in calls} == {"AAA", "NVDA"}
    assert all(kwargs["publish_to_cache"] is False for _symbol, kwargs in calls)


# ─── H-7 companions: narrowed verification errors and closed connections ─────


@pytest.mark.parametrize(
    "encoded",
    ["not-a-hash", "pbkdf2_sha256$many$abc$def", "pbkdf2_sha256$1000$!!!$!!!", "md5$1$a$b"],
)
def test_malformed_account_hashes_still_fail_closed(encoded):
    assert verify_account_password("fixture-password", encoded) is False


def test_account_store_closes_each_connection(tmp_path):
    store = AccountStore(tmp_path / "accounts.db")
    store.initialize()

    with store._connect() as connection:
        connection.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


# ─── H-8: search index reuse and bounded cache sizing ───────────────────────


def _directory(*tickers: str) -> dict:
    items = [
        {
            "ticker": ticker,
            "name": f"{ticker} Holdings Inc.",
            "market": "stocks",
            "type": "CS",
            "primary_exchange": "XNAS",
            "locale": "us",
            "currency_symbol": "USD",
            "active": True,
        }
        for ticker in tickers
    ]
    return {"items": items, "count": len(items), "provider": "Massive"}


def test_search_index_is_built_once_per_directory_generation_off_the_loop(monkeypatch):
    generations = [_directory("AAOI", "NBIS")]

    async def directory(*, allow_refresh):
        del allow_refresh
        return generations[-1]

    builds: list[bool] = []
    real_build = stocks._build_search_rows

    def counting_build(items):
        builds.append(threading.current_thread() is threading.main_thread())
        return real_build(items)

    monkeypatch.setattr(stocks, "_stock_directory", directory)
    monkeypatch.setattr(stocks, "_build_search_rows", counting_build)
    monkeypatch.setattr(stocks, "_search_index", None)

    async def scenario() -> list[list[dict]]:
        with request_owner_access_context(False):
            results = [await stocks.search_stocks("AAOI"), await stocks.search_stocks("NBIS")]
            generations.append(_directory("AAOI", "NBIS", "ZZZQ"))
            results.append(await stocks.search_stocks("ZZZQ"))
            return results

    aaoi, nbis, zzzq = asyncio.run(scenario())

    assert aaoi[0]["ticker"] == "AAOI"
    assert nbis[0]["ticker"] == "NBIS"
    assert zzzq[0]["ticker"] == "ZZZQ"
    assert builds == [False, False]


def test_large_cache_values_are_sized_from_a_sample(monkeypatch):
    row = {f"field_{index}": ("值" * 8 if index % 3 == 0 else index * 1.25) for index in range(40)}
    payload = {
        "earnings": [dict(row, ticker=f"T{index:05d}") for index in range(4000)],
        "as_of": "2026-09-25T00:00:00+00:00",
    }
    exact = len(json.dumps(payload, default=str, ensure_ascii=False))
    serialized: list[int] = []

    def recording_dumps(value, *args, **kwargs):
        text = json.dumps(value, *args, **kwargs)
        serialized.append(len(text))
        return text

    monkeypatch.setattr(cache_module, "json", SimpleNamespace(dumps=recording_dumps))

    estimate = cache_module.estimate_size(payload)

    assert abs(estimate - exact) <= exact * 0.05
    assert sum(serialized) < exact * 0.05


def test_upcoming_earnings_snapshot_reads_the_requested_date_and_sizes_off_loop(monkeypatch):
    now = time.time()
    today = datetime.now(NY).date()
    other_day = today + timedelta(days=1)
    payload = _public_home_payload("earnings", now)
    entry = phs.create_public_home_entry(
        "earnings", payload, saved_at=now, parameters={"market_date": other_day.isoformat()},
    )
    phs.write_public_home_snapshot(get_data_paths().public_home_snapshot, {"earnings": entry}, now=now)
    monkeypatch.setattr(
        earnings,
        "get_personal_config",
        lambda: SimpleNamespace(public_home=SimpleNamespace(earnings_seconds=1800.0)),
    )
    sized: list[tuple[bool, int]] = []
    real_estimate = earnings.estimate_size

    def recording_estimate(value):
        size = real_estimate(value)
        sized.append((threading.current_thread() is threading.main_thread(), size))
        return size

    sized_by_cache: list[int] = []

    def cache_side_estimate(value):
        sized_by_cache.append(1)
        return real_estimate(value)

    monkeypatch.setattr(earnings, "estimate_size", recording_estimate)
    monkeypatch.setattr(cache_module, "estimate_size", cache_side_estimate)
    key = f"earnings:upcoming:{other_day.isoformat()}"
    try:
        result = asyncio.run(earnings._read_current_upcoming_earnings_snapshot(other_day))
        stored_size = earnings.cache._sizes.get(key)
        today_result = asyncio.run(earnings._read_current_upcoming_earnings_snapshot(today))
    finally:
        earnings.cache._drop(key)
        earnings.cache._drop(f"earnings:upcoming:{today.isoformat()}")

    assert result is not None and result["as_of"] == payload["as_of"]
    assert today_result is None
    assert [on_main for on_main, _size in sized] == [False]
    # set() reused the hint instead of sizing the payload on the event loop.
    assert sized_by_cache == []
    assert stored_size == sized[0][1]


# ─── H-9: thread-side reads never remove process-cache entries ──────────────


def test_thread_side_watchlist_reads_leave_expiry_to_the_event_loop(monkeypatch):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    now = time.time()
    expired = stocks._EndpointCacheEntry(
        expires_at=now - 120, stale_until=now - 60, fetched_at=now - 600, value={"groups": []},
    )
    selected_key = stocks._watchlist_cache_key(["AAPL"])
    for key in ("watchlist", selected_key, "chart:AAPL:1d:raw"):
        stocks._endpoint_cache[key] = expired

    asyncio.run(asyncio.to_thread(stocks._cached_selected_watchlist, ["AAPL"]))
    asyncio.run(stocks._with_watchlist_daily_trends({"groups": [{"stocks": [{"ticker": "AAPL"}]}]}))

    for key in ("watchlist", selected_key, "chart:AAPL:1d:raw"):
        assert stocks._endpoint_cache[key] is expired
    assert stocks._usable_hit("watchlist", now) is None
    assert "watchlist" not in stocks._endpoint_cache


# ─── H-11: an entry published just after the reader sampled `now` ──────────


def test_entry_published_after_the_reader_sampled_now_is_served(tmp_path):
    now = time.time()
    published_at = now + 0.5
    path = tmp_path / "public-home.json"
    phs.write_public_home_snapshot(path, {"indices": _indices_entry(published_at)}, now=published_at)
    parameters = phs.public_home_resource_parameters("indices", now=now)

    assert phs.read_public_home_resource("indices", parameters=parameters, path=path, now=now) is not None
    owner = phs.read_owner_public_home_entry(
        "indices", parameters=parameters, fresh_for_seconds=60, path=path, now=now,
    )
    assert owner is not None and owner["fresh"] is True
    too_early = now - phs.PUBLIC_HOME_SAVED_AT_GRACE_SECONDS - 1
    assert phs.read_public_home_resource("indices", parameters=parameters, path=path, now=too_early) is None


# ─── H-12: visitor technical fallback keeps relative strength ───────────────


def test_visitor_technical_fallback_passes_spy_closes(monkeypatch):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    now = time.time()
    stocks._endpoint_cache["chart:XYZ:1d:raw"] = stocks._EndpointCacheEntry(
        expires_at=now + 600, stale_until=now + 3600, fetched_at=now,
        value=_daily_chart("XYZ", bars=80, extended_tail=False),
    )
    # SPY exists only in the worker's durable public bundle.
    _write_pull_document(
        public_stock_snapshot_path("SPY"),
        {"SPY": {"daily_chart": (_daily_chart("SPY", bars=80, extended_tail=False), now - 60)}},
    )
    captured: dict = {}

    def fake_structure(bars, *, ticker=None, spy_closes=None, **_kwargs):
        captured.update(ticker=ticker, spy_closes=spy_closes, bars=len(bars))
        return {"chart_analysis": None}

    monkeypatch.setattr("app.services.technical.structure.compute_technical_structure", fake_structure)

    with request_owner_access_context(False):
        payload = asyncio.run(stocks.stock_technical("XYZ"))

    assert payload["ticker"] == "XYZ"
    assert captured["ticker"] == "XYZ"
    assert captured["bars"] == 80
    assert captured["spy_closes"] is not None and len(captured["spy_closes"]) == 80


# ─── Diagnostics for converted or swallowed failures ────────────────────────


def _record_into(module, monkeypatch) -> list[str]:
    stages: list[str] = []
    monkeypatch.setattr(
        module, "record_fallback_failure", lambda stage, error, **_kwargs: stages.append(stage),
    )
    return stages


async def _provider_bug(*_args, **_kwargs):
    raise RuntimeError("code defect, not an outage")


@pytest.mark.parametrize(
    ("call", "stage"),
    [
        (lambda: stocks.watchlist(None), "stocks_watchlist_unavailable"),
        (lambda: stocks.stock_overview("AAPL"), "stocks_overview_unavailable"),
        (lambda: stocks.stock_chart("AAPL", range="5m", adjustment="raw"), "stocks_chart_unavailable"),
    ],
)
def test_stock_routes_record_unexpected_errors_before_answering_503(monkeypatch, call, stage):
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    monkeypatch.setattr(stocks, "_stale_while_revalidate_endpoint", _provider_bug)
    monkeypatch.setattr(stocks, "_load_watchlist_snapshot_once", lambda _now: None)
    stages = _record_into(stocks, monkeypatch)

    with request_owner_access_context(False), pytest.raises(HTTPException) as caught:
        asyncio.run(call())

    assert caught.value.status_code == 503
    assert stages == [stage]


@pytest.mark.parametrize(
    ("call", "stage"),
    [
        (lambda: options.expirations("AAPL", anonymous_get_request()), "options_expirations_unavailable"),
        (
            lambda: options.option_chain(
                "AAPL",
                expiration=(date.today() + timedelta(days=7)).isoformat(),
                request=anonymous_get_request(),
            ),
            "options_chain_unavailable",
        ),
    ],
)
def test_option_routes_record_unexpected_errors_before_answering_503(monkeypatch, call, stage):
    monkeypatch.setattr(options, "_cached_yahoo_option_resource", _provider_bug)
    monkeypatch.setattr(options, "cache", cache_module.TTLCache())
    stages = _record_into(options, monkeypatch)

    with request_owner_access_context(False), pytest.raises(HTTPException) as caught:
        asyncio.run(call())

    assert caught.value.status_code == 503
    assert stages == [stage]


def test_stock_directory_refresh_error_is_recorded(monkeypatch, tmp_path):
    monkeypatch.setattr(stocks, "_STOCK_DIRECTORY_PATH", tmp_path / "directory.json")
    monkeypatch.setattr(stocks, "_stock_directory_snapshot_observed", None)
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    monkeypatch.setattr(stocks, "_stale_while_revalidate_endpoint", _provider_bug)
    stages = _record_into(stocks, monkeypatch)

    assert asyncio.run(stocks._stock_directory(allow_refresh=True)) is None
    assert stages == ["stock_directory_refresh"]


def test_spy_closes_record_defects_but_not_a_missing_visitor_snapshot(monkeypatch):
    stages = _record_into(stocks, monkeypatch)

    async def unavailable(*_args, **_kwargs):
        raise public_snapshot_unavailable("chart:SPY:1d:raw")

    monkeypatch.setattr(stocks, "_stale_while_revalidate_endpoint", unavailable)
    assert asyncio.run(stocks._spy_closes_by_date(False)) is None
    assert stages == []

    monkeypatch.setattr(stocks, "_stale_while_revalidate_endpoint", _provider_bug)
    assert asyncio.run(stocks._spy_closes_by_date(True)) is None
    assert stages == ["stocks_spy_closes"]


def test_massive_chart_failure_is_recorded_before_the_yahoo_fallback(monkeypatch):
    stages = _record_into(stocks, monkeypatch)
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "to_symbol", lambda symbol: symbol)
    monkeypatch.setattr(massive, "to_yahoo_symbol", lambda symbol: symbol)

    def massive_defect(*_args, **_kwargs):
        raise RuntimeError("adapter defect")

    monkeypatch.setattr(stocks, "_massive_chart_history", massive_defect)
    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda _symbol: SimpleNamespace(history=lambda **_kwargs: pd.DataFrame()),
    )

    payload = asyncio.run(stocks._stock_chart_impl("AAPL", "1d"))

    assert payload["bars"] == []
    assert stages == ["stocks_chart_massive"]


def test_owner_yahoo_search_fallback_error_is_recorded(monkeypatch):
    stages = _record_into(stocks, monkeypatch)

    async def no_directory(*, allow_refresh):
        del allow_refresh
        return None

    def yahoo_defect(_symbol):
        raise RuntimeError("yahoo defect")

    monkeypatch.setattr(stocks, "_stock_directory", no_directory)
    monkeypatch.setattr(stocks.yf, "Ticker", yahoo_defect)

    with request_owner_access_context(True), pytest.raises(HTTPException) as caught:
        asyncio.run(stocks.search_stocks("QQQZ"))

    assert caught.value.status_code == 503
    assert stages == ["stocks_search_yahoo"]


def test_earnings_single_ticker_failures_are_recorded(monkeypatch):
    stages = _record_into(earnings, monkeypatch)

    async def no_rows(*_args, **_kwargs):
        return {"rows": []}

    def yahoo_defect(_ticker):
        raise RuntimeError("yahoo defect")

    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", no_rows)
    monkeypatch.setattr(earnings.earnings_enrichment, "fetch_fmp_calendar", no_rows)
    monkeypatch.setattr(earnings.yf, "Ticker", yahoo_defect)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(earnings._build_upcoming_earnings(datetime.now(NY).date()))

    assert caught.value.status_code == 503
    assert set(stages) == {"earnings_yahoo_ticker"}
    assert len(stages) == len(earnings.EARNINGS_TICKERS)
