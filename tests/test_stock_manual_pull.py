from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import threading
import time

import pandas as pd
import pytest
from fastapi import HTTPException

from app.access import request_owner_access_context
from app.api import signals as signals_api
from app.api import stocks
from app.services import signals as signal_service
from app.stock_pull_snapshot import (
    read_stock_pull_resource,
    write_stock_pull_resources,
)


def _request(address: str = "127.0.0.1"):
    return SimpleNamespace(
        scope={
            "type": "http",
            "client": (address, 50_000),
            "headers": [],
        }
    )


@pytest.fixture(autouse=True)
def _clear_stock_endpoint_state():
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()
    stocks._endpoint_refresh_retry_after.clear()
    stocks._stock_pull_tasks.clear()
    stocks._public_stock_pull_ticker_deadlines.clear()
    stocks._public_stock_pull_recent.clear()
    signal_service._cache.clear()
    yield
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()
    stocks._endpoint_refresh_retry_after.clear()
    stocks._stock_pull_tasks.clear()
    stocks._public_stock_pull_ticker_deadlines.clear()
    stocks._public_stock_pull_recent.clear()
    signal_service._cache.clear()


def test_manual_pull_bypasses_fresh_get_cache_and_publishes_all_resources(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    now = time.time()
    stocks._endpoint_cache["stock:AAOI"] = stocks._EndpointCacheEntry(
        expires_at=now + 600,
        stale_until=now + 1_200,
        fetched_at=now - 60,
        value={"ticker": "AAOI", "price": 1.0, "price_provider": "old"},
    )
    stocks._endpoint_cache["chart:AAOI:1d:raw"] = stocks._EndpointCacheEntry(
        expires_at=now + 600,
        stale_until=now + 1_200,
        fetched_at=now - 60,
        value={"ticker": "AAOI", "bars": [{"t": 1, "c": 1.0}]},
    )
    calls = {"overview": 0, "raw_chart": 0, "adjusted_chart": 0}
    signal_close: list[float] = []

    async def overview(symbol: str) -> dict:
        calls["overview"] += 1
        assert symbol == "AAOI"
        return {
            "ticker": symbol,
            "price": 112.02,
            "price_provider": "Massive",
            "as_of": "2026-07-24T01:00:00+00:00",
        }

    async def chart(symbol: str, range_key: str, adjustment: str) -> dict:
        assert (symbol, range_key) == ("AAOI", "1d")
        calls[f"{adjustment}_chart"] += 1
        close = 112.02 if adjustment == "raw" else 56.01
        return {
            "ticker": symbol,
            "range": "1d",
            "price_adjustment": adjustment,
            "price_provider": "Massive",
            "as_of": "2026-07-24T01:00:01+00:00",
            "last_bar_at": "2026-07-23T20:00:00+00:00",
            "bars": [
                {
                    "t": 1,
                    "o": close,
                    "h": close,
                    "l": close,
                    "c": close,
                    "v": 1,
                }
            ],
        }

    def compute_from_history(_symbol, history, **_kwargs):
        signal_close.append(float(history["Close"].iloc[-1]))
        return {
            "rsi14": {"value": 42.63},
            "return_20d": {"value": -20.0},
            "macd_hist": {"value": -0.2},
            "_price_provider": {"value": "Massive"},
        }

    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals_from_history",
        compute_from_history,
    )
    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.pull_stock_data("aaoi", request=_request())

    payload = asyncio.run(scenario())

    assert calls == {"overview": 1, "raw_chart": 1, "adjusted_chart": 1}
    assert signal_close == [56.01]
    assert payload["ticker"] == "AAOI"
    assert payload["status"] == "completed"
    assert payload["resources"]["overview"] == {
        "status": "available",
        "provider": "Massive",
        "as_of": "2026-07-24T01:00:00+00:00",
        "persisted": True,
    }
    assert payload["resources"]["daily_chart"]["status"] == "available"
    assert payload["resources"]["daily_chart"]["provider"] == "Massive"
    assert payload["resources"]["daily_chart"]["bar_count"] == 1
    assert payload["resources"]["signals"]["status"] == "available"
    assert all(resource["persisted"] for resource in payload["resources"].values())
    assert stocks._endpoint_cache["stock:AAOI"].value["price"] == 112.02
    assert (
        stocks._endpoint_cache["chart:AAOI:1d:raw"].value["bars"][0]["c"]
        == 112.02
    )

    # Simulate a backend restart: process caches disappear, while the durable
    # snapshot remains readable by the normal public GET routes.
    stocks._endpoint_cache.clear()
    signal_service._cache.clear()

    async def restarted_reads():
        with request_owner_access_context(False):
            return (
                await stocks.stock_overview("AAOI"),
                await stocks.stock_chart("AAOI", "1d", "raw"),
                await signals_api.stock_signals("AAOI"),
            )

    overview_after, chart_after, signals_after = asyncio.run(restarted_reads())
    assert overview_after["price"] == 112.02
    assert chart_after["bars"][0]["c"] == 112.02
    assert signals_after["signals"]["rsi14"]["value"] == 42.63
    assert signals_after["snapshot_source"] == "manual_pull"
    assert (
        read_stock_pull_resource("AAOI", "daily_chart")["payload"]["bars"][0]["c"]
        == 112.02
    )


