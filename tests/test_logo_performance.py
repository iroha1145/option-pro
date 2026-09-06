from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api import stocks
from app.services import company_logo_cache as disk

IMAGE = b'\x89PNG\r\n\x1a\n' + b'x' * 120
VALUE = {"content": IMAGE, "media_type": "image/png", "source": "https://static2.finnhub.io/AAPL.png"}


def test_success_lives_three_days_and_survives_memory_loss_for_public_reader(monkeypatch):
    calls = 0
    now = [1_788_617_000.0]
    monkeypatch.setattr(stocks.time, "time", lambda: now[0])

    async def fetch(symbol):
        nonlocal calls
        calls += 1
        return dict(VALUE)

    monkeypatch.setattr(stocks, "_fetch_company_logo", fetch)

    async def scenario():
        a = await stocks._cached_company_logo("AAPL")
        stocks._endpoint_cache.clear()  # Restart / memory eviction.
        now[0] += 2 * 86400 + 3600
        b = await stocks._cached_company_logo("US.AAPL", allow_refresh=False)
        assert b == a
        assert stocks._endpoint_cache["logo:AAPL"].expires_at == a["fetched_at"] + 3 * 86400
        assert calls == 1
    asyncio.run(scenario())


def test_cold_requests_for_same_logo_share_one_fetch(monkeypatch):
    calls = 0

    async def fetch(symbol):
        nonlocal calls
        calls += 1
        await asyncio.sleep(.01)
        return dict(VALUE)

    monkeypatch.setattr(stocks, "_fetch_company_logo", fetch)
    async def scenario():
        values = await asyncio.gather(*(stocks._cached_company_logo("MSFT") for _ in range(20)))
        assert all(v["content"] == IMAGE for v in values)
        assert calls == 1
    asyncio.run(scenario())


def test_stale_logo_returns_before_refresh_and_visitor_never_starts_fetch(monkeypatch):
    now = [1_788_617_000.0]
    monkeypatch.setattr(stocks.time, "time", lambda: now[0])
    started, finish = asyncio.Event(), asyncio.Event()
    calls = 0

    async def fetch(symbol):
        nonlocal calls
        calls += 1
        if calls > 1:
            started.set()
            await finish.wait()
        return dict(VALUE, content=IMAGE + (b'new' if calls > 1 else b''))

    monkeypatch.setattr(stocks, "_fetch_company_logo", fetch)

    async def scenario():
        await stocks._cached_company_logo("AAPL")
        now[0] += 3 * 86400 + 1
        stocks._endpoint_cache.clear()
        visitor = await stocks._cached_company_logo("AAPL", allow_refresh=False)
        assert visitor["content"] == IMAGE and calls == 1
        owner = await stocks._cached_company_logo("AAPL")
        assert owner["content"] == IMAGE
        await asyncio.wait_for(started.wait(), 1)
        await stocks._cached_company_logo("AAPL")
        assert calls == 2
        tasks = list(stocks._logo_refresh_tasks.values())
        finish.set()
        await asyncio.gather(*tasks)
        assert (await stocks._cached_company_logo("AAPL"))["content"] == IMAGE + b'new'
    asyncio.run(scenario())


def test_disk_failure_cannot_break_a_downloaded_logo(monkeypatch, tmp_path):
    blocked = tmp_path / "file"
    blocked.write_text("not a directory")
    monkeypatch.setattr(disk, "cache_path", lambda: blocked / "logos.sqlite")
    async def fetch(symbol):
        return dict(VALUE)
    monkeypatch.setattr(stocks, "_fetch_company_logo", fetch)
    assert asyncio.run(stocks._cached_company_logo("AAPL"))["content"] == IMAGE


def test_negative_cache_survives_restart_but_expires(monkeypatch):
    now = [1_788_617_000.0]
    monkeypatch.setattr(stocks.time, "time", lambda: now[0])
    calls = 0
    async def missing(symbol):
        nonlocal calls
        calls += 1
        raise HTTPException(404)
    monkeypatch.setattr(stocks, "_fetch_company_logo", missing)
    async def scenario():
        for advance in (0, 30, 3601):
            now[0] += advance
            stocks._endpoint_cache.clear()
            with pytest.raises(HTTPException) as e:
                await stocks._cached_company_logo("MISSING")
            assert e.value.status_code == 404
        assert calls == 2
    asyncio.run(scenario())


