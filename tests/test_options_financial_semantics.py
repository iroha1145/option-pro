from __future__ import annotations

import asyncio
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
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()
    yield
    options._option_failure_cache.clear()
    options.cache.clear()
    yahoo._cache.clear()


def test_owner_option_reads_cold_pull_yahoo_and_coalesce(
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
        with request_owner_access_context(True):
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


def test_owner_option_failure_is_cooled_without_repeating_provider_calls(
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
        with request_owner_access_context(True):
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

    with request_owner_access_context(True):
        with pytest.raises(HTTPException) as cooled:
            asyncio.run(options.option_chain("aaoi", "2030-08-16"))
    assert cooled.value.headers == {"Retry-After": "30"}
    assert calls == 1

    now[0] += 31
    with request_owner_access_context(True), pytest.raises(HTTPException):
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

    with request_owner_access_context(True), pytest.raises(HTTPException) as captured:
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
        with request_owner_access_context(True), pytest.raises(HTTPException) as captured:
            asyncio.run(options.option_chain("AAOI", "2030-08-23"))
        assert captured.value.status_code == 400
        assert captured.value.detail["code"] == "invalid_option_expiration"
        assert captured.value.headers == {"Retry-After": "30"}

    assert calls == 1
    assert chain_calls == 0


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