def test_manual_pull_falls_back_when_independent_adjusted_history_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls: list[str] = []

    async def overview(symbol: str) -> dict:
        return {
            "ticker": symbol,
            "price": 112.02,
            "price_provider": "Massive",
        }

    async def chart(symbol: str, _range: str, adjustment: str) -> dict:
        calls.append(adjustment)
        if adjustment == "adjusted":
            raise RuntimeError("adjusted provider failed")
        return {
            "ticker": symbol,
            "range": "1d",
            "price_adjustment": "raw",
            "price_provider": "Massive",
            "bars": [
                {
                    "t": 1,
                    "o": 110.0,
                    "h": 113.0,
                    "l": 109.0,
                    "c": 112.02,
                    "v": 1,
                }
            ],
        }

    fallback_calls: list[str] = []

    def fallback(symbol: str) -> dict:
        fallback_calls.append(symbol)
        return {
            "rsi14": {"value": 42.63},
            "return_20d": {"value": -20.0},
            "macd_hist": {"value": -0.2},
            "_price_provider": {"value": "Massive"},
        }

    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)
    monkeypatch.setattr(signal_service, "compute_stock_signals", fallback)
    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals_from_history",
        lambda *_args, **_kwargs: pytest.fail(
            "raw chart history must not be used for technical signals"
        ),
    )

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.pull_stock_data("AAOI", request=_request())

    payload = asyncio.run(scenario())

    assert calls == ["raw", "adjusted"]
    assert fallback_calls == ["AAOI"]
    assert payload["resources"]["daily_chart"]["status"] == "available"
    assert payload["resources"]["signals"]["status"] == "available"


