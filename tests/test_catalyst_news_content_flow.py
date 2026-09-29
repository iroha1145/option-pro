from __future__ import annotations

import copy
import asyncio
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.worker import process_job
from app.services.catalysts import local_intelligence as local
from app.services.catalysts.personal_service import PersonalCatalystService
from app.services.catalysts.errors import CatalystError
from test_catalyst_local_intelligence import (
    _apply_news,
    _finish_job,
    _job_payload,
    _news_change,
    _news_result,
    _stack,
)
from test_ai_jobs import _settings


@pytest.fixture(autouse=True)
def owner():
    with request_owner_access_context(True):
        yield


@pytest.fixture
def story(tmp_path):
    etl, ai, engine = _stack(tmp_path)
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 1, available_at=now - timedelta(minutes=5))], as_of=now)
    engine.reconcile()
    return etl, ai, engine


def article(*, text="新闻正文明确记载了新品交付时间与客户订单。" * 40, status="available"):
    return {
        "status": status,
        "text": text if status == "available" else "",
        "source_url": "https://example.test/news/1/1",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "reason": None if status == "available" else "http_403",
        "truncated": False,
    }


def publish(ai, engine, job):
    _finish_job(ai, job["job_id"], _news_result(news_id=1, change_sequence=1, content_hash="hash-1-1"))
    engine.reconcile()
    return engine.news(1, as_of=datetime.now(timezone.utc))["item"]


def test_body_is_persisted_bound_and_handed_to_model(story):
    _etl, ai, engine = story
    source = article()
    calls = []
    engine._article_fetcher = lambda url, **kwargs: (calls.append((url, kwargs)) or source)
    job = engine.request_analysis(1, force=False)
    payload = _job_payload(ai, job["job_id"])
    assert payload["article"]["text"] == source["text"]
    assert calls[0][1]["expected_title"].startswith("NVIDIA launches")
    request = runtime.build_runtime_request("news_impact", payload)
    assert source["text"] in request.input_text
    assert not request.use_web_search
    row = engine._current_revision(1)
    assert engine._news_payload_matches_revision(payload, row)
    changed = copy.deepcopy(payload)
    changed["article"]["text"] = "另一篇新闻的正文"
    assert not engine._news_payload_matches_revision(changed, row)
    changed = copy.deepcopy(payload)
    changed["article"]["source_url"] = "https://other.example/story"
    assert not engine._news_payload_matches_revision(changed, row)
    item = publish(ai, engine, job)
    public = PersonalCatalystService._project_news_item(item)
    assert public["analysis"] is not None
    assert public["analysis_input"]["basis"] == "article_body"
    assert public["analysis_input"]["body_characters"] == len(source["text"])
    assert public["source_title"].startswith("NVIDIA launches")
    assert "article_json" not in public and "_validation_article" not in public
    assert source["text"] not in json.dumps(public, ensure_ascii=False)
    cached = engine.request_analysis(1, force=False)
    assert cached["cached"] and cached["job_id"] == job["job_id"]
    assert len(calls) == 1


def test_failed_fetch_still_analyzes_summary_and_marks_actual_input(story):
    _etl, ai, engine = story
    engine._article_fetcher = lambda *_args, **_kwargs: article(status="unavailable")
    job = engine.request_analysis(1, force=False)
    payload = _job_payload(ai, job["job_id"])
    assert "article" not in payload
    assert payload["article_status"] == "unavailable"
    item = publish(ai, engine, job)
    assert item["analysis_input"] == {
        "basis": "title_summary", "article_status": "unavailable", "reason": "http_403",
        "body_characters": 0, "truncated": False,
    }


