from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.access import request_owner_access_context
from app.api import options
from app.services import yahoo


@pytest.fixture(autouse=True)
def _clear_option_state() -> None:
    options._unusual_failure_deadlines.clear()
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()
    yield
    options._unusual_failure_deadlines.clear()
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()


def _unusual_chain(*, strike: float = 110.0) -> SimpleNamespace:
    return SimpleNamespace(
        calls=pd.DataFrame(
            [
                {
                    "contractSymbol": "GOOD-CALL",
                    "strike": strike,
                    "volume": 100,
                    "openInterest": 10,
                    "lastPrice": 2.5,
                    "impliedVolatility": float("inf"),
                    "inTheMoney": float("nan"),
                },
                {
                    "contractSymbol": "BAD-CALL",
                    "strike": float("nan"),
                    "volume": 100,
                    "openInterest": 10,
                    "lastPrice": 2.5,
                    "impliedVolatility": 0.4,
                    "inTheMoney": False,
                },
            ]
        ),
        puts=pd.DataFrame(),
    )


def test_unusual_options_emits_only_finite_numbers_and_explicit_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ticker = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: _unusual_chain(),
    )
    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: ticker)

    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))

    assert len(payload["results"]) == 1
    row = payload["results"][0]
    assert row["contract_ticker"] == "GOOD-CALL"
    assert row["implied_volatility"] is None
    assert row["in_the_money"] is False
    assert row["moneyness"] == "otm"
    assert row["direction"] is None
    assert row["direction_confidence"] == 0
    assert row["direction_status"] == "unavailable_without_trade_side"
    assert row["signal"] == row["inferred_direction"] == "unknown"
    assert row["direction_deprecated"] is True
    json.dumps(payload, allow_nan=False)


def test_unusual_options_keeps_partial_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = SimpleNamespace(
        options=["2030-08-16"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: _unusual_chain(),
    )

    def ticker_factory(symbol: str):
        if symbol == "BROKEN":
            raise RuntimeError("provider down")
        return good

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["GOOD", "BROKEN"])
    monkeypatch.setattr(options.yf, "Ticker", ticker_factory)

    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))

    assert payload["succeeded"] == 1
    assert payload["failed_symbols"] == ["BROKEN"]
    assert payload["source_status"] == "degraded"


def test_unusual_total_failure_uses_short_negative_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [100.0]
    calls = 0

    def broken_ticker(_symbol: str):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", broken_ticker)
    monkeypatch.setattr(options.time, "monotonic", lambda: now[0])

    with pytest.raises(HTTPException) as first:
        asyncio.run(options.unusual_activity("all", 1.0))
    assert first.value.status_code == 503
    assert first.value.headers == {"Retry-After": "30"}
    assert calls == 2

    with pytest.raises(HTTPException) as cooled:
        asyncio.run(options.unusual_activity("all", 1.0))
    assert cooled.value.status_code == 503
    assert cooled.value.headers == {"Retry-After": "30"}
    assert calls == 2

    now[0] += 31
    with pytest.raises(HTTPException):
        asyncio.run(options.unusual_activity("all", 1.0))
    assert calls == 4


def test_concurrent_total_failures_share_one_negative_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def broken_ticker(_symbol: str):
        nonlocal calls
        calls += 1
        raise RuntimeError("provider down")

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", broken_ticker)

    async def scenario():
        return await asyncio.gather(
            *[options.unusual_activity("all", 1.0) for _ in range(5)],
            return_exceptions=True,
        )

    results = asyncio.run(scenario())

    assert calls == 2
    assert len(results) == 5
    assert all(
        isinstance(result, HTTPException) and result.status_code == 503
        for result in results
    )


