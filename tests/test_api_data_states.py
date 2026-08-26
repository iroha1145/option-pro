from __future__ import annotations

import asyncio
from datetime import date
import json
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api import earnings, options, stocks
from app.services import sectors as sector_service


def _watchlist_payload(label: str, *, succeeded: int = 1):
    return {
        "groups": [
            {
                "id": label,
                "name": label,
                "stocks": [
                    {
                        "ticker": "AAPL",
                        "name": "Apple",
                        "price": 100.0,
                        "change_percent": 1.25,
                        "spark": [98.0, 100.0],
                    }
                ],
            }
        ],
        "attempted": succeeded,
        "succeeded": succeeded,
        "failed": 0,
        "failed_tickers": [],
        "data_limited": False,
        "source_status": "active",
    }


def test_endpoint_cache_uses_only_bounded_marked_stale_data(monkeypatch):
    async def scenario():
        clock = [1_000.0]
        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()

        async def healthy_loader():
            return {"value": 42}

        fresh = await stocks._cached_endpoint(
            "test:bounded-stale", 10, healthy_loader, stale_ttl=20
        )
        assert fresh["value"] == 42
        assert fresh["_stale"] is False
        assert fresh["source_status"] == "active"

        async def failed_loader():
            raise RuntimeError("provider unavailable")

        clock[0] = 1_011.0
        stale = await stocks._cached_endpoint(
            "test:bounded-stale", 10, failed_loader, stale_ttl=20
        )
        assert stale["value"] == 42
        assert stale["_stale"] is True
        assert stale["source_status"] == "degraded"
        assert stale["stale_reason"] == "upstream_refresh_failed"

        clock[0] = 1_031.0
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await stocks._cached_endpoint(
                "test:bounded-stale", 10, failed_loader, stale_ttl=20
            )
        assert "test:bounded-stale" not in stocks._endpoint_cache

    asyncio.run(scenario())


def test_endpoint_cache_has_a_hard_capacity_limit():
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()
    now = time.time()
    for index in range(stocks._ENDPOINT_MAX_ENTRIES):
        stocks._endpoint_cache[f"full:{index}"] = stocks._EndpointCacheEntry(
            expires_at=now + 60,
            stale_until=now + 120 + index,
            fetched_at=now,
            value={"value": index},
        )

    async def load():
        return {"value": "new"}

    result = asyncio.run(stocks._cached_endpoint("full:new", 60, load))

    assert result["value"] == "new"
    assert len(stocks._endpoint_cache) == stocks._ENDPOINT_MAX_ENTRIES
    assert "full:0" not in stocks._endpoint_cache


def test_endpoint_cache_failed_unique_loads_do_not_retain_locks():
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()

    async def scenario():
        async def fail():
            raise RuntimeError("provider down")

        for index in range(20):
            with pytest.raises(RuntimeError, match="provider down"):
                await stocks._cached_endpoint(f"failure:{index}", 60, fail)

    asyncio.run(scenario())

    assert stocks._endpoint_locks == {}
    assert stocks._endpoint_lock_users == {}


def test_logo_not_found_uses_a_short_negative_cache(monkeypatch):
    stocks._endpoint_cache.clear()
    calls = 0

    async def missing(_symbol):
        nonlocal calls
        calls += 1
        raise HTTPException(status_code=404, detail="missing")

    monkeypatch.setattr(stocks, "_fetch_company_logo", missing)

    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(stocks._cached_company_logo("NONE"))
        assert exc.value.status_code == 404

    assert calls == 1
    cached = stocks._endpoint_cache["logo:NONE"]
    assert cached.value == stocks._LOGO_NOT_FOUND


def test_logo_response_sandboxes_svg_and_rejects_private_redirect_targets(monkeypatch):
    async def svg(_symbol, *, allow_refresh=True):
        assert allow_refresh is True
        return {
            "content": b"<svg xmlns='http://www.w3.org/2000/svg'></svg>" * 2,
            "media_type": "image/svg+xml",
            "source": "https://logos.example/AAPL.svg",
        }

    monkeypatch.setattr(stocks, "_cached_company_logo", svg)
    response = asyncio.run(stocks.stock_logo("AAPL"))

    assert response.headers["content-security-policy"].startswith("sandbox")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert stocks._safe_logo_url("https://127.0.0.1/internal.svg") is False
    assert stocks._safe_logo_url("http://logos.example/AAPL.svg") is False
    assert stocks._safe_logo_url("https://localhost/internal.svg") is False
    assert stocks._safe_logo_url("https://internal.example/AAPL.svg") is False
    assert stocks._safe_logo_url(
        "https://financialmodelingprep.com/image-stock/AAPL.png"
    ) is True


def test_logo_invalid_variants_cannot_bypass_the_canonical_negative_cache(monkeypatch):
    stocks._endpoint_cache.clear()
    calls = 0

    async def missing(_symbol):
        nonlocal calls
        calls += 1
        raise HTTPException(status_code=404, detail="missing")

    monkeypatch.setattr(stocks, "_fetch_company_logo", missing)

    for symbol in ("AAPL", "US.AAPL"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(stocks._cached_company_logo(symbol))
        assert exc_info.value.status_code == 404
    assert calls == 1
    assert set(key for key in stocks._endpoint_cache if key.startswith("logo:")) == {
        "logo:AAPL"
    }

    for invalid in ("AAPL!", "AAPL@", "AAPL..", "AAPL-"):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(stocks._cached_company_logo(invalid))
        assert exc_info.value.status_code == 404
    assert calls == 1


def test_unusual_options_reports_total_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", BrokenTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(options._unusual_activity_impl("all", 1.0))
    assert exc_info.value.status_code == 503


def test_unusual_options_treats_empty_expiration_payload_as_provider_failure(monkeypatch):
    class EmptyTicker:
        options = []

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: EmptyTicker())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(options._unusual_activity_impl("all", 1.0))
    assert exc_info.value.status_code == 503


