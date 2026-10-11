"""首页「财报临近」接口 GET /api/earnings/upcoming/highlights。

两件事要证明：
1. 选出来的行与改版前首页在浏览器里对整份日历做的排序逐行一致（共用夹具
   tests/fixtures/earnings_home_highlights_cases.json 也被前端节点测试读取）；
2. 数据来源、缓存、访问控制与 /upcoming 完全相同：访客只读公共快照，
   password 模式站长读 Worker 快照，任何命中都不触发外部请求。
"""

from __future__ import annotations

import asyncio
import functools
import json
import math
import random
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app import main
from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
    require_public_read_or_owner_access,
)
from app.api import accounts as accounts_api
from app.api import earnings, stocks
from app.data_paths import get_data_paths
from app.personal_config import AccessConfig
from app.public_home_snapshot import (
    create_public_home_entry,
    earnings_resource_parameters,
    write_public_home_snapshot,
)
from app.services.accounts import AccountStore, set_account_store
from app.services.cache import cache
from app.services.watchlist_scope import DEFAULT_WATCHLIST_TICKERS
from tests.http_response_support import (
    anonymous_get_request as _areq,
    response_payload as _rp,
)

CASES = json.loads(
    (Path(__file__).parent / "fixtures" / "earnings_home_highlights_cases.json").read_text(
        encoding="utf-8"
    )
)
MARKET_DATE = date(2026, 7, 22)
# 纽约 2026-07-22 10:00（EDT），与夹具的 market_date 同一天。
FIXED_NOW = datetime(2026, 7, 22, 14, 0, tzinfo=timezone.utc).timestamp()
SAME_ORIGIN = {"Origin": "https://localhost", "X-Optix-Action": "1"}


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def _old_home_rows(
    rows: list[dict],
    today: str,
    pool: set[str] | frozenset[str],
    limit: int,
) -> list[dict]:
    """改版前 Home.tsx 对 mapUpcoming 结果做的事，逐句转写成 Python。

    mapUpcoming 丢掉没有代码或日期的行，市值非有限数或 <= 0 记为缺失；
    比较器是 JS 原文：重点 0/1 → date.localeCompare → (capB ?? -1) - (capA ?? -1)，
    Array.prototype.sort 与 sorted 都稳定。
    """

    def text(value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    def cap(row: dict) -> float | None:
        value = row.get("market_cap")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value) if math.isfinite(value) and value > 0 else None

    def featured(row: dict) -> int:
        return 0 if row.get("public_featured") is True or row["ticker"].upper() in pool else 1

    def compare(a: dict, b: dict) -> float:
        if featured(a) != featured(b):
            return featured(a) - featured(b)
        if a["earnings_date"] != b["earnings_date"]:
            return -1 if a["earnings_date"] < b["earnings_date"] else 1
        cap_a, cap_b = cap(a), cap(b)
        return (cap_b if cap_b is not None else -1) - (cap_a if cap_a is not None else -1)

    items = [
        row for row in rows if text(row.get("ticker")) and text(row.get("earnings_date"))
    ]
    upcoming = [row for row in items if row["earnings_date"] >= today]
    return sorted(upcoming, key=functools.cmp_to_key(compare))[:limit]


def _visible(rows: list[dict], today: str, limit: int) -> list[dict]:
    """新首页对接口结果做的事：按纽约日再滤一次、截 limit 行，不再排序。"""

    return [row for row in rows if row["earnings_date"] >= today][:limit]


def _tickers(rows: list[dict]) -> list[str]:
    return [row["ticker"] for row in rows]


@pytest.mark.parametrize("case", CASES["cases"], ids=lambda case: case["name"])
def test_selection_matches_the_shared_fixture_and_the_old_browser_rule(case: dict) -> None:
    pool = frozenset(CASES["pool"])
    assert pool == frozenset(DEFAULT_WATCHLIST_TICKERS)
    market_date = date.fromisoformat(case["market_date"])
    today = market_date.isoformat()
    tomorrow = (market_date + timedelta(days=1)).isoformat()
    limit = case["limit"]

    selected = earnings._home_highlight_rows(
        case["rows"], market_date=market_date, limit=limit
    )

    assert _tickers(selected) == case["expected"]["highlights"]
    assert len(selected) <= 2 * limit
    assert _tickers(_visible(selected, today, limit)) == case["expected"]["today"]
    assert _tickers(_visible(selected, tomorrow, limit)) == case["expected"]["tomorrow"]
    # 期望值本身就是改版前浏览器规则的结果（前端测试用 JS 原文再算一遍）。
    assert _tickers(_old_home_rows(case["rows"], today, pool, limit)) == case["expected"]["today"]
    assert _tickers(_old_home_rows(case["rows"], tomorrow, pool, limit)) == case["expected"]["tomorrow"]


