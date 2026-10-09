from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from app.access import current_request_is_owner, request_owner_access_context
from app.data_paths import get_data_paths
from app.services.market_brief import errors, evidence
from app.services.market_brief.evidence import BENCHMARK_CODES, build_evidence, eod_batch_served_session
from app.services.market_brief.store import BriefRunRecord, BriefStore

NOW = datetime(2026, 10, 8, 12, 45, tzinfo=timezone.utc)  # 美东 08:45，开盘前
TRADING_DATE = date(2026, 10, 8)
SAMPLE_RESULT = json.loads(
    (Path(__file__).parent / "fixtures" / "market_brief_sample.json").read_text(encoding="utf-8")
)["brief"]["result"]


def _encoded(payload: Mapping[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def indices_payload() -> dict[str, Any]:
    return {
        "indices": [
            {"symbol": "^GSPC", "price": 6701.123, "change_percent": 0.61},
            {"symbol": "^IXIC", "price": 22801.5, "change_percent": 0.92},
            {"symbol": "^DJI", "price": 46000.0, "change_percent": -0.02},
            {"symbol": "^N225", "price": None, "change_percent": None},
            {"symbol": "000001.SS", "price": 3880.2, "change_percent": 0.3},
        ],
        "attempted": 5,
        "succeeded": 4,
        "data_limited": False,
        "source_status": "active",
        "as_of": "2026-10-08T12:30:00+00:00",
    }


def signals_payload() -> dict[str, Any]:
    return {
        "signals": {
            "rsp_spy_5d": {"value": -0.8123, "label": "等权重/SPY 5日相对强弱%", "top_score": 40, "bottom_score": 30},
            "sectors_above_50dma": {"value": 54.5454, "label": "行业ETF在50日线上方%", "top_score": 50, "bottom_score": 20},
            "vix": {"value": 16.2, "label": "VIX", "top_score": 30, "bottom_score": 20},
            "_breadth_coverage": {"available": 11, "expected": 11, "ratio": 1.0, "above_count": 6},
            "_source_status": {"value": "active", "label": "板块广度数据完整"},
        },
        "scores": {},
        "as_of": "2026-10-08T12:20:00+00:00",
    }


def context_payload() -> dict[str, Any]:
    return {
        "as_of": "2026-10-08T02:10:00+00:00",
        "served_session": "2026-10-07",
        "_stale": False,
        "stale_reason": None,
        "market_regime": {
            "as_of": "2026-10-08T02:10:00+00:00",
            "score": 61.24,
            "label": "偏强",
            "status": "active",
            "index_trend_score": 70.0,
            "market_breadth_score": 48.5,
            "spy_20d": 2.13,
            "spy_above_sma200": True,
            "vix": 16.2,
            "breadth": {"sectors_above_50dma": 54.5, "rsp_spy_20d": -1.2},
            "warnings": ["市场广度偏弱"],
            "rules": ["internal"],
        },
        "sectors": [],
    }


def theme_row(sector_id: str, *, excess: float | None, leaders: list[str]) -> dict[str, Any]:
    return {
        "sector_id": sector_id,
        "member_count": 14,
        "scored_count": 12,
        "avg_strength": 72.345,
        "avg_return_1mo": 5.0,
        "avg_return_3mo": 9.0,
        "avg_return_6mo": 15.0,
        "spy_return_1mo": 2.1,
        "spy_return_3mo": 4.2,
        "spy_return_6mo": 8.4,
        "excess_vs_spy_1mo": excess,
        "excess_vs_spy_3mo": 4.8,
        "excess_vs_spy_6mo": 6.6,
        "score_source_status": "degraded",
        "leaders": [{"ticker": ticker, "score": 90 - index} for index, ticker in enumerate(leaders)],
    }


def batch_payload() -> dict[str, Any]:
    return {
        "served_session": "2026-10-07",
        "generated_at": "2026-10-08T02:05:00+00:00",
        "coverage": {"eligible_count": 5894, "complete_bar_count": 5800, "scored_count": 4410},
        "variants": {"balanced|mid": {"eligible_n": 900, "watch_n": 300, "rejected_n": 3200}},
        "theme_statistics": {
            "served_session": "2026-10-07",
            "sectors": [
                theme_row("energy", excess=-1.5, leaders=["XOM", "CVX"]),
                theme_row("semiconductors", excess=3.4, leaders=["MU", "NVDA", "AMD", "AVGO"]),
                theme_row("china_adr", excess=None, leaders=["BABA"]),
            ],
        },
    }


def sector_iv_payload(sector_id: str) -> dict[str, Any] | None:
    rankings = {
        "semiconductors": [("NVDA", 45.0), ("MU", 61.5), ("AMD", 52.0)],
        "energy": [("XOM", 22.0), ("CVX", 24.5)],
        "crypto": [("MARA", 95.0)],
    }.get(sector_id)
    if rankings is None:
        return None
    return {
        "sector_id": sector_id,
        "as_of": "2026-10-08T12:00:00+00:00",
        "_stale": False,
        "rankings": [{"ticker": ticker, "atm_iv_percent": value} for ticker, value in rankings],
    }


def breakout_scan(count: int = 15) -> dict[str, Any]:
    states = ["TRIGGERED", "CONFIRMED", "WATCHING"]
    return {
        "scan_run_id": "scan_1",
        "published_at": "2026-10-07T20:00:00+00:00",
        "session": "regular",
        "events": [
            {
                "event_id": f"evt-{index:06d}",
                "ticker": f"T{index}",
                "setup_type": "DAILY_BASE_BREAKOUT",
                "lifecycle_state": states[index % 3],
                "features": {"session_change_pct": 3.21},
                "scores": {"breakout_quality_score": 71.55, "alert_priority_score": 66.04},
            }
            for index in range(count)
        ],
    }


def macro_block() -> dict[str, Any]:
    return {
        "status": "active",
        "as_of": "2026-10-08T12:31:00+00:00",
        "data_through": "2026-10-07",
        "composite_score": 58.04,
        "score_change_7d": 2.0,
        "regime": "neutral",
        "confidence": "medium",
        "module_scores": {"liquidity": 61.0, "rates": 40.0},
        "top_improving": [{"factor_id": "fed_net_liquidity", "display_name_zh": "联储净流动性", "score": 70.0, "score_change_7d": 5.0}],
        "top_deteriorating": [{"factor_id": "ust_10y_real", "display_name_zh": "10年期实际利率", "score": 30.0, "score_change_7d": -4.0}],
        "warnings": [],
    }


def hotspot(index: int, *, title: str | None = None, tickers: list[str] | None = None) -> dict[str, Any]:
    return {
        "event_group_id": f"evt_{index:032x}",
        "representative_title": title or f"存储芯片现货价格连续第{index + 1}周上涨",
        "summary_zh": "合约价谈判提前，此前市场只计入现货端涨价。" * 3,
        "hot_score": 80.0 - index,
        "event_type": "industry",
        "validated_tickers": tickers if tickers is not None else ["MU", "WDC"],
        "source_count": 3,
        "source_names": ["Reuters", "Bloomberg"],
        "first_published_at": "2026-10-07T21:00:00+00:00",
        "last_published_at": "2026-10-08T11:00:00+00:00",
        "representative_news_id": 100 + index,
    }


class FakeCatalyst:
    def __init__(self, hotspots: list[dict[str, Any]], feed_items: list[dict[str, Any]], calendar_items: list[dict[str, Any]]) -> None:
        self.hotspot_items = hotspots
        self.feed_items = feed_items
        self.calendar_items = calendar_items
        self.owner_flags: list[bool] = []
        self.feed_kwargs: dict[str, Any] = {}
        self.calendar_kwargs: dict[str, Any] = {}
        self.intelligence = SimpleNamespace(feed=self._feed)

    def hotspots(self, *, limit: int, now: datetime | None = None, include_owner_state: bool | None = None) -> dict[str, Any]:
        self.owner_flags.append(current_request_is_owner())
        return {"status": "active", "as_of": "2026-10-08T12:44:00+00:00", "data_through": "2026-10-08T12:40:00+00:00", "items": self.hotspot_items[:limit]}

    def _feed(self, **kwargs: Any) -> dict[str, Any]:
        self.owner_flags.append(current_request_is_owner())
        self.feed_kwargs = kwargs
        return {"status": "active", "data_through": "2026-10-08T12:40:00+00:00", "items": self.feed_items, "summary": {"count": len(self.feed_items)}}

    def calendar(self, **kwargs: Any) -> dict[str, Any]:
        self.owner_flags.append(current_request_is_owner())
        self.calendar_kwargs = kwargs
        return {"status": "active", "data_through": "2026-10-08T12:00:00+00:00", "items": self.calendar_items}


def feed_item(news_id: int, *, original: bool = True) -> dict[str, Any]:
    return {
        "news_id": news_id,
        "url": f"https://example.com/news/{news_id}",
        "published_at": "2026-10-08T11:00:00+00:00",
        "source": "Reuters",
        "title": "存储芯片现货价格上涨",
        "title_zh": "存储芯片现货价格上涨",
        "summary_zh": "合约价谈判提前。",
        "_validation_title": "Memory chip spot prices rise for a third week" if original else "",
        "_validation_summary": ("Contract talks moved earlier, Micron said. " * 20) if original else None,
    }


def earnings_payload() -> dict[str, Any]:
    rows = [
        {"ticker": "NVDA", "name": "NVIDIA Corporation", "earnings_date": "2026-10-09", "timing": "amc", "eps_estimate": 1.234, "market_cap": 4.5e12, "release_status": "scheduled"},
        {"ticker": "MU", "name": "Micron Technology", "earnings_date": "2026-10-08", "timing": "bmo", "eps_estimate": 2.1, "market_cap": 1.5e11, "release_status": "scheduled"},
        {"ticker": "SMALL", "name": "Small Co", "earnings_date": "2026-10-12", "timing": None, "eps_estimate": None, "market_cap": None, "release_status": "scheduled"},
        {"ticker": "LATE", "name": "Late Co", "earnings_date": "2026-10-13", "timing": "bmo", "eps_estimate": 0.5, "market_cap": 9e12, "release_status": "scheduled"},
        {"ticker": "PAST", "name": "Past Co", "earnings_date": "2026-10-07", "timing": "amc", "eps_estimate": 0.5, "market_cap": 9e12, "release_status": "released"},
    ]
    return {"earnings": rows, "as_of": "2026-10-08T06:00:00+00:00"}


def calendar_items() -> list[dict[str, Any]]:
    return [
        {"event_id": "ev-ppi", "title": "生产者物价指数", "impact": "high", "scheduled_at_utc": "2026-10-09T12:30:00+00:00", "forecast": "0.2%", "previous": "0.1%", "actual": None, "release_status": "scheduled"},
        {"event_id": "ev jobless/claims", "title": "初请失业金人数", "impact": "medium", "scheduled_at_utc": "2026-10-08T12:30:00+00:00", "forecast": "225K", "previous": "220K", "actual": None, "release_status": "scheduled"},
    ]


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """全部来源换成内存假数据；用例按需改其中的一项。"""

    state = SimpleNamespace(
        public={"indices": indices_payload(), "market_signals": signals_payload()},
        earnings_dates={"2026-10-07"},  # 只有前一天的财报条目（worker 还没按今天刷新）
        context=context_payload(),
        batch=batch_payload(),
        scan=breakout_scan(),
        macro=macro_block(),
        catalyst=FakeCatalyst([hotspot(0), hotspot(1), hotspot(2, tickers=["NVDA"])], [feed_item(100), feed_item(101, original=False)], calendar_items()),
        public_calls=[],
    )

    def read_public(resource: str, parameters: Mapping[str, Any], now_epoch: float) -> dict[str, Any] | None:
        state.public_calls.append((resource, dict(parameters)))
        assert now_epoch == NOW.timestamp()
        if resource == "earnings":
            if parameters["market_date"] not in state.earnings_dates:
                return None
            return {"payload": earnings_payload(), "saved_at": NOW.timestamp() - 3600, "fresh": True}
        payload = state.public.get(resource)
        return None if payload is None else {"payload": payload, "saved_at": NOW.timestamp() - 300, "fresh": True}

    monkeypatch.setattr(evidence, "_read_public_home", read_public)
    monkeypatch.setattr(evidence, "_read_strength_context", lambda now: state.context)
    monkeypatch.setattr(evidence, "_read_eod_batch", lambda root=None: state.batch)
    monkeypatch.setattr(evidence, "_read_sector_iv", lambda sector_id, now_epoch: sector_iv_payload(sector_id))
    monkeypatch.setattr(evidence, "_read_breakout_scan", lambda: state.scan)
    monkeypatch.setattr(evidence, "_read_macro_context", lambda: state.macro)
    monkeypatch.setattr(evidence, "_catalyst_service", lambda: state.catalyst)
    return state


def _prior_store(tmp_path: Path) -> BriefStore:
    store = BriefStore(tmp_path / "briefs")
    store.write_run(BriefRunRecord(
        run_id="mb_20261007_post_close_0a1b2c3d",
        slot="post_close",
        trading_date=date(2026, 10, 7),
        trigger="scheduled",
        status="completed",
        started_at=NOW - timedelta(hours=12),
        completed_at=NOW - timedelta(hours=11, minutes=50),
        model="claude-opus-5-5",
        effort="xhigh",
        result=SAMPLE_RESULT,
    ))
    return store


def test_full_pack_cites_every_block_and_stays_in_budget(sources: SimpleNamespace, tmp_path: Path) -> None:
    store = _prior_store(tmp_path)
    with request_owner_access_context(True):
        pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=store)

    payload = pack.payload
    assert pack.coverage["missing_blocks"] == []
    assert pack.coverage["trimmed"] == []
    assert pack.bytes == _encoded(payload) == payload["coverage"]["evidence_bytes"]
    assert pack.bytes <= evidence.DEFAULT_MAX_BYTES
    assert {
        "idx:^GSPC", "idx:000001.SS", "sig:rsp_spy_5d", "regime:market", "breadth:eod_batch",
        "theme:semiconductors", "iv:semiconductors", "iv:crypto", "bo:evt-000000", "macro:composite",
        "macro:liquidity", "macro:fed_net_liquidity", "macro:ust_10y_real", "news:100",
        f"hot:evt_{0:032x}", "earn:NVDA:2026-10-09", "cal:ev-ppi", "cal:ev_jobless_claims",
        "prior:mb_20261007_post_close_0a1b2c3d",
    } <= pack.evidence_ids
    assert BENCHMARK_CODES <= pack.allowed_codes
    assert {"MU", "NVDA", "AMD", "WDC", "MARA", "XOM", "T0", "SMALL", "000001.SS"} <= pack.allowed_codes
    assert "AVGO" not in pack.allowed_codes  # 只取前 3 名领涨股
    assert "LATE" not in pack.allowed_codes and "PAST" not in pack.allowed_codes
    assert payload["coverage"]["allowed_codes_count"] == len(pack.allowed_codes)

    session = payload["session"]
    assert session == {
        "slot": "pre_open", "trading_date": "2026-10-08", "now_utc": "2026-10-08T12:45:00Z",
        "now_et": "2026-10-08T08:45-04:00", "trading_day": True, "early_close": False,
        "close_time_et": "16:00", "next_trading_day": "2026-10-09",
    }
    coverage = pack.coverage
    assert coverage["universe_size"] == 5894
    assert coverage["scored_count"] == 4410
    assert coverage["quotes_valid"] == 4
    assert coverage["breadth_basis"] == "sector_etf_proxy_11"
    assert coverage["data_through"] == {
        "indices": "2026-10-08T12:30:00Z",
        "market_signals": "2026-10-08T12:20:00Z",
        "market_regime": "2026-10-07",
        "eod_batch": "2026-10-07",
        "sector_iv": "2026-10-08T12:00:00Z",
        "breakouts": "2026-10-07T20:00:00Z",
        "macro": "2026-10-07",
        "news": "2026-10-08T12:40:00Z",
        "earnings": "2026-10-08T06:00:00Z",
        "calendar": "2026-10-08T12:00:00Z",
    }

    themes = payload["themes"]
    assert [row["id"] for row in themes["rows"]] == ["theme:semiconductors", "theme:energy", "theme:china_adr"]
    assert themes["rows"][0]["leaders"] == [{"ticker": "MU", "score": 90}, {"ticker": "NVDA", "score": 89}, {"ticker": "AMD", "score": 88}]
    assert themes["rows"][2]["name"] == "中概股"
    assert themes["spy_return_1mo"] == 2.1
    regime = payload["internals"]["market_regime"]
    assert regime["score"] == 61.24 and "rules" not in regime and regime["breadth"]["rsp_spy_20d"] == -1.2
    assert payload["breadth_counts"]["eligible_n"] == 900
    assert payload["breadth_counts"]["breadth_basis"] == "sector_etf_proxy_11"
    breakouts = payload["breakouts"]
    assert len(breakouts["rows"]) == 12 and breakouts["event_count"] == 15
    assert breakouts["lifecycle_counts"] == {"CONFIRMED": 5, "TRIGGERED": 5, "WATCHING": 5}
    assert breakouts["state_names_zh"] == {"WATCHING": "观察中", "TRIGGERED": "已触发", "CONFIRMED": "已确认"}
    assert breakouts["rows"][0]["session_change_pct"] == 3.21 and breakouts["rows"][0]["alert_priority_score"] == 66.0
    iv = payload["sector_iv"]
    assert [row["id"] for row in iv["highest"]] == ["iv:crypto", "iv:semiconductors", "iv:energy"]
    assert iv["highest"][1]["median_atm_iv_pct"] == 52.0 and iv["highest"][1]["highest"] == {"ticker": "MU", "atm_iv_pct": 61.5}
    assert payload["macro"]["modules"][0] == {"id": "macro:liquidity", "name_zh": "流动性", "score": 61.0}

    news = payload["news"]["items"]
    assert [item["id"] for item in news] == [f"hot:evt_{index:032x}" for index in range(3)]
    assert news[0]["news"]["text_origin"] == "source"
    assert news[0]["news"]["title"] == "Memory chip spot prices rise for a third week"
    assert len(news[0]["news"]["summary"]) <= 400 and news[0]["news"]["summary"].endswith("…")
    assert news[1]["news"]["text_origin"] == "zh_fallback" and news[1]["news"]["title"] == "存储芯片现货价格上涨"
    assert news[2]["news"] is None  # 代表新闻不在 feed 这一页
    assert "Memory chip spot prices rise for a third week" in pack.source_texts
    assert "Reuters" in pack.source_texts and "Micron Technology" in pack.source_texts
    # 原文与日历都在匿名语境读取，即使调用方是 owner。
    assert sources.catalyst.owner_flags and not any(sources.catalyst.owner_flags)
    assert sources.catalyst.feed_kwargs == {
        "as_of": NOW, "window_hours": 24, "limit": 50, "include_unanalyzed": True, "include_neutral": True,
    }
    assert sources.catalyst.calendar_kwargs == {
        "date_from": date(2026, 10, 7), "date_to": date(2026, 10, 10), "as_of": NOW, "currencies": ("USD",),
        "min_impact": "medium", "timezone_offset_minutes": -240, "include_owner_state": False,
    }

    earnings = payload["calendar"]["earnings"]
    assert earnings["window"] == ["2026-10-08", "2026-10-12"]
    assert [row["id"] for row in earnings["rows"]] == ["earn:NVDA:2026-10-09", "earn:MU:2026-10-08", "earn:SMALL:2026-10-12"]
    assert earnings["rows"][0]["market_cap_usd_bn"] == 4500.0
    # 今天的条目还没发布：退到前一天的 market_date 参数。
    assert [params["market_date"] for resource, params in sources.public_calls if resource == "earnings"] == ["2026-10-08", "2026-10-07"]
    assert [row["id"] for row in payload["calendar"]["economic"]["rows"]] == ["cal:ev-ppi", "cal:ev_jobless_claims"]

    prior = payload["prior_brief"]
    assert prior["id"] == "prior:mb_20261007_post_close_0a1b2c3d"
    assert prior["headline"] == SAMPLE_RESULT["headline"]
    assert prior["watch_items"] == SAMPLE_RESULT["watch_items"]


def test_missing_and_failing_blocks_are_recorded_without_stopping(
    sources: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources.public = {}
    sources.earnings_dates = set()
    sources.batch = None
    sources.macro = None
    sources.context = {"market_regime": None}
    sources.catalyst.hotspot_items = []

    def broken_scan() -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(evidence, "_read_breakout_scan", broken_scan)

    configured = SimpleNamespace(macro_conditions_configured=True)
    pack = build_evidence(slot="post_close", trading_date=TRADING_DATE, now=NOW, store=None, settings=configured)
    assert pack.coverage["missing_blocks"] == [
        {"block": "indices", "reason": errors.SNAPSHOT_MISSING},
        {"block": "market_signals", "reason": errors.SNAPSHOT_MISSING},
        {"block": "market_regime", "reason": errors.SNAPSHOT_MISSING},
        {"block": "breadth_counts", "reason": errors.SNAPSHOT_MISSING},
        {"block": "themes", "reason": errors.SNAPSHOT_MISSING},
        {"block": "breakouts", "reason": errors.READ_FAILED},
        {"block": "macro", "reason": errors.UNAVAILABLE},
        {"block": "news", "reason": errors.EMPTY},
        {"block": "earnings", "reason": errors.SNAPSHOT_MISSING},
    ]
    assert pack.coverage["block_errors"] == {"breakouts": "RuntimeError"}
    assert "block_errors" not in pack.payload["coverage"]
    assert pack.payload["indices"] is None and pack.payload["breakouts"] is None
    assert pack.payload["internals"] == {"market_signals": None, "market_regime": None}
    assert pack.payload["prior_brief"] is None
    assert pack.coverage["quotes_valid"] == 0 and pack.coverage["universe_size"] is None
    assert pack.block_missing("indices") and not pack.block_missing("sector_iv")
    assert pack.payload["sector_iv"] is not None and pack.payload["calendar"]["economic"] is not None
    assert pack.bytes == _encoded(pack.payload)


def test_macro_without_fred_key_is_disabled(sources: SimpleNamespace) -> None:
    sources.macro = None
    unconfigured = SimpleNamespace(macro_conditions_configured=False)
    pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, settings=unconfigured)
    assert {"block": "macro", "reason": errors.DISABLED} in pack.coverage["missing_blocks"]


@pytest.mark.parametrize("status, reason", [("disabled", errors.DISABLED), ("unavailable", errors.UNAVAILABLE)])
def test_catalyst_status_maps_to_block_reasons(sources: SimpleNamespace, status: str, reason: str) -> None:
    catalyst = sources.catalyst
    catalyst.hotspots = lambda **kwargs: {"status": status, "items": []}
    catalyst.calendar = lambda **kwargs: {"status": status, "items": []}
    pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None)
    assert {"block": "news", "reason": reason} in pack.coverage["missing_blocks"]
    assert {"block": "economic_calendar", "reason": reason} in pack.coverage["missing_blocks"]