def test_unusual_options_allows_a_valid_chain_with_no_matching_activity(monkeypatch):
    class UsableTicker:
        options = ["2026-08-21"]
        fast_info = SimpleNamespace(last_price=100.0)

        def option_chain(self, _expiration):
            return SimpleNamespace(
                calls=pd.DataFrame([{"volume": 0, "openInterest": 100}]),
                puts=pd.DataFrame(),
            )

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: UsableTicker())

    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))

    assert payload["results"] == []
    assert payload["succeeded"] == 2
    assert payload["source_status"] == "active"


def test_expirations_route_preserves_freshness_metadata(monkeypatch):
    monkeypatch.setattr(
        options.yahoo,
        "get_expirations_snapshot",
        lambda _ticker: {
            "expirations": ["2026-08-21"],
            "_stale": True,
            "source_status": "stale",
            "as_of": "2026-07-10T00:00:00+00:00",
            "stale_age_seconds": 42.0,
        },
    )

    result = asyncio.run(options.expirations("aapl"))
    assert result["ticker"] == "AAPL"
    assert result["expirations"] == ["2026-08-21"]
    assert result["_stale"] is True
    assert result["source_status"] == "stale"
    assert result["stale_age_seconds"] == 42.0


def _unavailable_finnhub():
    async def unavailable(_today):
        return earnings._finnhub_fetch_result(
            configured=False,
            succeeded=False,
            error="not_configured",
        )

    return unavailable


def test_earnings_reports_total_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(earnings.yf, "Ticker", BrokenTicker)
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", _unavailable_finnhub())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))
    assert exc_info.value.status_code == 503


def test_earnings_treats_completely_empty_provider_payload_as_failure(monkeypatch):
    class EmptyTicker:
        calendar = {}
        info = {}

        def get_earnings_dates(self, limit=12):
            return None

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: EmptyTicker())
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", _unavailable_finnhub())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))
    assert exc_info.value.status_code == 503


def test_earnings_fetches_full_info_only_for_tickers_with_a_date(monkeypatch):
    info_accesses: list[str] = []
    table_accesses: list[str] = []

    class FakeTicker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        @property
        def calendar(self):
            if self.symbol == "HIT":
                return {"Earnings Date": [date(2026, 7, 20)], "Earnings Average": [1.25]}
            return {}

        def get_earnings_dates(self, limit=12):
            table_accesses.append(self.symbol)
            return None

        @property
        def info(self):
            info_accesses.append(self.symbol)
            if self.symbol != "HIT":
                raise AssertionError("full info must not be fetched for a ticker without a date")
            return {"shortName": "Hit Corp", "marketCap": 123, "sector": "Technology"}

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["HIT", "MISS"])
    monkeypatch.setattr(earnings.yf, "Ticker", FakeTicker)
    monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", _unavailable_finnhub())

    payload = asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))

    assert [item["ticker"] for item in payload["earnings"]] == ["HIT"]
    assert info_accesses == ["HIT"]
    assert table_accesses == ["MISS"]
    assert payload["source_status"] == "degraded"
    assert payload["failed_symbols"] == ["MISS"]


def test_legacy_stock_signals_reports_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(stocks.yf, "Ticker", BrokenTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.stock_signals("AAA"))
    assert exc_info.value.status_code == 503


def test_legacy_stock_signals_accepts_indices_but_rejects_invalid_symbols(monkeypatch):
    called = False

    class RecordingTicker:
        def __init__(self, _symbol):
            nonlocal called
            called = True

    monkeypatch.setattr(stocks.yf, "Ticker", RecordingTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.stock_signals("../../secret"))

    assert exc_info.value.status_code == 400
    assert called is False
    assert stocks._WATCHLIST_TICKER_PATTERN.fullmatch("^GSPC")


def test_legacy_stock_signals_ignores_incomplete_daily_bar(monkeypatch):
    completed_closes = [100.0 + index * 0.5 for index in range(60)]
    history = pd.DataFrame(
        {
            "Close": completed_closes + [float("nan")],
            "Volume": [1_000_000 + index for index in range(60)] + [5_000_000],
        },
        index=pd.date_range("2026-04-01", periods=61, freq="B"),
    )

    class IncompleteTicker:
        def __init__(self, _symbol):
            pass

        def history(self, *, period):
            assert period == "100d"
            return history

    monkeypatch.setattr(stocks.yf, "Ticker", IncompleteTicker)
    stocks._endpoint_cache.pop("technical-signals:NANBAR", None)

    payload = asyncio.run(stocks.stock_signals("NANBAR"))

    assert payload["price"] == completed_closes[-1]
    assert payload["signals"]["rsi"]["value"] == 100.0
    assert payload["signals"]["volume"]["value"] < 2


def test_watchlist_reports_provider_failure_without_unbounded_stale_data(monkeypatch):
    async def failed_watchlist():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(stocks, "_build_watchlist", failed_watchlist)
    stocks._endpoint_cache.pop("watchlist", None)
    stocks._endpoint_locks.pop("watchlist", None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.watchlist(None))
    assert exc_info.value.status_code == 503