def _random_calendar(rng: random.Random) -> list[dict]:
    caps = [None, None, 0.0, -5.0, 2e8, 5e9, 5e9, 2e10, 1e11, 1e11, 3e12]
    tickers = [*DEFAULT_WATCHLIST_TICKERS, *(f"T{index:03d}" for index in range(rng.randint(0, 60)))]
    rng.shuffle(tickers)
    rows = []
    for ticker in tickers:
        report = MARKET_DATE + timedelta(days=rng.randint(-3, 8))
        rows.append(
            {
                "ticker": ticker,
                "earnings_date": report.isoformat(),
                "public_featured": rng.random() < 0.3,
                "market_cap": rng.choice(caps) if rng.random() < 0.7 else rng.uniform(1e8, 4e12),
            }
        )
    # 快照里的日历按 (日期, 代码) 排好；同键并列时两边都必须沿用这个顺序。
    rows.sort(key=lambda row: (row["earnings_date"], row["ticker"]))
    return rows


@pytest.mark.parametrize("seed", range(200))
def test_cut_rows_reproduce_a_full_recompute_today_and_after_midnight(seed: int) -> None:
    rng = random.Random(seed)
    rows = _random_calendar(rng)
    limit = rng.choice([1, 2, 3, 6, 6, 6, 10])
    pool = frozenset(DEFAULT_WATCHLIST_TICKERS)
    selected = earnings._home_highlight_rows(rows, market_date=MARKET_DATE, limit=limit)

    for offset in (0, 1):
        day = (MARKET_DATE + timedelta(days=offset)).isoformat()
        expected = _old_home_rows(rows, day, pool, limit)
        assert [id(row) for row in _visible(selected, day, limit)] == [id(row) for row in expected]


def _snapshot_row(source: dict) -> dict:
    """把夹具行补成一条能通过公共快照校验的完整日历行（37 个字段）。"""

    report = date.fromisoformat(source["earnings_date"])
    observed = _iso(FIXED_NOW - 3600)
    market_cap = source["market_cap"]
    featured = source["public_featured"]
    return {
        "ticker": source["ticker"],
        "name": f"{source['ticker']} Inc.",
        "earnings_date": source["earnings_date"],
        "days_until": (report - MARKET_DATE).days,
        "timing": "amc",
        "eps_estimate": 1.25,
        "eps_actual": None,
        "eps_high": None,
        "eps_low": None,
        "revenue_estimate": None,
        "revenue_actual": None,
        "market_cap": market_cap,
        "market_cap_source": "massive_reference" if market_cap is not None else None,
        "market_cap_as_of": observed if market_cap is not None else None,
        "market_cap_status": "active" if market_cap is not None else "unavailable",
        "sector": "Technology",
        "earnings_date_source": "finnhub_calendar",
        "estimate_source": "finnhub_calendar",
        "estimate_sources": {"eps_estimate": "finnhub_calendar"},
        "actual_source": None,
        "release_status": "scheduled" if report >= MARKET_DATE else "reported_pending_actual",
        "quarter": None,
        "year": None,
        "source_status": "active",
        "observed_at": observed,
        "public_featured": featured,
        "featured_reasons": ["earnings_pool"] if featured else [],
        "calendar_sources": ["finnhub_calendar"],
        "calendar_date_status": "single_source",
        "calendar_conflict": None,
        "expected_move_pct": None,
        "expected_move_expiration": None,
        "expected_move_source": None,
        "expected_move_observed_at": None,
        "expected_move_underlying_price": None,
        "expected_move_method": None,
        "expected_move_status": "not_enriched",
    }


