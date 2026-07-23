from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone

from app.services.ai_jobs import runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts.local_intelligence import (
    NEWS_RESULT_CONTRACT_ID,
    LocalCatalystIntelligence,
)
from app.services.catalysts.personal_service import PersonalCatalystService


NOW = datetime(2026, 7, 23, 14, 5, tzinfo=timezone.utc)


def _create_news_job(
    repository: AIJobRepository,
    news_id: int,
    *,
    submission_source: str = "scheduled",
    batch_id: str | None = None,
    batch_position: int | None = None,
) -> str:
    schema_version, schema_sha256 = runtime.schema_identity("news_impact")
    row, created = repository.create_job(
        job_type="news_impact",
        payload={
            "news_id": news_id,
            "change_sequence": 1,
            "content_hash": f"{news_id:064x}",
            "allowed_tickers": [],
            "analysis_revision": 1,
        },
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="news-impact-zh-cn-v6",
        schema_version=schema_version,
        schema_sha256=schema_sha256,
        max_queued=200,
        submission_source=submission_source,
        priority=70,
        batch_id=batch_id,
        batch_position=batch_position,
    )
    assert created is True
    return str(row["job_id"])


def _set_job(
    repository: AIJobRepository,
    job_id: str,
    *,
    status: str,
    created_at: str,
    updated_at: str | None = None,
    result_json: str | None = None,
    submission_started_at: str | None = None,
    openai_response_id: str | None = None,
    error_code: str | None = None,
) -> None:
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status=?,created_at=?,updated_at=?,completed_at=?,
                result_json=?,submission_started_at=?,openai_response_id=?,
                error_code=?
            WHERE job_id=?
            """,
            (
                status,
                created_at,
                updated_at or created_at,
                (updated_at or created_at)
                if status
                in {
                    "completed",
                    "failed",
                    "cancelled",
                    "insufficient_context",
                    "budget_blocked",
                }
                else None,
                result_json,
                submission_started_at,
                openai_response_id,
                error_code,
                job_id,
            ),
        )
        connection.commit()


def _news_result(news_id: int) -> str:
    return json.dumps(
        {
            "output_language": "zh-CN",
            "news_id": news_id,
            "change_sequence": 1,
            "content_hash": f"{news_id:064x}",
            "title_zh": "公司发布最新业务进展",
            "summary_zh": "公司披露最新业务进展，市场仍需观察后续执行情况。",
            "headline_summary": "最新进展可能影响市场预期，实际影响仍需持续观察。",
            "overall_sentiment": 10,
            "classification": "neutral",
            "confidence": 60,
            "market_relevance": 70,
            "affected_stocks": [],
            "affected_sectors": [],
            "affected_commodities": [],
            "causal_summary": "信息可能改变市场预期，但目前缺少进一步验证材料。",
            "key_factors": ["业务进展", "市场预期"],
            "uncertainty_notes": ["后续执行结果仍待观察"],
            "insufficient_context": False,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _local_intelligence(tmp_path, repository: AIJobRepository):
    intelligence = LocalCatalystIntelligence(
        tmp_path / "catalyst-cache.db",
        repository,
        mode="scheduled",
        canonical_tickers=(),
    )
    intelligence.initialize()
    return intelligence


def _service(
    repository: AIJobRepository,
    intelligence: LocalCatalystIntelligence,
) -> PersonalCatalystService:
    service = object.__new__(PersonalCatalystService)
    service.ai_repository = repository
    service.intelligence = intelligence
    return service


def _insert_audit(
    intelligence: LocalCatalystIntelligence,
    *,
    job_id: str,
    result_json: str,
    outcome: str,
) -> None:
    with sqlite3.connect(intelligence.db_path) as connection:
        connection.execute(
            """INSERT INTO catalyst_local_analysis_result_audit(
                   job_id,contract_id,result_sha256,outcome,reason,result_json,
                   result_available_at,verified_at,observed_at
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                job_id,
                NEWS_RESULT_CONTRACT_ID,
                hashlib.sha256(result_json.encode("utf-8")).hexdigest(),
                outcome,
                None if outcome == "accepted" else "test_rejection",
                result_json,
                "2026-07-23T14:03:00Z",
                "2026-07-23T14:03:00Z",
                "2026-07-23T14:03:00Z",
            ),
        )
        connection.commit()