def test_public_option_reads_cold_pull_yahoo_and_coalesce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expiration_calls = 0
    chain_calls = 0

    def load_expirations(_symbol: str) -> dict:
        nonlocal expiration_calls
        expiration_calls += 1
        time.sleep(0.03)
        return {
            "expirations": ["2030-08-16"],
            "_stale": False,
            "source_status": "active",
            "as_of": "2030-07-01T00:00:00+00:00",
        }

    def load_chain(symbol: str, expiration: str) -> dict:
        nonlocal chain_calls
        chain_calls += 1
        time.sleep(0.03)
        return {
            "ticker": symbol,
            "expiration": expiration,
            "underlying_price": 100.0,
            "calls": [{"strike": 100.0}],
            "puts": [{"strike": 100.0}],
            "alerts": [],
            "data_limited": False,
        }

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", load_expirations)
    monkeypatch.setattr(yahoo, "get_option_chain", load_chain)

    async def scenario() -> tuple[list[dict], list[dict]]:
        with request_owner_access_context(False):
            expirations = await asyncio.gather(
                *[options.expirations("aaoi") for _ in range(5)]
            )
            chains = await asyncio.gather(
                *[
                    options.option_chain("aaoi", "2030-08-16")
                    for _ in range(5)
                ]
            )
        return expirations, chains

    expirations, chains = asyncio.run(scenario())

    assert expiration_calls == 1
    assert chain_calls == 1
    assert all(row["provider"] == "Yahoo/yfinance" for row in expirations)
    assert all(row["expirations"] == ["2030-08-16"] for row in expirations)
    assert all(row["provider"] == "Yahoo/yfinance" for row in chains)
    assert all(row["ticker"] == "AAOI" for row in chains)


def test_public_option_failure_is_cooled_without_repeating_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [500.0]
    calls = 0

    def unavailable(_symbol: str, _expiration: str) -> dict:
        nonlocal calls
        calls += 1
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(yahoo, "get_option_chain", unavailable)
    monkeypatch.setattr(
        yahoo,
        "get_expirations_snapshot",
        lambda _symbol: {"expirations": ["2030-08-16"]},
    )
    monkeypatch.setattr(options.time, "monotonic", lambda: now[0])

    async def scenario() -> list[object]:
        with request_owner_access_context(False):
            return await asyncio.gather(
                *[
                    options.option_chain("aaoi", "2030-08-16")
                    for _ in range(5)
                ],
                return_exceptions=True,
            )

    first = asyncio.run(scenario())

    assert calls == 1
    assert all(
        isinstance(result, HTTPException)
        and result.status_code == 503
        and result.headers == {"Retry-After": "30"}
        for result in first
    )

    with request_owner_access_context(False):
        with pytest.raises(HTTPException) as cooled:
            asyncio.run(options.option_chain("aaoi", "2030-08-16"))
    assert cooled.value.headers == {"Retry-After": "30"}
    assert calls == 1

    now[0] += 31
    with pytest.raises(HTTPException):
        asyncio.run(options.option_chain("aaoi", "2030-08-16"))
    assert calls == 2


@pytest.mark.parametrize(
    ("ticker", "expiration", "code"),
    [
        ("AAOI/../../AAPL", "2030-08-16", "invalid_ticker"),
        ("AAOI", "2030-02-30", "invalid_option_expiration"),
    ],
)
def test_option_chain_rejects_invalid_inputs_before_provider_work(
    monkeypatch: pytest.MonkeyPatch,
    ticker: str,
    expiration: str,
    code: str,
) -> None:
    calls = 0

    def unexpected(_symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"expirations": ["2030-08-16"]}

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", unexpected)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(options.option_chain(ticker, expiration))

    assert captured.value.status_code == 400
    assert captured.value.detail["code"] == code
    assert calls == 0


def test_option_chain_rejects_expiration_outside_ticker_membership_and_cools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    chain_calls = 0

    def expirations(_symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"expirations": ["2030-08-16"]}

    monkeypatch.setattr(yahoo, "get_expirations_snapshot", expirations)

    def unexpected_chain(_symbol: str, _expiration: str) -> dict:
        nonlocal chain_calls
        chain_calls += 1
        raise AssertionError("membership validation must run before chain fetch")

    monkeypatch.setattr(yahoo, "get_option_chain", unexpected_chain)

    for _ in range(2):
        with pytest.raises(HTTPException) as captured:
            asyncio.run(options.option_chain("AAOI", "2030-08-23"))
        assert captured.value.status_code == 400
        assert captured.value.detail["code"] == "invalid_option_expiration"
        assert captured.value.headers == {"Retry-After": "30"}

    assert calls == 1
    assert chain_calls == 0


