from __future__ import annotations

import asyncio
import json
import time

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
    require_public_read_or_owner_access,
)
from app.api import earnings, market, options, sectors, signals, stocks, strength
from app.personal_config import AccessConfig
from app.services import signals as signal_service
from app.services import yahoo
from app.services.cache import cache


def _request(ip: str = "203.0.113.10") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "scheme": "https",
            "server": ("example.test", 443),
            "client": (ip, 12345),
        }
    )


def _watchlist_payload(label: str) -> dict:
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
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "failed_tickers": [],
        "data_limited": False,
        "source_status": "active",
    }


def _strength_payload_with_option_rows(rows: list[dict]) -> dict:
    parameters = {
        key: value
        for key, value in strength.DEFAULT_STRENGTH_SCAN_PARAMETERS.items()
        if key != "include_options"
    }
    return {
        "as_of": "2026-07-23T15:30:00+00:00",
        "params": parameters,
        "count": len(rows),
        "rows": rows,
        "results": rows,
    }


@pytest.fixture(autouse=True)
def _clear_process_caches(monkeypatch: pytest.MonkeyPatch):
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()
    stocks._endpoint_refresh_tasks.clear()
    stocks._endpoint_refresh_retry_after.clear()
    cache.clear()
    sectors._cache.clear()
    sectors._locks.clear()
    sectors._sector_iv_failure_deadlines.clear()
    sectors._public_sector_iv_recent.clear()
    with signal_service._cache_lock:
        signal_service._cache.clear()
    with yahoo._cache_lock:
        yahoo._cache.clear()
    monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)
    monkeypatch.setattr(stocks, "_watchlist_snapshot_observed", None)
    yield
    cache.clear()


async def _expect_unavailable(awaitable) -> HTTPException:
    with pytest.raises(HTTPException) as captured:
        await awaitable
    assert captured.value.status_code == 503
    return captured.value


def test_public_cold_cache_never_calls_market_data_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    def unexpected(name: str):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"public request called {name}")

        return fail

    def unexpected_async(name: str):
        async def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"public request called {name}")

        return fail

    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing-watchlist.json")
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_async("watchlist"))
    monkeypatch.setattr(stocks, "_fetch_company_logo", unexpected_async("logo"))
    monkeypatch.setattr(stocks, "_stock_overview_impl", unexpected_async("stock-overview"))
    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected_async("stock-chart"))
    monkeypatch.setattr(stocks.yf, "Ticker", unexpected("stock-search"))
    monkeypatch.setattr(options, "_unusual_activity_impl", unexpected_async("unusual-options"))
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected_async("earnings"))
    monkeypatch.setattr(market, "_build_indices", unexpected_async("market-indices"))
    monkeypatch.setattr(
        sectors,
        "_SECTOR_IV_SNAPSHOT_DIR",
        tmp_path / "missing-sector-iv",
    )
    monkeypatch.setattr(signals, "compute_market_signals", unexpected("market-signals"))
    monkeypatch.setattr(signals, "compute_stock_signals", unexpected("stock-signals"))
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", tmp_path / "missing-strength.json")
    monkeypatch.setattr(strength, "stock_strength", unexpected_async("stock-strength"))
    monkeypatch.setattr(strength, "sector_strength", unexpected_async("sector-strength"))
    monkeypatch.setattr(strength, "market_strength", unexpected_async("market-strength"))

    async def scenario() -> None:
        with request_owner_access_context(False):
            await _expect_unavailable(stocks.watchlist(None))
            selected = await stocks.watchlist("AAPL,MSFT")
            assert selected["groups"] == []
            assert selected["failed_tickers"] == ["AAPL", "MSFT"]
            assert selected["attempted"] == 2 and selected["succeeded"] == 0
            with pytest.raises(HTTPException) as captured:
                await stocks.search_stocks("ZZZZUNLISTED")
            assert captured.value.status_code == 503
            assert captured.value.detail["code"] == "stock_directory_unavailable"
            await _expect_unavailable(stocks.stock_signals("AAPL"))
            await _expect_unavailable(stocks.stock_logo("AAPL"))
            await _expect_unavailable(stocks.stock_overview("AAPL"))
            await _expect_unavailable(stocks.stock_chart("AAPL", "1d", "raw"))
            await _expect_unavailable(options.unusual_activity(_request(), type="all", min_vol_oi=1.0))
            await _expect_unavailable(earnings.upcoming_earnings(_request()))
            await _expect_unavailable(market.market_indices(_request()))
            await _expect_unavailable(signals.market_signals(_request()))
            await _expect_unavailable(signals.stock_signals("AAPL"))
            await _expect_unavailable(strength.market())
            await _expect_unavailable(strength.stock("AAPL", "balanced"))
            await _expect_unavailable(strength.sectors("3mo"))

    asyncio.run(scenario())

    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_public_watchlist_reads_persisted_snapshot_without_refresh_or_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 50_000.0
    snapshot_path = tmp_path / "watchlist.json"
    stocks._write_watchlist_snapshot(
        snapshot_path,
        payload=_watchlist_payload("saved"),
        saved_at=now - 600,
    )
    original = snapshot_path.read_bytes()
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(stocks.time, "time", lambda: now)

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("public watchlist must not refresh")

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("public watchlist must not write")

    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)
    monkeypatch.setattr(stocks, "_persist_watchlist_snapshot", unexpected_write)

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.watchlist(None)

    payload = asyncio.run(scenario())

    assert payload["groups"][0]["id"] == "saved"
    assert payload["_stale"] is True
    assert payload["stale_reason"] == "public_snapshot_only"
    assert snapshot_path.read_bytes() == original
    assert stocks._endpoint_refresh_tasks == {}