def test_manual_pull_returns_partial_when_only_daily_chart_is_available(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    async def overview(_symbol: str) -> dict:
        raise RuntimeError("overview provider failed")

    async def chart(symbol: str, _range: str, _adjustment: str) -> dict:
        return {
            "ticker": symbol,
            "range": "1d",
            "price_adjustment": _adjustment,
            "price_provider": "Massive",
            "bars": [{"t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
        }

    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals_from_history",
        lambda *_args, **_kwargs: {
            "rsi14": {"value": 50.0},
            "return_20d": {"value": 0.0},
            "macd_hist": {"value": 0.0},
            "_price_provider": {"value": "Massive"},
        },
    )
    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.pull_stock_data("NBIS", request=_request())

    payload = asyncio.run(scenario())

    assert payload["status"] == "partial"
    assert payload["resources"]["overview"] == {
        "status": "failed",
        "error_code": "overview_provider_unavailable",
        "persisted": False,
    }
    assert payload["resources"]["daily_chart"]["status"] == "available"


def test_empty_daily_refresh_preserves_stale_persisted_manual_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    saved_at = time.time() - 10 * 60
    persisted_chart = {
        "ticker": "AAOI",
        "range": "1d",
        "price_adjustment": "raw",
        "price_provider": "Massive",
        "bars": [
            {
                "t": 1,
                "o": 99.0,
                "h": 100.0,
                "l": 98.0,
                "c": 99.5,
                "v": 100,
            }
        ],
    }
    assert write_stock_pull_resources(
        "AAOI",
        {"daily_chart": (persisted_chart, saved_at)},
    ) == {"daily_chart"}

    refresh_started = asyncio.Event()
    release_refresh = asyncio.Event()

    async def empty_chart(
        symbol: str,
        range_key: str,
        adjustment: str,
    ) -> dict:
        assert (symbol, range_key, adjustment) == ("AAOI", "1d", "raw")
        refresh_started.set()
        await release_refresh.wait()
        return {
            "ticker": symbol,
            "range": range_key,
            "price_adjustment": adjustment,
            "price_provider": "Massive",
            "source_status": "empty",
            "bars": [],
            "ema20": [],
            "sma50": [],
        }

    monkeypatch.setattr(stocks, "_stock_chart_impl", empty_chart)

    async def scenario() -> tuple[dict, dict]:
        with request_owner_access_context(True):
            first = await stocks.stock_chart("AAOI", "1d", "raw")
            await refresh_started.wait()
            refresh = stocks._endpoint_refresh_tasks["chart:AAOI:1d:raw"]
            release_refresh.set()
            with pytest.raises(RuntimeError, match="returned no bars"):
                await refresh
            await asyncio.sleep(0)
            second = await stocks.stock_chart("AAOI", "1d", "raw")
            return first, second

    first, second = asyncio.run(scenario())

    assert first["bars"][0]["c"] == 99.5
    assert first["_stale"] is True
    assert second["bars"][0]["c"] == 99.5
    assert second["_stale"] is True
    assert (
        stocks._endpoint_cache["chart:AAOI:1d:raw"].value["bars"][0]["c"]
        == 99.5
    )


def test_manual_pull_invalid_empty_payloads_preserve_last_usable_process_cache(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    now = time.time()
    old_overview = stocks._EndpointCacheEntry(
        expires_at=now + 600,
        stale_until=now + 1_200,
        fetched_at=now - 60,
        value={"ticker": "AAOI", "price": 99.0, "price_provider": "Massive"},
    )
    old_chart = stocks._EndpointCacheEntry(
        expires_at=now + 600,
        stale_until=now + 1_200,
        fetched_at=now - 60,
        value={
            "ticker": "AAOI",
            "range": "1d",
            "price_adjustment": "raw",
            "bars": [{"t": 1, "o": 99, "h": 99, "l": 99, "c": 99, "v": 1}],
        },
    )
    stocks._endpoint_cache["stock:AAOI"] = old_overview
    stocks._endpoint_cache["chart:AAOI:1d:raw"] = old_chart

    async def invalid_overview(_symbol: str) -> dict:
        return {"ticker": "AAOI", "price": 0, "price_provider": "Massive"}

    async def empty_chart(_symbol: str, _range: str, _adjustment: str) -> dict:
        return {
            "ticker": "AAOI",
            "range": "1d",
            "price_adjustment": "raw",
            "price_provider": "Massive",
            "bars": [],
        }

    monkeypatch.setattr(stocks, "_stock_overview_impl", invalid_overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", empty_chart)
    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals",
        lambda _symbol: {
            "rsi14": {"value": 50.0},
            "return_20d": {"value": 0.0},
            "macd_hist": {"value": 0.0},
            "_price_provider": {"value": "Massive"},
        },
    )

    async def scenario() -> dict:
        with request_owner_access_context(True):
            return await stocks.pull_stock_data("AAOI", request=_request())

    payload = asyncio.run(scenario())

    assert payload["status"] == "partial"
    assert payload["resources"]["overview"]["status"] == "failed"
    assert payload["resources"]["daily_chart"]["status"] == "failed"
    assert stocks._endpoint_cache["stock:AAOI"] is old_overview
    assert stocks._endpoint_cache["chart:AAOI:1d:raw"] is old_chart
    assert stocks._endpoint_cache["stock:AAOI"].value["price"] == 99.0
    assert stocks._endpoint_cache["chart:AAOI:1d:raw"].value["bars"][0]["c"] == 99


def test_manual_pull_raises_stable_503_when_both_resources_fail(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    async def failed(*_args) -> dict:
        raise RuntimeError("provider secret must not reach the response")

    def failed_signals(*_args) -> dict:
        raise RuntimeError("provider secret must not reach the response")

    monkeypatch.setattr(stocks, "_stock_overview_impl", failed)
    monkeypatch.setattr(stocks, "_stock_chart_impl", failed)
    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals",
        failed_signals,
    )

    async def scenario() -> None:
        with request_owner_access_context(True):
            await stocks.pull_stock_data("AAOI", request=_request())

    with pytest.raises(HTTPException) as captured:
        asyncio.run(scenario())

    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "stock_pull_failed"
    assert "provider secret" not in str(captured.value.detail)
    assert captured.value.detail["resources"]["overview"]["status"] == "failed"
    assert captured.value.detail["resources"]["daily_chart"]["status"] == "failed"
    assert captured.value.detail["resources"]["signals"]["status"] == "failed"


def test_force_replace_deduplicates_concurrent_manual_requests() -> None:
    calls = 0
    loader_started = asyncio.Event()
    release_loader = asyncio.Event()

    async def loader() -> dict:
        nonlocal calls
        calls += 1
        loader_started.set()
        await release_loader.wait()
        return {"ticker": "AAOI", "price": 112.02}

    async def scenario():
        first = asyncio.create_task(
            stocks._force_replace_endpoint("stock:AAOI", 60, 1_800, loader)
        )
        await loader_started.wait()
        second = asyncio.create_task(
            stocks._force_replace_endpoint("stock:AAOI", 60, 1_800, loader)
        )
        await asyncio.sleep(0)
        release_loader.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(scenario())

    assert calls == 1
    assert first is second


def test_complete_manual_pull_is_coalesced_per_ticker(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    raw_started = asyncio.Event()
    release_raw = asyncio.Event()
    calls = {
        "overview": 0,
        "raw": 0,
        "adjusted": 0,
        "signals": 0,
        "persist": 0,
    }

    async def overview(symbol: str) -> dict:
        calls["overview"] += 1
        return {
            "ticker": symbol,
            "price": 112.02,
            "price_provider": "Massive",
        }

    async def chart(symbol: str, _range: str, adjustment: str) -> dict:
        calls[adjustment] += 1
        if adjustment == "raw":
            raw_started.set()
            await release_raw.wait()
        return {
            "ticker": symbol,
            "range": "1d",
            "price_adjustment": adjustment,
            "price_provider": "Massive",
            "bars": [
                {
                    "t": 1,
                    "o": 112.0,
                    "h": 113.0,
                    "l": 111.0,
                    "c": 112.02,
                    "v": 100,
                }
            ],
        }

    def compute(*_args, **_kwargs) -> dict:
        calls["signals"] += 1
        return {
            "rsi14": {"value": 42.63},
            "return_20d": {"value": -20.0},
            "macd_hist": {"value": -0.2},
            "_price_provider": {"value": "Massive"},
        }

    def persist(_symbol: str, resources: dict) -> set[str]:
        calls["persist"] += 1
        return set(resources)

    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)
    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals_from_history",
        compute,
    )
    monkeypatch.setattr(stocks, "write_stock_pull_resources", persist)

    async def scenario() -> tuple[dict, dict]:
        with request_owner_access_context(True):
            first = asyncio.create_task(
                stocks.pull_stock_data("AAOI", request=_request())
            )
            await raw_started.wait()
            second = asyncio.create_task(
                stocks.pull_stock_data("aaoi", request=_request())
            )
            await asyncio.sleep(0)
            release_raw.set()
            results = await asyncio.gather(first, second)
            await asyncio.sleep(0)
            return results[0], results[1]

    first, second = asyncio.run(scenario())

    assert first == second
    assert first["status"] == "completed"
    assert calls == {
        "overview": 1,
        "raw": 1,
        "adjusted": 1,
        "signals": 1,
        "persist": 1,
    }
    assert stocks._stock_pull_tasks == {}


def test_public_pull_has_global_per_ticker_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", pull)

    async def scenario() -> HTTPException:
        with request_owner_access_context(False):
            first = await stocks.pull_stock_data(
                "AAOI",
                request=_request("203.0.113.10"),
            )
            assert first["status"] == "completed"
            await asyncio.sleep(0)
            with pytest.raises(HTTPException) as captured:
                await stocks.pull_stock_data(
                    "AAOI",
                    request=_request("203.0.113.99"),
                )
            return captured.value

    error = asyncio.run(scenario())

    assert calls == 1
    assert error.status_code == 429
    assert error.detail["code"] == "stock_pull_cooldown"
    assert int(error.headers["Retry-After"]) >= 1


def test_public_callers_join_the_same_inflight_ticker_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", pull)

    async def scenario() -> tuple[dict, dict]:
        with request_owner_access_context(False):
            first = asyncio.create_task(
                stocks.pull_stock_data(
                    "AAOI",
                    request=_request("203.0.113.11"),
                )
            )
            await started.wait()
            second = asyncio.create_task(
                stocks.pull_stock_data(
                    "aaoi",
                    request=_request("203.0.113.12"),
                )
            )
            await asyncio.sleep(0)
            release.set()
            result = await asyncio.gather(first, second)
            await asyncio.sleep(0)
            return result[0], result[1]

    first, second = asyncio.run(scenario())

    assert first == second == {"ticker": "AAOI", "status": "completed"}
    assert calls == 1
    assert stocks._stock_pull_tasks == {}


def test_public_pull_has_bounded_per_client_symbol_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(stocks, "_PUBLIC_STOCK_PULL_CLIENT_LIMIT", 2)
    calls: list[str] = []

    async def pull(symbol: str) -> dict:
        calls.append(symbol)
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", pull)

    async def scenario() -> HTTPException:
        request = _request("203.0.113.13")
        with request_owner_access_context(False):
            await stocks.pull_stock_data("AAOI", request=request)
            await asyncio.sleep(0)
            await stocks.pull_stock_data("NBIS", request=request)
            await asyncio.sleep(0)
            with pytest.raises(HTTPException) as captured:
                await stocks.pull_stock_data("AAPL", request=request)
            return captured.value

    error = asyncio.run(scenario())

    assert calls == ["AAOI", "NBIS"]
    assert error.status_code == 429
    assert error.detail["code"] == "stock_pull_rate_limited"


def test_different_tickers_run_in_parallel_with_bounded_blocking_work(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    symbols = ["AAOI", "NBIS", "AAPL", "MSFT"]
    raw_gate = asyncio.Event()
    raw_started: set[str] = set()
    raw_active = 0
    max_raw_active = 0
    blocking_lock = threading.Lock()
    blocking_active = 0
    max_blocking_active = 0
    compute_calls = 0
    persist_calls = 0

    async def overview(symbol: str) -> dict:
        return {
            "ticker": symbol,
            "price": 100.0,
            "price_provider": "Massive",
        }

    async def chart(symbol: str, _range: str, adjustment: str) -> dict:
        nonlocal raw_active, max_raw_active
        if adjustment == "raw":
            raw_started.add(symbol)
            raw_active += 1
            max_raw_active = max(max_raw_active, raw_active)
            if len(raw_started) >= 2:
                raw_gate.set()
            await asyncio.wait_for(raw_gate.wait(), timeout=1)
            raw_active -= 1
        return {
            "ticker": symbol,
            "range": "1d",
            "price_adjustment": adjustment,
            "price_provider": "Massive",
            "bars": [
                {
                    "t": 1,
                    "o": 99.0,
                    "h": 101.0,
                    "l": 98.0,
                    "c": 100.0,
                    "v": 100,
                }
            ],
        }

    def enter_bounded_operation() -> None:
        nonlocal blocking_active, max_blocking_active
        with blocking_lock:
            blocking_active += 1
            max_blocking_active = max(max_blocking_active, blocking_active)
        time.sleep(0.04)
        with blocking_lock:
            blocking_active -= 1

    def compute(*_args, **_kwargs) -> dict:
        nonlocal compute_calls
        with blocking_lock:
            compute_calls += 1
        enter_bounded_operation()
        return {
            "rsi14": {"value": 50.0},
            "return_20d": {"value": 0.0},
            "macd_hist": {"value": 0.0},
            "_price_provider": {"value": "Massive"},
        }

    def persist(_symbol: str, resources: dict) -> set[str]:
        nonlocal persist_calls
        with blocking_lock:
            persist_calls += 1
        enter_bounded_operation()
        return set(resources)

    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)
    monkeypatch.setattr(
        signal_service,
        "compute_stock_signals_from_history",
        compute,
    )
    monkeypatch.setattr(stocks, "write_stock_pull_resources", persist)

    async def scenario() -> list[dict]:
        with request_owner_access_context(True):
            results = await asyncio.gather(
                *(
                    stocks.pull_stock_data(symbol, request=_request())
                    for symbol in symbols
                )
            )
            await asyncio.sleep(0)
            return results

    results = asyncio.run(scenario())

    assert all(result["status"] == "completed" for result in results)
    assert raw_started == set(symbols)
    assert max_raw_active >= 2
    assert compute_calls == len(symbols)
    assert persist_calls == len(symbols)
    assert 1 < max_blocking_active <= stocks._STOCK_PULL_BLOCKING_MAX_WORKERS
    assert stocks._stock_pull_tasks == {}


def test_cancelling_one_waiter_does_not_cancel_shared_stock_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def shared_pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        assert symbol == "AAOI"
        started.set()
        await release.wait()
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", shared_pull)

    async def scenario() -> dict:
        with request_owner_access_context(True):
            first = asyncio.create_task(
                stocks.pull_stock_data("AAOI", request=_request())
            )
            await started.wait()
            second = asyncio.create_task(
                stocks.pull_stock_data("aaoi", request=_request())
            )
            await asyncio.sleep(0)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            release.set()
            result = await second
            await asyncio.sleep(0)
            return result

    result = asyncio.run(scenario())

    assert result == {"ticker": "AAOI", "status": "completed"}
    assert calls == 1
    assert stocks._stock_pull_tasks == {}


def test_option_enrichment_failure_does_not_erase_price_derived_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2025, 9, 1, tzinfo=timezone.utc)
    index = pd.DatetimeIndex([start + timedelta(days=i) for i in range(220)])
    close = pd.Series([80 + i * 0.1 for i in range(220)], index=index)
    history = pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": pd.Series(
                [1_000_000 + i * 1_000 for i in range(220)],
                index=index,
            ),
        }
    )
    monkeypatch.setattr(
        signal_service,
        "_history",
        lambda _symbol, period="1y": history,
    )
    monkeypatch.setattr(
        "app.services.yahoo.get_stock_iv",
        lambda _symbol: (_ for _ in ()).throw(RuntimeError("HTTP 402")),
    )

    payload = signal_service.compute_stock_signals_from_history(
        "AAOI",
        history,
        price_provider="Massive",
    )

    assert payload["rsi14"]["value"] is not None
    assert payload["macd_hist"]["value"] is not None
    assert payload["atm_iv_percent"]["value"] is None
    assert payload["_price_provider"]["value"] == "Massive"