def test_news_progress_uses_exact_batch_across_gaps_and_excludes_adjacent_batch(
    tmp_path,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    batch_id = "aib_exact_batch"
    first = _create_news_job(
        repository,
        1,
        batch_id=batch_id,
        batch_position=1,
    )
    adjacent = _create_news_job(
        repository,
        2,
        batch_id="aib_adjacent_batch",
        batch_position=1,
    )
    last = _create_news_job(
        repository,
        3,
        batch_id=batch_id,
        batch_position=2,
    )
    _set_job(
        repository,
        first,
        status="in_progress",
        created_at="2026-07-23T10:00:00Z",
    )
    _set_job(
        repository,
        adjacent,
        status="pending",
        created_at="2026-07-23T10:00:01Z",
    )
    _set_job(
        repository,
        last,
        status="pending",
        created_at="2026-07-23T13:00:00Z",
    )

    progress = repository.news_analysis_progress(now=NOW)

    assert progress["batch_id"] == batch_id
    assert progress["total"] == 2
    assert [row["job_id"] for row in progress["_batch_jobs"]] == [first, last]
    assert progress["current_index"] == 1
    assert progress["current_news_id"] == 1


def test_news_progress_treats_provider_queued_as_processing(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    batch_id = "aib_provider_queue"
    provider = _create_news_job(
        repository,
        11,
        batch_id=batch_id,
        batch_position=1,
    )
    local = _create_news_job(
        repository,
        12,
        batch_id=batch_id,
        batch_position=2,
    )
    _set_job(
        repository,
        provider,
        status="queued",
        created_at="2026-07-23T14:00:00Z",
        submission_started_at="2026-07-23T14:00:01Z",
    )
    _set_job(
        repository,
        local,
        status="queued",
        created_at="2026-07-23T14:00:02Z",
    )

    progress = repository.news_analysis_progress(now=NOW)

    assert progress["waiting"] == 1
    assert progress["in_progress"] == 1
    assert progress["current_index"] == 1
    assert progress["current_news_id"] == 11
    assert progress["current_phase"] == "provider_queued"
    assert progress["queue_waiting"] == 1
    assert progress["queue_in_progress"] == 1


def test_news_progress_hides_current_item_for_multiple_provider_jobs(
    tmp_path,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    batch_id = "aib_concurrent"
    jobs = [
        _create_news_job(
            repository,
            news_id,
            batch_id=batch_id,
            batch_position=position,
        )
        for position, news_id in enumerate((21, 22), start=1)
    ]
    for position, job_id in enumerate(jobs):
        _set_job(
            repository,
            job_id,
            status="in_progress",
            created_at=f"2026-07-23T14:00:0{position}Z",
            submission_started_at=f"2026-07-23T14:00:1{position}Z",
        )

    progress = repository.news_analysis_progress(now=NOW)

    assert progress["in_progress"] == 2
    assert progress["current_index"] is None
    assert progress["current_news_id"] is None
    assert progress["current_phase"] is None


def test_historical_jobs_without_batch_metadata_are_individual(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_news_job(repository, 31)
    second = _create_news_job(repository, 32)
    with repository._connect() as connection:
        connection.execute(
            "DELETE FROM ai_job_batch_members WHERE job_id IN (?,?)",
            (first, second),
        )
        connection.commit()
    _set_job(
        repository,
        first,
        status="pending",
        created_at="2026-07-23T14:00:00Z",
    )
    _set_job(
        repository,
        second,
        status="pending",
        created_at="2026-07-23T14:00:01Z",
    )

    progress = repository.news_analysis_progress(now=NOW)

    assert progress["batch_id"] is None
    assert progress["total"] == 1
    assert progress["_batch_jobs"][0]["job_id"] == first


def test_deduplicated_job_is_not_reassigned_to_a_new_batch(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first = _create_news_job(
        repository,
        41,
        batch_id="aib_original",
        batch_position=1,
    )
    schema_version, schema_sha256 = runtime.schema_identity("news_impact")
    existing, created = repository.create_job(
        job_type="news_impact",
        payload={
            "news_id": 41,
            "change_sequence": 1,
            "content_hash": f"{41:064x}",
            "allowed_tickers": [],
            "analysis_revision": 1,
        },
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="news-impact-zh-cn-v6",
        schema_version=schema_version,
        schema_sha256=schema_sha256,
        max_queued=200,
        submission_source="scheduled",
        priority=70,
        batch_id="aib_must_not_replace",
        batch_position=1,
    )

    assert created is False
    assert existing["job_id"] == first
    with repository._connect() as connection:
        member = connection.execute(
            """SELECT batch_id,position FROM ai_job_batch_members
               WHERE job_id=?""",
            (first,),
        ).fetchone()
        payload = json.loads(
            connection.execute(
                "SELECT payload_json FROM ai_jobs WHERE job_id=?",
                (first,),
            ).fetchone()[0]
        )
    assert tuple(member) == ("aib_original", 1)
    assert "batch_id" not in payload


def test_scheduled_round_passes_one_batch_with_stable_positions(
    tmp_path,
    monkeypatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    intelligence = LocalCatalystIntelligence(
        tmp_path / "catalyst-cache.db",
        repository,
        mode="scheduled",
        canonical_tickers=(),
    )
    candidates = [
        {
            "news_id": news_id,
            "change_sequence": 1,
            "content_hash": f"{news_id:064x}",
        }
        for news_id in (71, 72)
    ]
    calls: list[tuple[int, str, int]] = []
    monkeypatch.setattr(
        intelligence,
        "hotspots",
        lambda **_kwargs: {"items": []},
    )
    monkeypatch.setattr(
        intelligence,
        "_scheduled_news_candidates",
        lambda **_kwargs: candidates,
    )
    monkeypatch.setattr(
        intelligence,
        "_ai_job_snapshot",
        lambda **_kwargs: {},
    )
    monkeypatch.setattr(
        intelligence,
        "_scheduled_job_for_revision",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        repository,
        "health",
        lambda: {"healthy": True, "pending": 0},
    )

    def request_analysis(news_id: int, **kwargs):
        calls.append(
            (
                news_id,
                str(kwargs["_batch_id"]),
                int(kwargs["_batch_position"]),
            )
        )
        return {"status": "pending"}

    monkeypatch.setattr(intelligence, "request_analysis", request_analysis)

    assert intelligence.run_scheduled(now=NOW) == {"queued": 2, "skipped": 0}
    assert [call[0] for call in calls] == [71, 72]
    assert len({call[1] for call in calls}) == 1
    assert [call[2] for call in calls] == [1, 2]


def test_completed_results_split_accepted_awaiting_and_rejected(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    batch_id = "aib_validation"
    jobs = [
        _create_news_job(
            repository,
            news_id,
            batch_id=batch_id,
            batch_position=position,
        )
        for position, news_id in enumerate((51, 52, 53, 54), start=1)
    ]
    results = [_news_result(news_id) for news_id in (51, 52, 53)]
    for index, job_id in enumerate(jobs):
        _set_job(
            repository,
            job_id,
            status="completed",
            created_at=f"2026-07-23T14:00:0{index}Z",
            updated_at=f"2026-07-23T14:03:0{index}Z",
            result_json=results[index] if index < 3 else "{}",
        )
    intelligence = _local_intelligence(tmp_path, repository)
    _insert_audit(
        intelligence,
        job_id=jobs[0],
        result_json=results[0],
        outcome="accepted",
    )
    _insert_audit(
        intelligence,
        job_id=jobs[2],
        result_json=results[2],
        outcome="rejected",
    )

    repository_progress = repository.news_analysis_progress(now=NOW)
    progress = _service(repository, intelligence).analysis_progress(now=NOW)

    assert repository_progress["awaiting_validation"] == 4
    assert progress["total"] == 4
    assert progress["finished"] == 4
    assert progress["succeeded"] == 1
    assert progress["awaiting_validation"] == 1
    assert progress["rejected"] == 2
    assert progress["failed"] == 0
    assert progress["progress_percent"] == 100
    assert progress["status"] == "active"
    assert "_batch_jobs" not in progress


def test_progress_reads_do_not_initialize_reconcile_or_create_jobs(
    tmp_path,
    monkeypatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job_id = _create_news_job(repository, 61)
    result = _news_result(61)
    _set_job(
        repository,
        job_id,
        status="completed",
        created_at="2026-07-23T14:00:00Z",
        updated_at="2026-07-23T14:03:00Z",
        result_json=result,
    )
    intelligence = _local_intelligence(tmp_path, repository)
    _insert_audit(
        intelligence,
        job_id=job_id,
        result_json=result,
        outcome="accepted",
    )
    service = _service(repository, intelligence)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("progress reads must stay read-only")

    monkeypatch.setattr(repository, "initialize", forbidden)
    monkeypatch.setattr(intelligence, "initialize", forbidden)
    monkeypatch.setattr(intelligence, "reconcile", forbidden)
    with repository._connect() as connection:
        before_jobs = connection.execute(
            "SELECT COUNT(*) FROM ai_jobs"
        ).fetchone()[0]
        before_members = connection.execute(
            "SELECT COUNT(*) FROM ai_job_batch_members"
        ).fetchone()[0]
    with sqlite3.connect(intelligence.db_path) as connection:
        before_audits = connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_analysis_result_audit"
        ).fetchone()[0]

    first = service.analysis_progress(now=NOW)
    second = service.analysis_progress(now=NOW)

    assert first == second
    assert first["succeeded"] == 1
    with repository._connect() as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]
            == before_jobs
        )
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM ai_job_batch_members"
            ).fetchone()[0]
            == before_members
        )
    with sqlite3.connect(intelligence.db_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM catalyst_local_analysis_result_audit"
            ).fetchone()[0]
            == before_audits
        )


def test_news_progress_is_idle_without_an_initialized_store(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "missing-ai-jobs.db")

    progress = repository.news_analysis_progress(now=NOW)

    assert progress["status"] == "idle"
    assert progress["total"] == 0
    assert progress["progress_percent"] == 0
    assert not repository.path.exists()
