"""站长新闻流热请求不再随窗口条数重建条目（2026-10-11）。

生产实测（main 14c1924e）：站长 72 小时新闻流热请求每次 0.71 秒，7 天 1.62 秒。
访客有条目缓存，站长每次给窗口里每一条重建条目；没有已发布分析的条目还要按
任务身份核对一次，Luna 新闻每次把三种请求形态的身份全算一遍（一次请求 680 次）。

现在站长和访客共用同一份只由催化剂库决定的条目（按库游标缓存），站长在每次请求
时只给未发布分析的条目叠上任务状态：只读这些条目关联的任务，只读身份和状态需要
的列，每种模型与请求形态的身份一次请求只算一次。下面的测试按次数断言，不量挂钟。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.access import request_owner_access_context
from app.personal_config import FeatureConfig, PersonalConfig
from app.services.ai_jobs import repository as ai_repository_module
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService
from test_catalyst_local_intelligence import (
    _apply_news,
    _fail_job,
    _finish_job,
    _news_change,
    _news_result,
)

NOW = datetime(2030, 7, 16, 20, 0, tzinfo=timezone.utc)
LUNA = "gpt-5.6-luna"


@pytest.fixture
def clock(monkeypatch):
    state = {"now": NOW}
    monkeypatch.setattr(local_module, "_utc_now", lambda: state["now"])
    monkeypatch.setattr(ai_repository_module, "_utcnow", lambda: state["now"])
    return state


def _stack(tmp_path: Path) -> tuple[CatalystEtlRepository, AIJobRepository, LocalCatalystIntelligence]:
    local_module._reset_revision_cache()
    cache_path = tmp_path / "catalyst-cache.db"
    etl = CatalystEtlRepository(cache_path)
    etl.initialize()
    ai = AIJobRepository(tmp_path / "ai-jobs.db")
    ai.initialize()
    intelligence = LocalCatalystIntelligence(
        cache_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"),
        news_model=LUNA, news_reasoning="max", max_queued=500,
    )
    with request_owner_access_context(True):
        intelligence.initialize()
    return etl, ai, intelligence


def _service(intelligence: LocalCatalystIntelligence, ai: AIJobRepository) -> PersonalCatalystService:
    return PersonalCatalystService(
        type("SettingsStub", (), {
            "cache_db_path": str(intelligence.db_path), "model": LUNA, "reasoning": "max",
        })(),
        intelligence=intelligence,
        ai_repository=ai,
        personal_config=PersonalConfig(features=FeatureConfig(catalyst_mode="manual")),
        ai_settings=type("AISettingsStub", (), {"personal_etl_enabled": True})(),
    )


def _store(tmp_path: Path, clock: dict, *, published: int, waiting: int, unrequested: int = 2):
    """News 1..N: ``published`` analyzed, ``waiting`` with a queued job, the rest never requested.

    The waiting items alternate summary lengths so their Luna jobs carry
    different request identities (search / summary).
    """

    etl, ai, intelligence = _stack(tmp_path)
    total = published + waiting + unrequested
    _apply_news(
        etl,
        [
            _news_change(
                news_id, news_id, available_at=NOW - timedelta(minutes=30 + news_id),
                summary=("Long source summary " * 20) if news_id % 2 else None,
            )
            for news_id in range(1, total + 1)
        ],
        as_of=NOW - timedelta(minutes=20),
    )
    with request_owner_access_context(True):
        intelligence.reconcile()
        for news_id in range(1, published + 1):
            clock["now"] = NOW - timedelta(minutes=15) + timedelta(seconds=news_id)
            job = intelligence.request_analysis(news_id, force=False)
            _finish_job(ai, job["job_id"], _news_result(
                news_id=news_id, change_sequence=news_id, content_hash=f"hash-{news_id}-{news_id}",
            ))
        intelligence.reconcile()
        for news_id in range(published + 1, published + waiting + 1):
            clock["now"] = NOW - timedelta(minutes=10) + timedelta(seconds=news_id)
            intelligence.request_analysis(news_id, force=False)
    clock["now"] = NOW
    local_module._reset_revision_cache()
    return etl, ai, intelligence


def _count(monkeypatch, owner, name: str) -> list[int]:
    original = getattr(owner, name)
    calls = [0]

    def counted(*args, **kwargs):
        calls[0] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, counted)
    return calls


@pytest.mark.parametrize("waiting", [4, 24])
def test_hot_owner_read_costs_do_not_grow_with_the_window(tmp_path, clock, monkeypatch, waiting) -> None:
    _etl, ai, intelligence = _store(tmp_path, clock, published=3, waiting=waiting)
    service = _service(intelligence, ai)
    with request_owner_access_context(True):
        cold = service.feed(as_of=NOW, window_hours=24, limit=50)
        local = intelligence.feed(as_of=NOW, window_hours=24, limit=50)
    assert cold["summary"]["count"] == 3 + waiting + 2
    assert [item["analysis_status"] for item in local["items"]].count("pending") == waiting

    identity_builds = _count(monkeypatch, ai_runtime, "_schema_identity")
    item_builds = _count(monkeypatch, intelligence, "_published_item")
    public_jobs = _count(monkeypatch, intelligence, "_public_job")
    job_rows = _count(monkeypatch, service, "_verified_news_job_row")
    statements: list[str] = []
    real_connect = local_module.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(local_module.sqlite3, "connect", traced_connect)
    with request_owner_access_context(True):
        hot = service.feed(as_of=NOW, window_hours=24, limit=50)
    monkeypatch.setattr(local_module.sqlite3, "connect", real_connect)

    assert hot == cold
    # The store-derived items come from the shared cache entry.
    assert item_builds[0] == 0
    # Job state needs the status only: no public projection, no per-item read.
    assert public_jobs[0] == 0
    # One identity per Luna request variant per pass, whatever the job count:
    # the local read and the projection are one pass each.
    assert identity_builds[0] <= 2 * len(ai_runtime.LUNA_NEWS_MODES)
    # The projection reads a job only for page items without a published
    # analysis (published ones keep their status, list items report no job).
    assert job_rows[0] == waiting
    snapshot = [statement for statement in statements if "WHERE j.job_id IN" in statement]
    assert len(snapshot) == 1
    for column in ("payload_json", "result_json", "provider_result_json", "j.*"):
        assert column not in snapshot[0]


def _job_states_store(tmp_path: Path, clock: dict):
    """One news item per job state, plus one whose stored identity is stale."""

    etl, ai, intelligence = _stack(tmp_path)
    _apply_news(
        etl,
        [_news_change(news_id, news_id, available_at=NOW - timedelta(minutes=60 + news_id))
         for news_id in range(1, 10)],
        as_of=NOW - timedelta(minutes=50),
    )
    with request_owner_access_context(True):
        clock["now"] = NOW - timedelta(minutes=40)
        intelligence.reconcile()
        jobs = {}
        for news_id in range(1, 9):
            # Distinct creation times keep the claim order fixed.
            clock["now"] = NOW - timedelta(minutes=40) + timedelta(seconds=news_id)
            jobs[news_id] = intelligence.request_analysis(news_id, force=False)["job_id"]
        clock["now"] = NOW - timedelta(minutes=30)
        _finish_job(ai, jobs[1], _news_result(news_id=1, change_sequence=1, content_hash="hash-1-1"))
        intelligence.reconcile()  # 1 is published
        _finish_job(ai, jobs[2], _news_result(news_id=2, change_sequence=2, content_hash="hash-2-2"))  # unpublished
        _fail_job(ai, jobs[3])
        ai.request_cancel(jobs[4])
        claimed = ai.claim_due("owner-5", lease_seconds=3600)
        assert claimed is not None and claimed["job_id"] == jobs[5]
        assert ai.mark_submission_started(jobs[5], "owner-5") == "started"
    later = local_module._iso(NOW - timedelta(minutes=10))
    with sqlite3.connect(ai.path) as connection:
        # 5 was polled later; 6 was submitted later (OpenAI has one paid slot,
        # so it is written directly).
        connection.execute("UPDATE ai_jobs SET last_polled_at=?,updated_at=? WHERE job_id=?", (later, later, jobs[5]))
        connection.execute(
            """UPDATE ai_jobs SET status='in_progress',submission_started_at=?,submitted_at=?,
                      lease_owner='owner-6',updated_at=? WHERE job_id=?""",
            (later, later, later, jobs[6]),
        )
        connection.execute(
            "UPDATE ai_jobs SET status='budget_blocked',error_code='daily_token_limit' WHERE job_id=?", (jobs[7],))
        connection.execute("UPDATE ai_jobs SET schema_sha256=? WHERE job_id=?", ("0" * 64, jobs[8]))
    clock["now"] = NOW
    local_module._reset_revision_cache()
    return ai, intelligence


@pytest.mark.parametrize(
    "moment",
    [NOW, NOW - timedelta(minutes=35), NOW - timedelta(minutes=20), NOW - timedelta(minutes=45)],
)
def test_window_status_matches_the_full_job_projection(tmp_path, clock, moment) -> None:
    """The window path reads state columns only; the detail path builds the
    public job from the whole row. Both must report the same status."""

    ai, intelligence = _job_states_store(tmp_path, clock)
    with request_owner_access_context(True):
        with intelligence._connect() as connection:
            rows, items = intelligence._window_items(connection, as_of=moment, window_hours=24)
            expected = [intelligence._item(connection, row, as_of=moment) for row in rows]
    assert items == expected
    statuses = {item["news_id"]: item["analysis_status"] for item in items}
    if moment == NOW:
        assert statuses == {
            1: "completed", 2: "pending", 3: "failed", 4: "cancelled", 5: "in_progress",
            6: "in_progress", 7: "budget_blocked", 8: "not_requested", 9: "not_requested",
        }
    if moment == NOW - timedelta(minutes=20):
        # Both rows moved on since: 5 had been submitted by then, 6 had not.
        assert (statuses[5], statuses[6]) == ("in_progress", "pending")
    if moment == NOW - timedelta(minutes=35):
        # 1 completed and was published after this moment.
        assert statuses[1] == "pending"
    if moment == NOW - timedelta(minutes=45):
        assert set(statuses.values()) == {"not_requested"}


def test_owner_reads_follow_job_transitions_while_the_store_cache_stays(tmp_path, clock, monkeypatch) -> None:
    _etl, ai, intelligence = _store(tmp_path, clock, published=2, waiting=4, unrequested=1)
    key = (str(intelligence.db_path), 24)
    with request_owner_access_context(True):
        before = intelligence.feed(as_of=NOW, window_hours=24, limit=50)
    entry = local_module._REVISION_CACHE[key]
    with intelligence._connect() as connection:
        cursor = local_module._revision_store_cursor(connection)
    waiting = sorted(item["news_id"] for item in before["items"] if item["analysis_status"] == "pending")
    assert len(waiting) == 4

    clock["now"] = NOW + timedelta(seconds=5)
    claimed = {}
    for index in range(4):
        row = ai.claim_due(f"owner-{index}", lease_seconds=600, max_concurrency=4)
        claimed[json.loads(row["payload_json"])["news_id"]] = (row["job_id"], f"owner-{index}")
    failed, cancelled, started, completed = (claimed[news_id] for news_id in waiting)
    ai.fail(*failed, "provider_failed")
    ai.mark_cancelled(*cancelled)
    assert ai.mark_submission_started(*completed, max_concurrency=4) == "started"
    ai.complete(*completed, _news_result(
        news_id=waiting[3], change_sequence=waiting[3], content_hash=f"hash-{waiting[3]}-{waiting[3]}",
    ), {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1, "reasoning_tokens": 0, "total_tokens": 2})
    assert ai.mark_submission_started(*started, max_concurrency=4) == "started"
    clock["now"] = NOW + timedelta(seconds=10)

    item_builds = _count(monkeypatch, intelligence, "_published_item")
    as_of = NOW + timedelta(seconds=10)
    with request_owner_access_context(True):
        after = intelligence.feed(as_of=as_of, window_hours=24, limit=50)
    with request_owner_access_context(False):
        visitor = intelligence.feed(as_of=as_of, window_hours=24, limit=50)
    with intelligence._connect() as connection:
        assert local_module._revision_store_cursor(connection) == cursor
    assert local_module._REVISION_CACHE[key] is entry
    assert item_builds[0] == 0
    statuses = {item["news_id"]: item["analysis_status"] for item in after["items"]}
    # Completed but not yet published reads as pending until reconcile runs.
    assert [statuses[news_id] for news_id in waiting] == ["failed", "cancelled", "in_progress", "pending"]
    assert after["summary"]["pending"] == before["summary"]["pending"]
    assert {item["analysis_status"] for item in visitor["items"] if item["analysis"] is None} == {"not_requested"}


def test_a_policy_change_between_owner_reads_moves_the_identity(tmp_path, clock, monkeypatch) -> None:
    """The identity memo lives for one read: the next read sees a moved policy."""

    _etl, _ai, intelligence = _store(tmp_path, clock, published=1, waiting=2, unrequested=0)

    def waiting_statuses() -> list[str]:
        with request_owner_access_context(True):
            payload = intelligence.feed(as_of=NOW, window_hours=24, limit=50)
        return [item["analysis_status"] for item in payload["items"] if item["analysis"] is None]

    assert waiting_statuses() == ["pending", "pending"]
    ceiling = ai_runtime.AI_TASK_MAX_OUTPUT_TOKENS["news_impact"]
    monkeypatch.setitem(ai_runtime.AI_TASK_MAX_OUTPUT_TOKENS, "news_impact", ceiling + 1)
    # A stored job made under another policy no longer looks current.
    assert waiting_statuses() == ["not_requested", "not_requested"]
    monkeypatch.setitem(ai_runtime.AI_TASK_MAX_OUTPUT_TOKENS, "news_impact", ceiling)
    assert waiting_statuses() == ["pending", "pending"]


@pytest.mark.parametrize("model", [LUNA, "gpt-5.6-terra", "claude-haiku-5-5", "claude-sonnet-5-5", None])
def test_the_identity_memo_answers_like_the_uncached_check(model) -> None:
    stored = {
        ai_runtime._schema_identity("news_impact", model=model, mode=mode)
        for mode in (*ai_runtime.LUNA_NEWS_MODES, None)
    } | {
        identity
        for identities in ai_runtime._IDENTITY_PREDECESSORS.values()
        for identity in identities
    } | {ai_runtime.LEGACY_NEWS_V6_SCHEMA_IDENTITY, ("news_impact_zh_cn_v6", "0" * 64)}
    memo: dict = {}
    for job_type in ("news_impact", "market_focus"):
        for prompt in ("news-impact-zh-cn-v7", "news-impact-zh-cn-v6", "market-focus-zh-cn-v7"):
            for version, digest in sorted(stored):
                assert ai_runtime.schema_identity_current(
                    job_type, prompt, version, digest, model=model, identities=memo,
                ) == ai_runtime.schema_identity_current(job_type, prompt, version, digest, model=model)
    # Each (job type, model, request variant) was built once for the pass.
    assert len(memo) <= 2 * (len(ai_runtime.LUNA_NEWS_MODES) + 1)


def test_budget_snapshot_reads_the_days_rows_once(tmp_path, clock, monkeypatch) -> None:
    _etl, ai, intelligence = _store(tmp_path, clock, published=3, waiting=2, unrequested=0)
    with sqlite3.connect(ai.path) as connection:
        # Mixed settled usage: one completed job without usage keeps its reservation.
        connection.execute(
            "UPDATE ai_jobs SET usage_total_tokens=NULL WHERE job_id=(SELECT job_id FROM ai_jobs WHERE status='completed' LIMIT 1)")
        expected = connection.execute(
            """SELECT COUNT(*),COALESCE(SUM(budget_charge_microusd),0),COALESCE(SUM(usage_total_tokens),0)
               FROM ai_jobs WHERE submission_started_at>=? AND submission_started_at<?""",
            ("2030-07-16T00:00:00Z", "2030-07-17T00:00:00Z"),
        ).fetchone()
    assert expected[0] == 3
    statements: list[str] = []
    real_connect = ai_repository_module.sqlite3.connect

    def traced_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(statements.append)
        return connection

    monkeypatch.setattr(ai_repository_module.sqlite3, "connect", traced_connect)
    snapshot = ai.budget_snapshot(daily_limit=1, daily_budget_usd=1.0, now=NOW, lane="manual", model=LUNA)
    monkeypatch.setattr(ai_repository_module.sqlite3, "connect", real_connect)
    day_scans = [
        statement for statement in statements
        if "WHERE submission_started_at>=" in " ".join(statement.split())
    ]
    assert len(day_scans) == 1
    assert snapshot["submitted_jobs"] == expected[0]
    assert snapshot["budget_used_usd"] == expected[1] / 1_000_000
    assert snapshot["usage_total_tokens"] == expected[2]
    empty = ai.budget_snapshot(daily_limit=1, daily_budget_usd=1.0, now=NOW + timedelta(days=3), model=LUNA)
    assert (empty["submitted_jobs"], empty["budget_used_usd"], empty["usage_total_tokens"]) == (0, 0.0, 0)
