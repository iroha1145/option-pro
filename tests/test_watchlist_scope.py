from __future__ import annotations

import asyncio
from copy import deepcopy
import time

import pytest

from app.access import request_owner_access_context
from app.api import stocks
from app.services.accounts import AccountStore
from app.services.watchlist_scope import DEFAULT_WATCHLIST_TICKERS, collection_watchlist_tickers, scope_watchlist


def test_collection_defaults_stay_four_and_only_owner_saves_expand_the_budget(tmp_path, monkeypatch):
    store = AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr("app.services.accounts.get_account_store", lambda: store)
    assert collection_watchlist_tickers() == list(DEFAULT_WATCHLIST_TICKERS)
    owner = store.ensure_owner_account()
    customer = store.register("customer", "fixture-password-for-tests").account
    store.replace_watchlist(customer.user_id, ["TSLA", "QQQ"])
    store.replace_watchlist(owner.user_id, ["SPY", "AMD", "AAOI"])
    assert collection_watchlist_tickers() == ["AAPL", "MSFT", "NVDA", "SPY", "AMD", "AAOI"]
    assert store.watchlist(owner.user_id) == ["SPY", "AMD", "AAOI"]


def test_legacy_large_snapshot_is_scoped_without_changing_the_saved_payload():
    rows = [{"ticker": ticker, "price": 100} for ticker in [*DEFAULT_WATCHLIST_TICKERS, "TSLA", "QQQ"]]
    payload = {"groups": [{"stocks": rows}], "attempted": 214, "delayed_tickers": ["QQQ", "MSFT"]}
    original = deepcopy(payload)
    scoped = scope_watchlist(payload, DEFAULT_WATCHLIST_TICKERS)
    assert [row["ticker"] for row in scoped["groups"][0]["stocks"]] == list(DEFAULT_WATCHLIST_TICKERS)
    assert (scoped["attempted"], scoped["succeeded"], scoped["failed"]) == (4, 4, 0)
    assert scoped["delayed_tickers"] == ["MSFT"]
    assert payload == original


@pytest.mark.parametrize("owner", [True, False])
def test_personal_combinations_use_cached_rows_without_any_provider_calls(tmp_path, monkeypatch, owner):
    now = time.time()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing.json")
    payload = {"groups": [{"name": "科技", "stocks": [{"ticker": "AAPL", "price": 100}, {"ticker": "MSFT", "price": 200}]}]}
    monkeypatch.setattr(stocks, "_endpoint_cache", {"watchlist": stocks._EndpointCacheEntry(now + 60, now + 300, now - 30, payload)})
    reads = []
    def read(symbol, resource, **kwargs):
        reads.append((symbol, resource))
        if symbol == "AAPL" and resource == "overview":
            return {"saved_at": now, "payload": {"ticker": "AAPL", "price": 101, "change": 1, "change_percent": 1, "quote_as_of": "2026-09-04T20:00:00Z"}}
        return None
    def forbidden(*args, **kwargs):
        pytest.fail("A list read must not fetch any provider")
    monkeypatch.setattr(stocks, "read_stock_pull_resource", read)
    monkeypatch.setattr(stocks, "_build_watchlist", forbidden)
    monkeypatch.setattr(stocks, "download_in_bounded_batches", forbidden)
    with request_owner_access_context(owner):
        result = asyncio.run(stocks.watchlist("AAPL,MISSING"))
    assert [row["ticker"] for row in result["groups"][0]["stocks"]] == ["AAPL"]
    assert result["groups"][0]["stocks"][0]["price"] == 101
    assert result["failed_tickers"] == ["MISSING"]
    assert result["attempted"] == 2
    assert ("MSFT", "overview") not in reads
    assert payload["groups"][0]["stocks"][0]["price"] == 100
