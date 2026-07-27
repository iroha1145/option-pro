"""财报页升级的行为契约：重点公司、双日历、市值解析与预期波动 provider 链。

对应任务清单：
- #1 市值 ≥ 门槛进入重点；#2 市值 unknown ≠ small；#3 公共池仍进入重点；
- #5 预期波动缺失不影响资格；#6 FMP 未配置/失败时 Finnhub 主路不变；
- #7 双日历重复记录稳定去重；#8 日期冲突不静默合并；
- #14 访客读取不触发供应商；#15 Owner 手动刷新受权限保护；
- provider 链优先级、宽价差/last-price 拒绝、市值缓存与快照校验器。
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.api import earnings
from app.services import earnings_enrichment as enrich
from app.public_home_snapshot import validate_public_home_payload


TODAY = date(2026, 7, 23)


def _finnhub_success(rows: list[dict[str, object]]) -> dict[str, object]:
    return earnings._finnhub_fetch_result(
        rows=rows,
        configured=True,
        succeeded=True,
    )


def _fmp_success(rows: list[dict[str, object]]) -> dict[str, object]:
    return {
        "rows": rows,
        "configured": True,
        "succeeded": True,
        "error": None,
    }


def _fmp_absent() -> dict[str, object]:
    return {
        "rows": [],
        "configured": False,
        "succeeded": False,
        "error": "not_configured",
    }


def _calendar_row(
    ticker: str,
    *,
    days: int = 5,
    eps_estimate: float | None = 1.2,
) -> dict[str, object]:
    return {
        "ticker": ticker,
        "earnings_date": (TODAY + timedelta(days=days)).isoformat(),
        "days_until": days,
        "timing": "amc",
        "eps_estimate": eps_estimate,
        "eps_actual": None,
        "revenue_estimate": None,
        "revenue_actual": None,
        "quarter": None,
        "year": None,
    }


class _NoYahooTicker:
    """策展池的 Yahoo 探测在这些测试里保持沉默（数据全部来自日历源）。"""

    calendar = None
    info: dict[str, object] = {}

    def get_earnings_dates(self, limit=12):  # noqa: ANN001 - yfinance signature
        raise RuntimeError("yahoo probing disabled in this test")


@pytest.fixture()
def isolated_build(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """构建器的隔离环境：无策展池、无市值缓存文件、无预期波动外呼。"""

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", [])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: _NoYahooTicker())
    monkeypatch.setattr(
        enrich,
        "market_cap_cache_path",
        lambda: tmp_path / "earnings-market-caps-v1.json",
    )
    monkeypatch.setattr(
        earnings,
        "_expected_move_for_report",
        lambda *_args, **_kwargs: {
            "expected_move_status": "unavailable:no_expiration"
        },
    )

    def build(
        *,
        finnhub_rows: list[dict[str, object]] | None = None,
        fmp_result: dict[str, object] | None = None,
        profiles: dict[str, dict[str, object]] | None = None,
        threshold: float | None = None,
    ) -> dict[str, object]:
        async def finnhub(_today: date) -> dict[str, object]:
            return _finnhub_success(list(finnhub_rows or []))

        async def fmp(_today: date, **_kwargs) -> dict[str, object]:
            return dict(fmp_result) if fmp_result is not None else _fmp_absent()

        async def fmp_profiles(tickers: list[str]) -> dict[str, object]:
            available = {
                ticker: profile
                for ticker, profile in (profiles or {}).items()
                if ticker in tickers
            }
            return {
                "configured": bool(profiles),
                "succeeded": bool(profiles),
                "error": None if profiles else "not_configured",
                "profiles": available,
            }

        monkeypatch.setattr(earnings, "_fetch_finnhub_earnings", finnhub)
        monkeypatch.setattr(enrich, "fetch_fmp_calendar", fmp)
        monkeypatch.setattr(enrich, "fetch_fmp_profiles", fmp_profiles)
        if threshold is not None:
            config = earnings.get_personal_config()
            monkeypatch.setattr(
                type(config.earnings),
                "featured_market_cap_usd",
                property(lambda _self: threshold),
            )
        return asyncio.run(earnings._build_upcoming_earnings(TODAY))

    return build


# ── #1 / #2 / #3 / #5：重点资格 ───────────────────────────────


def test_market_cap_above_threshold_marks_public_featured(isolated_build) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("BIGCO")],
        profiles={"BIGCO": {"market_cap": 25_000_000_000.0, "name": "Big Co"}},
    )
    row = payload["earnings"][0]
    assert row["market_cap"] == 25_000_000_000.0
    assert row["market_cap_source"] == "fmp_profile"
    assert row["market_cap_status"] == "active"
    assert row["public_featured"] is True
    assert row["featured_reasons"] == ["market_cap"]


def test_unknown_market_cap_is_not_treated_as_small(isolated_build) -> None:
    payload = isolated_build(finnhub_rows=[_calendar_row("MYSTERY")])
    row = payload["earnings"][0]
    # unknown 状态是显式的，不是「小公司」：行完整保留在全市场日历里
    assert row["market_cap"] is None
    assert row["market_cap_status"] == "unavailable"
    assert row["market_cap_source"] is None
    assert row["public_featured"] is False
    assert row["featured_reasons"] == []
    assert payload["succeeded"] == 1


def test_pool_member_below_threshold_stays_featured(
    isolated_build,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["GROWTH"])
    monkeypatch.setattr(earnings.yf, "Ticker", lambda _symbol: _NoYahooTicker())
    payload = isolated_build(
        finnhub_rows=[_calendar_row("GROWTH")],
        profiles={"GROWTH": {"market_cap": 3_000_000_000.0, "name": "Growth"}},
    )
    row = payload["earnings"][0]
    assert row["market_cap"] == 3_000_000_000.0
    assert row["public_featured"] is True
    assert row["featured_reasons"] == ["earnings_pool"]


def test_missing_expected_move_does_not_affect_featured(isolated_build) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("BIGCO")],
        profiles={"BIGCO": {"market_cap": 25_000_000_000.0}},
    )
    row = payload["earnings"][0]
    assert row["expected_move_pct"] is None
    assert row["expected_move_status"] == "unavailable:no_expiration"
    assert row["public_featured"] is True


# ── #6：FMP 未配置/失败时 Finnhub 主路不变 ───────────────────


def test_finnhub_calendar_is_unchanged_without_fmp(isolated_build) -> None:
    rows = [_calendar_row("AAOI"), _calendar_row("SOLO", days=8)]
    without_fmp = isolated_build(finnhub_rows=rows, fmp_result=_fmp_absent())
    failed_fmp = isolated_build(
        finnhub_rows=rows,
        fmp_result={
            "rows": [],
            "configured": True,
            "succeeded": False,
            "error": "timeout",
        },
    )
    for payload in (without_fmp, failed_fmp):
        assert [r["ticker"] for r in payload["earnings"]] == ["AAOI", "SOLO"]
        assert payload["providers"] == ["Finnhub"]
        assert payload["data_limited"] is False
        assert all(
            r["calendar_sources"] == ["finnhub_calendar"]
            and r["calendar_date_status"] == "single_source"
            for r in payload["earnings"]
        )


# ── #7 / #8：双日历去重与日期冲突 ────────────────────────────


def test_duplicate_calendar_rows_deduplicate_and_confirm(isolated_build) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("BOTH")],
        fmp_result=_fmp_success([_calendar_row("BOTH", eps_estimate=1.4)]),
    )
    rows = [r for r in payload["earnings"] if r["ticker"] == "BOTH"]
    assert len(rows) == 1, "同一公司同一日期必须稳定去重成一行"
    row = rows[0]
    assert sorted(row["calendar_sources"]) == ["finnhub_calendar", "fmp_calendar"]
    assert row["calendar_date_status"] == "confirmed"
    assert row["calendar_conflict"] is None
    # 主源（Finnhub）的预期值保留，不被次源覆盖
    assert row["eps_estimate"] == 1.2
    assert row["estimate_source"] == "finnhub_calendar"


def test_calendar_date_conflict_is_recorded_not_silently_merged(
    isolated_build,
) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("CLASH", days=5)],
        fmp_result=_fmp_success([_calendar_row("CLASH", days=7)]),
    )
    row = next(r for r in payload["earnings"] if r["ticker"] == "CLASH")
    # 主源日期保留
    assert row["earnings_date"] == (TODAY + timedelta(days=5)).isoformat()
    assert row["calendar_date_status"] == "conflict"
    assert row["calendar_conflict"] == {
        "fmp_calendar": (TODAY + timedelta(days=7)).isoformat()
    }
    # 冲突行仍通过公开快照校验（冲突是一等公民，不是坏数据）
    assert validate_public_home_payload("earnings", payload) is not None


def test_fmp_only_company_joins_the_calendar(isolated_build) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("FINN")],
        fmp_result=_fmp_success([_calendar_row("FMPO", days=6)]),
    )
    tickers = [r["ticker"] for r in payload["earnings"]]
    assert "FMPO" in tickers
    row = next(r for r in payload["earnings"] if r["ticker"] == "FMPO")
    assert row["earnings_date_source"] == "fmp_calendar"
    assert row["calendar_sources"] == ["fmp_calendar"]
    assert set(payload["providers"]) == {"Finnhub", "FMP"}


# ── 市值缓存与来源优先级 ─────────────────────────────────────


def test_market_cap_cache_roundtrip_and_fresh_hit(tmp_path) -> None:
    path = tmp_path / "caps.json"
    now_iso = datetime.now(timezone.utc).isoformat()
    enrich.store_market_cap_cache(
        {"CACHED": {"market_cap": 9e9, "source": "fmp_profile", "as_of": now_iso}},
        path,
    )
    loaded = enrich.load_market_cap_cache(path)
    assert loaded["CACHED"]["market_cap"] == 9e9

    async def unexpected_profiles(_tickers):
        raise AssertionError("fresh cache hit must not call FMP")

    resolved = asyncio.run(
        _resolve_with(
            [{"ticker": "CACHED", "market_cap": None, "days_until": 3}],
            path,
            unexpected_profiles,
        )
    )
    assert resolved["CACHED"]["market_cap"] == 9e9
    assert resolved["CACHED"]["status"] == "cached"


async def _resolve_with(rows, path, profiles_fn):
    import unittest.mock as mock

    with mock.patch.object(enrich, "fetch_fmp_profiles", profiles_fn):
        return await enrich.resolve_market_caps(
            rows,
            cache_days=3,
            cache_path=path,
        )


def test_stale_cache_is_used_as_cached_when_providers_fail(tmp_path) -> None:
    path = tmp_path / "caps.json"
    old_iso = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    enrich.store_market_cap_cache(
        {"OLD": {"market_cap": 5e9, "source": "fmp_profile", "as_of": old_iso}},
        path,
    )

    async def failing_profiles(_tickers):
        return {
            "configured": True,
            "succeeded": False,
            "error": "timeout",
            "profiles": {},
        }

    resolved = asyncio.run(
        _resolve_with(
            [{"ticker": "OLD", "market_cap": None, "days_until": 2}],
            path,
            failing_profiles,
        )
    )
    # 过期缓存好过没有：值可用但状态与 as_of 明确可识别为旧
    assert resolved["OLD"]["market_cap"] == 5e9
    assert resolved["OLD"]["status"] == "cached"
    assert resolved["OLD"]["as_of"] == old_iso


# ── 预期波动 provider 链 ─────────────────────────────────────


def _success_payload(source: str) -> dict[str, object]:
    return {
        "expected_move_pct": 5.5,
        "expected_move_expiration": "2026-08-01",
        "expected_move_source": source,
        "expected_move_observed_at": "2026-07-23T20:00:00+00:00",
        "expected_move_underlying_price": 100.0,
        "expected_move_method": "atm_straddle_mid",
        "expected_move_status": "active",
    }


def test_provider_priority_first_success_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def massive(*_args):
        calls.append("massive")
        return _success_payload("Massive options")

    def marketdata(*_args):
        calls.append("marketdata")
        return _success_payload("MarketData.app options")

    def yahoo(*_args):
        calls.append("yahoo")
        return _success_payload("Yahoo/yfinance options")

    monkeypatch.setattr(
        enrich,
        "EXPECTED_MOVE_PROVIDERS",
        (("massive", massive), ("marketdata", marketdata), ("yahoo", yahoo)),
    )
    result = enrich.expected_move_for_report("T", TODAY + timedelta(days=5), TODAY, "amc")
    assert result["expected_move_source"] == "Massive options"
    assert calls == ["massive"], "第一个成功的 provider 之后不得再请求其它来源"


def test_provider_chain_falls_through_permission_and_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "EXPECTED_MOVE_PROVIDERS",
        (
            ("massive", lambda *_: {"expected_move_status": "unavailable:not_permitted"}),
            ("marketdata", lambda *_: {"expected_move_status": "unavailable:not_configured"}),
            ("yahoo", lambda *_: _success_payload("Yahoo/yfinance options")),
        ),
    )
    result = enrich.expected_move_for_report("T", TODAY + timedelta(days=5), TODAY, "amc")
    assert result["expected_move_source"] == "Yahoo/yfinance options"


def test_provider_chain_keeps_real_failure_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        enrich,
        "EXPECTED_MOVE_PROVIDERS",
        (
            ("massive", lambda *_: {"expected_move_status": "unavailable:not_permitted"}),
            ("yahoo", lambda *_: {"expected_move_status": "unavailable:no_usable_straddle"}),
        ),
    )
    result = enrich.expected_move_for_report("T", TODAY + timedelta(days=5), TODAY, "amc")
    # 未配置/无权限是「不在场」；真正的失败原因来自实际尝试过的 provider
    assert result == {"expected_move_status": "unavailable:no_usable_straddle"}


def test_straddle_rejects_last_price_only_and_wide_spreads() -> None:
    base = {"underlying_price": 100}
    # last price 不是报价：缺 bid/ask/midpoint 的合约必须被拒绝
    assert (
        enrich.compute_straddle_move(
            {
                **base,
                "calls": [{"strike": 100, "last_price": 4.0}],
                "puts": [{"strike": 100, "last_price": 3.5}],
            }
        )
        is None
    )
    # 宽价差（>50% 中值）拒绝
    assert (
        enrich.compute_straddle_move(
            {
                **base,
                "calls": [{"strike": 100, "bid": 1.0, "ask": 8.0}],
                "puts": [{"strike": 100, "bid": 3.4, "ask": 3.6}],
            }
        )
        is None
    )
    # 无共同有效行权价拒绝
    assert (
        enrich.compute_straddle_move(
            {
                **base,
                "calls": [{"strike": 95, "bid": 3.9, "ask": 4.1}],
                "puts": [{"strike": 105, "bid": 3.4, "ask": 3.6}],
            }
        )
        is None
    )
    # 干净的 bid/ask 报价照常成立
    move = enrich.compute_straddle_move(
        {
            **base,
            "calls": [{"strike": 100, "bid": 3.9, "ask": 4.1}],
            "puts": [{"strike": 100, "bid": 3.4, "ask": 3.6}],
        }
    )
    assert move is not None and move["move_pct"] == 7.5


# ── #14 / #15：访客与 Owner 边界 ─────────────────────────────


def test_visitor_read_never_starts_a_provider_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.access import request_owner_access_context
    from app.services.cache import cache

    async def unexpected_build(_today):
        raise AssertionError("visitor GET must not build the calendar")

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected_build)
    monkeypatch.setattr(
        earnings,
        "read_public_home_resource_async",
        _async_none,
    )
    cache.clear()

    async def scenario():
        with request_owner_access_context(False):
            return await earnings.upcoming_earnings()

    with pytest.raises(Exception) as excinfo:
        asyncio.run(scenario())
    assert getattr(excinfo.value, "status_code", None) == 503


async def _async_none(*_args, **_kwargs):
    return None


def test_anonymous_refresh_post_is_not_a_public_surface() -> None:
    from app import main

    assert (
        main._is_public_read_request("/api/earnings/upcoming/refresh", "POST")
        is False
    )
    assert (
        main._is_public_read_request(
            "/api/earnings/upcoming/refresh",
            "POST",
            visitor_ai_actions=True,
            visitor_live_pulls=True,
        )
        is False
    ), "财报刷新是 Owner 动作，任何访客开关都不放行"


# ── 快照校验器：新字段的结构一致性 ───────────────────────────


def test_snapshot_validator_rejects_watchlist_reason_and_bad_conflict(
    isolated_build,
) -> None:
    payload = isolated_build(
        finnhub_rows=[_calendar_row("GOOD")],
        profiles={"GOOD": {"market_cap": 25_000_000_000.0}},
    )
    assert validate_public_home_payload("earnings", payload) is not None

    # 账号自选理由绝不允许进入公共快照
    tampered = json.loads(json.dumps(payload))
    tampered["earnings"][0]["featured_reasons"] = ["watchlist"]
    with pytest.raises(ValueError):
        validate_public_home_payload("earnings", tampered)

    # 声称 conflict 却不记录冲突内容 → 拒绝（冲突必须可追踪）
    tampered = json.loads(json.dumps(payload))
    tampered["earnings"][0]["calendar_date_status"] = "conflict"
    tampered["earnings"][0]["calendar_conflict"] = None
    with pytest.raises(ValueError):
        validate_public_home_payload("earnings", tampered)

    # market_cap 有值却缺来源三元组 → 拒绝
    tampered = json.loads(json.dumps(payload))
    tampered["earnings"][0]["market_cap_source"] = None
    with pytest.raises(ValueError):
        validate_public_home_payload("earnings", tampered)