def _pulled_daily_chart_payload(n: int = 160) -> dict:
    """Chart-contract daily bars long enough for structure analysis (>=30)."""

    start = int(datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc).timestamp())
    bars = []
    price = 60.0
    for i in range(n):
        # 6 上 6 下的长波：产生 span=3 可确认的分形摆动点
        price += 0.5 if (i // 6) % 2 == 0 else -0.45
        close = round(price, 2)
        bars.append(
            {
                "t": start + i * 86_400,
                "o": round(close - 0.3, 2),
                "h": round(close + 0.6, 2),
                "l": round(close - 0.8, 2),
                "c": close,
                "v": 900_000 + (i % 7) * 40_000,
            }
        )
    return {
        "ticker": "AAOI",
        "range": "1d",
        "price_adjustment": "raw",
        "price_provider": "Massive",
        "as_of": "2026-07-24T01:00:01+00:00",
        "last_bar_at": "2026-07-23T20:00:00+00:00",
        "bars": bars,
    }


def test_guest_technical_reads_manual_pull_snapshot_after_restart(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拉取过的股票重启后，访客的结构分析必须与 K 线同源可读。

    chart 路由会 hydrate 手动拉取的日线快照给访客画蜡烛；technical 的访客
    回退若只读 public_home，就会出现「K 线画得出来、结构卡 503」的割裂。
    """

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    chart_payload = _pulled_daily_chart_payload()
    write_stock_pull_resources(
        "AAOI",
        {"daily_chart": (chart_payload, time.time())},
    )
    stocks._endpoint_cache.clear()  # simulate a backend restart

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.stock_technical("aaoi")

    payload = asyncio.run(scenario())

    assert payload["ticker"] == "AAOI"
    assert payload["basis"] == "raw_daily"
    assert payload["bar_count"] == len(chart_payload["bars"])
    assert payload["as_of"] == chart_payload["as_of"]
    assert payload["price_action"]["swing_highs"]
    assert payload["technicals"]["rsi14"] is not None


def test_guest_technical_still_requires_some_snapshot(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没拉取过也没有公开快照的股票，访客不得触发任何回源计算。"""

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.stock_technical("aaoi")

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(scenario())
    assert exc_info.value.status_code == 503