def test_full_watchlist_returns_stale_immediately_then_atomically_replaces_it(monkeypatch):
    async def scenario():
        clock = [10_000.0]
        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()

        old_entry = stocks._EndpointCacheEntry(
            expires_at=clock[0] - 1,
            stale_until=clock[0] + 1_000,
            fetched_at=clock[0] - 600,
            value={"groups": [{"id": "old"}], "attempted": 1},
        )
        stocks._endpoint_cache["watchlist"] = old_entry
        started = asyncio.Event()
        release = asyncio.Event()

        async def refreshed_watchlist():
            started.set()
            await release.wait()
            return {"groups": [{"id": "new"}], "attempted": 2}

        monkeypatch.setattr(stocks, "_build_watchlist", refreshed_watchlist)

        result = await asyncio.wait_for(stocks.watchlist(None), timeout=0.5)
        assert result["groups"] == [{"id": "old"}]
        assert result["_stale"] is True
        assert result["stale_reason"] == "background_refresh_pending"
        assert result["stale_age_seconds"] == 600.0

        await asyncio.wait_for(started.wait(), timeout=0.5)
        refresh_task = stocks._endpoint_refresh_tasks["watchlist"]
        assert stocks._endpoint_cache["watchlist"] is old_entry

        clock[0] += 30
        release.set()
        await refresh_task
        await asyncio.sleep(0)

        replacement = stocks._endpoint_cache["watchlist"]
        assert replacement is not old_entry
        assert replacement.value["groups"] == [{"id": "new"}]
        assert replacement.expires_at == clock[0] + 300
        assert replacement.stale_until == clock[0] + 24 * 60 * 60
        assert "watchlist" not in stocks._endpoint_refresh_tasks

        fresh = await stocks.watchlist(None)
        assert fresh["groups"] == [{"id": "new"}]
        assert fresh["_stale"] is False

    asyncio.run(scenario())


def test_full_watchlist_stale_requests_share_one_background_refresh(monkeypatch):
    async def scenario():
        now = time.time()
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        stocks._endpoint_cache["watchlist"] = stocks._EndpointCacheEntry(
            expires_at=now - 1,
            stale_until=now + 1_000,
            fetched_at=now - 600,
            value={"groups": [{"id": "old"}]},
        )
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def refreshed_watchlist():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"groups": [{"id": "new"}]}

        monkeypatch.setattr(stocks, "_build_watchlist", refreshed_watchlist)

        results = await asyncio.gather(*(stocks.watchlist(None) for _ in range(20)))
        await asyncio.wait_for(started.wait(), timeout=0.5)

        assert calls == 1
        assert all(result["groups"] == [{"id": "old"}] for result in results)
        assert len(stocks._endpoint_refresh_tasks) == 1

        refresh_task = stocks._endpoint_refresh_tasks["watchlist"]
        release.set()
        await refresh_task
        await asyncio.sleep(0)
        assert "watchlist" not in stocks._endpoint_refresh_tasks

    asyncio.run(scenario())


def test_full_watchlist_failed_refresh_keeps_only_bounded_stale_snapshot(monkeypatch):
    async def scenario():
        clock = [20_000.0]
        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        old_entry = stocks._EndpointCacheEntry(
            expires_at=clock[0] - 1,
            stale_until=clock[0] + 60,
            fetched_at=clock[0] - 600,
            value={"groups": [{"id": "old"}]},
        )
        stocks._endpoint_cache["watchlist"] = old_entry
        calls = 0

        async def failed_watchlist():
            nonlocal calls
            calls += 1
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(stocks, "_build_watchlist", failed_watchlist)

        stale = await stocks.watchlist(None)
        assert stale["groups"] == [{"id": "old"}]
        refresh_task = stocks._endpoint_refresh_tasks["watchlist"]
        await asyncio.gather(refresh_task, return_exceptions=True)
        await asyncio.sleep(0)

        assert calls == 1
        assert stocks._endpoint_cache["watchlist"] is old_entry
        assert old_entry.stale_until == 20_060.0

        clock[0] = old_entry.stale_until + 1
        with pytest.raises(HTTPException) as exc_info:
            await stocks.watchlist(None)
        assert exc_info.value.status_code == 503
        assert calls == 2
        assert "watchlist" not in stocks._endpoint_cache

    asyncio.run(scenario())


def test_full_watchlist_refresh_failure_cools_down_then_retries(monkeypatch, tmp_path):
    async def scenario():
        clock = [30_000.0]
        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        monkeypatch.setattr(
            stocks,
            "_WATCHLIST_SNAPSHOT_PATH",
            tmp_path / "watchlist.json",
        )
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        stocks._endpoint_refresh_retry_after.clear()
        monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)

        old_entry = stocks._EndpointCacheEntry(
            expires_at=clock[0] - 1,
            stale_until=clock[0] + 1_000,
            fetched_at=clock[0] - 600,
            value=_watchlist_payload("old"),
        )
        stocks._endpoint_cache["watchlist"] = old_entry
        calls = 0

        async def refresh_watchlist():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("provider unavailable")
            return _watchlist_payload("new")

        monkeypatch.setattr(stocks, "_build_watchlist", refresh_watchlist)

        first = await stocks.watchlist(None)
        assert first["groups"][0]["id"] == "old"
        first_task = stocks._endpoint_refresh_tasks["watchlist"]
        await asyncio.gather(first_task, return_exceptions=True)
        await asyncio.sleep(0)

        assert calls == 1
        assert stocks._endpoint_cache["watchlist"] is old_entry
        assert old_entry.stale_until == 31_000.0
        assert stocks._endpoint_refresh_retry_after["watchlist"] == 30_060.0

        clock[0] = 30_030.0
        cooling = await stocks.watchlist(None)
        await asyncio.sleep(0)
        assert cooling["groups"][0]["id"] == "old"
        assert cooling["stale_reason"] == "upstream_refresh_failed"
        assert calls == 1
        assert "watchlist" not in stocks._endpoint_refresh_tasks
        assert old_entry.stale_until == 31_000.0

        clock[0] = 30_060.0
        retrying = await stocks.watchlist(None)
        assert retrying["stale_reason"] == "background_refresh_pending"
        retry_task = stocks._endpoint_refresh_tasks["watchlist"]
        await retry_task
        await asyncio.sleep(0)

        assert calls == 2
        assert "watchlist" not in stocks._endpoint_refresh_retry_after
        replacement = stocks._endpoint_cache["watchlist"]
        assert replacement is not old_entry
        assert replacement.value["groups"][0]["id"] == "new"
        assert replacement.stale_until == 30_060.0 + 24 * 60 * 60

    asyncio.run(scenario())


