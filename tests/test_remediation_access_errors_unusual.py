from __future__ import annotations

import asyncio
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.access import (
    bind_trusted_system_task,
    current_request_is_owner,
    request_owner_access_context,
)
from app.api import options
from app.services import yahoo
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import CatalystSyncTask
from tests.http_response_support import anonymous_get_request as _areq


@pytest.fixture(autouse=True)
def _clear_state() -> None:
    options._unusual_failure_deadlines.clear()
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()
    yield
    options._unusual_failure_deadlines.clear()
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()


def test_ac01_missing_context_is_not_owner() -> None:
    assert current_request_is_owner() is False


def test_ac02_verified_owner_context_is_true() -> None:
    with request_owner_access_context(True):
        assert current_request_is_owner() is True


def test_ac03_registered_and_anonymous_stay_non_owner() -> None:
    with request_owner_access_context(False):
        assert current_request_is_owner() is False


def test_ac05_nested_and_exception_restore_context() -> None:
    assert current_request_is_owner() is False
    try:
        with request_owner_access_context(True):
            assert current_request_is_owner() is True
            with request_owner_access_context(False):
                assert current_request_is_owner() is False
                raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert current_request_is_owner() is False


def test_ac06_thread_does_not_inherit_owner() -> None:
    seen: list[bool] = []

    def _probe() -> None:
        seen.append(current_request_is_owner())

    with request_owner_access_context(True):
        worker = threading.Thread(target=_probe)
        worker.start()
        worker.join()
        assert current_request_is_owner() is True
    assert seen == [False]


def test_ac07_visitor_without_snapshot_does_not_call_yahoo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("visitor must not cold-pull")

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", unexpected)
    monkeypatch.setattr(yahoo, "get_option_chain", unexpected)
    with request_owner_access_context(False), pytest.raises(HTTPException) as captured:
        asyncio.run(options.option_chain("AAPL", "2030-08-16"))
    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "public_snapshot_unavailable"
    assert calls == 0


def test_er01_bad_date_is_400_without_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def unexpected(_symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"expirations": ["2030-08-16"]}

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", unexpected)
    with request_owner_access_context(True), pytest.raises(HTTPException) as captured:
        asyncio.run(options.option_chain("AAPL", "2030-02-30"))
    assert captured.value.status_code == 400
    assert captured.value.detail["code"] == "invalid_option_expiration"
    assert calls == 0


def test_er03_provider_value_error_is_not_user_date_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        yahoo,
        "get_expirations_snapshot",
        lambda _symbol: {"expirations": ["2030-08-16"]},
    )

    def explode(_symbol: str, _expiration: str) -> dict:
        raise ValueError("yfinance could not parse option chain payload")

    monkeypatch.setattr(yahoo, "get_option_chain", explode)
    with request_owner_access_context(True), pytest.raises(HTTPException) as captured:
        asyncio.run(options.option_chain("AAPL", "2030-08-16"))
    assert captured.value.status_code == 503
    assert captured.value.detail["code"] == "yahoo_options_unavailable"
    assert "yfinance" not in str(captured.value.detail).lower() or "parse" not in str(
        captured.value.detail
    ).lower()


def test_er04_error_key_does_not_block_other_expiration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def expirations(_symbol: str) -> dict:
        return {"expirations": ["2030-08-16", "2030-08-23"]}

    def chain(symbol: str, expiration: str) -> dict:
        if expiration == "2030-08-16":
            raise ValueError("broken payload")
        return {
            "ticker": symbol,
            "expiration": expiration,
            "calls": [{"strike": 100.0}],
            "puts": [],
        }

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", expirations)
    monkeypatch.setattr(yahoo, "get_option_chain", chain)
    with request_owner_access_context(True):
        with pytest.raises(HTTPException) as failed:
            asyncio.run(options.option_chain("AAPL", "2030-08-16"))
        assert failed.value.status_code == 503
        payload = asyncio.run(options.option_chain("AAPL", "2030-08-23"))
    assert payload["expiration"] == "2030-08-23"