def test_later_fetch_does_not_relabel_old_analysis(story):
    _etl, ai, engine = story
    original = engine.request_analysis(1, force=False)
    publish(ai, engine, original)
    before = datetime.now(timezone.utc)
    source = article()
    engine._article_fetcher = lambda *_args, **_kwargs: source
    replacement = engine.request_analysis(1, force=True)
    assert replacement["job_id"] != original["job_id"]
    item = engine.news(1, as_of=datetime.now(timezone.utc))["item"]
    assert item["analysis_input"]["basis"] == "title_summary"
    historical = engine.news(1, as_of=before)["item"]
    assert historical["_validation_article"] is None
    assert historical["analysis_input"]["basis"] == "title_summary"
    publish(ai, engine, replacement)
    assert engine.news(1, as_of=datetime.now(timezone.utc))["item"]["analysis_input"]["basis"] == "article_body"


def test_body_and_summary_share_paid_byte_budget(story):
    _etl, ai, engine = story
    long_summary = "摘要<财务数据>" * 20_000
    with sqlite3.connect(engine.db_path) as connection:
        connection.execute("UPDATE catalyst_local_news_revisions SET raw_summary=?", (long_summary,))
    source = article(text=("正文<财务数据>" * 2000)[:12_000])
    engine._article_fetcher = lambda *_args, **_kwargs: source
    job = engine.request_analysis(1, force=False)
    payload = _job_payload(ai, job["job_id"])
    assert payload["article"]["truncated"]
    assert payload["truncated_fields"] == ["summary"]
    assert len(runtime.build_runtime_request("news_impact", payload).input_text.encode()) < 60_000
    assert engine._news_payload_matches_revision(payload, engine._current_revision(1))


def test_body_entities_are_accepted_through_result_and_public_validation(story):
    _etl, ai, engine = story
    source = article(text="NVIDIA signs an agreement with Fictiva Labs. " * 15)
    engine._article_fetcher = lambda *_args, **_kwargs: source
    job = engine.request_analysis(1, force=False)
    result = _news_result(news_id=1, change_sequence=1, content_hash="hash-1-1")
    result["summary_zh"] = "英伟达与Fictiva Labs签署合作协议，交付情况仍待后续验证。"
    payload = _job_payload(ai, job["job_id"])
    validate_result("news_impact", json.dumps(result), payload)
    _finish_job(ai, job["job_id"], result)
    engine.reconcile()
    projected = PersonalCatalystService._project_news_item(engine.news(1, as_of=datetime.now(timezone.utc))["item"])
    assert projected["analysis"]["summary_zh"] == result["summary_zh"]


def test_routine_items_do_not_fill_feed_pages_or_scheduled_slots(tmp_path):
    etl, _ai, engine = _stack(tmp_path)
    now = datetime.now(timezone.utc)
    changes = [
        _news_change(1, 1, available_at=now - timedelta(minutes=3), title="Fed cuts interest rates by 25 basis points", tickers=()),
        _news_change(2, 2, available_at=now - timedelta(minutes=2), title="Example declares $0.2175 dividend", summary="Quarterly dividend payable on October 30."),
        _news_change(3, 3, available_at=now - timedelta(minutes=1), title="Example declares $0.50 special dividend"),
    ]
    _apply_news(etl, changes, as_of=now)
    engine.reconcile()
    first = engine.feed(as_of=now, limit=1)
    second = engine.feed(as_of=now, limit=1, cursor=first["next_cursor"])
    assert [first["items"][0]["news_id"], second["items"][0]["news_id"]] == [3, 1]
    assert first["summary"]["count"] == 2
    assert second["next_cursor"] is None
    assert [row["news_id"] for row in engine._scheduled_news_candidates(now=now, limit=2)] == [3, 1]
    with sqlite3.connect(engine.db_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_news_revisions").fetchone()[0] == 3
    with engine._connect() as connection:
        plan = engine._plan_hotspots(connection, now=now)
    assert {identity["news_id"] for group in plan for identity in json.loads(group["news_identities_json"])} == {1, 3}
    assert engine.news(2, as_of=now) is not None


def test_material_body_rescues_routine_title_without_changing_history(tmp_path):
    etl, ai, engine = _stack(tmp_path)
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 1, available_at=now - timedelta(minutes=5), title="Example declares $0.2175 dividend")], as_of=now)
    engine.reconcile()
    before = datetime.now(timezone.utc)
    assert engine.feed(as_of=before)["items"] == []
    source = article(text="The board increased the dividend by 25 percent, the first increase in two years. " * 12)
    engine._article_fetcher = lambda *_args, **_kwargs: source
    engine.request_analysis(1, force=False)
    assert [i["news_id"] for i in engine.feed(as_of=datetime.now(timezone.utc))["items"]] == [1]
    assert engine.feed(as_of=before)["items"] == []