def test_owner_watchlist_restart_snapshot_reuses_fresh_generation_without_provider(
    monkeypatch,
    tmp_path,
):
    config = stocks.get_personal_config()
    password_config = config.model_copy(
        update={
            "access": config.access.model_copy(update={"mode": "password"})
        }
    )
    monkeypatch.setattr(stocks, "get_personal_config", lambda: password_config)

    async def scenario():
        clock = [40_000.0]
        saved_at = clock[0] - 300
        snapshot_path = tmp_path / "watchlist.json"
        old_payload = {
            **_watchlist_payload("persisted"),
            "_stale": False,
            "as_of": "not-the-saved-time",
            "source_status": "active",
            "stale_age_seconds": 0,
            "stale_reason": "not-stale",
        }
        snapshot_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "saved_at": saved_at,
                    "parameters": {"tickers": None},
                    "payload": old_payload,
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
        monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        stocks._endpoint_refresh_retry_after.clear()
        original = snapshot_path.read_bytes()
        calls = 0

        async def refreshed_watchlist():
            nonlocal calls
            calls += 1
            raise AssertionError("fresh worker snapshot must suppress provider")

        monkeypatch.setattr(stocks, "_build_watchlist", refreshed_watchlist)

        restored = await asyncio.wait_for(stocks.watchlist(None), timeout=0.5)
        assert restored["groups"][0]["id"] == "persisted"
        assert restored["_stale"] is False
        assert restored["source_status"] == "active"
        assert restored["as_of"] != "not-the-saved-time"

        restored_entry = stocks._endpoint_cache["watchlist"]
        assert restored_entry.fetched_at == saved_at
        assert restored_entry.stale_until == saved_at + 24 * 60 * 60
        assert restored_entry.expires_at == saved_at + 1800
        assert stocks._endpoint_refresh_tasks == {}
        assert calls == 0
        assert snapshot_path.read_bytes() == original

    asyncio.run(scenario())


def test_private_watchlist_reloads_newer_snapshot_but_keeps_live_refresh_semantics(
    monkeypatch,
    tmp_path,
):
    async def scenario():
        now = 45_000.0
        snapshot_path = tmp_path / "watchlist.json"
        stocks._write_watchlist_snapshot(
            snapshot_path,
            payload=_watchlist_payload("initial"),
            saved_at=44_000.0,
        )
        monkeypatch.setattr(stocks.time, "time", lambda: now)
        monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
        monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)
        monkeypatch.setattr(stocks, "_watchlist_snapshot_observed", None)
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        stocks._endpoint_refresh_retry_after.clear()

        stocks._load_watchlist_snapshot_once(now)
        assert stocks._endpoint_cache["watchlist"].value["groups"][0]["id"] == "initial"

        stocks._write_watchlist_snapshot(
            snapshot_path,
            payload=_watchlist_payload("worker"),
            saved_at=44_990.0,
        )

        async def no_network():
            raise RuntimeError("network is disabled in this test")

        monkeypatch.setattr(stocks, "_build_watchlist", no_network)
        refreshed = await stocks.watchlist(None)
        refresh_task = stocks._endpoint_refresh_tasks["watchlist"]
        with pytest.raises(RuntimeError, match="network is disabled"):
            await refresh_task
        await asyncio.sleep(0)
        assert refreshed["groups"][0]["id"] == "worker"
        assert refreshed["as_of"].startswith("1970-")
        assert refreshed["_stale"] is True
        assert refreshed["source_status"] == "degraded"
        assert "watchlist" not in stocks._endpoint_refresh_tasks
        assert stocks._endpoint_cache["watchlist"].fetched_at == 44_990.0

    asyncio.run(scenario())