def test_er07_failure_cache_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(options, "_OPTION_FAILURE_MAX_KEYS", 3)
    for index in range(8):
        options._record_option_failure(
            f"options:chain:AAA:2030-01-0{index % 9 + 1}",
            status_code=503,
            detail={"code": "yahoo_options_unavailable", "message": "Yahoo/yfinance 期权数据暂不可用"},
        )
    assert len(options._option_failure_cache) <= 3


def test_fl01_quality_mid_premium_is_estimated_notional(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = SimpleNamespace(
        calls=pd.DataFrame(
            [
                {
                    "contractSymbol": "AAA-CALL",
                    "strike": 100.0,
                    "volume": 100,
                    "openInterest": 10,
                    "lastPrice": 9.0,
                    "bid": 1.9,
                    "ask": 2.1,
                    "impliedVolatility": 0.2,
                    "inTheMoney": False,
                }
            ]
        ),
        puts=pd.DataFrame(),
    )
    ticker = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: chain,
    )
    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: ticker)
    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))
    row = payload["results"][0]
    assert row["premium"] == 20_000.0
    assert row["premium_basis"] == "quality_mid"
    assert row["premium_kind"] == "estimated_notional"
    assert payload["premium_kind"] == "estimated_notional"
    assert payload["contract_multiplier"] == 100


def test_fl02_last_price_estimate_is_traceable(monkeypatch: pytest.MonkeyPatch) -> None:
    chain = SimpleNamespace(
        calls=pd.DataFrame(
            [
                {
                    "contractSymbol": "BBB-CALL",
                    "strike": 100.0,
                    "volume": 100,
                    "openInterest": 10,
                    "lastPrice": 3.5,
                    "bid": 0.0,
                    "ask": 0.0,
                    "impliedVolatility": 0.2,
                    "inTheMoney": False,
                }
            ]
        ),
        puts=pd.DataFrame(),
    )
    ticker = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: chain,
    )
    monkeypatch.setattr(options, "POPULAR_TICKERS", ["BBB"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: ticker)
    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))
    row = payload["results"][0]
    assert row["premium"] == 35_000.0
    assert row["premium_basis"] == "last_price"
    assert row["last_price"] == 3.5


def test_fl03_wide_spread_without_last_leaves_premium_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = SimpleNamespace(
        calls=pd.DataFrame(
            [
                {
                    "contractSymbol": "WIDE",
                    "strike": 100.0,
                    "volume": 100,
                    "openInterest": 10,
                    "lastPrice": float("nan"),
                    "bid": 1.0,
                    "ask": 8.0,
                    "impliedVolatility": 0.2,
                    "inTheMoney": False,
                }
            ]
        ),
        puts=pd.DataFrame(),
    )
    ticker = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: chain,
    )
    monkeypatch.setattr(options, "POPULAR_TICKERS", ["WIDE"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: ticker)
    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))
    assert payload["results"][0]["premium"] is None
    assert payload["results"][0]["premium_basis"] is None


def test_fl04_does_not_infer_opening_or_infinite_ratio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: SimpleNamespace(
            calls=pd.DataFrame(
                [
                    {
                        "contractSymbol": "C",
                        "strike": 100.0,
                        "volume": 5000,
                        "openInterest": 10,
                        "lastPrice": 2.0,
                        "bid": 1.9,
                        "ask": 2.1,
                        "impliedVolatility": 0.3,
                        "inTheMoney": False,
                    }
                ]
            ),
            puts=pd.DataFrame(),
        ),
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
    payload = yahoo.get_option_chain("OPEN", "2030-08-16")
    reasons = " ".join(payload["alerts"][0]["reasons"])
    assert "新仓" not in reasons
    assert "开仓" not in reasons or "无法" in reasons
    assert payload["alerts"][0]["direction"] is None
    assert payload["alerts"][0]["vol_oi_ratio"] == 500.0


