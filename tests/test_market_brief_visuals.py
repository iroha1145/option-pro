"""首页市场报告的图表数据：只从该次证据包取标签、数字与日期，缺了就是 None，不补值。"""

from __future__ import annotations

import copy
import json
import math
from typing import Any

import pytest

from app.services.market_brief.visuals import (
    MAX_THEME_ROWS,
    breadth_chart,
    brief_visuals,
    cited_theme_ids,
    macro_chart,
    market_chart,
    sectors_chart,
)

# 生产运行 mb_20261009_post_close_ec65b19c 的证据形状（数值照抄，无关字段从简）。
EVIDENCE: dict[str, Any] = {
    "version": "market-brief-evidence-v1",
    "indices": {
        "as_of": "2026-10-10T11:58:40Z",
        "saved_at": "2026-10-10T11:58:52Z",
        "stale": True,
        "unit": "change_percent 为百分数",
        "rows": [
            {"id": "idx:^GSPC", "symbol": "^GSPC", "name_zh": "标普500指数", "price": 7811.54, "change_percent": 0.59},
            {"id": "idx:^IXIC", "symbol": "^IXIC", "name_zh": "纳斯达克综合指数", "price": 26101.3, "change_percent": 0.64},
            {"id": "idx:^DJI", "symbol": "^DJI", "name_zh": "道琼斯工业平均指数", "price": 48011.2, "change_percent": 0.83},
            {"id": "idx:^N225", "symbol": "^N225", "name_zh": "日经225指数", "price": 48210.0, "change_percent": -0.27},
            {"id": "idx:000001.SS", "symbol": "000001.SS", "name_zh": "上证综合指数", "price": 3881.0, "change_percent": 0.07},
        ],
    },
    "internals": {
        "market_signals": {
            "as_of": "2026-10-10T17:58:33Z",
            "saved_at": "2026-10-10T17:58:40Z",
            "stale": False,
            "sector_etf_breadth": {"available": 11, "expected": 11, "above_50dma": 3},
            "signals": [{"id": "sig:vix", "field": "vix", "value": 14.84, "label": "低波动"}],
        },
        "market_regime": {"id": "regime:market", "served_session": "2026-10-09", "score": 59.5},
    },
    "themes": {
        "served_session": "2026-10-09",
        "spy_return_1mo": 1.87,
        "rows": [
            {"id": "theme:software", "name": "软件基础设施", "excess_vs_spy_1mo": 10.68, "leaders": [{"ticker": "CRWD", "score": 90.1}]},
            {"id": "theme:ai_cloud", "name": "AI 与云", "excess_vs_spy_1mo": 4.48, "leaders": []},
            {"id": "theme:semiconductors", "name": "半导体", "excess_vs_spy_1mo": 4.19, "leaders": []},
            {"id": "theme:healthcare", "name": "医疗保健", "excess_vs_spy_1mo": 0.34, "leaders": []},
            {"id": "theme:airlines", "name": "航空运输", "excess_vs_spy_1mo": -2.8, "leaders": []},
            {"id": "theme:real_estate", "name": "房地产", "excess_vs_spy_1mo": -4.78, "leaders": []},
            {"id": "theme:telecom", "name": "电信", "excess_vs_spy_1mo": -18.35, "leaders": []},
        ],
    },
    "macro": {
        "id": "macro:composite",
        "data_through": "2026-10-02",
        "unit": "分位分数 0-100，越高越宽松或越支持风险资产",
        "composite_score": 49.5,
        "modules": [
            {"id": "macro:credit", "name_zh": "信用", "score": 40.9},
            {"id": "macro:external", "name_zh": "外部冲击", "score": 51.7},
            {"id": "macro:funding", "name_zh": "融资", "score": 72.0},
            {"id": "macro:liquidity", "name_zh": "流动性", "score": 27.2},
            {"id": "macro:rates", "name_zh": "利率", "score": 34.6},
            {"id": "macro:risk", "name_zh": "风险", "score": 58.1},
            {"id": "macro:treasury", "name_zh": "国债", "score": 62.4},
        ],
    },
}
RESULT: dict[str, Any] = {
    "internals": {"evidence_ids": ["idx:^GSPC", "sig:sma50_distance", "theme:software", "theme:telecom"]},
    "macro_check": {"evidence_ids": ["sig:vix", "macro:composite", "macro:rates"]},
    "sectors": [
        {"name": "电信运营商", "evidence_ids": ["theme:telecom", "prior:mb_20261009_pre_open_a86cbde1"]},
        {"name": "软件基础设施", "evidence_ids": ["theme:software", "iv:software"]},
        {"name": "房地产", "evidence_ids": ["theme:real_estate"]},
    ],
    "key_news": [{"evidence_id": "hot:evt_1"}],
}


