"""The news-page hotspot strip stayed on 「当前时段暂无热点」 (2026-10-10).

Titles come from the production snapshot of that morning: two groups carried
published Chinese analyses, the hottest three still had English source titles.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import repository as repository_module
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts import personal_service as personal_module
from app.services.catalysts.local_intelligence import (
    HOTSPOT_WAITING,
    SUMMARY_WAITING,
    LocalCatalystIntelligence,
)
from test_catalyst_local_intelligence import (
    _apply_news,
    _finish_job,
    _news_change,
    _news_result,
    _stack,
)
from test_personal_catalyst_service import FakeIntelligence, _service
from test_verified_hotspot_publication import complete_verified


REIT_TITLE = "通信塔REIT股票因太空探索技术公司与格兰管理公司的频谱交易下跌"
REIT_SUMMARY = "频谱交易可能削弱通信塔租户需求，相关股票承压。"
REIT_SOURCE_TITLE = "Cell tower REIT stocks fall on SpaceX spectrum deal with Gran Management"
WRAP_TITLE = "10月9日市场动态：太空探索技术公司的频谱交易威胁电信股"
WRAP_SUMMARY = "频谱交易引发电信股下跌，市场关注后续监管审批。"
WRAP_SOURCE_TITLE = "October 9 market wrap: SpaceX spectrum deal threatens telecom stocks"
ENGLISH_LEADERS = {
    31: "NYMEX: Precious Metals Prices",
    32: "Egypt Inflation Slows Again in September",
    33: "Israel's economy prospers despite the war",
}
# Unanalyzed, but its source title is already Chinese.
UNANALYZED_ZH_TITLE = "美联储会议纪要显示官员对降息节奏存在分歧"


@pytest.fixture(autouse=True)
def _owner_request_context():
    # Setup writes as the owner; visitor reads opt out explicitly and then use
    # the read-only connection a public request gets.
    with request_owner_access_context(True):
        yield


def _visitor_strip(service, **kwargs):
    with request_owner_access_context(False):
        return service.hotspots(**kwargs)


def _strip_stack(tmp_path):
    """Three multi-source English leaders, one unanalyzed Chinese source
    title, and two low-relevance groups with published analyses behind them."""

    etl, ai, intelligence = _stack(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    changes = [
        _news_change(
            sequence,
            news_id,
            available_at=now - timedelta(minutes=10),
            title=title,
            sources=("Reuters", "Bloomberg", "CNBC", "Dow Jones"),
        )
        for sequence, (news_id, title) in enumerate(ENGLISH_LEADERS.items(), start=1)
    ]
    changes += [
        _news_change(
            4, 34, available_at=now - timedelta(minutes=10),
            title=UNANALYZED_ZH_TITLE, summary="会议纪要显示多数官员支持逐步降息。",
            source="Xinhua",
        ),
        _news_change(5, 41, available_at=now - timedelta(minutes=10), title=REIT_SOURCE_TITLE),
        _news_change(6, 42, available_at=now - timedelta(minutes=10), title=WRAP_SOURCE_TITLE),
    ]
    _apply_news(etl, changes, as_of=now - timedelta(minutes=9))
    intelligence.reconcile()
    for sequence, news_id, title, summary in (
        (5, 41, REIT_TITLE, REIT_SUMMARY),
        (6, 42, WRAP_TITLE, WRAP_SUMMARY),
    ):
        job = intelligence.request_analysis(news_id, force=False)
        result = _news_result(
            news_id=news_id,
            change_sequence=sequence,
            content_hash=f"hash-{news_id}-{sequence}",
        )
        result.update(
            title_zh=title,
            summary_zh=summary,
            headline_summary=summary,
            market_relevance=5,
        )
        _finish_job(ai, job["job_id"], result)
    intelligence.reconcile()
    return ai, intelligence


def _titles(payload):
    return [item["representative_title"] for item in payload["items"]]


def test_published_filter_runs_before_the_engine_limit(tmp_path):
    _ai, intelligence = _strip_stack(tmp_path)

    # The scheduler's read is unchanged: untranslated leaders first, so it
    # can queue their analyses.
    leaders = intelligence._prepared_hotspots(limit=3)["items"]
    assert {item["representative_news_id"] for item in leaders} == set(ENGLISH_LEADERS)
    assert not any(item["_analysis_published"] for item in leaders)

    published = intelligence.hotspots(limit=3, published_only=True)["items"]
    assert [item["representative_news_id"] for item in published] in ([41, 42], [42, 41])
    assert {item["_analysis_copy"]["title_zh"] for item in published} == {REIT_TITLE, WRAP_TITLE}
    assert {item["_analysis_copy"]["summary_zh"] for item in published} == {REIT_SUMMARY, WRAP_SUMMARY}


def test_visitor_strip_lists_only_groups_with_published_analysis(tmp_path):
    ai, intelligence = _strip_stack(tmp_path)

    payload = _visitor_strip(
        _service("manual", engine=intelligence, repository=ai),
        limit=8,
    )

    assert payload["status"] == "active"
    assert sorted(_titles(payload)) == sorted([REIT_TITLE, WRAP_TITLE])
    serialized = json.dumps(payload, ensure_ascii=False)
    assert UNANALYZED_ZH_TITLE not in serialized
    for english in (*ENGLISH_LEADERS.values(), REIT_SOURCE_TITLE, WRAP_SOURCE_TITLE, "NYMEX"):
        assert english not in serialized
    assert not any(key.startswith("_") for item in payload["items"] for key in item)


def test_untranslated_leaders_cannot_fill_the_visitor_scan_window(tmp_path, monkeypatch):
    # Production had 2,661 groups; shrink the service window to the three
    # English leaders so the old order (truncate, then filter) shows up.
    ai, intelligence = _strip_stack(tmp_path)
    monkeypatch.setattr(personal_module, "_HOTSPOT_PROJECTION_SCAN_LIMIT", 3)

    payload = _visitor_strip(
        _service("manual", engine=intelligence, repository=ai),
        limit=3,
    )

    assert sorted(_titles(payload)) == sorted([REIT_TITLE, WRAP_TITLE])


def test_owner_strip_keeps_the_unfiltered_read(tmp_path, monkeypatch):
    ai, intelligence = _strip_stack(tmp_path)
    reads = []
    original = intelligence.hotspots

    def recording_hotspots(**kwargs):
        reads.append(kwargs["published_only"])
        return original(**kwargs)

    monkeypatch.setattr(intelligence, "hotspots", recording_hotspots)
    service = _service("manual", engine=intelligence, repository=ai)

    owner = service.hotspots(limit=8)
    visitor = _visitor_strip(service, limit=8)

    assert reads == [False, True]
    assert sorted(_titles(owner)) == sorted([UNANALYZED_ZH_TITLE, REIT_TITLE, WRAP_TITLE])
    assert sorted(_titles(visitor)) == sorted([REIT_TITLE, WRAP_TITLE])
    serialized = json.dumps(owner, ensure_ascii=False)
    for english in (*ENGLISH_LEADERS.values(), REIT_SOURCE_TITLE, WRAP_SOURCE_TITLE):
        assert english not in serialized


@pytest.mark.parametrize(
    ("stored_title", "stored_summary"),
    [
        # A group version planned before its analysis landed keeps the
        # source text until the next plan.
        (REIT_SOURCE_TITLE, "Raw English summary for item 41"),
        # Legacy rows written with the waiting placeholders.
        (HOTSPOT_WAITING, SUMMARY_WAITING),
    ],
)
def test_published_group_shows_its_analysis_copy_not_the_stored_text(
    tmp_path,
    stored_title,
    stored_summary,
):
    ai, intelligence = _strip_stack(tmp_path)
    with sqlite3.connect(intelligence.db_path) as connection:
        connection.execute(
            """UPDATE catalyst_local_event_groups
               SET representative_title_zh=?,representative_summary_zh=?
               WHERE representative_news_id=41""",
            (stored_title, stored_summary),
        )
    service = _service("manual", engine=intelligence, repository=ai)

    for payload in (_visitor_strip(service, limit=8), service.hotspots(limit=8)):
        reit = next(
            item for item in payload["items"] if item["representative_news_id"] == 41
        )
        assert reit["representative_title"] == REIT_TITLE
        assert reit["summary_zh"] == REIT_SUMMARY
        serialized = json.dumps(payload, ensure_ascii=False)
        for hidden in (REIT_SOURCE_TITLE, "Raw English summary", HOTSPOT_WAITING, SUMMARY_WAITING):
            assert hidden not in serialized


def test_waiting_placeholders_in_checked_copy_are_never_shown():
    engine = FakeIntelligence()

    def hotspots(*, limit, now=None, published_only=False):
        return {
            "status": "active",
            "items": [
                {
                    "event_group_id": "verified",
                    "status": "verified",
                    "verification_status": "verified",
                    "representative_title": WRAP_TITLE,
                    "summary_zh": SUMMARY_WAITING,
                    "validated_tickers": [],
                },
                {
                    # The copy is a placeholder, so the checked fallback runs on
                    # the English source title and hides the group.
                    "event_group_id": "placeholder-copy",
                    "representative_title": REIT_SOURCE_TITLE,
                    "summary_zh": "Raw English summary",
                    "validated_tickers": [],
                    "_analysis_published": True,
                    "_analysis_copy": {"title_zh": HOTSPOT_WAITING, "summary_zh": REIT_SUMMARY},
                },
                {
                    "event_group_id": "published",
                    "representative_title": REIT_SOURCE_TITLE,
                    "summary_zh": "Raw English summary",
                    "validated_tickers": [],
                    "_analysis_published": True,
                    "_analysis_copy": {"title_zh": REIT_TITLE, "summary_zh": SUMMARY_WAITING},
                },
            ][:limit],
        }

    engine.hotspots = hotspots
    payload = _visitor_strip(_service("read", engine=engine), limit=8)

    assert [item["event_group_id"] for item in payload["items"]] == ["verified", "published"]
    assert _titles(payload) == [WRAP_TITLE, REIT_TITLE]
    assert [item["summary_zh"] for item in payload["items"]] == ["", ""]
    serialized = json.dumps(payload, ensure_ascii=False)
    for hidden in (HOTSPOT_WAITING, SUMMARY_WAITING, REIT_SOURCE_TITLE, "Raw English summary"):
        assert hidden not in serialized


@pytest.fixture
def verified_strip(tmp_path, monkeypatch):
    """Production configuration: Sonnet verification decides the strip."""

    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now]
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock[0])
    monkeypatch.setattr(repository_module, "_utcnow", lambda: clock[0])
    monkeypatch.setattr(local_module, "macro_conditions_context", lambda: None)
    etl, ai, original = _stack(tmp_path)
    intelligence = LocalCatalystIntelligence(
        original.db_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"),
        news_model="gpt-5.6-luna", news_reasoning="max",
        focus_model="claude-sonnet-5-5", focus_reasoning="xhigh",
    )
    intelligence.initialize()
    change = _news_change(
        1, 41, available_at=now - timedelta(minutes=10),
        title=REIT_SOURCE_TITLE, sources=("Reuters", "Bloomberg"),
    )
    change["news"]["url"] = "https://www.reuters.com/news/41"
    _apply_news(etl, [change], as_of=now - timedelta(minutes=8))
    revision = intelligence.reconcile()["prepared_revision"]
    return ai, intelligence, revision, clock


def test_verified_hotspot_keeps_its_source_bound_title(verified_strip):
    ai, intelligence, revision, clock = verified_strip
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"], supported_copy=(REIT_TITLE, REIT_SUMMARY))
    intelligence.reconcile()
    # The verified item is stored without its source texts; checking REIT
    # again without them used to drop it from every reader's strip.
    assert _titles(intelligence.hotspots(limit=20)) == [REIT_TITLE]
    service = _service("manual", engine=intelligence, repository=ai)

    for payload in (
        _visitor_strip(service, limit=8, now=clock[0]),
        service.hotspots(limit=8, now=clock[0]),
    ):
        assert payload["status"] == "active"
        assert _titles(payload) == [REIT_TITLE]
        assert payload["items"][0]["summary_zh"] == REIT_SUMMARY
        assert payload["items"][0]["verification_status"] == "verified"
