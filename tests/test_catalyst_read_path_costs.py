"""Catalyst read-path costs: what each request pays must not grow with history.

Assertions count SQLite virtual-machine steps, calls or rows, never wall time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence

NOW = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _owner_request_context():
    with request_owner_access_context(True):
        yield


def _stack(tmp_path: Path) -> LocalCatalystIntelligence:
    cache_path = tmp_path / "catalyst-cache.db"
    local_module._reset_revision_cache()
    CatalystEtlRepository(cache_path).initialize()
    ai = AIJobRepository(tmp_path / "ai-jobs.db")
    ai.initialize()
    intelligence = LocalCatalystIntelligence(
        cache_path, ai, mode="manual", canonical_tickers=("NVDA",),
    )
    intelligence.initialize()
    return intelligence


def _cursor_steps(path: Path) -> tuple[tuple, int]:
    steps = 0

    def tick() -> int:
        nonlocal steps
        steps += 1
        return 0

    connection = sqlite3.connect(path)
    connection.set_progress_handler(tick, 1)
    try:
        cursor = local_module._revision_store_cursor(connection)
    finally:
        connection.close()
    return cursor, steps


def _add_history(path: Path, start: int, count: int) -> None:
    connection = sqlite3.connect(path)
    body = '{"summary_zh":"' + "结论" * 400 + '"}'
    with connection:
        for index in range(start, start + count):
            job_id = f"job-{index:06d}"
            connection.execute(
                """INSERT INTO catalyst_local_analysis_links(
                       news_id,change_sequence,content_hash,job_id,result_json,
                       result_available_at,verified_at,created_at
                   ) VALUES(?,?,?,?,?,?,?,?)""",
                (index, 1, f"hash-{index}", job_id, body, "2026-10-01T00:00:00Z",
                 "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
            )
            connection.execute(
                """INSERT INTO catalyst_local_analysis_result_audit(
                       job_id,contract_id,result_sha256,outcome,reason,result_json,
                       result_available_at,verified_at,observed_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (job_id, "contract", f"{index:064d}", "accepted", None, body,
                 "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
            )
    connection.close()


def test_store_cursor_cost_does_not_grow_with_links_and_audits(tmp_path: Path) -> None:
    intelligence = _stack(tmp_path)
    path = intelligence.db_path
    _add_history(path, 1, 50)
    _, small = _cursor_steps(path)
    _add_history(path, 51, 2_000)
    _, large = _cursor_steps(path)
    # The old fingerprint summed result lengths over both tables: forty times
    # the rows meant forty times the steps.
    assert large == small


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE catalyst_local_analysis_links SET result_json='{\"tampered\":1}' WHERE job_id='job-000001'",
        "UPDATE catalyst_local_analysis_links SET verified_at='2026-10-02T00:00:00Z' WHERE job_id='job-000001'",
        "UPDATE catalyst_local_analysis_result_audit SET outcome='rejected' WHERE job_id='job-000001'",
        "DELETE FROM catalyst_local_analysis_result_audit WHERE job_id='job-000002'",
        "DELETE FROM catalyst_local_analysis_links WHERE job_id='job-000002'",
    ],
)
def test_in_place_edits_change_the_store_cursor_immediately(
    tmp_path: Path, statement: str,
) -> None:
    intelligence = _stack(tmp_path)
    _add_history(intelligence.db_path, 1, 3)
    before, _ = _cursor_steps(intelligence.db_path)
    connection = sqlite3.connect(intelligence.db_path)
    with connection:
        assert connection.execute(statement).rowcount == 1
    connection.close()
    after, _ = _cursor_steps(intelligence.db_path)
    assert after != before


def test_revision_article_check_changes_the_store_cursor(tmp_path: Path) -> None:
    intelligence = _stack(tmp_path)
    connection = sqlite3.connect(intelligence.db_path)
    with connection:
        connection.execute(
            """INSERT INTO catalyst_local_news_revisions(
                   news_id,change_sequence,content_hash,source,raw_title,raw_summary,url,
                   image_url,published_at,fetched_at,source_available_at,
                   source_tickers_json,canonical_tickers_json,source_names_json,
                   source_count,ingested_at
               ) VALUES(1,1,'hash-1','Reuters','title','summary','https://example.test/1',
                        NULL,'2026-10-09T13:00:00Z','2026-10-09T13:00:00.000000Z',
                        '2026-10-09T13:00:00.000000Z','["NVDA"]','["NVDA"]','["Reuters"]',1,
                        '2026-10-09T13:00:00Z')"""
        )
    before, _ = _cursor_steps(intelligence.db_path)
    with connection:
        connection.execute(
            "UPDATE catalyst_local_news_revisions SET article_checked_at=? WHERE news_id=1",
            ("2026-10-09T13:30:00Z",),
        )
    connection.close()
    after, _ = _cursor_steps(intelligence.db_path)
    assert after != before


def test_unmigrated_store_never_shares_a_cached_view(tmp_path: Path) -> None:
    intelligence = _stack(tmp_path)
    connection = sqlite3.connect(intelligence.db_path)
    with connection:
        connection.execute("DROP TABLE catalyst_local_store_version")
    connection.close()
    first, _ = _cursor_steps(intelligence.db_path)
    second, _ = _cursor_steps(intelligence.db_path)
    assert first != second
