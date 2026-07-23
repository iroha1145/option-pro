from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import HTTPException

from app.access import request_owner_access_context
from app.api import stocks
from app.services import massive


def _directory_payload() -> dict:
    return {
        "items": [
            {
                "ticker": "AAOI",
                "name": "Applied Optoelectronics, Inc.",
                "market": "stocks",
                "type": "CS",
                "primary_exchange": "XNAS",
                "locale": "us",
                "currency_symbol": "USD",
                "active": True,
            },
            {
                "ticker": "NBIS",
                "name": "Nebius Group N.V. Class A",
                "market": "stocks",
                "type": "CS",
                "primary_exchange": "XNAS",
                "locale": "us",
                "currency_symbol": "USD",
                "active": True,
            },
            {
                "ticker": "BRK.B",
                "name": "Berkshire Hathaway Inc. Class B",
                "market": "stocks",
                "type": "CS",
                "primary_exchange": "XNYS",
                "locale": "us",
                "currency_symbol": "USD",
                "active": True,
            },
        ],
        "count": 3,
        "provider": "Massive",
    }


@pytest.fixture(autouse=True)
def _clear_directory_cache(monkeypatch: pytest.MonkeyPatch, tmp_path):
    path = tmp_path / "stock-symbol-directory-v1.json"
    monkeypatch.setattr(stocks, "_STOCK_DIRECTORY_PATH", path)
    monkeypatch.setattr(stocks, "_stock_directory_snapshot_observed", None)
    stocks._endpoint_cache.pop(stocks._STOCK_DIRECTORY_CACHE_KEY, None)
    stocks._endpoint_refresh_retry_after.pop(stocks._STOCK_DIRECTORY_CACHE_KEY, None)
    yield path
    stocks._endpoint_cache.pop(stocks._STOCK_DIRECTORY_CACHE_KEY, None)


def test_public_search_reads_persisted_massive_directory_without_provider(
    monkeypatch: pytest.MonkeyPatch,
    _clear_directory_cache,
) -> None:
    path = _clear_directory_cache
    stocks._write_stock_directory_snapshot(
        path,
        payload=_directory_payload(),
        saved_at=time.time(),
    )
    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("public symbol search called Yahoo")
        ),
    )
    monkeypatch.setattr(
        massive,
        "reference_tickers",
        lambda: (_ for _ in ()).throw(
            AssertionError("public symbol search called Massive")
        ),
    )

    async def scenario() -> tuple[list[dict], list[dict]]:
        with request_owner_access_context(False):
            return (
                await stocks.search_stocks("AAOI"),
                await stocks.search_stocks("NBIS"),
            )

    aaoi, nbis = asyncio.run(scenario())
    assert aaoi[0]["ticker"] == "AAOI"
    assert aaoi[0]["name_en"] == "Applied Optoelectronics, Inc."
    assert nbis[0]["ticker"] == "NBIS"
    assert nbis[0]["name_en"] == "Nebius Group N.V. Class A"


@pytest.mark.parametrize("query", ["BRK.B", "brk.b", "BRK-B", "US.BRK.B"])
def test_public_search_normalizes_massive_class_share_symbols(
    monkeypatch: pytest.MonkeyPatch,
    _clear_directory_cache,
    query: str,
) -> None:
    stocks._write_stock_directory_snapshot(
        _clear_directory_cache,
        payload=_directory_payload(),
        saved_at=time.time(),
    )
    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("class-share lookup fell through to Yahoo")
        ),
    )

    async def scenario() -> list[dict]:
        with request_owner_access_context(False):
            return await stocks.search_stocks(query)

    result = asyncio.run(scenario())
    assert result[0]["ticker"] == "BRK.B"
    assert result[0]["name_en"] == "Berkshire Hathaway Inc. Class B"