def test_scheduler_probes_routine_title_before_discarding_a_material_body(tmp_path):
    etl, _ai, engine = _stack(tmp_path)
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 1, available_at=now - timedelta(minutes=5), title="Example declares $0.2175 dividend")], as_of=now)
    engine.reconcile()
    engine._article_fetcher = lambda *_args, **_kwargs: article(text="The company increased its dividend by 25 percent. " * 15)
    candidates = engine._scheduled_news_candidates(now=now, limit=1)
    assert [row["news_id"] for row in candidates] == [1]
    assert engine.feed(as_of=now)["items"] == []
    assert engine.feed(as_of=datetime.now(timezone.utc))["items"][0]["news_id"] == 1


def test_failed_fetch_cooldown_and_first_success_is_immutable(story):
    _etl, _ai, engine = story
    calls = []
    engine._article_fetcher = lambda *_args, **_kwargs: (calls.append(1) or article(status="unavailable"))
    row = engine._prepare_article(engine._current_revision(1), force=False)
    engine._prepare_article(row, force=False)
    assert len(calls) == 1
    source = article()
    engine._article_fetcher = lambda *_args, **_kwargs: (calls.append(1) or source)
    row = engine._prepare_article(row, force=True)
    immutable = row["article_json"]
    row = engine._prepare_article(row, force=True)
    assert len(calls) == 2 and row["article_json"] == immutable


def test_inflight_body_never_appears_in_an_earlier_snapshot(story):
    _etl, _ai, engine = story
    row = engine._current_revision(1)
    started = datetime.now(timezone.utc) - timedelta(seconds=5)
    source = article()
    source["fetched_at"] = started.isoformat()
    engine._article_fetcher = lambda *_args, **_kwargs: source
    stored = engine._prepare_article(row, force=False)
    assert local._article_snapshot(stored, as_of=started + timedelta(seconds=1)) is None
    assert local._article_snapshot(stored, as_of=datetime.now(timezone.utc))["text"] == source["text"]


