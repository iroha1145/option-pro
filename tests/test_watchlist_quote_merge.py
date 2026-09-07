"""Personal watchlist quote order follows market timestamps, not cache writes."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from app.api import stocks

NOW = datetime(2026, 9, 8, 15, 0, tzinfo=timezone.utc).timestamp()
NEW_QUOTE = "2026-09-08T14:59:00+00:00"
OLD_QUOTE = "2026-09-07T20:00:00+00:00"


@pytest.fixture
def cached_watchlist(tmp_path, monkeypatch):
    monkeypatch.setattr(stocks.time, "time", lambda: NOW)
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing.json")
    original = {"groups": [{"name": "自定义", "stocks": [{
        "ticker": "TEST", "price": 110, "change": 10, "change_percent": 10,
        "quote_as_of": NEW_QUOTE, "spark": [100, 105, 110],
    }]}]}
    monkeypatch.setattr(stocks, "_endpoint_cache", {
        "watchlist": stocks._EndpointCacheEntry(NOW + 60, NOW + 300, NOW - 30, original),
    })
    overview = {"saved_at": NOW - 5, "payload": {
        "ticker": "TEST", "price": 100, "change": 0, "change_percent": 0,
        "as_of": OLD_QUOTE, "industry": "Optical components",
    }}
    monkeypatch.setattr(stocks, "read_stock_pull_resource", lambda *a, **k: overview)
    return original, overview


def _row(result):
    return result["groups"][0]["stocks"][0]


def test_later_saved_older_quote_does_not_replace_newer_price(cached_watchlist):
    original, _overview = cached_watchlist
    before = deepcopy(original)
    result = stocks._cached_selected_watchlist(["TEST"])
    row = _row(result)
    assert (row["price"], row["change"], row["change_percent"]) == (110, 10, 10)
    assert row["quote_as_of"] == NEW_QUOTE
    assert row["sector"] == "Optical components"
    assert row["spark"] == [100, 105, 110]
    assert original == before


def test_older_saved_newer_market_quote_replaces_price_and_time_together(cached_watchlist):
    _original, overview = cached_watchlist
    overview["saved_at"] = NOW - 50
    overview["payload"].update(price=112, change=12, change_percent=12, as_of="2026-09-08T10:59:30-04:00")
    row = _row(stocks._cached_selected_watchlist(["TEST"]))
    assert (row["price"], row["change"], row["change_percent"]) == (112, 12, 12)
    assert row["quote_as_of"] == "2026-09-08T14:59:30+00:00"


@pytest.mark.parametrize("stamp", [None, "", "not-a-time", "2026-09-08T14:59:30", datetime(2026, 9, 8, 14, 59, 30)])
def test_unknown_or_ambiguous_time_never_overwrites_known_quote(cached_watchlist, stamp):
    _original, overview = cached_watchlist
    overview["payload"]["as_of"] = stamp
    row = _row(stocks._cached_selected_watchlist(["TEST"]))
    assert (row["price"], row["change"], row["quote_as_of"]) == (110, 10, NEW_QUOTE)


def test_actual_overview_as_of_is_retained_without_previous_row(cached_watchlist):
    original, overview = cached_watchlist
    original["groups"][0]["stocks"].clear()
    row = _row(stocks._cached_selected_watchlist(["TEST"]))
    assert row["price"] == 100
    assert row["quote_as_of"] == OLD_QUOTE


def test_snapshot_merge_also_compares_quote_time_and_preserves_delayed_flags(cached_watchlist, monkeypatch):
    original, _overview = cached_watchlist
    original["groups"][0]["stocks"][0]["quote_delayed"] = True
    original["delayed_tickers"] = ["TEST"]
    late_saved = {"groups": [{"name": "科技", "stocks": [{
        "ticker": "TEST", "price": 99, "quote_as_of": OLD_QUOTE,
    }]}]}
    stocks._endpoint_cache[stocks._watchlist_cache_key(["TEST"])] = stocks._EndpointCacheEntry(
        NOW + 60, NOW + 300, NOW - 5, late_saved,
    )
    monkeypatch.setattr(stocks, "read_stock_pull_resource", lambda *a, **k: None)
    result = stocks._cached_selected_watchlist(["TEST"])
    assert _row(result)["price"] == 110
    assert _row(result)["quote_as_of"] == NEW_QUOTE
    assert result["delayed_tickers"] == ["TEST"]
    assert result["data_limited"] is True


def test_metadata_enrichment_does_not_refresh_a_stale_selected_quote(cached_watchlist):
    _original, _overview = cached_watchlist
    stocks._endpoint_cache["watchlist"].fetched_at = NOW - 600
    stocks._endpoint_cache["watchlist"].expires_at = NOW - 300
    result = stocks._cached_selected_watchlist(["TEST"])
    assert _row(result)["price"] == 110
    assert _row(result)["sector"] == "Optical components"
    assert result["_stale"] is True