def test_owner_cold_search_builds_and_persists_massive_directory(
    monkeypatch: pytest.MonkeyPatch,
    _clear_directory_cache,
) -> None:
    path = _clear_directory_cache
    calls = 0

    def reference_tickers() -> list[dict]:
        nonlocal calls
        calls += 1
        return _directory_payload()["items"]

    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "reference_tickers", reference_tickers)
    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("Massive exact search fell through to Yahoo")
        ),
    )

    async def scenario() -> list[dict]:
        with request_owner_access_context(True):
            return await stocks.search_stocks("AAOI")

    result = asyncio.run(scenario())
    assert result[0]["ticker"] == "AAOI"
    assert calls == 1
    assert path.is_file()
    saved = stocks._read_stock_directory_snapshot(path, now=time.time())
    assert saved is not None
    assert saved.value["count"] == 3


def test_daily_directory_refresh_waits_for_provider_and_persists_fresh_data(
    monkeypatch: pytest.MonkeyPatch,
    _clear_directory_cache,
) -> None:
    path = _clear_directory_cache
    old_payload = _directory_payload()
    old_payload["items"] = old_payload["items"][:1]
    old_payload["count"] = 1
    saved_at = time.time() - stocks._STOCK_DIRECTORY_FRESH_TTL_SECONDS - 60
    stocks._write_stock_directory_snapshot(
        path,
        payload=old_payload,
        saved_at=saved_at,
    )
    calls = 0

    def reference_tickers() -> list[dict]:
        nonlocal calls
        calls += 1
        return _directory_payload()["items"]

    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "reference_tickers", reference_tickers)

    payload = asyncio.run(stocks._refresh_stock_directory())
    assert calls == 1
    assert payload["_stale"] is False
    assert payload["count"] == 3
    persisted = stocks._read_stock_directory_snapshot(path, now=time.time())
    assert persisted is not None
    assert persisted.expires_at > time.time()
    assert persisted.value["count"] == 3


def test_daily_directory_refresh_surfaces_failure_instead_of_claiming_stale_success(
    monkeypatch: pytest.MonkeyPatch,
    _clear_directory_cache,
) -> None:
    path = _clear_directory_cache
    stocks._write_stock_directory_snapshot(
        path,
        payload=_directory_payload(),
        saved_at=time.time() - stocks._STOCK_DIRECTORY_FRESH_TTL_SECONDS - 60,
    )
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(
        massive,
        "reference_tickers",
        lambda: (_ for _ in ()).throw(
            massive.MassiveError(
                "rate limited",
                code="rate_limited",
                status=429,
            )
        ),
    )

    with pytest.raises(massive.MassiveError) as captured:
        asyncio.run(stocks._refresh_stock_directory())
    assert captured.value.code == "rate_limited"


def test_daily_directory_refresh_surfaces_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(
        massive,
        "reference_tickers",
        lambda: _directory_payload()["items"],
    )
    monkeypatch.setattr(
        stocks,
        "_persist_stock_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("read-only volume")
        ),
    )

    with pytest.raises(OSError, match="read-only volume"):
        asyncio.run(stocks._refresh_stock_directory())


def test_directory_snapshot_rejects_symlink_and_duplicate_tickers(
    _clear_directory_cache,
    tmp_path,
) -> None:
    path = _clear_directory_cache
    duplicate = _directory_payload()
    duplicate["items"].append(dict(duplicate["items"][0]))
    with pytest.raises(ValueError):
        stocks._write_stock_directory_snapshot(
            path,
            payload=duplicate,
            saved_at=time.time(),
        )

    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "directory-link.json"
    link.symlink_to(target)
    assert stocks._read_stock_directory_snapshot(link, now=time.time()) is None


@pytest.mark.parametrize(
    ("query", "is_owner"),
    [
        ("ZZZZUNLISTED", False),
        ("NVDA", False),
        ("ZZZZUNLISTED", True),
    ],
)
def test_missing_directory_is_an_error_not_a_false_search_result(
    monkeypatch: pytest.MonkeyPatch,
    query: str,
    is_owner: bool,
) -> None:
    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(
        stocks.yf,
        "Ticker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("Yahoo unavailable")
        ),
    )

    async def scenario() -> None:
        with request_owner_access_context(is_owner):
            with pytest.raises(HTTPException) as captured:
                await stocks.search_stocks(query)
            assert captured.value.status_code == 503
            assert captured.value.detail["code"] == "stock_directory_unavailable"

    asyncio.run(scenario())