def test_unusual_scan_keeps_a_ticker_when_one_expiration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def option_chain(expiration: str):
        if expiration == "2030-08-23":
            raise RuntimeError("one expiration failed")
        return _unusual_chain()

    ticker = SimpleNamespace(
        options=["2030-08-16", "2030-08-23"],
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=option_chain,
    )
    monkeypatch.setattr(options, "POPULAR_TICKERS", ["PARTIAL"])
    monkeypatch.setattr(options.yf, "Ticker", lambda _symbol: ticker)

    payload = asyncio.run(options._unusual_activity_impl("all", 1.0))

    assert payload["succeeded"] == 1
    assert payload["results"][0]["ticker"] == "PARTIAL"
    assert payload["data_limited"] is True
    assert payload["source_status"] == "degraded"
    assert payload["partial_symbols"] == ["PARTIAL"]


def _chain_row(strike: float, symbol: str, *, last_price: float | None = 2.0) -> dict:
    return {
        "contractSymbol": symbol,
        "strike": strike,
        "lastPrice": last_price,
        "impliedVolatility": 0.3,
        "volume": 2500,
        "openInterest": 100,
        "inTheMoney": False,
        "bid": 1.9,
        "ask": 2.1,
        "change": 0.0,
        "percentChange": 0.0,
    }


def test_option_chain_moneyness_is_side_aware_and_direction_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = SimpleNamespace(
        calls=pd.DataFrame(
            [_chain_row(80.0, "C80"), _chain_row(120.0, "C120")]
        ),
        puts=pd.DataFrame(
            [_chain_row(120.0, "P120"), _chain_row(80.0, "P80")]
        ),
    )
    ticker = SimpleNamespace(
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: chain,
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)

    payload = yahoo.get_option_chain("SEMANTICS", "2030-08-16")

    contracts = {
        (item["type"], item["strike"]): item
        for item in [*payload["calls"], *payload["puts"]]
    }
    assert contracts[("call", 80.0)]["moneyness"] == "itm"
    assert contracts[("call", 120.0)]["moneyness"] == "otm"
    assert contracts[("put", 120.0)]["moneyness"] == "itm"
    assert contracts[("put", 80.0)]["moneyness"] == "otm"
    assert contracts[("call", 80.0)]["in_the_money"] is True
    assert contracts[("put", 120.0)]["in_the_money"] is True

    alerts = {(item["type"], item["strike"]): item for item in payload["alerts"]}
    assert not any("深度虚值" in reason for reason in alerts[("call", 80.0)]["reasons"])
    assert not any("深度虚值" in reason for reason in alerts[("put", 120.0)]["reasons"])
    assert any("深度虚值" in reason for reason in alerts[("call", 120.0)]["reasons"])
    assert any("深度虚值" in reason for reason in alerts[("put", 80.0)]["reasons"])
    for alert in alerts.values():
        assert alert["direction"] is None
        assert alert["direction_confidence"] == 0
        assert alert["direction_status"] == "unavailable_without_trade_side"
        assert alert["signal"] == alert["inferred_direction"] == "unknown"
        assert alert["direction_deprecated"] is True


def test_missing_option_price_does_not_create_fake_break_even(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = SimpleNamespace(
        calls=pd.DataFrame([_chain_row(100.0, "C100", last_price=None)]),
        puts=pd.DataFrame(),
    )
    ticker = SimpleNamespace(
        fast_info=SimpleNamespace(last_price=100.0),
        option_chain=lambda _expiration: chain,
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)

    payload = yahoo.get_option_chain("NOQUOTE", "2030-08-16")

    assert payload["calls"][0]["last_price"] is None
    assert payload["calls"][0]["break_even"] is None
    assert payload["calls"][0]["break_even_price"] is None