def test_stale_refresh_failure_preserves_image_and_backs_off(monkeypatch):
    now = [1_788_617_000.0]
    monkeypatch.setattr(stocks.time, "time", lambda: now[0])
    calls = 0
    async def fetch(symbol):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise HTTPException(503)
        return dict(VALUE)
    monkeypatch.setattr(stocks, "_fetch_company_logo", fetch)
    async def scenario():
        await stocks._cached_company_logo("AAPL")
        now[0] += 3 * 86400 + 1
        await stocks._cached_company_logo("AAPL")
        await asyncio.gather(*list(stocks._logo_refresh_tasks.values()))
        for _ in range(10):
            assert (await stocks._cached_company_logo("AAPL"))["content"] == IMAGE
        assert calls == 2
        assert stocks._logo_refresh_tasks == {}
    asyncio.run(scenario())


def test_disk_and_memory_payload_limits(monkeypatch):
    monkeypatch.setattr(disk, "MAX_DISK_ENTRIES", 3)
    monkeypatch.setattr(disk, "MAX_DISK_BYTES", len(IMAGE) * 2)
    monkeypatch.setattr(stocks, "_LOGO_MEMORY_ENTRIES", 2)
    monkeypatch.setattr(stocks, "_LOGO_MEMORY_BYTES", len(IMAGE) * 2)
    for i in range(6):
        entry = stocks._EndpointCacheEntry(1000, 2000, 100 + i, dict(VALUE))
        assert disk.write(f"S{i}", vars(entry), 200)
        stocks._remember_company_logo(f"logo:S{i}", entry)
    assert sum(disk.read(f"S{i}", 200) is not None for i in range(6)) == 2
    assert sum(k.startswith("logo:") for k in stocks._endpoint_cache) == 2


def test_conditional_browser_cache_has_correct_age_and_three_day_lifetime(monkeypatch):
    async def cached(symbol, *, allow_refresh):
        return dict(VALUE, fetched_at=stocks.time.time() - 86400)
    monkeypatch.setattr(stocks, "_cached_company_logo", cached)
    response = asyncio.run(stocks.stock_logo("AAPL"))
    assert response.headers["cache-control"] == "public, max-age=259200, stale-while-revalidate=604800"
    assert response.headers["age"] == "86400"
    request = Request({"type": "http", "headers": [(b"if-none-match", ('W/' + response.headers['etag']).encode())]})
    response = asyncio.run(stocks.stock_logo("AAPL", request))
    assert response.status_code == 304
    assert response.body == b""
    assert response.headers["content-security-policy"].startswith("sandbox")


def test_racing_fallback_finishes_without_waiting_for_slow_primary(monkeypatch):
    cancelled = asyncio.Event()
    async def transport(request):
        if request.url.host == "storage.googleapis.com":
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        return httpx.Response(200, headers={"content-type": "image/png"}, content=IMAGE)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(stocks, "_logo_http", client)
            monkeypatch.setattr(stocks, "_logo_slots", asyncio.Semaphore(6))
            value = await asyncio.wait_for(stocks._fetch_company_logo("AAPL"), 1)
            assert value["content"] == IMAGE
            assert cancelled.is_set()
    asyncio.run(scenario())