def _evidence() -> dict[str, Any]:
    return copy.deepcopy(EVIDENCE)


def test_production_evidence_yields_the_four_charts() -> None:
    visuals = brief_visuals(_evidence(), RESULT)
    assert visuals == {
        "market": {
            "as_of": "2026-10-10T11:58:40Z",
            "stale": True,
            "rows": [
                {"id": "idx:^GSPC", "label": "标普500", "value": 0.59},
                {"id": "idx:^IXIC", "label": "纳指综合", "value": 0.64},
                {"id": "idx:^DJI", "label": "道琼斯", "value": 0.83},
            ],
        },
        "breadth": {"above": 3, "total": 11, "expected": 11, "as_of": "2026-10-10T17:58:33Z", "stale": False},
        "sectors": {
            "served_session": "2026-10-09",
            # 只画研判引用过的主题（行业段与两段正文的 evidence_ids），从高到低。
            "rows": [
                {"id": "theme:software", "label": "软件基础设施", "value": 10.68},
                {"id": "theme:real_estate", "label": "房地产", "value": -4.78},
                {"id": "theme:telecom", "label": "电信", "value": -18.35},
            ],
        },
        "macro": {
            "data_through": "2026-10-02",
            "rows": [
                {"id": "macro:credit", "label": "信用", "value": 40.9},
                {"id": "macro:external", "label": "外部冲击", "value": 51.7},
                {"id": "macro:funding", "label": "融资", "value": 72.0},
                {"id": "macro:liquidity", "label": "流动性", "value": 27.2},
                {"id": "macro:rates", "label": "利率", "value": 34.6},
                {"id": "macro:risk", "label": "风险", "value": 58.1},
                {"id": "macro:treasury", "label": "国债", "value": 62.4},
            ],
        },
    }
    encoded = json.dumps(visuals, ensure_ascii=False)
    for leaked in ("日经", "上证", "7811.54", "CRWD", "leaders", "低波动", "spy_return", "composite", "AI 与云", "半导体"):
        assert leaked not in encoded, leaked


def test_russell_is_drawn_when_present_and_other_markets_never_are() -> None:
    evidence = _evidence()
    evidence["indices"]["rows"].insert(0, {"id": "idx:^RUT", "symbol": "^RUT", "name_zh": None, "price": 2300.0, "change_percent": -1.2})
    chart = market_chart(evidence)
    assert chart is not None
    assert [row["id"] for row in chart["rows"]] == ["idx:^GSPC", "idx:^IXIC", "idx:^DJI", "idx:^RUT"]
    assert chart["rows"][-1] == {"id": "idx:^RUT", "label": "罗素2000", "value": -1.2}


@pytest.mark.parametrize("bad", [None, "0.59", True, math.nan, math.inf, -math.inf, 31.0, -45.0, [0.5]])
def test_an_unusable_index_value_drops_only_that_row(bad: Any) -> None:
    evidence = _evidence()
    evidence["indices"]["rows"][0]["change_percent"] = bad
    chart = market_chart(evidence)
    assert chart is not None
    assert [row["id"] for row in chart["rows"]] == ["idx:^IXIC", "idx:^DJI"]


def test_index_chart_is_none_without_usable_rows_and_keeps_unknown_times_empty() -> None:
    evidence = _evidence()
    for row in evidence["indices"]["rows"]:
        row["change_percent"] = None
    assert market_chart(evidence) is None
    assert market_chart({"indices": None}) is None
    assert market_chart({"indices": {"rows": "oops"}}) is None
    evidence = _evidence()
    evidence["indices"]["as_of"] = "yesterday"
    evidence["indices"].pop("stale")
    chart = market_chart(evidence)
    assert chart is not None and chart["as_of"] is None and chart["stale"] is False


@pytest.mark.parametrize(
    "breadth",
    [
        {"available": 11, "expected": 11, "above_50dma": 12},
        {"available": 12, "expected": 11, "above_50dma": 3},
        {"available": 0, "expected": 11, "above_50dma": 0},
        {"available": 11, "expected": 11, "above_50dma": -1},
        {"available": 11, "expected": 11, "above_50dma": True},
        {"available": 11.0, "expected": 11, "above_50dma": 3},
        {"available": None, "expected": 11, "above_50dma": 3},
        {"available": 200, "expected": 200, "above_50dma": 3},
        "3/11",
    ],
)
def test_breadth_needs_consistent_whole_counts(breadth: Any) -> None:
    evidence = _evidence()
    evidence["internals"]["market_signals"]["sector_etf_breadth"] = breadth
    assert breadth_chart(evidence) is None


