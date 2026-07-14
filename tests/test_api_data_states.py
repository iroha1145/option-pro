from __future__ import annotations

import asyncio
from datetime import date
import time
from types import SimpleNamespace

import pandas as pd
import pytest
from fastapi import HTTPException

from app.api import earnings, options, stocks


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
    async def svg(_symbol):
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


def test_earnings_reports_total_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(earnings.yf, "Ticker", BrokenTicker)

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


def test_watchlist_reports_provider_failure_without_unbounded_stale_data(monkeypatch):
    async def failed_watchlist():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(stocks, "_build_watchlist", failed_watchlist)
    stocks._endpoint_cache.pop("watchlist", None)
    stocks._endpoint_locks.pop("watchlist", None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.watchlist(None))
    assert exc_info.value.status_code == 503


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

    async def uncached(key, _ttl, loader, *, stale_ttl=None):
        cache_keys.append((key, stale_ttl))
        return await loader()

    monkeypatch.setattr(stocks, "_build_watchlist", targeted_builder)
    monkeypatch.setattr(stocks, "_cached_endpoint", uncached)

    first = asyncio.run(stocks.watchlist(None, " msft,AAPL,msft "))
    second = asyncio.run(stocks.watchlist(None, "AAPL,MSFT"))
    third = asyncio.run(stocks.watchlist(None, "AAPL,NVDA"))

    assert first["attempted"] == 2
    assert second["attempted"] == 2
    assert requested_calls == [["MSFT", "AAPL"], ["AAPL", "MSFT"], ["AAPL", "NVDA"]]
    assert cache_keys[0][0] == cache_keys[1][0]
    assert cache_keys[0][0] != cache_keys[2][0]
    assert all(stale_ttl == 900 for _, stale_ttl in cache_keys)


def test_targeted_watchlist_downloads_only_requested_tickers(monkeypatch):
    captured = {}

    def fake_download(*, tickers, **_kwargs):
        captured["tickers"] = tickers
        columns = pd.MultiIndex.from_tuples([
            ("AAPL", "Close"),
            ("MSFT", "Close"),
            ("ES=F", "Close"),
        ])
        return pd.DataFrame(
            [
                [190.0, 420.0, 5_500.0],
                [192.0, 424.0, 5_525.0],
            ],
            columns=columns,
        )

    monkeypatch.setattr(stocks.yf, "download", fake_download)

    payload = asyncio.run(stocks._build_watchlist(["AAPL", "MSFT", "ES=F"]))
    returned = [
        item["ticker"]
        for group in payload["groups"]
        for item in group["stocks"]
    ]

    assert captured["tickers"] == "AAPL MSFT ES=F"
    assert sorted(returned) == ["AAPL", "ES=F", "MSFT"]
    assert len(returned) == len(set(returned))
    assert payload["attempted"] == 3
    assert payload["succeeded"] == 3
    assert any(group["id"] == "custom" for group in payload["groups"])
