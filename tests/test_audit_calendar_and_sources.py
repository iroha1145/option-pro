from collections import defaultdict
from datetime import datetime, timezone

import pytest

from app.services.catalysts.local_intelligence import LocalCatalystIntelligence, _public_calendar_title


@pytest.mark.parametrize("family", ["CPI", "PPI", "PCE Price Index"])
def test_economic_release_titles_keep_core_and_comparison_identity(family):
    raw = [f"Core {family} m/m", f"{family} m/m", f"Core {family} y/y", f"{family} y/y"]
    titles = [_public_calendar_title(title) for title in raw]
    assert len(set(titles)) == 4
    assert "核心" in titles[0] and "核心" not in titles[1]
    assert "环比" in titles[0] and "同比" in titles[2]
    assert all(source in title for source, title in zip(raw, titles))


@pytest.mark.parametrize("source", ["Core Retail Sales m/m", "Average Earnings Index 3m/y", "GDP q/q Annualized (Q2)",
                                  "EIA Natural Gas Storage", "EIA Crude Oil Inventories", "Trimmed Mean CPI y/y"])
def test_calendar_keeps_specific_source_when_a_generic_translation_would_lose_it(source):
    assert source in _public_calendar_title(source)


def test_calendar_initial_and_final_estimates_remain_distinct_and_markup_is_removed():
    first = _public_calendar_title("<b>Prelim GDP q/q</b>")
    final = _public_calendar_title("Final GDP q/q")
    assert "初值" in first and "终值" in final and first != final
    assert "<b>" not in first


@pytest.mark.parametrize("count,label", [(1, "单一来源"), (2, "多个来源报道")])
def test_hotspot_source_explanation_matches_count_without_changing_scores(count, label):
    score, components, reasons = LocalCatalystIntelligence._hot_score({"source_count": count}, None, datetime.now(timezone.utc))
    assert score == 30 + 18 * count and components == {"source_breadth": score}
    assert reasons == [label]


def test_stored_legacy_hotspots_are_corrected_at_the_public_boundary():
    row = defaultdict(lambda: None, {
        "prepared_revision": 1, "event_group_id": "legacy", "event_group_version": 1, "hot_score": 48,
        "source_count": 1, "representative_news_id": 1, "reasons_json": '["发布时间较近","多来源交叉出现"]',
    })
    projected = LocalCatalystIntelligence._project_hotspot_rows([row], prepared_at=None)[0]
    assert projected["reasons"] == ["发布时间较近", "单一来源"]
    assert projected["hot_score"] == 48