def test_breadth_keeps_partial_coverage_and_survives_dropped_internals() -> None:
    evidence = _evidence()
    evidence["internals"]["market_signals"]["sector_etf_breadth"] = {"available": 9, "expected": 11, "above_50dma": 2}
    assert breadth_chart(evidence) == {"above": 2, "total": 9, "expected": 11, "as_of": "2026-10-10T17:58:33Z", "stale": False}
    # 证据超预算时 internals 整块撤下，结构保留、各来源为 None。
    assert breadth_chart({"internals": {"market_signals": None, "market_regime": None}}) is None


def test_cited_themes_come_from_every_evidence_list_with_sectors_first() -> None:
    assert cited_theme_ids(RESULT) == ["theme:telecom", "theme:software", "theme:real_estate"]
    assert cited_theme_ids({"internals": {"evidence_ids": ["theme:energy", "theme:energy", "theme:bad id", 3]}}) == ["theme:energy"]
    assert cited_theme_ids(None) == []


def test_sectors_chart_is_none_when_the_report_cites_no_theme() -> None:
    assert sectors_chart(_evidence(), {"internals": {"evidence_ids": ["idx:^GSPC"]}, "sectors": []}) is None
    assert sectors_chart(_evidence(), {"sectors": [{"evidence_ids": ["theme:not_in_evidence"]}]}) is None
    assert sectors_chart({"themes": None}, RESULT) is None


def test_sectors_chart_skips_bad_rows_and_caps_the_count() -> None:
    evidence = _evidence()
    evidence["themes"]["rows"][0]["excess_vs_spy_1mo"] = math.nan  # software
    evidence["themes"]["rows"][-1]["name"] = "  "  # telecom
    chart = sectors_chart(evidence, RESULT)
    assert chart == {"served_session": "2026-10-09", "rows": [{"id": "theme:real_estate", "label": "房地产", "value": -4.78}]}

    many = {"themes": {"served_session": "bad", "rows": [
        {"id": f"theme:t{index}", "name": f"主题{index}", "excess_vs_spy_1mo": float(index)} for index in range(15)
    ]}}
    cited = {"sectors": [{"evidence_ids": [f"theme:t{index}" for index in range(15)]}]}
    chart = sectors_chart(many, cited)
    assert chart is not None and chart["served_session"] is None
    assert [row["value"] for row in chart["rows"]] == [float(index) for index in reversed(range(MAX_THEME_ROWS))]


def test_macro_chart_keeps_only_scores_between_zero_and_one_hundred() -> None:
    evidence = _evidence()
    modules = evidence["macro"]["modules"]
    modules[0]["score"] = 101
    modules[1]["score"] = None
    modules[2]["id"] = "theme:funding"
    modules.append({"id": "macro:rates", "name_zh": "利率", "score": 1.0})
    chart = macro_chart(evidence)
    assert chart is not None
    assert [row["id"] for row in chart["rows"]] == ["macro:liquidity", "macro:rates", "macro:risk", "macro:treasury"]
    assert chart["rows"][1]["value"] == 34.6

    evidence = _evidence()
    evidence["macro"]["data_through"] = "2026-10-02T21:00:00+00:00"
    assert macro_chart(evidence)["data_through"] == "2026-10-02T21:00:00Z"
    evidence["macro"]["data_through"] = None
    assert macro_chart(evidence)["data_through"] is None
    assert macro_chart({"macro": {"modules": []}}) is None


def test_whole_visuals_are_none_when_nothing_can_be_drawn() -> None:
    assert brief_visuals(None, RESULT) is None
    assert brief_visuals("evidence", RESULT) is None
    assert brief_visuals({"version": "market-brief-evidence-v1", "session": {"slot": "post_close"}}, RESULT) is None
    only_macro = brief_visuals({"macro": EVIDENCE["macro"]}, RESULT)
    assert only_macro is not None
    assert only_macro["market"] is None and only_macro["breadth"] is None and only_macro["sectors"] is None


def test_building_visuals_does_not_change_the_cached_evidence() -> None:
    evidence = _evidence()
    snapshot = copy.deepcopy(evidence)
    brief_visuals(evidence, RESULT)
    assert evidence == snapshot