_TRIM_STAGES = ("news", "themes.leaders", "breakouts", "calendar", "sector_iv")


def _stage(block: str) -> str:
    return "calendar" if block.startswith("calendar.") else block


def test_budget_trims_in_the_documented_order(sources: SimpleNamespace) -> None:
    sources.catalyst.hotspot_items = [hotspot(index, title="新闻标题" * 25, tickers=[f"N{index}"]) for index in range(20)]
    sources.catalyst.feed_items = [feed_item(100 + index) for index in range(20)]
    full = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, max_bytes=1_000_000)
    assert full.coverage["trimmed"] == []
    news_ids = {item["id"] for item in full.payload["news"]["items"]}

    seen_stages: set[str] = set()
    for budget in range(full.bytes - 1, 3_000, -max(1, full.bytes // 60)):
        pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, max_bytes=budget)
        assert pack.bytes <= budget
        assert pack.bytes == _encoded(pack.payload) == pack.payload["coverage"]["evidence_bytes"]
        stages = [_stage(item["block"]) for item in pack.coverage["trimmed"]]
        seen_stages.update(stages)
        # 只有前一级裁到底，下一级才会动：裁剪过的阶段是固定顺序的前缀。
        assert list(dict.fromkeys(stages)) == list(_TRIM_STAGES[: len(set(stages))])
        # 逐行裁剪仍不够时整块撤下（下一个用例专门测），撤下的块记为 None。
        news_items = (pack.payload["news"] or {"items": []})["items"]
        if "themes.leaders" in stages:
            assert news_items == []
        if "breakouts" in stages and pack.payload["themes"] is not None:
            assert all(row["leaders"] == [] for row in pack.payload["themes"]["rows"])
        kept = {item["id"] for item in news_items}
        assert (news_ids - kept).isdisjoint(pack.evidence_ids)
        kept_tickers = {ticker for item in news_items for ticker in item["tickers"]}
        dropped = {f"N{index}" for index in range(20)} - kept_tickers
        assert dropped.isdisjoint(pack.allowed_codes)
    assert set(_TRIM_STAGES) <= seen_stages
    first = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, max_bytes=full.bytes - 1)
    assert first.coverage["trimmed"] == [{"block": "news", "from": 20, "to": 19}]