def _calendar_payload() -> dict:
    # 夹具第一组，去掉快照校验本就不会放行的「无代码 / 无日期」两行。
    rows = [
        _snapshot_row(row)
        for row in CASES["cases"][0]["rows"]
        if row["ticker"] and row["earnings_date"]
    ]
    return {
        "earnings": rows,
        "attempted": len(rows) + 2,
        "succeeded": len(rows),
        "failed_symbols": ["ABNB", "UBER"],
        "data_limited": False,
        "source_status": "active",
        "providers": ["Finnhub"],
        "as_of": _iso(FIXED_NOW - 600),
    }


def _publish_calendar(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    payload = _calendar_payload()
    entry = create_public_home_entry(
        "earnings",
        payload,
        saved_at=FIXED_NOW - 60,
        parameters=earnings_resource_parameters(MARKET_DATE),
    )
    write_public_home_snapshot(
        get_data_paths().public_home_snapshot,
        {"earnings": entry},
        now=FIXED_NOW,
    )
    return payload


@pytest.fixture()
def frozen_market_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """把财报模块的时钟钉在 FIXED_NOW：快照日期、过滤日期都是 MARKET_DATE。"""

    monkeypatch.setattr(
        earnings,
        "time",
        SimpleNamespace(time=lambda: FIXED_NOW, monotonic=time.monotonic),
    )
    monkeypatch.setattr(earnings, "_market_today", lambda: MARKET_DATE)
    cache.clear()
    yield
    cache.clear()


def _no_provider_scan(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def forbidden(*_args, **_kwargs):
        calls.append("build")
        raise AssertionError("an ordinary GET started a provider scan")

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", forbidden)
    return calls


def test_visitor_reads_the_same_snapshot_as_upcoming_with_same_rows_and_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    published = _publish_calendar(tmp_path, monkeypatch)
    calls = _no_provider_scan(monkeypatch)

    async def scenario():
        with request_owner_access_context(False):
            full = await earnings.upcoming_earnings(_areq())
            cut = await earnings.upcoming_earnings_highlights(_areq(), limit=6)
            revalidated = await earnings.upcoming_earnings_highlights(
                _areq(headers={"If-None-Match": cut.headers["etag"]}),
                limit=6,
            )
        return full, cut, revalidated

    full, cut, revalidated = asyncio.run(scenario())
    full_body, cut_body = _rp(full), _rp(cut)

    assert calls == []
    assert _tickers(cut_body["earnings"]) == CASES["cases"][0]["expected"]["highlights"]
    # 行与 /upcoming 同形状、同内容：前端 mapUpcomingPayload 原样复用。
    by_ticker = {row["ticker"]: row for row in full_body["earnings"]}
    assert all(row == by_ticker[row["ticker"]] for row in cut_body["earnings"])
    assert len(full_body["earnings"]) == len(published["earnings"])
    # 顶层元数据（含访客读快照时加的 _stale / degraded / snapshot_saved_at）原样带上。
    assert {key: value for key, value in cut_body.items() if key not in {"earnings", "market_date", "limit"}} == {
        key: value for key, value in full_body.items() if key != "earnings"
    }
    assert cut_body["_stale"] is True
    assert cut_body["source_status"] == "degraded"
    assert cut_body["snapshot_saved_at"] == full_body["snapshot_saved_at"]
    assert (cut_body["market_date"], cut_body["limit"]) == (MARKET_DATE.isoformat(), 6)
    assert cut.headers["cache-control"] == full.headers["cache-control"]
    assert cut.headers["etag"] != full.headers["etag"]
    assert revalidated.status_code == 304
    assert revalidated.body == b""


def test_password_owner_reads_the_worker_snapshot_and_never_scans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    _publish_calendar(tmp_path, monkeypatch)
    calls = _no_provider_scan(monkeypatch)
    private_config = earnings.get_personal_config()
    password_config = private_config.model_copy(
        update={"access": private_config.access.model_copy(update={"mode": "password"})}
    )
    monkeypatch.setattr(earnings, "get_personal_config", lambda: password_config)

    async def scenario():
        with request_owner_access_context(True):
            return (
                _rp(await earnings.upcoming_earnings(_areq())),
                _rp(await earnings.upcoming_earnings_highlights(_areq(), limit=6)),
            )

    full, cut = asyncio.run(scenario())

    assert calls == []
    assert "_stale" not in cut and cut["source_status"] == "active"
    assert {key: value for key, value in cut.items() if key not in {"earnings", "market_date", "limit"}} == {
        key: value for key, value in full.items() if key != "earnings"
    }
    assert _tickers(cut["earnings"]) == CASES["cases"][0]["expected"]["highlights"]


@pytest.mark.parametrize("owner", [False, True])
def test_missing_snapshot_is_unavailable_exactly_like_upcoming(
    owner: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls = _no_provider_scan(monkeypatch)
    private_config = earnings.get_personal_config()
    password_config = private_config.model_copy(
        update={"access": private_config.access.model_copy(update={"mode": "password"})}
    )
    monkeypatch.setattr(earnings, "get_personal_config", lambda: password_config)

    async def detail(endpoint):
        with request_owner_access_context(owner):
            with pytest.raises(HTTPException) as captured:
                await endpoint
        return captured.value.status_code, captured.value.detail

    upcoming = asyncio.run(detail(earnings.upcoming_earnings(_areq())))
    cut = asyncio.run(detail(earnings.upcoming_earnings_highlights(_areq(), limit=6)))

    assert cut == upcoming
    assert cut[0] == 503 and cut[1]["code"] == "public_snapshot_unavailable"
    assert calls == []


def test_private_network_owner_shares_the_upcoming_cache_key_and_single_build(
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    builds: list[date] = []
    payload = {**_calendar_payload(), "source_status": "active"}

    async def build(today: date):
        builds.append(today)
        await asyncio.sleep(0.01)
        return payload

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", build)

    async def scenario():
        with request_owner_access_context(True):
            cut, full = await asyncio.gather(
                earnings.upcoming_earnings_highlights(_areq(), limit=6),
                earnings.upcoming_earnings(_areq()),
            )
            again = await earnings.upcoming_earnings_highlights(_areq(), limit=6)
        return _rp(cut), _rp(full), again

    cut, full, again = asyncio.run(scenario())

    # 两个接口同一个缓存键、同一把单飞锁：冷缓存也只扫一次上游，命中后不再扫。
    assert builds == [MARKET_DATE]
    assert full["earnings"] == payload["earnings"]
    assert _tickers(cut["earnings"]) == CASES["cases"][0]["expected"]["highlights"]
    assert _tickers(_rp(again)["earnings"]) == _tickers(cut["earnings"])
    assert again.headers["etag"]


def test_repeat_reads_reuse_the_cut_rows_until_the_snapshot_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    _publish_calendar(tmp_path, monkeypatch)
    _no_provider_scan(monkeypatch)
    cuts: list[int] = []
    original = earnings._home_highlight_rows

    def counted(*args, **kwargs):
        cuts.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(earnings, "_home_highlight_rows", counted)

    async def read(limit: int):
        with request_owner_access_context(False):
            return _rp(await earnings.upcoming_earnings_highlights(_areq(), limit=limit))

    first = asyncio.run(read(6))
    second = asyncio.run(read(6))
    other_limit = asyncio.run(read(3))

    assert first == second
    assert len(cuts) == 2  # limit=6 一次、limit=3 一次；第二次 limit=6 直接复用
    assert _tickers(other_limit["earnings"])[:3] == CASES["cases"][0]["expected"]["today"][:3]


def _account_app(store: AccountStore) -> TestClient:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("owner-password-for-highlight-tests"),
    )
    read_gate = [Depends(require_public_read_or_owner_access)]
    app.include_router(accounts_api.router)
    app.include_router(earnings.router, dependencies=read_gate)
    app.include_router(stocks.router, dependencies=read_gate)
    return TestClient(app, base_url="https://localhost")


def test_visitor_and_signed_in_account_get_the_pool_the_old_home_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    """登录账号自选里有一只非公共重点的公司（AAOI，当天、市值 50 亿）。

    改版前首页的 poolTickers 来自不带 tickers 的 /api/stocks/watchlist，访客和
    登录账号拿到的都是默认关注池；账号自选只进「关注动态」。新接口必须给出同样
    的几行：AAOI 不提前，默认池里的 MSFT（公共标注为否）照旧提前。
    """

    _publish_calendar(tmp_path, monkeypatch)
    calls = _no_provider_scan(monkeypatch)
    store = AccountStore(tmp_path / "accounts.db")
    set_account_store(store)
    accounts_api.reset_rate_limits()
    now = time.time()
    quote_rows = [
        {"ticker": ticker, "name": ticker, "price": 100.0, "change_percent": 1.0, "spark": [99.0, 100.0]}
        for ticker in [*DEFAULT_WATCHLIST_TICKERS, "TSLA"]
    ]
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing-watchlist.json")
    monkeypatch.setattr(
        stocks,
        "_endpoint_cache",
        {
            "watchlist": stocks._EndpointCacheEntry(
                now + 60,
                now + 300,
                now - 30,
                {"groups": [{"id": "core", "name": "Core", "stocks": quote_rows}]},
            )
        },
    )
    try:
        visitor = _account_app(store)
        customer = _account_app(store)
        registered = customer.post(
            "/api/account/register",
            json={"username": "highlight-reader", "password": "fixture-password-for-tests"},
            headers=SAME_ORIGIN,
        )
        assert registered.status_code == 201, registered.text
        edited = customer.patch(
            "/api/account/watchlist", json={"add": ["AAOI"]}, headers=SAME_ORIGIN
        )
        assert edited.status_code == 200, edited.text
        assert customer.get("/api/account/watchlist").json()["tickers"] == ["AAOI"]

        bodies = {}
        for label, client in (("visitor", visitor), ("customer", customer)):
            upcoming = client.get("/api/earnings/upcoming")
            pool_response = client.get("/api/stocks/watchlist")
            cut = client.get("/api/earnings/upcoming/highlights?limit=6")
            assert (upcoming.status_code, pool_response.status_code, cut.status_code) == (200, 200, 200)
            # 改版前首页的 poolTickers：mapWatchlist 取出的代码（大写）。
            old_pool = {
                row["ticker"].upper()
                for group in pool_response.json()["groups"]
                for row in group["stocks"]
            }
            assert old_pool == set(DEFAULT_WATCHLIST_TICKERS)
            old_card = _old_home_rows(upcoming.json()["earnings"], MARKET_DATE.isoformat(), old_pool, 6)
            new_card = _visible(cut.json()["earnings"], MARKET_DATE.isoformat(), 6)
            assert _tickers(new_card) == _tickers(old_card), label
            assert "AAOI" not in _tickers(cut.json()["earnings"])
            assert "MSFT" in _tickers(cut.json()["earnings"])
            bodies[label] = cut.content
        # 若错把账号自选并进关注池，AAOI 会挤进前 6 行——夹具对口径来源敏感。
        assert "AAOI" in _tickers(
            _old_home_rows(
                _calendar_payload()["earnings"],
                MARKET_DATE.isoformat(),
                {*DEFAULT_WATCHLIST_TICKERS, "AAOI"},
                6,
            )
        )
        assert bodies["customer"] == bodies["visitor"]
        assert calls == []
    finally:
        set_account_store(None)
        accounts_api.reset_rate_limits()


@pytest.mark.parametrize("query", ["limit=0", "limit=21", "limit=six"])
def test_limit_is_bounded(
    query: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    frozen_market_clock: None,
) -> None:
    _publish_calendar(tmp_path, monkeypatch)
    store = AccountStore(tmp_path / "accounts.db")
    set_account_store(store)
    try:
        client = _account_app(store)
        assert client.get(f"/api/earnings/upcoming/highlights?{query}").status_code == 422
        default = client.get("/api/earnings/upcoming/highlights")
        assert default.status_code == 200
        assert default.json()["limit"] == earnings.HOME_HIGHLIGHT_DEFAULT_LIMIT == 6
    finally:
        set_account_store(None)


def test_gateway_treats_highlights_like_upcoming() -> None:
    for path in ("/api/earnings/upcoming", "/api/earnings/upcoming/highlights"):
        assert main._is_public_read_request(path, "GET") is True
        assert main._is_cached_market_read_path(path, "GET") is True
        assert main._is_heavy_api_path(path, "GET") is False
    assert main._is_public_read_request("/api/earnings/upcoming/highlights", "POST") is False