def test_watchlist_snapshot_write_failure_does_not_fail_refresh(monkeypatch, tmp_path):
    async def scenario():
        clock = [50_000.0]
        snapshot_path = tmp_path / "watchlist.json"
        original_document = {
            "version": 1,
            "saved_at": clock[0] - 24 * 60 * 60 - 1,
            "parameters": {"tickers": None},
            "payload": _watchlist_payload("persisted"),
        }
        snapshot_path.write_text(json.dumps(original_document), encoding="utf-8")
        original_bytes = snapshot_path.read_bytes()

        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
        monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", True)
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()
        stocks._endpoint_lock_users.clear()
        stocks._endpoint_refresh_tasks.clear()
        stocks._endpoint_refresh_retry_after.clear()
        stocks._endpoint_cache["watchlist"] = stocks._EndpointCacheEntry(
            expires_at=clock[0] - 1,
            stale_until=clock[0] + 1_000,
            fetched_at=clock[0] - 600,
            value=_watchlist_payload("old"),
        )

        async def refreshed_watchlist():
            return _watchlist_payload("live")

        def failed_replace(_source, _destination):
            raise OSError("disk unavailable")

        monkeypatch.setattr(stocks, "_build_watchlist", refreshed_watchlist)
        monkeypatch.setattr(stocks.os, "replace", failed_replace)

        stale = await stocks.watchlist(None)
        assert stale["groups"][0]["id"] == "old"
        refresh_task = stocks._endpoint_refresh_tasks["watchlist"]
        await refresh_task
        await asyncio.sleep(0)

        assert refresh_task.exception() is None
        assert stocks._endpoint_cache["watchlist"].value["groups"][0]["id"] == "live"
        assert snapshot_path.read_bytes() == original_bytes
        assert "watchlist" not in stocks._endpoint_refresh_retry_after
        assert list(tmp_path.glob(".watchlist.json.*.tmp")) == []

    asyncio.run(scenario())


def test_watchlist_snapshot_rejects_expired_corrupt_and_oversized_files(tmp_path):
    now = 100_000.0
    snapshot_path = tmp_path / "watchlist.json"

    snapshot_path.write_text(
        json.dumps(
            {
                "version": 1,
                "saved_at": now - 24 * 60 * 60,
                "parameters": {"tickers": None},
                "payload": _watchlist_payload("expired"),
            }
        ),
        encoding="utf-8",
    )
    assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None

    snapshot_path.write_text("{broken", encoding="utf-8")
    assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None

    for invalid_payload in (
        {**_watchlist_payload("empty"), "groups": []},
        {**_watchlist_payload("failed"), "succeeded": 0},
        {**_watchlist_payload("null-group"), "groups": [None]},
        {
            **_watchlist_payload("bad-stocks"),
            "groups": [{"id": "bad", "name": "Bad", "stocks": "invalid"}],
        },
    ):
        snapshot_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "saved_at": now - 1,
                    "parameters": {"tickers": None},
                    "payload": invalid_payload,
                }
            ),
            encoding="utf-8",
        )
        assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None

    overflow_document = json.dumps(
        {
            "version": 1,
            "saved_at": now - 1,
            "parameters": {"tickers": None},
            "payload": _watchlist_payload("overflow"),
        }
    ).replace('"price": 100.0', '"price": 1e309', 1)
    assert '"price": 1e309' in overflow_document
    snapshot_path.write_text(overflow_document, encoding="utf-8")
    assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None

    snapshot_path.write_bytes(b"x" * (stocks._WATCHLIST_SNAPSHOT_MAX_BYTES + 1))
    assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None

    stocks._write_watchlist_snapshot(
        snapshot_path,
        payload=_watchlist_payload("mismatched"),
        saved_at=now - 1,
    )
    document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    document["parameters"] = {"tickers": ["AAPL"]}
    snapshot_path.write_text(json.dumps(document), encoding="utf-8")
    assert stocks._read_watchlist_snapshot(snapshot_path, now=now) is None


def test_watchlist_ticker_query_normalizes_deduplicates_and_accepts_common_formats():
    assert stocks._parse_watchlist_tickers(None) is None
    assert stocks._parse_watchlist_tickers(
        " aapl,MSFT,aapl,^gspc,brk-b,es=f,rms.pa "
    ) == ["AAPL", "MSFT", "^GSPC", "BRK-B", "ES=F", "RMS.PA"]


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "AAPL,,MSFT",
        "$SPY",
        "AAPL/B",
        "^",
        "A" * 33,
    ],
)
def test_watchlist_ticker_query_rejects_invalid_symbols_with_400(raw):
    with pytest.raises(HTTPException) as exc_info:
        stocks._parse_watchlist_tickers(raw)
    assert exc_info.value.status_code == 400


def test_watchlist_ticker_query_rejects_more_than_100_unique_symbols():
    raw = ",".join(f"T{index}" for index in range(101))
    with pytest.raises(HTTPException) as exc_info:
        stocks._parse_watchlist_tickers(raw)
    assert exc_info.value.status_code == 400


def test_watchlist_cache_key_is_stable_for_the_same_ticker_set():
    first = stocks._watchlist_cache_key(["MSFT", "AAPL"])
    reordered = stocks._watchlist_cache_key(["AAPL", "MSFT"])
    different = stocks._watchlist_cache_key(["AAPL", "NVDA"])

    assert first == reordered
    assert first != different
    assert stocks._watchlist_cache_key(None) == "watchlist"


