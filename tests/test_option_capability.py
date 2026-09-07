"""INDEX-OPTIONS-01: provider capability, zero outbound, and process-local I/O budget."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OWNER_COOKIE_NAME,
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
)
from app.api import options
from app import main
from app.personal_config import AccessConfig
from app.public_home_snapshot import PUBLIC_HOME_INDEX_SYMBOLS
from app.services import option_capability as capability
from app.services import signals, yahoo
from app.services.option_capability import (
    CAPABILITY_VERSION,
    YAHOO_UNSUPPORTED_QUOTE_SYMBOLS,
    canonicalize_option_symbol,
    is_declared_unsupported,
    resolve_option_capability,
    unsupported_payload,
)
from app.services.yahoo_option_io import (
    YahooOptionBusy,
    YahooOptionIO,
    YahooOptionTimeout,
    reset_yahoo_option_io,
)


ROOT = Path(__file__).resolve().parents[1]
UNSUPPORTED_ALIASES = [
    ("SPX", "^GSPC"),
    ("^SPX", "^GSPC"),
    ("GSPC", "^GSPC"),
    ("^GSPC", "^GSPC"),
    ("IXIC", "^IXIC"),
    ("^IXIC", "^IXIC"),
    ("DJI", "^DJI"),
    ("^DJI", "^DJI"),
    ("N225", "^N225"),
    ("^N225", "^N225"),
    ("SSE", "000001.SS"),
    ("000001.SS", "000001.SS"),
]


@pytest.fixture(autouse=True)
def _isolate_option_state():
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    if hasattr(yahoo, "_key_lock_users"):
        yahoo._key_lock_users.clear()
    options.cache.clear()
    options._option_failure_cache.clear()
    reset_yahoo_option_io(
        YahooOptionIO(
            max_in_flight=3,
            max_queue=8,
            queue_wait_seconds=1.0,
            call_timeout_seconds=2.0,
        )
    )
    yield
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    if hasattr(yahoo, "_key_lock_users"):
        yahoo._key_lock_users.clear()
    options.cache.clear()
    options._option_failure_cache.clear()
    reset_yahoo_option_io()


def _forbidden_ticker(symbol: str):
    raise AssertionError(f"provider ticker constructed for {symbol}")


def _flat_history(size: int = 30) -> pd.DataFrame:
    close = pd.Series([100.0 + index * 0.1 for index in range(size)])
    return pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": pd.Series([1_000_000.0] * size),
        }
    )


def _runtime(*, visitor_live_pulls: bool = False) -> OwnerAccessRuntime:
    return OwnerAccessRuntime(
        AccessConfig(mode="password", visitor_live_pulls=visitor_live_pulls),
        password_hash=hash_owner_password("option-capability-test-password"),
    )


def _option_app(runtime):
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(
        options.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )
    app.add_middleware(main._GatewayMiddleware, access_runtime=runtime)
    return app


def _owner_client(runtime):
    client = TestClient(_option_app(runtime), base_url="https://testserver")
    session = runtime.login("option-capability-test-password", client_key="test-owner")
    client.cookies.set(OWNER_COOKIE_NAME, session.session_token)
    return client


def test_capability_table_matches_frontend_and_home_indices():
    frontend = (ROOT / "frontend-src/src/lib/optionCapability.ts").read_text()
    version = re.search(r"OPTION_CAPABILITY_VERSION = '([^']+)'", frontend)
    symbols = re.findall(
        r"YAHOO_UNSUPPORTED_QUOTE_SYMBOLS = \[([^\]]+)\]",
        frontend,
        flags=re.S,
    )
    assert version is not None and symbols
    frontend_symbols = {
        item.strip().strip("'\"")
        for item in symbols[0].split(",")
        if item.strip().strip("'\"")
    }
    assert version.group(1) == CAPABILITY_VERSION
    assert frontend_symbols == set(YAHOO_UNSUPPORTED_QUOTE_SYMBOLS)
    assert set(PUBLIC_HOME_INDEX_SYMBOLS) == set(YAHOO_UNSUPPORTED_QUOTE_SYMBOLS)
    assert "^NDX" not in YAHOO_UNSUPPORTED_QUOTE_SYMBOLS
    assert "^RUT" not in YAHOO_UNSUPPORTED_QUOTE_SYMBOLS
    assert "^VIX" not in YAHOO_UNSUPPORTED_QUOTE_SYMBOLS


@pytest.mark.parametrize("alias,canonical", UNSUPPORTED_ALIASES)
def test_declared_unsupported_aliases_are_canonical(alias, canonical):
    resolved = resolve_option_capability(alias)
    assert canonicalize_option_symbol(alias) == canonical
    assert resolved.ticker == canonical
    assert resolved.options_status == "unsupported_by_provider"
    assert resolved.retryable is False
    assert is_declared_unsupported(alias)


def test_caret_prefix_is_not_a_blanket_ban():
    for symbol in ("^NDX", "^RUT", "^VIX", "AAPL", "SPY"):
        resolved = resolve_option_capability(symbol)
        assert resolved.options_status == "unknown"
        assert resolved.retryable is True


def test_unsupported_payload_is_stable_business_state():
    payload = unsupported_payload("SPX")
    assert payload["ticker"] == "^GSPC"
    assert payload["options_status"] == "unsupported_by_provider"
    assert payload["expirations"] == []
    assert payload["retryable"] is False
    assert payload["capability_version"] == CAPABILITY_VERSION
    assert "yfinance" in payload["provider"]


def test_known_unsupported_has_zero_provider_outbound(monkeypatch):
    monkeypatch.setattr(yahoo, "_get_ticker", _forbidden_ticker)
    monkeypatch.setattr(yahoo.yf, "Ticker", _forbidden_ticker)

    for alias, canonical in UNSUPPORTED_ALIASES:
        snapshot = yahoo.get_expirations_snapshot(alias)
        assert snapshot["expirations"] == []
        assert snapshot["options_status"] == "unsupported_by_provider"
        assert snapshot["retryable"] is False
        assert snapshot["ticker"] == canonical
        assert yahoo.get_expirations(alias) == []
        assert yahoo.get_stock_iv(alias) is None
        iv_snapshot = yahoo.get_stock_iv_snapshot(alias)
        assert iv_snapshot["atm_iv"] is None
        with pytest.raises(capability.UnsupportedOptionsError):
            yahoo.get_option_chain(alias, "2030-08-16")

        history = _flat_history()
        computed = signals.compute_stock_signals_from_history(
            alias,
            history,
            spy_history=history,
            include_options=True,
        )
        assert computed["atm_iv_percent"]["value"] is None
        assert computed["close_position"]["value"] is not None


def test_http_bypass_still_returns_unsupported_without_provider(monkeypatch):
    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", _forbidden_ticker)
    monkeypatch.setattr(options.yahoo, "get_option_chain", _forbidden_ticker)
    monkeypatch.setattr(yahoo, "_get_ticker", _forbidden_ticker)
    runtime = _runtime()
    with _owner_client(runtime) as client:
        for path in (
            "/api/options/SPX/expirations",
            "/api/options/%5EGSPC/expirations",
            "/api/options/000001.SS/expirations",
        ):
            response = client.get(path)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["options_status"] == "unsupported_by_provider"
            assert body["expirations"] == []
            assert body["retryable"] is False

        chain = client.get("/api/options/%5EGSPC/chain?expiration=2030-08-16")
        assert chain.status_code == 200
        payload = chain.json()
        assert payload["calls"] == []
        assert payload["puts"] == []
        assert payload["options_status"] == "unsupported_by_provider"

        invalid = client.get("/api/options/AAOI%2F..%2FAAPL/expirations")
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["code"] == "invalid_ticker"


def test_same_key_coalesces_success_empty_and_error(monkeypatch):
    calls = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    class SuccessTicker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        @property
        def options(self):
            with lock:
                calls.append(("success", self.symbol))
            time.sleep(0.05)
            return ["2030-08-16"]

    monkeypatch.setattr(yahoo, "_get_ticker", lambda symbol: SuccessTicker(symbol))

    def run(_index: int):
        start.wait()
        return yahoo.get_expirations_snapshot("AAPL")

    with ThreadPoolExecutor(max_workers=20) as executor:
        results = list(executor.map(run, range(20)))

    assert calls == [("success", "AAPL")]
    assert all(row["expirations"] == ["2030-08-16"] for row in results)
    assert all(row["options_status"] == "supported" for row in results)

    yahoo._cache.clear()
    calls.clear()

    class EmptyTicker:
        @property
        def options(self):
            with lock:
                calls.append("empty")
            time.sleep(0.03)
            return []

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: EmptyTicker())
    start_empty = threading.Barrier(12)

    def run_empty(_index: int):
        start_empty.wait()
        return yahoo.get_expirations_snapshot("MSFT")

    with ThreadPoolExecutor(max_workers=12) as executor:
        empty_results = list(executor.map(run_empty, range(12)))

    assert calls == ["empty"]
    assert all(row["expirations"] == [] for row in empty_results)
    assert all(row["options_status"] == "empty_unconfirmed" for row in empty_results)
    assert all(row["retryable"] is True for row in empty_results)

    yahoo._cache.clear()
    calls.clear()

    class BoomTicker:
        @property
        def options(self):
            with lock:
                calls.append("boom")
            time.sleep(0.02)
            raise TimeoutError("provider timeout")

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: BoomTicker())
    start_boom = threading.Barrier(8)

    def run_boom(_index: int):
        start_boom.wait()
        with pytest.raises(TimeoutError):
            yahoo.get_expirations_snapshot("NVDA")

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(run_boom, range(8)))

    assert calls == ["boom"]


def test_cross_ticker_outbound_budget_and_queue(monkeypatch):
    gate = YahooOptionIO(
        max_in_flight=2,
        max_queue=3,
        queue_wait_seconds=0.2,
        call_timeout_seconds=2.0,
    )
    entered = threading.Event()
    release = threading.Event()
    in_flight_seen: list[int] = []

    def block():
        with gate._lock:
            in_flight_seen.append(gate._in_flight)
        entered.set()
        if not release.wait(2):
            raise TimeoutError("test release missed")
        return ["2030-08-16"]

    started = []

    def worker(index: int):
        try:
            started.append(index)
            return gate.run(block)
        except YahooOptionBusy:
            return "busy"

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(worker, index) for index in range(6)]
        assert entered.wait(1)
        time.sleep(0.05)
        stats = gate.stats()
        assert stats["in_flight"] <= 2
        assert stats["peak_in_flight"] <= 2
        assert stats["queued"] + stats["in_flight"] <= 2 + 3
        release.set()
        results = [future.result() for future in futures]

    assert results.count("busy") >= 1
    assert any(result == ["2030-08-16"] for result in results)
    assert max(in_flight_seen) <= 2


def test_iv_reuses_expiration_discovery(monkeypatch):
    options_calls = []
    chain_calls = []

    class SharedTicker:
        def __init__(self, symbol: str):
            self.symbol = symbol

        @property
        def options(self):
            options_calls.append(self.symbol)
            return ["2030-09-18"]

        @property
        def fast_info(self):
            return SimpleNamespace(last_price=100.0)

        def option_chain(self, expiration: str):
            chain_calls.append((self.symbol, expiration))
            return SimpleNamespace(
                calls=pd.DataFrame(
                    [
                        {
                            "strike": 100.0,
                            "impliedVolatility": 0.32,
                            "lastPrice": 1.5,
                            "bid": 1.4,
                            "ask": 1.6,
                            "volume": 10,
                            "openInterest": 20,
                            "inTheMoney": True,
                        }
                    ]
                ),
                puts=pd.DataFrame(),
            )

    monkeypatch.setattr(yahoo, "_get_ticker", lambda symbol: SharedTicker(symbol))
    snapshot = yahoo.get_expirations_snapshot("AAPL")
    iv = yahoo.get_stock_iv("AAPL")
    assert snapshot["expirations"] == ["2030-09-18"]
    assert snapshot["options_status"] == "supported"
    assert iv == 0.32
    assert options_calls == ["AAPL"]
    assert chain_calls == [("AAPL", "2030-09-18")]


def test_time_advance_does_not_autostart_refresh(monkeypatch):
    calls = []

    class EmptyThenDates:
        def __init__(self, symbol: str):
            self.symbol = symbol

        @property
        def options(self):
            calls.append(self.symbol)
            return []

    monkeypatch.setattr(yahoo, "_get_ticker", lambda symbol: EmptyThenDates(symbol))
    monkeypatch.setattr(
        yahoo,
        "get_settings",
        lambda: SimpleNamespace(option_empty_discovery_seconds=2),
    )
    first = yahoo.get_expirations_snapshot("TSLA")
    assert first["options_status"] == "empty_unconfirmed"
    assert calls == ["TSLA"]

    time.sleep(0.05)
    second = yahoo.get_expirations_snapshot("TSLA")
    assert second["expirations"] == []
    assert calls == ["TSLA"]

    now = datetime.now(timezone.utc)
    yahoo._cache["expirations:TSLA"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=3),
        [],
    )
    third = yahoo.get_expirations_snapshot("TSLA")
    assert third["options_status"] == "empty_unconfirmed"
    assert calls == ["TSLA", "TSLA"]

    monkeypatch.setattr(yahoo, "_get_ticker", _forbidden_ticker)
    later = yahoo.get_expirations_snapshot("^GSPC")
    assert later["options_status"] == "unsupported_by_provider"


def test_empty_list_is_recoverable_after_ttl(monkeypatch):
    calls = []
    payload = {"AAPL": []}

    class FlipTicker:
        @property
        def options(self):
            calls.append("load")
            return list(payload["AAPL"])

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: FlipTicker())
    monkeypatch.setattr(
        yahoo,
        "get_settings",
        lambda: SimpleNamespace(option_empty_discovery_seconds=1),
    )
    empty = yahoo.get_expirations_snapshot("AAPL")
    assert empty["options_status"] == "empty_unconfirmed"
    payload["AAPL"] = ["2030-12-18"]
    now = datetime.now(timezone.utc)
    yahoo._cache["expirations:AAPL"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=2),
        [],
    )
    recovered = yahoo.get_expirations_snapshot("AAPL")
    assert recovered["expirations"] == ["2030-12-18"]
    assert recovered["options_status"] == "supported"
    assert calls == ["load", "load"]


def test_capability_declaration_change_is_recoverable(monkeypatch):
    monkeypatch.setattr(capability, "YAHOO_UNSUPPORTED_QUOTE_SYMBOLS", frozenset())
    monkeypatch.setattr(yahoo, "is_declared_unsupported", lambda _symbol, **_kw: False)
    monkeypatch.setattr(yahoo, "resolve_option_capability", capability.resolve_option_capability)

    class RecoverTicker:
        @property
        def options(self):
            return ["2030-08-16"]

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: RecoverTicker())
    snapshot = yahoo.get_expirations_snapshot("^GSPC")
    assert snapshot["options_status"] == "supported"
    assert snapshot["expirations"] == ["2030-08-16"]


def test_visitor_cannot_probe_unknown_but_can_read_declared_unsupported(monkeypatch):
    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", _forbidden_ticker)
    monkeypatch.setattr(options.yahoo, "get_option_chain", _forbidden_ticker)
    runtime = _runtime(visitor_live_pulls=False)
    with TestClient(_option_app(runtime), base_url="https://testserver") as client:
        denied = client.get("/api/options/AAPL/expirations")
        assert denied.status_code == 503
        assert denied.json()["detail"]["code"] == "public_snapshot_unavailable"

        allowed = client.get("/api/options/%5EGSPC/expirations")
        assert allowed.status_code == 200
        assert allowed.json()["options_status"] == "unsupported_by_provider"
        assert allowed.json()["retryable"] is False


def test_waiter_timeout_keeps_in_flight_slot():
    gate = YahooOptionIO(
        max_in_flight=1,
        max_queue=2,
        queue_wait_seconds=0.2,
        call_timeout_seconds=0.1,
    )
    started = threading.Event()
    finish = threading.Event()

    def slow():
        started.set()
        finish.wait(2)
        return "done"

    with pytest.raises(YahooOptionTimeout):
        gate.run(slow)
    assert started.wait(1)
    stats = gate.stats()
    assert stats["in_flight"] == 1
    assert stats["timed_out_waiters"] == 1
    assert stats["completed"] == 0
    finish.set()
    time.sleep(0.05)
    later = gate.stats()
    assert later["in_flight"] == 0
    assert later["completed"] == 1


def test_process_local_budget_is_documented():
    text = (ROOT / "backend/app/services/yahoo_option_io.py").read_text()
    assert "per process" in text
    assert "cluster-wide" in text or "worker count" in text
    assert "This budget is per process" in text


def test_http_same_key_coalesces_through_route(monkeypatch):
    from app.access import request_owner_access_context

    calls = []
    started = threading.Event()
    release = threading.Event()

    def slow_snapshot(symbol: str):
        calls.append(symbol)
        started.set()
        release.wait(2)
        return {
            "expirations": ["2030-08-16"],
            "options_status": "supported",
            "retryable": False,
            "ticker": symbol,
            "provider": "Yahoo/yfinance",
        }

    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", slow_snapshot)

    async def run_requests():
        with request_owner_access_context(True):
            return await asyncio.gather(*[options.expirations("aapl") for _ in range(8)])

    box: dict[str, list] = {}

    def worker() -> None:
        box["result"] = asyncio.run(run_requests())

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    assert started.wait(1)
    time.sleep(0.05)
    assert calls == ["AAPL"]
    release.set()
    thread.join(2)
    results = box["result"]
    assert len(results) == 8
    assert all(row["expirations"] == ["2030-08-16"] for row in results)
    assert calls == ["AAPL"]