def test_public_expired_watchlist_never_refreshes_or_rewrites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 200_000.0
    snapshot_path = tmp_path / "watchlist.json"
    stocks._write_watchlist_snapshot(
        snapshot_path,
        payload=_watchlist_payload("expired"),
        saved_at=now - stocks._WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS - 1,
    )
    original = snapshot_path.read_bytes()
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(stocks.time, "time", lambda: now)

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("expired public snapshot must not refresh")

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("expired public snapshot must not write")

    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)
    monkeypatch.setattr(stocks, "_persist_watchlist_snapshot", unexpected_write)

    async def scenario() -> None:
        with request_owner_access_context(False):
            await _expect_unavailable(stocks.watchlist(None))

    asyncio.run(scenario())

    assert snapshot_path.read_bytes() == original
    assert stocks._endpoint_refresh_tasks == {}


def test_public_endpoints_never_read_the_owner_process_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """Identity is part of the cache scope: a visitor must not observe the
    owner's live-rebuild cache entries, and (with no published snapshot) the
    endpoints stay unavailable without ever calling a loader."""

    now = time.time()
    today = earnings._market_today()
    cache.set(f"earnings:upcoming:{today.isoformat()}", {"items": ["saved"]}, 60)
    cache.set("market:indices", {"indices": ["saved"]}, 60)
    cache.set(options._unusual_key("all", 1.0), {"items": ["saved"]}, 60)
    sector_id = next(iter(sectors.SECTORS))
    sectors._cache[f"iv:{sector_id}"] = (
        now + 60,
        now,
        {"sector_id": sector_id, "rankings": []},
    )

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", lambda *_args: pytest.fail("loader called"))
    monkeypatch.setattr(market, "_build_indices", lambda: pytest.fail("loader called"))
    monkeypatch.setattr(options, "_unusual_activity_impl", lambda *_args: pytest.fail("loader called"))
    monkeypatch.setattr(sectors, "_iv_ranking_payload", lambda *_args: pytest.fail("loader called"))
    # No published public-home snapshot on disk.
    from app import public_home_snapshot as phs
    from app.data_paths import get_data_paths

    missing = tmp_path / "missing-public-home.json"
    original_get = get_data_paths

    class _Paths:
        def __getattr__(self, name):
            if name == "public_home_snapshot":
                return missing
            return getattr(original_get(), name)

    monkeypatch.setattr(phs, "get_data_paths", lambda: _Paths())

    async def scenario() -> None:
        with request_owner_access_context(False):
            with pytest.raises(HTTPException) as earn_exc:
                await earnings.upcoming_earnings(_request())
            assert earn_exc.value.status_code == 503
            with pytest.raises(HTTPException) as idx_exc:
                await market.market_indices(_request())
            assert idx_exc.value.status_code == 503
            with pytest.raises(HTTPException) as unusual_exc:
                await options.unusual_activity(_request(), type="all", min_vol_oi=1.0)
            assert unusual_exc.value.status_code == 503
            # The sector IV process cache is deliberately shared: it only ever
            # holds data that is simultaneously published to disk.
            assert (
                await sectors.iv_ranking(sector_id, _request())
            )["sector_id"] == sector_id

    asyncio.run(scenario())