def test_fl05_partial_scan_coverage_matches_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: SimpleNamespace(
            calls=pd.DataFrame(
                [
                    {
                        "contractSymbol": "GOOD",
                        "strike": 110.0,
                        "volume": 100,
                        "openInterest": 10,
                        "lastPrice": 2.5,
                        "bid": 2.4,
                        "ask": 2.6,
                        "impliedVolatility": 0.2,
                        "inTheMoney": False,
                    }
                ]
            ),
            puts=pd.DataFrame(),
        ),
    )

    def factory(symbol: str):
        if symbol == "BROKEN":
            raise RuntimeError("down")
        return good

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["GOOD", "BROKEN"])
    monkeypatch.setattr(options.yf, "Ticker", factory)
    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))
    assert payload["planned_tickers"] == 2
    assert payload["successful_tickers"] == 1
    assert payload["attempted"] == 2
    assert payload["succeeded"] == 1
    assert payload["failed_symbols"] == ["BROKEN"]
    assert payload["source_status"] == "degraded"
    assert payload["data_limited"] is True


def test_ac04_worker_task_gets_explicit_owner_context(tmp_path) -> None:
    seen: list[bool] = []

    async def runner() -> TaskResult:
        seen.append(current_request_is_owner())
        return TaskResult(status="idle")

    repository = WorkerStateRepository(tmp_path / "worker.db")
    repository.initialize()
    supervisor = WorkerSupervisor(
        repository,
        (TaskSpec(name="probe_owner", runner=runner, interval_seconds=60),),
        owner_id="test-owner",
        process_lock=ProcessFileLock(tmp_path / "worker.lock"),
    )
    asyncio.run(supervisor.run_once())
    assert seen == [True]
    assert current_request_is_owner() is False


def test_ac04_trusted_entry_restores_missing_context() -> None:
    @bind_trusted_system_task
    async def entry() -> str:
        assert current_request_is_owner() is True
        raise RuntimeError("trusted-entry-boom")

    assert current_request_is_owner() is False
    with pytest.raises(RuntimeError, match="trusted-entry-boom"):
        asyncio.run(entry())
    assert current_request_is_owner() is False


def _empty_etl_page(path: str) -> dict:
    as_of = "2026-07-16T00:00:00Z"
    payload = {
        "items": [],
        "has_more": False,
        "next_cursor": None,
        "next_updated_after": as_of,
        "next_after_sequence": 0,
        "watermark": {"sequence": 0, "as_of": as_of},
    }
    if path.endswith("/calendar"):
        payload["watermark"]["snapshot_token"] = None
        payload["data_through"] = None
        payload["is_stale"] = False
    return payload


def test_ac04_direct_catalyst_cli_entry_writes_without_outer_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    cache_path = tmp_path / "catalyst-cache.db"
    ai_path = tmp_path / "ai-jobs.db"
    seen_owner: list[bool] = []
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.fixture")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    from app.services.catalysts.local_intelligence import LocalCatalystIntelligence

    original_initialize = LocalCatalystIntelligence.initialize

    def _record_owner(self: LocalCatalystIntelligence) -> None:
        seen_owner.append(current_request_is_owner())
        return original_initialize(self)

    monkeypatch.setattr(LocalCatalystIntelligence, "initialize", _record_owner)
    task = CatalystSyncTask(
        "container-catalyst-smoke",
        settings=SimpleNamespace(
            internal_api_token=SecretStr("owner-token"),
            macrolens_url="https://macrolens.fixture",
            macrolens_ca_bundle="",
            macrolens_cache_db_path=cache_path,
            openai_job_db_path=ai_path,
            personal_etl_enabled=True,
        ),
        personal_config=SimpleNamespace(
            catalyst=SimpleNamespace(sync_seconds=37),
            features=SimpleNamespace(catalyst_mode="read"),
            ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
        ),
        etl_transport=httpx.MockTransport(handler),
    )
    assert current_request_is_owner() is False

    async def run() -> TaskResult:
        try:
            return await task()
        finally:
            await task.aclose()

    result = asyncio.run(run())
    assert current_request_is_owner() is False
    assert result.status == "idle"
    assert seen_owner
    assert all(seen_owner)
    with sqlite3.connect(cache_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "catalyst_local_schema" in tables
    assert "macrolens_etl_state" in tables