def test_targeted_watchlist_uses_normalized_tickers_and_isolated_cache_keys(monkeypatch):
    requested_calls = []
    cache_keys = []

    async def targeted_builder(requested):
        requested_calls.append(requested)
        return {"groups": [], "attempted": len(requested)}

    async def uncached(
        key,
        _ttl,
        loader,
        *,
        stale_ttl=None,
        allow_refresh=True,
    ):
        assert allow_refresh is True
        cache_keys.append((key, stale_ttl))
        return await loader()

    monkeypatch.setattr(stocks, "_build_watchlist", targeted_builder)
    monkeypatch.setattr(stocks, "_cached_endpoint", uncached)

    async def unexpected_background_cache(*_args, **_kwargs):
        raise AssertionError("targeted watchlists must keep the synchronous cache path")

    monkeypatch.setattr(
        stocks,
        "_stale_while_revalidate_endpoint",
        unexpected_background_cache,
    )

    first = asyncio.run(stocks.watchlist(" msft,AAPL,msft "))
    second = asyncio.run(stocks.watchlist("AAPL,MSFT"))
    third = asyncio.run(stocks.watchlist("AAPL,NVDA"))

    assert first["attempted"] == 2
    assert second["attempted"] == 2
    assert requested_calls == [["MSFT", "AAPL"], ["AAPL", "MSFT"], ["AAPL", "NVDA"]]
    assert cache_keys[0][0] == cache_keys[1][0]
    assert cache_keys[0][0] != cache_keys[2][0]
    assert all(stale_ttl == 900 for _, stale_ttl in cache_keys)


def test_targeted_watchlist_downloads_only_requested_tickers(monkeypatch):
    captured = []

    def fake_download(*, tickers, interval, **_kwargs):
        captured.append((tickers, interval))
        columns = pd.MultiIndex.from_tuples([
            ("AAPL", "Close"),
            ("MSFT", "Close"),
            ("ES=F", "Close"),
        ])
        if interval == "5m":
            return pd.DataFrame(
                [
                    [192.5, 424.5, 5_526.0],
                    [193.0, 425.0, 5_530.0],
                ],
                index=pd.DatetimeIndex(
                    ["2026-07-14 15:55", "2026-07-14 16:00"],
                    tz="America/New_York",
                ),
                columns=columns,
            )
        return pd.DataFrame(
            [
                [190.0, 420.0, 5_500.0],
                [192.0, 424.0, 5_525.0],
            ],
            index=pd.to_datetime(["2026-07-13", "2026-07-14"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)
    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_closes",
        lambda tickers, *, session: {"ES=F": 5_525.0},
    )

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "MSFT", "ES=F"]))
    returned = [
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    ]

    assert captured == [
        ("AAPL MSFT ES=F", "1d"),
        ("AAPL MSFT ES=F", "5m"),
    ]
    assert sorted(returned) == ["AAPL", "ES=F", "MSFT"]
    assert len(returned) == len(set(returned))
    assert payload["attempted"] == 3
    assert payload["succeeded"] == 3
    assert any(group["id"] == "custom" for group in payload["groups"])
    assert stocks._clean_watchlist_snapshot_payload(payload) is not None