def test_over_budget_drops_whole_blocks_and_rejects_impossible_budgets(sources: SimpleNamespace) -> None:
    pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, max_bytes=2_600)
    assert pack.bytes <= 2_600
    dropped = [item["block"] for item in pack.coverage["missing_blocks"] if item["reason"] == errors.OVER_BUDGET]
    assert dropped[:3] == ["news", "themes", "breakouts"]
    assert pack.payload["session"]["slot"] == "pre_open"
    with pytest.raises(ValueError, match="cannot fit"):
        build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None, max_bytes=300)


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        build_evidence(slot="midday", trading_date=TRADING_DATE, now=NOW, store=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=datetime(2026, 10, 8, 12), store=None)


def test_real_snapshot_files_in_data_dir() -> None:
    """不替换读取函数：用真实的快照文件格式走一遍公开快照、EOD 批次、强度上下文、板块隐含波动率。"""

    from app.api.sectors import _write_sector_iv_snapshot
    from app.public_home_snapshot import create_public_home_entry, public_home_resource_parameters, write_public_home_snapshot

    root = get_data_paths().root
    root.mkdir(parents=True, exist_ok=True)
    saved_at = NOW.timestamp() - 60
    entry = create_public_home_entry(
        "indices",
        indices_payload(),
        saved_at=saved_at,
        parameters=public_home_resource_parameters("indices", now=saved_at),
    )
    write_public_home_snapshot(get_data_paths().public_home_snapshot, {"indices": entry}, now=NOW.timestamp())
    batch_dir = root / "eod-limited-v1"
    batch_dir.mkdir(parents=True)
    (batch_dir / "batch.json").write_text(json.dumps(batch_payload()), encoding="utf-8")
    (root / "strength-context-v1.json").write_text(
        json.dumps({"version": 1, "published_at": NOW.timestamp() - 3600, "payload": context_payload()}),
        encoding="utf-8",
    )
    _write_sector_iv_snapshot(
        "semiconductors",
        {
            "sector_id": "semiconductors",
            "rankings": [
                {"ticker": "NVDA", "atm_iv_percent": 45.0, "sector_iv_rank": 0.0, "price": 180.0},
                {"ticker": "MU", "atm_iv_percent": 61.5, "sector_iv_rank": 100.0, "price": 150.0},
            ],
            "success_count": 2,
            "requested_count": 14,
            "failed_symbols": [],
            "as_of": "2026-10-08T12:00:00+00:00",
        },
        saved_at=NOW.timestamp() - 1800,
        snapshot_origin="worker",
    )

    pack = build_evidence(slot="pre_open", trading_date=TRADING_DATE, now=NOW, store=None)
    missing = {item["block"]: item["reason"] for item in pack.coverage["missing_blocks"]}
    for block in ("indices", "market_regime", "breadth_counts", "themes", "sector_iv"):
        assert block not in missing
    assert missing["market_signals"] == errors.SNAPSHOT_MISSING
    assert missing["breakouts"] == errors.SNAPSHOT_MISSING  # optix.db 不存在
    assert pack.coverage["quotes_valid"] == 4
    assert pack.payload["indices"]["stale"] is False
    assert pack.payload["internals"]["market_regime"]["served_session"] == "2026-10-07"
    assert pack.payload["sector_iv"]["highest"][0]["median_atm_iv_pct"] == 53.2
    assert eod_batch_served_session() == date(2026, 10, 7)


def test_eod_batch_served_session_without_batch(tmp_path: Path) -> None:
    assert eod_batch_served_session(tmp_path) is None
    target = tmp_path / "eod-limited-v1"
    target.mkdir()
    (target / "batch.json").write_text(json.dumps({"served_session": "not-a-date"}), encoding="utf-8")
    assert eod_batch_served_session(tmp_path) is None
