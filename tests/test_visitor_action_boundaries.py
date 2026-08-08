"""密码模式下非 Owner 的动作边界（2026-07-27 审计批）。

目标口径：
- 匿名访客默认只读已保存的快照，不触发模型任务、实时行情拉取、
  期权冷启动扫描或外部数据补全；
- 个股手动拉取（2026-08-08 起）对登录客户开放并按账号限额——正向
  路径钉在 tests/test_stock_pull_access.py，本文件守匿名/伪造 cookie
  仍被拒的一半；其余动作面朋友账号与匿名同样只读；
- `[access] visitor_live_pulls` / `visitor_ai_actions` 是仅有的两个例外
  开关，默认关闭，打开后原有限流、冷却与同源校验仍然生效。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_allows_visitor_live_pulls,
    request_owner_access_context,
    require_public_read_or_owner_access,
    require_same_origin_action,
)
from app.api import accounts as accounts_api
from app.api import sectors, stocks
from app.api.accounts import ACCOUNT_COOKIE_NAME
from app.services.accounts import AccountStore, set_account_store
from app.main import _GatewayMiddleware
from app.personal_config import AccessConfig
from app.services.catalysts import economic_calendar_actuals as actuals

PASSWORD = "visitor-boundary-test-password"


def _runtime(**config_kwargs) -> OwnerAccessRuntime:
    return OwnerAccessRuntime(
        AccessConfig(mode="password", **config_kwargs),
        password_hash=hash_owner_password(PASSWORD),
    )


def _action_headers(origin: str = "https://testserver") -> dict[str, str]:
    return {
        "Origin": origin,
        "Sec-Fetch-Site": "same-origin",
        "X-Optix-Action": "1",
        "Content-Type": "application/json",
    }


def _bare_request(app: FastAPI | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "query_string": b"",
        "scheme": "https",
        "server": ("example.test", 443),
        "client": ("203.0.113.9", 12345),
    }
    if app is not None:
        scope["app"] = app
    return Request(scope)


def _gateway_app(runtime: OwnerAccessRuntime) -> FastAPI:
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(
        stocks.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )
    app.add_middleware(_GatewayMiddleware, access_runtime=runtime)
    return app


# ── 个股手动拉取 ──────────────────────────────────────────────


def test_anonymous_stock_pull_is_rejected_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def unexpected_pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("visitor pull must not reach providers by default")

    monkeypatch.setattr(stocks, "_pull_stock_data_once", unexpected_pull)
    app = _gateway_app(_runtime())
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post(
            "/api/stocks/AAOI/pull",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 401
    # 2026-08-08 起拉取对登录客户开放：匿名的拒绝语义从 owner_login_required
    # 改为「请先登录」，引导注册/登录而不是索要 Owner 口令。
    assert response.json()["error"] == "account_login_required"
    assert calls == 0


def test_forged_account_cookie_is_still_anonymous_for_stock_pull(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """伪造的账号 Cookie 不构成登录会话——拉取面保持匿名 401。

    登录客户可拉取的正向路径（含 acct:{uid} 限额）钉在
    tests/test_stock_pull_access.py；这里守边界的另一半：随手捏一个
    cookie 值不能把写面打开，也不能把请求抬成 Owner。
    """

    async def unexpected_pull(symbol: str) -> dict:
        raise AssertionError("forged account cookie must not reach providers")

    monkeypatch.setattr(stocks, "_pull_stock_data_once", unexpected_pull)
    app = _gateway_app(_runtime())
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(ACCOUNT_COOKIE_NAME, "usr_forged_cookie_value")
        response = client.post(
            "/api/stocks/AAOI/pull",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 401
    assert response.json()["error"] == "account_login_required"


def test_signed_in_customer_pull_passes_both_gateway_layers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    """真实账号会话要同时过 ASGI 中间件与路由依赖两层。

    test_stock_pull_access.py 的迷你 app 没挂 _GatewayMiddleware，只测了
    依赖层；这里把两层叠起来测，防止两层口径漂移（历史上两层各自为政
    出过分叉）。"""

    calls = 0

    async def pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", pull)
    stocks._stock_pull_tasks.clear()
    stocks._public_stock_pull_ticker_deadlines.clear()
    stocks._public_stock_pull_recent.clear()
    set_account_store(AccountStore(tmp_path / "accounts.db"))
    accounts_api.reset_rate_limits()
    app = _gateway_app(_runtime())
    app.include_router(accounts_api.router)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            registered = client.post(
                "/api/account/register",
                json={"username": "boundary_carol", "password": "pw"},
                headers=_action_headers(),
            )
            assert registered.status_code == 201
            response = client.post(
                "/api/stocks/AAOI/pull",
                json={},
                headers=_action_headers(),
            )
        assert response.status_code == 200
        assert calls == 1
    finally:
        set_account_store(None)
        accounts_api.reset_rate_limits()
        stocks._stock_pull_tasks.clear()
        stocks._public_stock_pull_ticker_deadlines.clear()
        stocks._public_stock_pull_recent.clear()


def test_visitor_stock_pull_flag_reopens_the_bounded_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    async def pull(symbol: str) -> dict:
        nonlocal calls
        calls += 1
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_pull_stock_data_once", pull)
    stocks._stock_pull_tasks.clear()
    stocks._public_stock_pull_ticker_deadlines.clear()
    stocks._public_stock_pull_recent.clear()
    app = _gateway_app(_runtime(visitor_live_pulls=True))
    try:
        with TestClient(app, base_url="https://testserver") as client:
            response = client.post(
                "/api/stocks/AAOI/pull",
                json={},
                headers=_action_headers(),
            )
        assert response.status_code == 200
        assert calls == 1
    finally:
        stocks._stock_pull_tasks.clear()
        stocks._public_stock_pull_ticker_deadlines.clear()
        stocks._public_stock_pull_recent.clear()


# ── 财报影响 AI 提交 ─────────────────────────────────────────


def _post_request(app: FastAPI, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [
                (b"origin", b"https://example.test"),
                (b"host", b"example.test"),
                (b"content-type", b"application/json"),
                (b"sec-fetch-site", b"same-origin"),
                (b"x-optix-action", b"1"),
            ],
            "query_string": b"",
            "scheme": "https",
            "server": ("example.test", 443),
            "client": ("203.0.113.9", 12345),
            "app": app,
        }
    )


def test_visitor_earnings_submission_requires_owner_by_default() -> None:
    app = FastAPI()
    app.state.access_runtime = _runtime()
    request = _post_request(app, "/api/ai/earnings-impact/AAPL/reports/2026-07-23")
    request.state.owner_access = False
    with pytest.raises(Exception) as excinfo:
        require_same_origin_action(request)
    detail = getattr(excinfo.value, "detail", {})
    assert detail.get("code") == "owner_login_required"


def test_visitor_earnings_submission_flag_restores_same_origin_branch() -> None:
    app = FastAPI()
    app.state.access_runtime = _runtime(visitor_ai_actions=True)
    request = _post_request(app, "/api/ai/earnings-impact/AAPL/reports/2026-07-23")
    request.state.owner_access = False
    # 打开开关后走同源 JSON 校验分支；同源头齐全时不抛异常。
    require_same_origin_action(request)


# ── 板块 IV 冷启动 ───────────────────────────────────────────


def test_visitor_cold_sector_iv_returns_503_without_provider_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sector_id = "default-off-sector"
    monkeypatch.setitem(
        sectors.SECTORS,
        sector_id,
        {"name": "Default Off Sector", "tickers": ["AAA", "BBB"]},
    )
    monkeypatch.setattr(sectors, "_SECTOR_IV_SNAPSHOT_DIR", tmp_path / "missing")
    calls = 0

    async def unexpected_scan(_sector_id: str) -> dict:
        nonlocal calls
        calls += 1
        raise AssertionError("visitor cold start must not scan providers by default")

    monkeypatch.setattr(sectors, "_iv_ranking_payload", unexpected_scan)

    async def scenario():
        with request_owner_access_context(False):
            try:
                await sectors._request_iv_payload(
                    sector_id,
                    public_client_id="203.0.113.9",
                    visitor_live_allowed=False,
                )
            except Exception as error:  # noqa: BLE001 - assert on shape below
                return error
            return None

    error = asyncio.run(scenario())
    assert error is not None
    assert getattr(error, "status_code", None) == 503
    assert error.detail["code"] == "public_snapshot_unavailable"
    assert calls == 0
    assert not (tmp_path / "missing").exists()


def test_request_allows_visitor_live_pulls_prefers_injected_runtime() -> None:
    app = FastAPI()
    app.state.access_runtime = _runtime(visitor_live_pulls=True)
    assert request_allows_visitor_live_pulls(_bare_request(app)) is True
    app.state.access_runtime = _runtime()
    assert request_allows_visitor_live_pulls(_bare_request(app)) is False
    # 无 app 的裸请求回落到部署配置的默认值（False）。
    assert request_allows_visitor_live_pulls(_bare_request(None)) is False


# ── 经济日历 actual 补全 ─────────────────────────────────────


def _calendar_payload(scheduled_at: datetime) -> dict:
    return {
        "items": [
            {
                "event_id": "evt-1",
                "title": "CPI",
                "scheduled_at_utc": scheduled_at.isoformat(),
                "actual": None,
            }
        ]
    }


def test_calendar_enrich_without_fetch_stays_local(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("visitor calendar read must not call the provider")

    monkeypatch.setattr(actuals, "_fetch_source_rows", unexpected_fetch)
    monkeypatch.setattr(actuals, "_cache", {})
    as_of = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)
    payload = _calendar_payload(as_of - timedelta(hours=3))

    result = asyncio.run(
        actuals.enrich_recent_actuals(
            payload,
            date_from=date(2026, 7, 25),
            date_to=date(2026, 7, 28),
            as_of=as_of,
            allow_fetch=False,
        )
    )

    fallback = result["actual_fallback"]
    assert fallback["status"] == "skipped"
    assert fallback["reason"] == "visitor_read_only"
    assert fallback["filled"] == 0
    # 条目原样保留，actual 仍为 None——不伪造已公布值。
    assert result["items"][0]["actual"] is None


def test_calendar_enrich_without_fetch_reuses_fresh_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    as_of = datetime(2026, 7, 27, 15, 0, tzinfo=timezone.utc)
    scheduled = as_of - timedelta(hours=3)
    payload = _calendar_payload(scheduled)

    async def unexpected_fetch(*_args, **_kwargs):
        raise AssertionError("fresh cache must be reused without provider calls")

    monkeypatch.setattr(actuals, "_fetch_source_rows", unexpected_fetch)

    captured: dict = {}

    def fake_merge(output, source_rows, *, as_of):
        captured["rows"] = source_rows
        return dict(output), 1, 1

    monkeypatch.setattr(actuals, "merge_recent_actuals", fake_merge)

    start = max(
        date(2026, 7, 25),
        as_of.date() - timedelta(days=actuals._MAX_ENRICH_DAYS - 1),
    )
    end = min(date(2026, 7, 28), as_of.date())
    key = (start.isoformat(), end.isoformat())
    cached_rows = [{"id": "row-1"}]
    monkeypatch.setattr(
        actuals,
        "_cache",
        {key: (actuals.monotonic_time.monotonic() + 60, cached_rows)},
    )

    result = asyncio.run(
        actuals.enrich_recent_actuals(
            payload,
            date_from=date(2026, 7, 25),
            date_to=date(2026, 7, 28),
            as_of=as_of,
            allow_fetch=False,
        )
    )

    assert captured["rows"] == cached_rows
    assert result["actual_fallback"]["status"] == "active"
    assert result["actual_fallback"]["filled"] == 1
