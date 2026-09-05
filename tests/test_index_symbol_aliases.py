import asyncio
import time

import pytest

from app.access import request_owner_access_context
from app.api import stocks, signals
from app.services import massive
from app.services.symbols import quote_symbol


@pytest.mark.parametrize("alias,canonical", [
    (" spx ", "^GSPC"), ("^SPX", "^GSPC"), ("NDX", "^NDX"),
    ("IXIC", "^IXIC"), ("DJI", "^DJI"), ("RUT", "^RUT"),
    ("SOX", "^SOX"), ("VIX", "^VIX"), ("N225", "^N225"),
    ("SSE", "000001.SS"), ("spy", "SPY"), ("QQQ", "QQQ"),
])
def test_index_aliases_are_canonical_and_etfs_remain_separate(alias, canonical):
    assert quote_symbol(alias) == canonical
    assert signals._normalize_ticker(alias) == canonical
    assert massive.to_symbol(alias) == massive.to_symbol(canonical)


@pytest.mark.parametrize("alias,canonical", [("SPX", "^GSPC"), ("NDX", "^NDX"), ("IXIC", "^IXIC")])
def test_visitor_alias_reads_same_cached_index_overview_and_chart(monkeypatch, tmp_path, alias, canonical):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    now = time.time()
    overview = {"ticker": canonical, "price": 21000.0}
    chart = {"ticker": canonical, "range": "1d", "bars": [{"t": 1, "c": 21000.0}]}
    for key, value in [(f"stock:{canonical}", overview), (f"chart:{canonical}:1d:raw", chart)]:
        stocks._endpoint_cache[key] = stocks._EndpointCacheEntry(now + 60, now + 120, now, value)

    async def identity(_symbol, payload):
        return payload

    async def upstream_forbidden(*args, **kwargs):
        pytest.fail("a cached index alias must not trigger provider calls")

    monkeypatch.setattr(stocks, "_attach_macro_fit_async", identity)
    monkeypatch.setattr(stocks, "_stock_overview_impl", upstream_forbidden)
    monkeypatch.setattr(stocks, "_load_stock_chart", upstream_forbidden)
    with request_owner_access_context(False):
        actual_overview = asyncio.run(stocks.stock_overview(alias))
        actual_chart = asyncio.run(stocks.stock_chart(alias, "1d", "raw"))
    assert actual_overview["ticker"] == actual_chart["ticker"] == canonical
    assert actual_overview["price"] == actual_chart["bars"][-1]["c"] == 21000.0
