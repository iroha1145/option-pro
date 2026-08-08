"""CTA 端点与快照边界：访客只读快照、不触发供应商、真负载过校验层。

- 匿名/普通读 = worker 快照，快照缺失 → 诚实 503，绝不现算；
- 真实构建器输出必须通过 public_home 校验层（否则 worker 发布会被静默拒收）；
- 盘中未收盘末根不进正式估算，另做暂定标记；
- method_version 进快照参数：模型换代旧快照自动失效。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import OwnerAccessRuntime, hash_owner_password
from app.api import market
from app.main import _GatewayMiddleware
from app.personal_config import AccessConfig, PublicHomeConfig
from app.public_home_snapshot import (
    PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER,
    PUBLIC_HOME_RESOURCE_ORDER,
    create_public_home_entry,
    public_home_resource_parameters,
)
from app.services.cta.config import INSTRUMENTS, METHOD_VERSION, MIN_BARS_REQUIRED
from tests.test_cta_trend_model import _bars_from_closes, _drift_series

PASSWORD = "cta-endpoint-test-password"


def _app() -> FastAPI:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password(PASSWORD),
    )
    app.include_router(market.router)
    app.add_middleware(_GatewayMiddleware, access_runtime=app.state.access_runtime)
    return app


def _past_bars(n: int = MIN_BARS_REQUIRED + 40) -> list[dict]:
    bars = _bars_from_closes(_drift_series(n, 0.002, 5, 0.006))
    # 时间戳压到远过去，保证「末根已收盘」判定恒成立。
    base = int(time.time()) - (n + 30) * 86_400
    for i, bar in enumerate(bars):
        bar["t"] = base + i * 86_400
    return bars


async def _fake_chart_impl(symbol: str, range_key: str, adjustment: str):
    return {"bars": _past_bars()}


def test_anonymous_read_serves_snapshot_only(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_snapshot(*args, **kwargs):
        return None

    async def must_not_build():
        raise AssertionError("匿名读取绝不允许现算/触发供应商")

    monkeypatch.setattr(market, "read_public_home_resource_async", no_snapshot)
    monkeypatch.setattr(market, "_build_cta_trend", must_not_build)
    client = TestClient(_app(), base_url="https://testserver")
    response = client.get("/api/market/cta")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "public_snapshot_unavailable"


def test_anonymous_read_returns_published_snapshot_with_etag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = {
        "method_version": METHOD_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "proxy_note": "etf_trend_proxy",
        "source_status": "active",
        "instruments": [],
        "snapshot_saved_at": time.time(),
        "_stale": False,
    }

    async def snapshot(*args, **kwargs):
        return dict(published)

    async def must_not_build():
        raise AssertionError("有快照时也不许现算")

    monkeypatch.setattr(market, "read_public_home_resource_async", snapshot)
    monkeypatch.setattr(market, "_build_cta_trend", must_not_build)
    client = TestClient(_app(), base_url="https://testserver")
    response = client.get("/api/market/cta")
    assert response.status_code == 200
    assert response.json()["method_version"] == METHOD_VERSION
    etag = response.headers.get("etag")
    assert etag
    replay = client.get("/api/market/cta", headers={"If-None-Match": etag})
    assert replay.status_code == 304


def test_real_builder_output_passes_snapshot_validators(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import stocks

    monkeypatch.setattr(stocks, "_stock_chart_impl", _fake_chart_impl)
    import asyncio

    payload = asyncio.run(market._build_cta_trend())
    assert payload["source_status"] == "active"
    assert len(payload["instruments"]) == len(INSTRUMENTS)
    row = payload["instruments"][0]
    assert row["settlement_confirmed"] is True
    assert row["position_score"] is not None
    assert row["trigger_levels"]["above"] or row["trigger_levels"]["below"]
    # 关键：真负载必须通过 public_home 的 cta_trend 校验层，否则 worker
    # 每次发布都会被静默拒收，访客永远 503。
    now = time.time()
    entry = create_public_home_entry(
        "cta_trend",
        payload,
        saved_at=now,
        parameters=public_home_resource_parameters("cta_trend", now=now),
    )
    assert entry["schema"] == "cta-trend-v1"


def test_builder_degrades_per_instrument_without_fabricating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import stocks

    async def flaky_chart(symbol: str, range_key: str, adjustment: str):
        if symbol == "IWM":
            raise RuntimeError("provider down")
        return {"bars": _past_bars()}

    monkeypatch.setattr(stocks, "_stock_chart_impl", flaky_chart)
    import asyncio

    payload = asyncio.run(market._build_cta_trend())
    assert payload["source_status"] == "degraded"
    broken = next(r for r in payload["instruments"] if r["instrument"] == "russell2000")
    assert broken["source_status"] == "unavailable"
    assert broken["position_score"] is None
    assert broken["trigger_levels"] is None


def test_intraday_last_bar_is_provisional_not_settled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from app.api import stocks

    # 固定时钟到 2026-08-05（周三）14:00 ET 盘中——真实时钟落在周末时，
    # 「当日 bar」会被正确判为已收盘，测试就失去意义。
    session_now = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(
        market,
        "datetime",
        SimpleNamespace(now=lambda tz=None: session_now, fromtimestamp=datetime.fromtimestamp),
    )
    bars = _past_bars()
    bars[-1] = {**bars[-1], "t": int(datetime(2026, 8, 5, 13, 30, tzinfo=timezone.utc).timestamp())}

    async def chart_with_live_bar(symbol: str, range_key: str, adjustment: str):
        return {"bars": [dict(b) for b in bars]}

    monkeypatch.setattr(stocks, "_stock_chart_impl", chart_with_live_bar)
    import asyncio

    payload = asyncio.run(market._build_cta_trend())
    row = payload["instruments"][0]
    assert row["source_status"] == "active"
    # 正式估算截止于倒数第二根（最后一根未收盘）。
    assert row["data_through"] == bars[-2]["trade_date"]
    # 历史里绝不含盘中暂定值。
    assert all(item["date"] != bars[-1]["trade_date"] for item in row["history"])
    if row["intraday"] is not None:
        assert row["intraday"]["provisional"] is True


def test_cta_trend_is_registered_as_optional_resource() -> None:
    assert "cta_trend" in PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER
    assert "cta_trend" not in PUBLIC_HOME_RESOURCE_ORDER  # 不卡 release 闸门
    params = public_home_resource_parameters("cta_trend", now=time.time())
    assert params["method_version"] == METHOD_VERSION
    assert params["instruments"] == [item.key for item in INSTRUMENTS]
    assert PublicHomeConfig().cta_seconds == 1800