def test_fetch_finishing_cannot_attach_a_new_page_to_a_superseded_revision(story):
    etl, ai, engine = story
    started = datetime.now(timezone.utc)

    def fetch(*_args, **_kwargs):
        changed = datetime.now(timezone.utc)
        _apply_news(etl, [_news_change(2, 1, available_at=changed)], as_of=changed)
        engine.reconcile()
        return article()

    engine._article_fetcher = fetch
    with pytest.raises(CatalystError) as error:
        engine.request_analysis(1, force=False, as_of=started,
                                expected_change_sequence=1, expected_content_hash="hash-1-1",
                                submission_source="scheduled")
    assert error.value.code == "news_revision_changed"
    with sqlite3.connect(ai.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 0


def test_v6_database_upgrades_without_rewriting_paid_history(tmp_path):
    _etl, _ai, engine = _stack(tmp_path)
    with sqlite3.connect(engine.db_path) as connection:
        connection.execute("ALTER TABLE catalyst_local_news_revisions DROP COLUMN article_json")
        connection.execute("ALTER TABLE catalyst_local_news_revisions DROP COLUMN article_checked_at")
        connection.execute("ALTER TABLE catalyst_local_analysis_links DROP COLUMN input_context_json")
        connection.execute("DELETE FROM catalyst_local_schema WHERE version=?", (local.SCHEMA_VERSION,))
        connection.execute("INSERT INTO catalyst_local_schema VALUES('optix-local-catalyst-v6','prior-checksum','2026-09-01T00:00:00Z')")
    engine.initialize()
    engine.initialize()
    with sqlite3.connect(engine.db_path) as connection:
        assert connection.execute("SELECT checksum FROM catalyst_local_schema WHERE version='optix-local-catalyst-v6'").fetchone()[0] == "prior-checksum"
        assert {r[1] for r in connection.execute("PRAGMA table_info(catalyst_local_news_revisions)")} >= {"article_json", "article_checked_at"}


def test_previous_prompt_remains_readable_without_refetch_or_repayment(story):
    _etl, ai, engine = story
    job = engine.request_analysis(1, force=False)
    with sqlite3.connect(ai.path) as connection:
        connection.execute("UPDATE ai_jobs SET prompt_version='news-impact-zh-cn-v6',schema_sha256=? WHERE job_id=?", (runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY[1], job["job_id"]))
    publish(ai, engine, job)
    engine._article_fetcher = lambda *_args, **_kwargs: pytest.fail("must not refetch a paid cached result")
    reused = engine.request_analysis(1, force=False)
    assert reused["job_id"] == job["job_id"] and reused["cached"]


def test_legacy_news_contract_exception_is_exact_and_keeps_policy_guard(monkeypatch):
    assert runtime.schema_identity("news_impact") == runtime.NEWS_CONTENT_SCHEMA_IDENTITY
    assert runtime.news_schema_identity_matches("news-impact-zh-cn-v6", *runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY)
    assert not runtime.news_schema_identity_matches("news-impact-zh-cn-v7", *runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY)
    monkeypatch.setattr(runtime, "schema_identity", lambda _job: ("news_impact_zh_cn_v6", "another-policy"))
    assert not runtime.news_schema_identity_matches("news-impact-zh-cn-v6", *runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY)


def test_pending_real_v6_contract_continues_in_worker_without_new_job(story, monkeypatch):
    _etl, ai, engine = story
    job = engine.request_analysis(1, force=False)
    with sqlite3.connect(ai.path) as connection:
        connection.execute("UPDATE ai_jobs SET prompt_version='news-impact-zh-cn-v6',schema_sha256=? WHERE job_id=?", (runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY[1], job["job_id"]))
    submissions = []

    async def submit(*_args, **_kwargs):
        submissions.append(1)
        return SimpleNamespace(status="queued", id="response-legacy-news-test")

    monkeypatch.setattr(runtime, "submit_background", submit)
    owner = "legacy-news-worker"
    claimed = ai.claim_due(owner, 60)
    asyncio.run(process_job(ai, _settings(ai.path), claimed, owner))
    stored = ai.get_job(job["job_id"])
    assert submissions == [1]
    assert stored["status"] == "queued" and stored["error_code"] is None
    assert engine.analysis_job(job["job_id"])["status"] == "queued"


def test_concurrent_summary_and_body_fetches_cannot_create_two_paid_jobs(story):
    _etl, ai, engine = story
    barrier = threading.Barrier(2)
    calls = []

    def fetch(*_args, **_kwargs):
        calls.append(1)
        # A second fetch would produce a different payload. The same revision
        # must have only one enqueue even when it was requested concurrently.
        return article(status="unavailable" if len(calls) == 1 else "available")

    engine._article_fetcher = fetch

    def submit():
        with request_owner_access_context(True):
            barrier.wait(timeout=3)
            return engine.request_analysis(1, force=False)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(lambda _index: submit(), range(2)))
    assert first["job_id"] == second["job_id"]
    assert len(calls) == 1
    with sqlite3.connect(ai.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1


def test_stale_scheduled_retry_reuses_the_running_job(story):
    _etl, ai, engine = story
    calls = []
    engine._article_fetcher = lambda *_args, **_kwargs: (calls.append(1) or article(status="unavailable" if len(calls) == 1 else "available"))
    options = dict(force=True, as_of=datetime.now(timezone.utc), expected_change_sequence=1,
                   expected_content_hash="hash-1-1", submission_source="scheduled", _job_snapshot={})
    first = engine.request_analysis(1, **options)
    second = engine.request_analysis(1, **options)
    assert first["job_id"] == second["job_id"] and len(calls) == 1
    with sqlite3.connect(ai.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1