def test_sector_iv_reads_persisted_strength_worker_options_without_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 1_790_000_000.0
    path = tmp_path / "strength-snapshot-v1.json"
    rows = [
        {
            "ticker": "NVDA",
            "name": "英伟达",
            "price": 172.25,
            "option_context": {
                "provider": "Yahoo/yfinance",
                "source_status": "active",
                "atm_iv_percent": 48.0,
                "as_of": "2026-07-23T15:29:00+00:00",
            },
        },
        {
            "ticker": "AMD",
            "name": "超威半导体",
            "price": 161.5,
            "option_context": {
                "provider": "Yahoo/yfinance",
                "source_status": "active",
                "atm_iv_percent": 32.0,
                "as_of": "2026-07-23T15:28:00+00:00",
            },
        },
        {
            "ticker": "MSFT",
            "name": "微软",
            "price": 501.0,
            "option_context": {
                "provider": "Yahoo/yfinance",
                "source_status": "active",
                "atm_iv_percent": 25.0,
                "as_of": "2026-07-23T15:27:00+00:00",
            },
        },
    ]
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_strength_payload_with_option_rows(rows),
        saved_at=now - 300,
    )
    original = path.read_bytes()
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(
        sectors,
        "_SECTOR_IV_SNAPSHOT_DIR",
        tmp_path / "missing-sector-iv",
    )
    monkeypatch.setattr(sectors.time, "time", lambda: now)
    calls = 0

    async def unexpected_scan(_sector_id: str) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("persisted worker snapshot must suppress provider scan")

    monkeypatch.setattr(sectors, "_iv_ranking_payload", unexpected_scan)

    async def scenario() -> tuple[dict, dict, dict]:
        with request_owner_access_context(False):
            public_payload = await sectors.iv_ranking("semiconductors", _request())
            heatmap_payload = await sectors.heatmap("semiconductors", _request())
        with request_owner_access_context(True):
            owner_payload = await sectors.iv_ranking("software", _request())
        return public_payload, heatmap_payload, owner_payload

    public_payload, heatmap_payload, owner_payload = asyncio.run(scenario())

    assert calls == 0
    assert public_payload["snapshot_source"] == "strength_worker"
    assert public_payload["_cached"] is True
    assert public_payload["_stale"] is False
    assert public_payload["source_status"] == "degraded"
    assert public_payload["success_count"] == 2
    assert public_payload["requested_count"] == len(
        sectors.SECTORS["semiconductors"]["tickers"]
    )
    assert [item["ticker"] for item in public_payload["rankings"]] == [
        "NVDA",
        "AMD",
    ]
    assert [item["sector_iv_rank"] for item in public_payload["rankings"]] == [
        100.0,
        0.0,
    ]
    assert public_payload["providers"] == ["Yahoo/yfinance"]
    assert heatmap_payload["data"] == heatmap_payload["rankings"]
    assert heatmap_payload["data"][0]["ticker"] == "NVDA"
    assert owner_payload["rankings"][0]["ticker"] == "MSFT"
    # 单个样本没有可辩护的板块内分位——绝对 IV 照常给出，分位留空。
    assert owner_payload["rankings"][0]["sector_iv_rank"] is None
    assert owner_payload["rankings"][0]["atm_iv_percent"] == 25.0
    assert path.read_bytes() == original


