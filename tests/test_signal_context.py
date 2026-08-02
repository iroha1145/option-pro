"""signal_analysis 证据包 v2：上下文块组装、降级、载荷合并与校验联动。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.api import signals
from app.services import signal_context
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import (
    _validation_source_texts,
    validate_job_payload,
    validate_result,
)


def _chain_fixture() -> dict:
    def contract(strike, volume, oi, iv, mid):
        return {
            "strike": strike,
            "volume": volume,
            "open_interest": oi,
            "implied_volatility": iv,
            "mid": mid,
            "last_price": mid,
        }

    return {
        "expiration": "2026-08-07",
        "dte": 5.2,
        "underlying_price": 160.0,
        "calls": [
            contract(150, 100, 4_000, 0.52, 11.0),
            contract(160, 5_000, 2_000, 0.48, 4.0),
            contract(170, 3_000, 9_000, 0.50, 1.2),
            contract(180, 800, 6_000, 0.55, 0.4),
            contract(190, 20, 0, None, 0.1),
        ],
        "puts": [
            contract(150, 2_500, 7_000, 0.51, 1.5),
            contract(160, 1_500, 1_000, 0.49, 3.8),
            contract(140, 900, 5_000, 0.56, 0.6),
        ],
        "alerts": [
            {
                "type": "call",
                "strike": 160,
                "volume": 5_000,
                "open_interest": 2_000,
                "vol_oi_ratio": 2.5,
                "premium_flow": 2_000_000,
                "moneyness": "atm",
                "reasons": ["高成交量 5,000", "大额权利金 $2,000,000", "Vol/OI 2.5x", "第四条理由"],
            }
        ]
        * 7,
    }


def test_chain_summary_reports_totals_ratios_atm_and_bounded_tops():
    summary = signal_context._summarize_chain(_chain_fixture())

    assert summary["call_volume"] == 8_920
    assert summary["put_volume"] == 4_900
    assert summary["call_open_interest"] == 21_000
    assert summary["put_open_interest"] == 13_000
    assert summary["volume_put_call_ratio"] == pytest.approx(4_900 / 8_920, abs=1e-3)
    assert summary["open_interest_put_call_ratio"] == pytest.approx(
        13_000 / 21_000, abs=1e-3
    )
    assert summary["atm_strike"] == 160
    assert summary["atm_call_iv"] == 0.48
    assert summary["atm_put_iv"] == 0.49
    # ATM 跨式中值 (4.0 + 3.8) / 160 = 4.875%
    assert summary["expected_move_pct"] == pytest.approx(4.88, abs=0.01)
    assert len(summary["top_open_interest"]) == 4
    assert summary["top_open_interest"][0]["strike"] == 170
    assert len(summary["top_volume"]) == 4
    assert summary["top_volume"][0]["strike"] == 160
    # 告警条数与理由条数都有硬上限。
    assert len(summary["unusual_alerts"]) == 5
    assert all(len(a["reasons"]) == 3 for a in summary["unusual_alerts"])


def test_chain_summary_returns_none_for_an_empty_chain():
    assert (
        signal_context._summarize_chain(
            {"expiration": "2026-08-07", "calls": [], "puts": []}
        )
        is None
    )


def test_news_block_projects_compact_items_and_collects_tickers(monkeypatch):
    """条目形状对齐匿名公共投影：title_zh/headline_summary/trusted_stock_impacts。"""

    long_title = "标题" * 200
    items = [
        {
            "published_at": f"2026-08-01T0{i}:00:00Z",
            "source": "reuters",
            # 匿名投影下 title/summary 为 None，文本在 *_zh 字段里。
            "title": None,
            "summary": None,
            "title_zh": long_title if i == 0 else f"新闻标题{i}",
            "summary_zh": "泛化摘要" * 100,
            "headline_summary": "一句话结论" * 60,
            "classification": "bullish",
            "confidence": 70,
            "source_tickers": ["AMD", "NVDA", "TSM", "AVGO", "MU", "INTC", "QCOM"],
            "trusted_stock_impacts": [
                {
                    "ticker": "AMD",
                    "impact_score": 25,
                    "horizon": "weeks",
                    "reason": "理由" * 200,
                },
                {"ticker": "NVDA", "impact_score": 10, "reason": "别家的理由"},
            ],
        }
        for i in range(12)
    ]
    # fail-closed 压掉全部文本的条目必须被跳过，不能以空标题进证据。
    items.insert(
        0,
        {
            "published_at": "2026-08-01T09:00:00Z",
            "source": "reuters",
            "title_zh": "",
            "headline_summary": "",
            "source_tickers": ["XXXX"],
        },
    )

    class FakeService:
        def __init__(self, settings):
            # 真实构造契约：CatalystSettings（含 cache_db_path），不是应用
            # Settings——首版就是在这里被宽松 mock 掩盖的。
            assert hasattr(settings, "cache_db_path")

        def ticker(self, symbol, **kwargs):
            assert symbol == "AMD"
            # 匿名投影会压掉未分析条目的原文标题，取了也没有文本可用。
            assert kwargs["include_unanalyzed"] is False
            assert kwargs["include_neutral"] is True
            return {
                "status": "ok",
                "as_of": "2026-08-02T00:00:00Z",
                "data_through": "2026-08-01T23:00:00Z",
                "items": items,
            }

    monkeypatch.setattr(
        "app.services.catalysts.personal_service.PersonalCatalystService",
        FakeService,
    )

    block, tickers = signal_context._news_block("AMD")

    assert len(block["items"]) == 10
    first = block["items"][0]
    assert len(first["title"]) == signal_context._NEWS_TITLE_MAX_CHARS
    assert first["title"].endswith("…")
    assert len(first["summary"]) == signal_context._NEWS_SUMMARY_MAX_CHARS
    assert first["summary"].startswith("一句话结论")
    assert first["ticker_impact_score"] == 25
    assert first["ticker_impact_horizon"] == "weeks"
    assert len(first["ticker_impact_reason"]) == signal_context._NEWS_REASON_MAX_CHARS
    assert first["tickers"] == ["AMD", "NVDA", "TSM", "AVGO", "MU", "INTC"]
    assert "AMD" in tickers and "NVDA" in tickers
    assert "XXXX" not in tickers


def test_news_block_returns_none_when_catalysts_are_disabled(monkeypatch):
    class FakeService:
        def __init__(self, settings):
            assert hasattr(settings, "cache_db_path")

        def ticker(self, _symbol, **_kwargs):
            return {"status": "disabled", "items": []}

    monkeypatch.setattr(
        "app.services.catalysts.personal_service.PersonalCatalystService",
        FakeService,
    )

    assert signal_context._news_block("AMD") is None


def test_build_signal_context_isolates_failures_and_dedupes_tickers(monkeypatch):
    async def market_ok():
        return {"source": "worker_snapshot", "signals": {}}

    async def earnings_ok(_symbol):
        return {"earnings_date": "2026-08-27"}

    def options_boom(_symbol):
        raise RuntimeError("yahoo down")

    def news_ok(_symbol):
        return (
            {"items": [{"title": "新闻"}]},
            ["NVDA", "AMD", "NVDA", "TSM"],
        )

    monkeypatch.setattr(signal_context, "_market_block", market_ok)
    monkeypatch.setattr(signal_context, "_earnings_block", earnings_ok)
    monkeypatch.setattr(signal_context, "_options_block", options_boom)
    monkeypatch.setattr(signal_context, "_news_block", news_ok)
    monkeypatch.setattr(signal_context, "macro_conditions_context", lambda: None)

    context = asyncio.run(signal_context.build_signal_context("AMD"))

    assert context["status"] == {
        "market_context": "ok",
        "macro_conditions": "unavailable",
        "options_chain": "unavailable",
        "recent_news": "ok",
        "upcoming_earnings": "ok",
    }
    assert set(context["blocks"]) == {
        "market_context",
        "recent_news",
        "upcoming_earnings",
    }
    # 主票剔除、去重、排序。
    assert context["context_tickers"] == ["NVDA", "TSM"]


def test_build_signal_context_drops_blocks_that_exceed_the_time_budget(
    monkeypatch,
):
    async def market_slow():
        await asyncio.sleep(30)
        return {"never": True}

    async def earnings_ok(_symbol):
        return {"earnings_date": "2026-08-27"}

    monkeypatch.setattr(signal_context, "CONTEXT_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(signal_context, "_market_block", market_slow)
    monkeypatch.setattr(signal_context, "_earnings_block", earnings_ok)
    monkeypatch.setattr(
        signal_context, "_options_block", lambda _symbol: None
    )
    monkeypatch.setattr(signal_context, "_news_block", lambda _symbol: None)
    monkeypatch.setattr(signal_context, "macro_conditions_context", lambda: None)

    context = asyncio.run(signal_context.build_signal_context("AMD"))

    assert context["status"]["market_context"] == "unavailable"
    assert context["status"]["upcoming_earnings"] == "ok"


def test_market_block_prefers_fresh_snapshot_and_falls_back_to_live(monkeypatch):
    entries = {"value": None}

    async def fake_entry(resource, **_kwargs):
        assert resource == "market_signals"
        return entries["value"]

    monkeypatch.setattr(
        signal_context, "read_owner_public_home_entry_async", fake_entry
    )
    monkeypatch.setattr(
        signal_context,
        "compute_market_signals",
        lambda: {"rsi14": {"value": 55.0}, "_cached": True},
    )
    monkeypatch.setattr(
        signal_context, "compute_market_scores", lambda _signals: {"trend": 60}
    )

    live = asyncio.run(signal_context._market_block())
    assert live["source"] == "live"
    assert live["signals"] == {"rsi14": {"value": 55.0}}
    assert live["scores"] == {"trend": 60}

    entries["value"] = {
        "payload": {"signals": {"vix": {"value": 17.0}}, "scores": {}, "as_of": "t"},
        "saved_at": 1_700_000_000.0,
        "fresh": True,
    }
    snapshot = asyncio.run(signal_context._market_block())
    assert snapshot["source"] == "worker_snapshot"
    assert snapshot["stale"] is False
    assert snapshot["signals"] == {"vix": {"value": 17.0}}


def test_market_block_uses_stale_snapshot_when_live_compute_fails(monkeypatch):
    stale_entry = {
        "payload": {"signals": {"vix": {"value": 30.0}}, "scores": {}, "as_of": "t"},
        "saved_at": 0.0,
        "fresh": False,
    }

    async def fake_entry(_resource, **_kwargs):
        return stale_entry

    def boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr(
        signal_context, "read_owner_public_home_entry_async", fake_entry
    )
    monkeypatch.setattr(signal_context, "compute_market_signals", boom)

    block = asyncio.run(signal_context._market_block())
    assert block["source"] == "worker_snapshot"
    assert block["stale"] is True


def test_earnings_block_projects_the_matching_calendar_row(monkeypatch):
    async def fake_entry(resource, **_kwargs):
        assert resource == "earnings"
        return {
            "payload": {
                "snapshot_saved_at": 1_754_000_000.0,
                "earnings": [
                    {"ticker": "NVDA", "earnings_date": "2026-08-26"},
                    {
                        "ticker": "AMD",
                        "earnings_date": "2026-08-27",
                        "days_until": 25,
                        "timing": "AMC",
                        "release_status": "scheduled",
                        "eps_estimate": 1.23,
                        "revenue_estimate": 8_100_000_000,
                        "eps_actual": None,
                        "revenue_actual": None,
                        "expected_move_pct": 7.4,
                    },
                ],
            },
            "saved_at": 1_754_000_000.0,
            "fresh": True,
        }

    monkeypatch.setattr(
        signal_context, "read_owner_public_home_entry_async", fake_entry
    )

    block = asyncio.run(signal_context._earnings_block("AMD"))
    assert block["earnings_date"] == "2026-08-27"
    assert block["timing"] == "AMC"
    assert block["expected_move_pct"] == 7.4

    missing = asyncio.run(signal_context._earnings_block("TSLA"))
    assert missing is None


def _context(blocks: dict, tickers: list[str] | None = None) -> dict:
    return {
        "blocks": blocks,
        "status": {
            key: ("ok" if key in blocks else "unavailable")
            for key in signal_context.CONTEXT_BLOCK_KEYS
        },
        "context_tickers": tickers or [],
    }


def test_payload_merges_context_blocks_and_hash_covers_them():
    signals_data = {"rsi14": {"value": 62.0}}
    scores = {"trend": 70}
    context = _context(
        {"macro_conditions": {"composite_score": 55}},
        ["NVDA"],
    )

    with_context = signals._signal_analysis_payload(
        "AMD", signals_data, scores, context=context
    )
    without_context = signals._signal_analysis_payload("AMD", signals_data, scores)

    assert with_context["macro_conditions"] == {"composite_score": 55}
    assert with_context["context_tickers"] == ["NVDA"]
    assert with_context["context_status"]["macro_conditions"] == "ok"
    assert with_context["context_status"]["recent_news"] == "unavailable"
    assert with_context["evidence_hash"] != without_context["evidence_hash"]
    # 旧形态（无上下文）保持逐字节稳定：不惊扰既有去重语义。
    assert "context_status" not in without_context


def test_payload_drops_oversized_blocks_in_declared_order():
    huge = {"items": [{"title": "x" * 60_000}]}
    context = _context(
        {
            "recent_news": huge,
            "macro_conditions": {"composite_score": 55},
        }
    )

    payload = signals._signal_analysis_payload(
        "AMD", {"rsi14": {"value": 62.0}}, {}, context=context
    )

    assert "recent_news" not in payload
    assert payload["context_status"]["recent_news"] == "omitted_size"
    assert payload["macro_conditions"] == {"composite_score": 55}
    assert len(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ) < signals._EVIDENCE_MAX_BYTES + 200


def test_job_payload_validation_bounds_context_tickers():
    validate_job_payload("signal_analysis", {"ticker": "AMD"})
    validate_job_payload(
        "signal_analysis", {"ticker": "AMD", "context_tickers": ["NVDA", "TSM"]}
    )
    with pytest.raises(ValueError, match="context_tickers_invalid"):
        validate_job_payload(
            "signal_analysis",
            {"ticker": "AMD", "context_tickers": [f"T{i}" for i in range(30)]},
        )
    with pytest.raises(ValueError, match="context_tickers_invalid"):
        validate_job_payload(
            "signal_analysis",
            {"ticker": "AMD", "context_tickers": ["../etc"]},
        )
    with pytest.raises(ValueError, match="context_tickers_contains_duplicate"):
        validate_job_payload(
            "signal_analysis",
            {"ticker": "AMD", "context_tickers": ["NVDA", "NVDA"]},
        )


def _signal_result(asset: str = "AMD") -> dict:
    return {
        "output_language": "zh-CN",
        "asset": asset,
        "horizon": "未来一至两周",
        "dominant_regime": "区间震荡偏弱",
        "trend_bias_confidence": 55,
        "top_risk_confidence": 40,
        "bottom_opportunity_confidence": 45,
        "dip_buy_quality": 50,
        "breakdown_risk": 35,
        "data_quality": 70,
        "final_bias": "range_consolidation",
        "top_evidence": ["价格接近区间上沿。"],
        "bottom_evidence": ["近期出现放量下跌。"],
        "dip_buy_evidence": ["回撤幅度接近历史均值。"],
        "bearish_evidence": ["动量指标转弱。"],
        "contradictions": ["量能与价格方向不一致。"],
        "options_flow_read": {
            "net_direction": "unknown",
            "confidence": 30,
            "bullish_flow_evidence": [],
            "bearish_flow_evidence": [],
            "unknown_or_neutral_flow": ["缺少成交主动方数据。"],
            "warnings": [],
        },
        "key_levels": {
            "support": ["支撑位约150美元。"],
            "resistance": ["阻力位约165美元。"],
            "vwap_levels": ["未提供VWAP数据。"],
            "options_levels": ["期权持仓集中在160美元。"],
        },
        "confirmation_signals": ["放量突破区间上沿。"],
        "invalidation_signals": ["跌破150美元支撑。"],
        "event_risks": ["财报临近可能放大波动。"],
        "data_quality_notes": ["新闻数据不可用。"],
        "summary": "整体处于区间震荡，方向确认需要更多量价信号。",
    }


def test_result_may_reference_context_tickers_but_not_strangers():
    result = _signal_result()
    result["event_risks"] = ["相关新闻显示NVDA供应链改善，对AMD构成参照。"]
    payload = {"ticker": "AMD", "context_tickers": ["NVDA"]}

    validated = validate_result(
        "signal_analysis", json.dumps(result, ensure_ascii=False), payload
    )
    assert "NVDA" in validated["event_risks"][0]

    # 不在上下文代码表里的代码照旧拒：幻觉实体闸不放松。
    with pytest.raises(Exception):
        validate_result(
            "signal_analysis",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AMD"},
        )


def test_source_binding_covers_context_news_titles():
    result = _signal_result()
    result["event_risks"] = ["Hormel Foods公布最新季度业绩可能影响防御板块情绪。"]
    bound_payload = {
        "ticker": "AMD",
        "recent_news": {
            "items": [
                {"title": "Hormel Foods reports fiscal third-quarter results"}
            ]
        },
    }

    validated = validate_result(
        "signal_analysis", json.dumps(result, ensure_ascii=False), bound_payload
    )
    assert validated["event_risks"][0].startswith("Hormel Foods")

    with pytest.raises(Exception):
        validate_result(
            "signal_analysis",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AMD"},
        )


def test_source_texts_collect_every_context_block():
    payload = {
        "ticker": "AMD",
        "signals": {"rsi14": {"label": "RSI(14)"}},
        "scores": {"trend": 70},
        "recent_news": {"items": [{"title": "Meridian Holdings sponsorship"}]},
        "options_chain": {"expirations": [{"unusual_alerts": [{"reasons": ["Vol/OI 3.0x"]}]}]},
        "market_context": {"signals": {"vix": {"label": "VIX"}}},
        "macro_conditions": {"top_improving": [{"display_name_zh": "金融条件"}]},
        "upcoming_earnings": {"earnings_date": "2026-08-27"},
    }

    texts = _validation_source_texts("signal_analysis", payload)

    assert "Meridian Holdings sponsorship" in texts
    assert "Vol/OI 3.0x" in texts
    assert "VIX" in texts
    assert "金融条件" in texts
    assert "2026-08-27" in texts


def test_signal_analysis_prompt_declares_context_semantics():
    request = runtime.build_runtime_request("signal_analysis", {"ticker": "AMD"})

    assert request.schema_name == "signal_analysis_zh_cn_v5"
    for phrase in (
        "market_context",
        "macro_conditions",
        "options_chain",
        "recent_news",
        "upcoming_earnings",
        "不得重新计算、修改或补造",
        "不得提及或推测该维度",
        "只能在data_quality_notes中说明该数据不可用",
        "不能把Call解读为看多或把Put解读为看空",
        "历史滚动分位，不是概率",
        "只能使用输入中出现的代码",
        "若evidence_stale为true",
    ):
        assert phrase in request.instructions, phrase