def test_watchlist_overlays_extended_quote_when_current_daily_bar_is_missing(monkeypatch):
    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [196.0, 197.0]},
                index=pd.DatetimeIndex(
                    ["2026-07-14 19:50", "2026-07-14 19:55"],
                    tz="America/New_York",
                ),
            )
        return pd.DataFrame(
            {"Close": [190.0, 192.0, float("nan")]},
            index=pd.to_datetime(["2026-07-10", "2026-07-13", "2026-07-14"]),
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL"]))
    item = payload["groups"][0]["stocks"][0]

    assert item["price"] == 197.0
    assert item["change"] == 5.0
    assert item["change_percent"] == 2.6
    assert item["spark"] == [190.0, 192.0, 197.0]
    assert item["quote_session"] == "post_market"
    assert item["quote_as_of"] == "2026-07-14T23:55:00+00:00"
    assert payload["data_through"] == item["quote_as_of"]
    assert payload["source_status"] == "active"


def test_watchlist_replaces_current_daily_bar_and_keeps_previous_close_baseline(monkeypatch):
    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [196.0, 197.0]},
                index=pd.DatetimeIndex(
                    ["2026-07-14 15:55", "2026-07-14 16:00"],
                    tz="America/New_York",
                ),
            )
        return pd.DataFrame(
            {"Close": [190.0, 195.0]},
            index=pd.to_datetime(["2026-07-13", "2026-07-14"]),
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL"]))
    item = payload["groups"][0]["stocks"][0]

    assert item["price"] == 197.0
    assert item["change"] == 7.0
    assert item["change_percent"] == 3.68
    assert item["spark"] == [190.0, 197.0]
    assert item["quote_session"] == "post_market"


def test_watchlist_omits_ticker_without_latest_quote_and_marks_batch_degraded(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("MSFT", "Close"),
        ("NVDA", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [[197.0, 425.0, float("nan")]],
                index=pd.DatetimeIndex(["2026-07-14 16:00"], tz="America/New_York"),
                columns=columns,
            )
        return pd.DataFrame(
            [[190.0, 420.0, 200.0], [192.0, 424.0, 203.0]],
            index=pd.to_datetime(["2026-07-13", "2026-07-14"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "MSFT", "NVDA"]))
    returned = {
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    }

    assert returned == {"AAPL", "MSFT"}
    assert payload["succeeded"] == 2
    assert payload["failed"] == 1
    assert payload["failed_tickers"] == ["NVDA"]
    assert payload["source_status"] == "degraded"
    assert payload["data_limited"] is True


def test_full_watchlist_partial_latest_batch_fails_closed_per_ticker(monkeypatch):
    monkeypatch.setattr(
        sector_service,
        "SECTORS",
        {"test": {"name": "测试", "tickers": ["AAPL", "MSFT", "NVDA"]}},
    )
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("MSFT", "Close"),
        ("NVDA", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [[197.0, 425.0, float("nan")]],
                index=pd.DatetimeIndex(["2026-07-14 16:00"], tz="America/New_York"),
                columns=columns,
            )
        return pd.DataFrame(
            [[190.0, 420.0, 200.0], [192.0, 424.0, 203.0]],
            index=pd.to_datetime(["2026-07-13", "2026-07-14"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist())
    returned = {
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    }

    assert returned == {"AAPL", "MSFT"}
    assert payload["attempted"] == 3
    assert payload["succeeded"] == 2
    assert payload["failed_tickers"] == ["NVDA"]
    assert payload["source_status"] == "degraded"


def test_watchlist_marks_previous_market_date_quote_delayed_and_uses_oldest_time(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("MSFT", "Close"),
        ("NVDA", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [
                    [float("nan"), float("nan"), 203.0],
                    [197.0, 425.0, float("nan")],
                ],
                index=pd.DatetimeIndex(
                    ["2026-07-13 16:00", "2026-07-14 16:00"],
                    tz="America/New_York",
                ),
                columns=columns,
            )
        return pd.DataFrame(
            [[188.0, 418.0, 198.0], [192.0, 424.0, 202.0]],
            index=pd.to_datetime(["2026-07-12", "2026-07-13"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "MSFT", "NVDA"]))
    by_ticker = {
        item["ticker"]: item
        for group in payload["groups"]
        for item in group["stocks"]
    }

    assert set(by_ticker) == {"AAPL", "MSFT", "NVDA"}
    assert by_ticker["NVDA"]["quote_delayed"] is True
    assert "quote_delayed" not in by_ticker["AAPL"]
    assert payload["delayed"] == 1
    assert payload["delayed_tickers"] == ["NVDA"]
    assert payload["data_through"] == "2026-07-13T20:00:00+00:00"
    assert payload["oldest_quote_at"] == payload["data_through"]
    assert payload["latest_quote_at"] == "2026-07-14T20:00:00+00:00"
    assert payload["source_status"] == "degraded"
    assert payload["data_limited"] is True


def test_watchlist_does_not_compare_foreign_exchange_or_premarket_dates(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("MSFT", "Close"),
        ("RMS.PA", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [
                    [195.0, float("nan"), float("nan")],
                    [float("nan"), 424.5, float("nan")],
                    [float("nan"), float("nan"), 2_420.0],
                ],
                index=pd.DatetimeIndex(
                    [
                        "2026-07-14 19:55",
                        "2026-07-15 04:05",
                        "2026-07-15 05:30",
                    ],
                    tz="America/New_York",
                ),
                columns=columns,
            )
        return pd.DataFrame(
            [
                [190.0, 420.0, 2_390.0],
                [192.0, 424.0, 2_410.0],
            ],
            index=pd.to_datetime(["2026-07-13", "2026-07-14"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "MSFT", "RMS.PA"]))

    assert payload["succeeded"] == 3
    assert payload["delayed"] == 0
    assert payload["delayed_tickers"] == []
    assert payload["data_through"] == "2026-07-14T23:55:00+00:00"
    assert payload["latest_quote_at"] == "2026-07-15T09:30:00+00:00"
    assert payload["source_status"] == "active"


def test_watchlist_uses_exchange_local_date_for_tokyo_quotes(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("7203.T", "Close"),
        ("^N225", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [[3_050.0, 42_250.0]],
                index=pd.DatetimeIndex(["2026-07-15 00:05"], tz="UTC"),
                columns=columns,
            )
        return pd.DataFrame(
            [[3_000.0, 42_000.0], [3_040.0, 42_200.0]],
            index=pd.to_datetime(["2026-07-14", "2026-07-15"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)
    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_closes",
        lambda tickers, *, session: {"^N225": 42_100.0},
    )

    payload = asyncio.run(stocks._build_watchlist(["7203.T", "^N225"]))
    by_ticker = {
        item["ticker"]: item
        for group in payload["groups"]
        for item in group["stocks"]
    }

    assert set(by_ticker) == {"7203.T", "^N225"}
    assert by_ticker["7203.T"]["price"] == 3_050.0
    assert by_ticker["7203.T"]["change"] == 50.0
    assert by_ticker["7203.T"]["quote_as_of"] == "2026-07-15T00:05:00+00:00"
    assert by_ticker["7203.T"]["quote_session"] == "exchange_session"
    assert by_ticker["7203.T"]["previous_close_source"] == "daily_close"
    assert by_ticker["^N225"]["change"] == 150.0
    assert by_ticker["^N225"]["previous_close_source"] == "provider_metadata"
    assert payload["succeeded"] == 2
    assert payload["source_status"] == "active"


def test_watchlist_treats_sunday_futures_quote_as_monday_daily_session(monkeypatch):
    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                {"Close": [5_550.0]},
                index=pd.DatetimeIndex(
                    ["2026-07-12 18:00"],
                    tz="America/Chicago",
                ),
            )
        return pd.DataFrame(
            {"Close": [5_500.0, 5_540.0]},
            index=pd.to_datetime(["2026-07-10", "2026-07-13"]),
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)
    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_closes",
        lambda tickers, *, session: {"ES=F": 5_525.0},
    )

    payload = asyncio.run(stocks._build_watchlist(["ES=F"]))
    item = payload["groups"][0]["stocks"][0]

    assert item["price"] == 5_550.0
    assert item["change"] == 25.0
    assert item["spark"] == [5_500.0, 5_550.0]
    assert item["quote_session"] == "exchange_session"
    assert item["previous_close_source"] == "provider_metadata"
    assert payload["source_status"] == "active"


def test_watchlist_rejects_previous_day_futures_close_before_new_session(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("ES=F", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [
                    [float("nan"), 5_530.0],
                    [193.0, float("nan")],
                ],
                index=pd.DatetimeIndex(
                    ["2026-07-14 21:00", "2026-07-15 19:55"],
                    tz="UTC",
                ),
                columns=columns,
            )
        return pd.DataFrame(
            [[190.0, 5_500.0], [192.0, 5_525.0]],
            index=pd.to_datetime(["2026-07-14", "2026-07-15"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)
    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_closes",
        lambda tickers, *, session: {"ES=F": 5_525.0},
    )

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "ES=F"]))
    returned = [
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    ]

    assert returned == ["AAPL"]
    assert payload["source_status"] == "degraded"
    assert payload["failed_tickers"] == ["ES=F"]


def test_watchlist_provider_previous_close_metadata_priority_and_validation(monkeypatch):
    metadata_by_ticker = {
        "^PRIMARY": {"previousClose": 101.5, "chartPreviousClose": 99.0},
        "^FALLBACK": {"previousClose": "bad", "chartPreviousClose": 88.25},
        "^INVALID": {"previousClose": float("nan"), "chartPreviousClose": 0},
    }

    class FakeInstrument:
        def __init__(self, ticker):
            self.ticker = ticker
            self.history_metadata = {}

        def history(self, **_kwargs):
            if self.ticker == "^ERROR":
                raise RuntimeError("provider error")
            self.history_metadata = metadata_by_ticker[self.ticker]

    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda ticker, session=None: FakeInstrument(ticker),
    )

    assert stocks._fetch_watchlist_provider_previous_close(
        "^PRIMARY",
        session=object(),
    ) == 101.5
    assert stocks._fetch_watchlist_provider_previous_close(
        "^FALLBACK",
        session=object(),
    ) == 88.25
    assert stocks._fetch_watchlist_provider_previous_close(
        "^INVALID",
        session=object(),
    ) is None
    assert stocks._fetch_watchlist_provider_previous_close(
        "^ERROR",
        session=object(),
    ) is None


def test_watchlist_provider_previous_close_batch_has_total_deadline(monkeypatch):
    symbols = [f"^DEADLINE{i}" for i in range(8)]
    gate = stocks.threading.Event()

    def blocked_fetch(ticker, *, session):
        gate.wait(timeout=1)
        return 100.0

    with stocks._watchlist_provider_close_lock:
        for ticker in symbols:
            stocks._watchlist_provider_close_cache.pop(ticker, None)
            stocks._watchlist_provider_close_inflight.pop(ticker, None)

    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_close",
        blocked_fetch,
    )
    monkeypatch.setattr(
        stocks,
        "_WATCHLIST_PROVIDER_CLOSE_BATCH_TIMEOUT_SECONDS",
        0.05,
    )

    started = time.monotonic()
    result = stocks._fetch_watchlist_provider_previous_closes(symbols, session=object())
    elapsed = time.monotonic() - started

    with stocks._watchlist_provider_close_lock:
        futures = list(stocks._watchlist_provider_close_inflight.values())
    gate.set()
    for future in futures:
        future.result(timeout=1)

    assert result == {}
    assert elapsed < 0.5
    assert len(futures) <= stocks._WATCHLIST_PROVIDER_CLOSE_WORKERS


def test_watchlist_provider_previous_close_frees_slots_before_delayed_callback(monkeypatch):
    symbols = [f"^CALLBACK{i}" for i in range(5)]
    release_fetches = stocks.threading.Event()
    original_cache_result = stocks._cache_watchlist_provider_previous_close

    def fetch_after_release(ticker, *, session):
        release_fetches.wait(timeout=1)
        return 100.0

    def delay_worker_callback(ticker, future):
        if stocks.threading.current_thread().name.startswith(
            "watchlist-previous-close"
        ):
            time.sleep(0.1)
        original_cache_result(ticker, future)

    with stocks._watchlist_provider_close_lock:
        for ticker in symbols:
            stocks._watchlist_provider_close_cache.pop(ticker, None)
            stocks._watchlist_provider_close_inflight.pop(ticker, None)

    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_close",
        fetch_after_release,
    )
    monkeypatch.setattr(
        stocks,
        "_cache_watchlist_provider_previous_close",
        delay_worker_callback,
    )
    timer = stocks.threading.Timer(0.05, release_fetches.set)
    timer.start()
    try:
        result = stocks._fetch_watchlist_provider_previous_closes(
            symbols,
            session=object(),
        )
    finally:
        release_fetches.set()
        timer.join(timeout=1)

    assert result == {ticker: 100.0 for ticker in symbols}


def test_watchlist_omits_index_when_provider_previous_close_is_unavailable(monkeypatch):
    columns = pd.MultiIndex.from_tuples([
        ("AAPL", "Close"),
        ("^N225", "Close"),
    ])

    def fake_download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame(
                [[193.0, 42_250.0]],
                index=pd.DatetimeIndex(["2026-07-15 20:05"], tz="UTC"),
                columns=columns,
            )
        return pd.DataFrame(
            [[190.0, 42_000.0], [192.0, 42_200.0]],
            index=pd.to_datetime(["2026-07-14", "2026-07-15"]),
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)
    monkeypatch.setattr(
        stocks,
        "_fetch_watchlist_provider_previous_closes",
        lambda tickers, *, session: {},
    )

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "^N225"]))
    returned = [
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    ]

    assert returned == ["AAPL"]
    assert payload["attempted"] == 2
    assert payload["succeeded"] == 1
    assert payload["source_status"] == "degraded"
    assert payload["failed_tickers"] == ["^N225"]