def test_upstream_concurrency_is_bounded_across_different_symbols(monkeypatch):
    active = peak = 0
    async def transport(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(.01)
            return httpx.Response(200, headers={"content-type": "image/png"}, content=IMAGE)
        finally:
            active -= 1
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(stocks, "_logo_http", client)
            monkeypatch.setattr(stocks, "_logo_slots", asyncio.Semaphore(6))
            await asyncio.gather(*(stocks._fetch_company_logo(f"S{i}") for i in range(20)))
        assert peak <= 6 and peak > 1 and active == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("url", ["https://financialmodelingprep.com:8443/a", "https://user@static2.finnhub.io/a", "https://127.0.0.1/a"])
def test_redirect_allowlist_does_not_allow_credentials_or_other_ports(url):
    assert not stocks._safe_logo_url(url)


def test_public_cold_logo_cannot_start_a_network_request(monkeypatch):
    async def unexpected(symbol):
        raise AssertionError("public upstream request")
    monkeypatch.setattr(stocks, "_fetch_company_logo", unexpected)
    with pytest.raises(HTTPException) as e:
        asyncio.run(stocks._cached_company_logo("COLD", allow_refresh=False))
    assert e.value.status_code == 503


def test_transient_outage_is_briefly_shared_but_not_persisted_as_missing(monkeypatch):
    calls = 0
    async def unavailable(symbol):
        nonlocal calls
        calls += 1
        raise HTTPException(503)
    monkeypatch.setattr(stocks, "_fetch_company_logo", unavailable)
    async def scenario():
        for _ in range(10):
            with pytest.raises(HTTPException) as e:
                await stocks._cached_company_logo("AAPL")
            assert e.value.status_code == 503
        assert calls == 1
        assert not disk.cache_path().exists()
    asyncio.run(scenario())


def test_disk_image_cannot_be_used_after_the_bounded_stale_window(monkeypatch):
    now = stocks.time.time()
    entry = stocks._EndpointCacheEntry(now - 8 * 86400, now - 86400, now - 11 * 86400, dict(VALUE))
    assert disk.write("AAPL", vars(entry), now - 11 * 86400)
    assert disk.read("AAPL", now) is None


def test_logo_urls_try_iex_cdn_before_slower_vendor_hosts():
    urls = stocks._logo_urls("NKE")
    assert urls[0] == "https://storage.googleapis.com/iex/api/logos/NKE.png"
    assert urls[1].startswith("https://static2.finnhub.io/")
    assert any("financialmodelingprep.com" in url for url in urls)
    assert any("eodhd.com" in url for url in urls)


def test_class_share_logo_candidates_stay_within_public_snapshot_limit():
    urls = stocks._logo_urls("BRK.B", "https://www.berkshirehathaway.com")
    assert urls[0] == "https://storage.googleapis.com/iex/api/logos/BRK.B.png"
    assert urls[1] == "https://storage.googleapis.com/iex/api/logos/BRK-B.png"
    assert len(urls) <= 8
    assert len(urls) == len(set(urls))


def test_iex_gcs_logo_path_must_stay_on_the_logo_prefix():
    assert stocks._safe_logo_url("https://storage.googleapis.com/iex/api/logos/AAPL.png")
    assert not stocks._safe_logo_url("https://storage.googleapis.com/evil/AAPL.png")
    assert not stocks._safe_logo_url("https://storage.googleapis.com/iex/api/logos/../secret.png")
    assert not stocks._safe_logo_url("https://storage.googleapis.com/iex/api/logos/%2e%2e/secret.png")
    assert not stocks._safe_logo_url("https://storage.googleapis.com/iex/api/logos/foo/bar.png")


def test_racing_fetch_rejects_oversized_wrong_type_and_private_redirects(monkeypatch):
    visited = []
    async def transport(request):
        visited.append(request.url.host)
        if request.url.host == "storage.googleapis.com":
            return httpx.Response(404)
        if request.url.host == "financialmodelingprep.com":
            return httpx.Response(302, headers={"location": "https://127.0.0.1/internal"})
        if request.url.host == "static2.finnhub.io":
            return httpx.Response(200, headers={"content-type": "image/png"}, content=b'x' * (stocks._LOGO_MAX_BYTES + 1))
        return httpx.Response(200, headers={"content-type": "text/html"}, content=b'<html>' * 30)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(stocks, "_logo_http", client)
            monkeypatch.setattr(stocks, "_logo_slots", asyncio.Semaphore(6))
            with pytest.raises(HTTPException) as e:
                await stocks._fetch_company_logo("AAPL")
            assert e.value.status_code == 404
        assert '127.0.0.1' not in visited
    asyncio.run(scenario())