def test_sector_iv_marks_expired_strength_worker_snapshot_stale(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 1_790_000_000.0
    path = tmp_path / "strength-snapshot-v1.json"
    row = {
        "ticker": "NVDA",
        "price": 170.0,
        "option_context": {
            "provider": "Yahoo/yfinance",
            "source_status": "active",
            "atm_iv_percent": 45.0,
        },
    }
    saved_at = now - strength.STRENGTH_CACHE_TTL_SECONDS - 60
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_strength_payload_with_option_rows([row]),
        saved_at=saved_at,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(
        sectors,
        "_SECTOR_IV_SNAPSHOT_DIR",
        tmp_path / "missing-sector-iv",
    )
    monkeypatch.setattr(sectors.time, "time", lambda: now)

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await sectors.iv_ranking("semiconductors", _request())

    payload = asyncio.run(scenario())

    assert payload["_stale"] is True
    assert payload["source_status"] == "stale"
    assert payload["stale_reason"] == "strength_worker_snapshot_expired"
    assert payload["stale_age_seconds"] == pytest.approx(now - saved_at)


def test_sector_iv_rejects_non_provider_option_placeholders(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 1_790_000_000.0
    path = tmp_path / "strength-snapshot-v1.json"
    row = {
        "ticker": "NVDA",
        "price": 170.0,
        "option_context": {
            "provider": "Yahoo/yfinance",
            "status": "skipped",
            "atm_iv_percent": 99.0,
        },
    }
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_strength_payload_with_option_rows([row]),
        saved_at=now - 60,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(
        sectors,
        "_SECTOR_IV_SNAPSHOT_DIR",
        tmp_path / "missing-sector-iv",
    )
    monkeypatch.setattr(sectors.time, "time", lambda: now)

    calls = 0

    async def unavailable_scan(_sector_id: str) -> dict:
        nonlocal calls
        calls += 1
        return {
            "sector_id": "semiconductors",
            "rankings": [],
            "source_status": "insufficient_data",
        }

    monkeypatch.setattr(sectors, "_iv_ranking_payload", unavailable_scan)
    # 占位数据被拒后访客要能走到实扫，需要 owner 打开 visitor_live_pulls。
    monkeypatch.setattr(sectors, "request_allows_visitor_live_pulls", lambda _r: True)

    async def scenario() -> list[HTTPException]:
        with request_owner_access_context(False):
            return [
                await _expect_unavailable(
                    sectors.iv_ranking("semiconductors", _request())
                )
                for _ in range(2)
            ]

    failures = asyncio.run(scenario())

    assert calls == 1
    assert all(
        failure.detail["code"] == "sector_iv_cooldown"
        and int(failure.headers["Retry-After"]) > 0
        for failure in failures
    )


def test_owner_cold_sector_scan_persists_for_public_restart_without_strength_hit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 1_790_000_000.0
    strength_path = tmp_path / "strength-snapshot-v1.json"
    strength_row = {
        "ticker": "PSX",
        "price": 150.0,
        "option_context": {
            "provider": "Yahoo/yfinance",
            "source_status": "active",
            "atm_iv_percent": 28.0,
        },
    }
    strength._write_strength_snapshot(
        strength_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_strength_payload_with_option_rows([strength_row]),
        saved_at=now - 60,
    )
    snapshot_dir = tmp_path / "sector-iv-snapshots-v1"
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", strength_path)
    monkeypatch.setattr(sectors, "_SECTOR_IV_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(sectors.time, "time", lambda: now)
    calls = 0

    async def live_rows(sector_id: str) -> list[dict]:
        nonlocal calls
        calls += 1
        assert sector_id == "semiconductors"
        return [
            {
                "ticker": ticker,
                "name": ticker,
                "price": 170.0 if ticker == "NVDA" else 160.0,
                "iv": 0.48 if ticker == "NVDA" else 0.32,
                "_stale": False,
                "as_of": "2026-07-23T15:40:00+00:00",
                "source_status": "active",
                "provider": "Yahoo/yfinance",
            }
            if ticker in {"NVDA", "AMD"}
            else {
                "ticker": ticker,
                "iv": None,
                "source_status": "insufficient_data",
            }
            for ticker in sectors.SECTORS[sector_id]["tickers"]
        ]

    monkeypatch.setattr(sectors, "_sector_iv_rows", live_rows)

    async def owner_scan() -> dict:
        with request_owner_access_context(True):
            return await sectors.iv_ranking("semiconductors", _request())

    owner_payload = asyncio.run(owner_scan())
    snapshot_path = snapshot_dir / "semiconductors.json"

    assert calls == 1
    assert owner_payload["snapshot_source"] == "owner_live"
    assert owner_payload["snapshot_origin"] == "owner_live"
    assert owner_payload["snapshot_persisted"] is True
    assert owner_payload["providers"] == ["Yahoo/yfinance"]
    assert [row["ticker"] for row in owner_payload["rankings"]] == [
        "NVDA",
        "AMD",
    ]
    assert snapshot_path.is_file()
    assert json.loads(snapshot_path.read_text())["snapshot_origin"] == "owner_live"
    original = snapshot_path.read_bytes()

    # 模拟发布重启：进程内缓存清空，Strength Top20 仍没有半导体。
    sectors._cache.clear()
    sectors._locks.clear()

    async def unexpected_live_rows(_sector_id: str) -> list[dict]:
        raise AssertionError("public restart read must not scan providers")

    monkeypatch.setattr(sectors, "_sector_iv_rows", unexpected_live_rows)

    async def public_read() -> dict:
        with request_owner_access_context(False):
            return await sectors.iv_ranking("semiconductors", _request())

    public_payload = asyncio.run(public_read())

    assert calls == 1
    assert public_payload["snapshot_source"] == "sector_snapshot"
    assert public_payload["snapshot_origin"] == "owner_live"
    assert public_payload["_cached"] is True
    assert public_payload["_stale"] is False
    assert [row["ticker"] for row in public_payload["rankings"]] == [
        "NVDA",
        "AMD",
    ]
    assert snapshot_path.read_bytes() == original


def test_public_cold_sector_iv_uses_yahoo_and_persists_restart_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sector_id = "public-cold-sector"
    monkeypatch.setitem(
        sectors.SECTORS,
        sector_id,
        {"name": "Public Cold Sector", "tickers": ["AAA", "BBB"]},
    )
    # 冷启动实扫是 visitor_live_pulls 开启后的行为；默认关闭时访客得到
    # public_snapshot_unavailable（见 test_visitor_action_boundaries）。
    monkeypatch.setattr(sectors, "request_allows_visitor_live_pulls", lambda _r: True)
    snapshot_dir = tmp_path / "sector-iv-snapshots-v1"
    monkeypatch.setattr(sectors, "_SECTOR_IV_SNAPSHOT_DIR", snapshot_dir)
    monkeypatch.setattr(
        strength,
        "_STRENGTH_SNAPSHOT_PATH",
        tmp_path / "missing-strength.json",
    )
    calls = 0

    async def live_rows(requested_sector_id: str) -> list[dict]:
        nonlocal calls
        calls += 1
        assert requested_sector_id == sector_id
        return [
            {
                "ticker": "AAA",
                "name": "AAA",
                "price": 100.0,
                "iv": 0.25,
                "_stale": False,
                "as_of": "2026-07-24T05:00:00+00:00",
                "source_status": "active",
                "provider": "Yahoo/yfinance",
            },
            {
                "ticker": "BBB",
                "name": "BBB",
                "price": 200.0,
                "iv": 0.5,
                "_stale": False,
                "as_of": "2026-07-24T05:00:00+00:00",
                "source_status": "active",
                "provider": "Yahoo/yfinance",
            },
        ]

    monkeypatch.setattr(sectors, "_sector_iv_rows", live_rows)

    async def public_read() -> dict:
        with request_owner_access_context(False):
            return await sectors.iv_ranking(sector_id, _request())

    first = asyncio.run(public_read())
    snapshot_path = snapshot_dir / f"{sector_id}.json"

    assert calls == 1
    assert first["snapshot_source"] == "public_live"
    assert first["snapshot_origin"] == "public_live"
    assert first["snapshot_persisted"] is True
    assert first["providers"] == ["Yahoo/yfinance"]
    assert [row["ticker"] for row in first["rankings"]] == ["BBB", "AAA"]
    assert snapshot_path.is_file()
    assert json.loads(snapshot_path.read_text())["snapshot_origin"] == "public_live"
    original = snapshot_path.read_bytes()

    sectors._cache.clear()
    sectors._locks.clear()

    async def unexpected_live_rows(_sector_id: str) -> list[dict]:
        raise AssertionError("durable public IV snapshot must suppress a repeat scan")

    monkeypatch.setattr(sectors, "_sector_iv_rows", unexpected_live_rows)
    second = asyncio.run(public_read())

    assert calls == 1
    assert second["snapshot_source"] == "sector_snapshot"
    assert second["snapshot_origin"] == "public_live"
    assert second["_cached"] is True
    assert second["_stale"] is False
    assert snapshot_path.read_bytes() == original


def test_request_owner_context_is_task_local_and_restored() -> None:
    async def observe(owner: bool) -> bool:
        from app.access import current_request_is_owner

        with request_owner_access_context(owner):
            await asyncio.sleep(0)
            return current_request_is_owner()

    async def scenario() -> list[bool]:
        return list(await asyncio.gather(observe(False), observe(True)))

    assert asyncio.run(scenario()) == [False, True]


def test_directly_mounted_public_router_keeps_cold_cache_snapshot_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    async def unexpected_refresh(*_args, **_kwargs):
        calls.append("watchlist")
        raise AssertionError("anonymous request called the watchlist provider")

    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)

    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("public-boundary-test-password"),
    )
    app.include_router(
        stocks.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/stocks/watchlist")
        head = client.head("/api/stocks/watchlist")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "public_snapshot_unavailable"
    assert head.status_code in {405, 503}
    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_guest_index_overview_falls_back_to_indices_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """指数码（/market 指数卡点开）的访客行情头吃纸带 indices 快照。

    手动拉取的 overview 快照只有 24h；过期后指数页不该退回整页空态，
    纸带那份对访客常绿的快照足以保住页面准入。
    """

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    async def fake_read(resource, *, parameters=None, now=None):
        if resource == "indices":
            return {
                "indices": [
                    {"symbol": "^GSPC", "price": 7709.96, "change_percent": -0.18},
                    {"symbol": "^N225", "price": None, "change_percent": None},
                ],
                "as_of": "2026-08-07T07:00:00+00:00",
            }
        return None

    monkeypatch.setattr(stocks, "read_public_home_resource_async", fake_read)

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.stock_overview("^gspc")

    payload = asyncio.run(scenario())
    assert payload["ticker"] == "^GSPC"
    assert payload["price"] == 7709.96
    assert payload["snapshot_source"] == "indices"
    assert payload["prev_close"] == pytest.approx(7709.96 / (1 - 0.0018), abs=0.01)
    assert payload["as_of"] == "2026-08-07T07:00:00+00:00"


def test_guest_index_overview_rejects_unusable_snapshot_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """快照里价格为 None 的指数、以及非指数码，仍保持 503 边界。"""

    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    async def fake_read(resource, *, parameters=None, now=None):
        if resource == "indices":
            return {
                "indices": [{"symbol": "^N225", "price": None, "change_percent": None}],
                "as_of": "2026-08-07T07:00:00+00:00",
            }
        return None

    monkeypatch.setattr(stocks, "read_public_home_resource_async", fake_read)

    async def scenario() -> None:
        with request_owner_access_context(False):
            # 快照行存在但无可用价格 → 拒绝拼头，维持诚实 503
            await _expect_unavailable(stocks.stock_overview("^N225"))
            # 普通股票代码不走指数回退
            await _expect_unavailable(stocks.stock_overview("AAPL"))

    asyncio.run(scenario())
