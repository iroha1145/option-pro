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


def _legacy_quality(row: dict, *, as_of: datetime) -> str | None:
    """The per-read computation the stored verdicts replace."""

    article = local_module._available_article(dict(row), as_of=as_of)
    return local_module.news_quality(
        str(row.get("raw_title") or ""),
        row.get("raw_summary"),
        article.get("text") if article is not None else None,
    )


_QUALITY_CASES = [
    # (title, summary, article text or None, article status)
    ("Acme declares quarterly dividend of $0.25 per share", None, None, None),
    ("Acme declares quarterly dividend of $0.25 per share", None,
     "Acme also announced a share buyback programme.", "available"),
    ("Company announces update", "Company announces update", None, None),
    ("Company announces update", "Company announces update",
     "The update lists a new supply agreement and its delivery dates.", "available"),
    ("Company announces update", None, "", "unavailable"),
    ("NVIDIA beats revenue forecast", "Guidance raised", None, None),
    ("Acme sets record date for dividend", "Payable on Oct 30", None, None),
]


def _quality_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> tuple[LocalCatalystIntelligence, list[datetime]]:
    from test_catalyst_local_intelligence import _apply_news, _news_change

    intelligence = _stack(tmp_path)
    etl = CatalystEtlRepository(intelligence.db_path)
    changes = [
        _news_change(
            index + 1,
            index + 1,
            available_at=NOW - timedelta(hours=2),
            title=title,
            summary=summary,
        )
        for index, (title, summary, _text, _status) in enumerate(_QUALITY_CASES)
    ]
    _apply_news(etl, changes, as_of=NOW - timedelta(hours=1))
    intelligence.reconcile()
    article_times = []
    for index, (_title, _summary, text, status) in enumerate(_QUALITY_CASES):
        if status is None:
            continue
        fetched = NOW - timedelta(minutes=30 - index)
        article_times.append(fetched)
        # The article check is stamped by the store clock right after the fetch.
        monkeypatch.setattr(local_module, "_utc_now", lambda _at=fetched: _at + timedelta(seconds=20))
        intelligence._article_fetcher = lambda *_args, _text=text, _status=status, _at=fetched, **_kwargs: {
            "status": _status,
            "text": _text,
            "source_url": "https://example.test/article",
            "fetched_at": _at.isoformat(),
            "reason": None if _status == "available" else "http_403",
            "truncated": False,
        }
        row = intelligence._current_revision(index + 1)
        intelligence._prepare_article(row, force=True)
    monkeypatch.undo()
    return intelligence, article_times


def _stored_rows(intelligence: LocalCatalystIntelligence) -> list[dict]:
    with intelligence._connect() as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM catalyst_local_news_revisions ORDER BY news_id"
            ).fetchall()
        ]


def test_stored_quality_matches_per_read_computation_at_every_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    intelligence, article_times = _quality_store(tmp_path, monkeypatch)
    rows = _stored_rows(intelligence)
    assert {row["quality_rules_version"] for row in rows} == {local_module.NEWS_QUALITY_RULES_VERSION}
    moments = [NOW - timedelta(hours=1), NOW + timedelta(days=1)]
    moments += [moment - timedelta(seconds=1) for moment in article_times]
    moments += [moment + timedelta(minutes=5) for moment in article_times]
    verdicts = set()
    for row in rows:
        for as_of in moments:
            expected = _legacy_quality(row, as_of=as_of)
            verdicts.add(expected)
            assert local_module._news_quality_reason(row, as_of=as_of) == expected
    # The fixture exercises hidden and visible outcomes alike.
    assert {None, "routine_dividend", "incomplete_information"} <= verdicts
    # A dividend headline whose article reports a buyback is hidden until the
    # article becomes visible at as_of, and shown after.
    dividend_with_buyback = rows[1]
    fetched = article_times[0]
    assert local_module._news_quality_reason(
        dividend_with_buyback, as_of=fetched - timedelta(seconds=1)
    ) == "routine_dividend"
    assert local_module._news_quality_reason(
        dividend_with_buyback, as_of=fetched + timedelta(minutes=5)
    ) is None


def test_unbackfilled_and_old_rule_rows_compute_on_read_and_backfill(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    intelligence, _article_times = _quality_store(tmp_path, monkeypatch)
    with intelligence._connect() as connection:
        connection.execute(
            """UPDATE catalyst_local_news_revisions SET quality_rules_version=NULL,
                   quality_reason='stale',quality_reason_article='stale'
               WHERE news_id%2=0"""
        )
        connection.execute(
            """UPDATE catalyst_local_news_revisions SET quality_rules_version='news-quality-v0',
                   quality_reason='stale',quality_reason_article='stale'
               WHERE news_id%2=1"""
        )
        connection.commit()
    as_of = NOW + timedelta(days=1)
    for row in _stored_rows(intelligence):
        assert local_module._news_quality_reason(row, as_of=as_of) == _legacy_quality(row, as_of=as_of)
    intelligence._quality_backfill_complete = False
    assert intelligence._backfill_revision_quality() == len(_QUALITY_CASES)
    assert intelligence._backfill_revision_quality() == 0
    for row in _stored_rows(intelligence):
        assert row["quality_rules_version"] == local_module.NEWS_QUALITY_RULES_VERSION
        assert "stale" not in (row["quality_reason"], row["quality_reason_article"])
        assert local_module._news_quality_reason(row, as_of=as_of) == _legacy_quality(row, as_of=as_of)


def test_feed_reads_stored_quality_without_running_the_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    intelligence, _article_times = _quality_store(tmp_path, monkeypatch)
    calls = 0
    original = local_module.news_quality

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(local_module, "news_quality", counted)
    local_module._reset_revision_cache()
    payload = intelligence.feed(as_of=NOW, window_hours=72, limit=12)
    assert payload["items"]
    assert calls == 0


def test_quality_rules_version_is_pinned_to_the_rule_source() -> None:
    import hashlib
    import inspect

    from app.services.catalysts import news_quality as rules

    digest = hashlib.sha256(inspect.getsource(rules).encode("utf-8")).hexdigest()
    # Changing any rule changes stored verdicts: bump NEWS_QUALITY_RULES_VERSION
    # and record the new digest here in the same change.
    assert (rules.NEWS_QUALITY_RULES_VERSION, digest) == (
        "news-quality-v1",
        "53ddf2ec55d941884b4b7327df76744a8ec7554de1cadc67448196a51333cced",
    )


def _analyzed_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from test_catalyst_local_intelligence import (
        _apply_news,
        _finish_job,
        _news_change,
        _news_result,
    )

    from app.services.ai_jobs import repository as ai_repository_module

    monkeypatch.setattr(local_module, "_utc_now", lambda: NOW)
    monkeypatch.setattr(ai_repository_module, "_utcnow", lambda: NOW - timedelta(minutes=2))
    intelligence = _stack(tmp_path)
    etl = CatalystEtlRepository(intelligence.db_path)
    _apply_news(
        etl,
        [
            _news_change(index, 300 + index, available_at=NOW - timedelta(minutes=10 + index))
            for index in range(1, 13)
        ],
        as_of=NOW - timedelta(minutes=5),
    )
    intelligence.reconcile()
    for news_id in (301, 302, 303):
        job = intelligence.request_analysis(news_id, force=False)
        _finish_job(
            intelligence.ai_repository,
            job["job_id"],
            _news_result(
                news_id=news_id,
                change_sequence=news_id - 300,
                content_hash=f"hash-{news_id}-{news_id - 300}",
            ),
        )
    intelligence.reconcile()
    local_module._reset_revision_cache()
    return intelligence


def test_anonymous_hot_feed_copies_only_the_returned_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    intelligence = _analyzed_store(tmp_path, monkeypatch)
    copies = 0
    original = local_module._copy_feed_item

    def counted(item):
        nonlocal copies
        copies += 1
        return original(item)

    monkeypatch.setattr(local_module, "_copy_feed_item", counted)
    with request_owner_access_context(False):
        cold = intelligence.feed(as_of=NOW, window_hours=24, limit=4)
        assert cold["summary"]["count"] == 12
        assert len(cold["items"]) == 4
        copies = 0
        hot = intelligence.feed(as_of=NOW, window_hours=24, limit=4)
    assert copies == 4
    assert [item["news_id"] for item in hot["items"]] == [item["news_id"] for item in cold["items"]]


def test_anonymous_callers_cannot_mutate_the_cached_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    intelligence = _analyzed_store(tmp_path, monkeypatch)
    with request_owner_access_context(False):
        first = intelligence.feed(as_of=NOW, window_hours=24, limit=20)
        pristine = [dict(item) for item in first["items"]]
        for item in first["items"]:
            item["title"] = "tampered"
            item["source_tickers"].append("HACK")
            if isinstance(item.get("analysis"), dict):
                item["analysis"]["classification"] = "tampered"
                item["analysis"]["affected_stocks"].append({"ticker": "HACK"})
        second = intelligence.feed(as_of=NOW, window_hours=24, limit=20)
        batch = intelligence.batch(["NVDA", "AMD"], as_of=NOW, window_hours=24, limit=20)
    analyzed = [item for item in second["items"] if isinstance(item.get("analysis"), dict)]
    assert len(analyzed) == 3
    for fresh, before in zip(second["items"], pristine):
        assert fresh["title"] == before["title"] != "tampered"
        assert "HACK" not in fresh["source_tickers"]
    for item in analyzed:
        assert item["analysis"]["classification"] != "tampered"
        assert all(stock.get("ticker") != "HACK" for stock in item["analysis"]["affected_stocks"])
    nvda_items = batch["results"]["NVDA"]["items"]
    assert nvda_items and all(item["title"] != "tampered" for item in nvda_items)


_JOURNAL_ACTIVE_REVISIONS_SQL = """
    SELECT r.* FROM catalyst_local_news_revisions r
    WHERE COALESCE(r.published_at,r.fetched_at)>=?
      AND r.change_sequence=(
          SELECT MAX(c.change_sequence)
          FROM macrolens_etl_news_changes c
          WHERE c.news_id=r.news_id
            AND c.available_at<=?
      )
      AND EXISTS(
          SELECT 1 FROM macrolens_etl_news_changes c2
          WHERE c2.news_id=r.news_id
            AND c2.change_sequence=r.change_sequence
            AND c2.operation='upsert'
            AND c2.available_at<=?
      )
"""


def _visible_revisions(connection, body: str, params: tuple) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            f"WITH active_revisions AS ({body}) "
            "SELECT news_id,change_sequence,content_hash FROM active_revisions "
            "ORDER BY news_id,change_sequence",
            params,
        ).fetchall()
    ]


def test_mirror_window_predicate_matches_the_journal_at_every_as_of(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_catalyst_local_intelligence import (
        _apply_news,
        _delete_change,
        _news_change,
    )

    monkeypatch.setattr(local_module, "_utc_now", lambda: NOW)
    intelligence = _stack(tmp_path)
    etl = CatalystEtlRepository(intelligence.db_path)
    base = NOW - timedelta(hours=6)
    changes = [
        _news_change(1, 401, available_at=base),
        _news_change(2, 402, available_at=base),
        # 402 is updated twice; the newest version is visible later.
        _news_change(3, 402, available_at=base + timedelta(hours=1), content_hash="hash-402-3"),
        _news_change(4, 402, available_at=base + timedelta(hours=3), content_hash="hash-402-4"),
        _news_change(5, 403, available_at=base),
        _delete_change(6, 403, available_at=base + timedelta(hours=2)),
        # A future-dated change: its mirror row is newer than near-now reads.
        _news_change(7, 404, available_at=base),
        _news_change(8, 404, available_at=NOW + timedelta(hours=2), content_hash="hash-404-8"),
        _news_change(9, 405, available_at=base + timedelta(hours=4)),
    ]
    _apply_news(etl, changes, as_of=NOW + timedelta(hours=3))
    intelligence.reconcile()
    with intelligence._connect() as connection:
        # A revision whose mirror row is missing still follows the journal.
        connection.execute("DELETE FROM macrolens_etl_news WHERE news_id=405")
        connection.commit()
    moments = [
        base - timedelta(minutes=1),
        base,
        base + timedelta(minutes=90),
        base + timedelta(hours=2, minutes=30),
        NOW,
        NOW + timedelta(hours=3),
    ]
    window_start = local_module._iso(NOW - timedelta(days=3))
    observed = set()
    with intelligence._connect() as connection:
        for moment in moments:
            cutoff = local_module._iso(moment)
            journal = _visible_revisions(
                connection, _JOURNAL_ACTIVE_REVISIONS_SQL, (window_start, cutoff, cutoff),
            )
            mirror = _visible_revisions(
                connection,
                local_module._WINDOW_ACTIVE_REVISIONS_SQL,
                (window_start, cutoff, cutoff, cutoff),
            )
            assert mirror == journal, moment
            observed.update(journal)
    # Every branch was exercised: superseded, deleted, future-dated, unmirrored.
    assert (402, 3, "hash-402-3") in observed and (402, 4, "hash-402-4") in observed
    assert (403, 5, "hash-403-5") in observed
    assert (404, 7, "hash-404-7") in observed and (404, 8, "hash-404-8") in observed
    assert (405, 9, "hash-405-9") in observed


def _ingest_steps(intelligence: LocalCatalystIntelligence) -> tuple[int, int]:
    steps = 0

    def tick() -> int:
        nonlocal steps
        steps += 1
        return 0

    with intelligence._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.set_progress_handler(tick, 1)
        inserted, _watermark = intelligence._ingest_revisions(connection)
        connection.set_progress_handler(None, 0)
        connection.rollback()
    return inserted, steps


def _journal_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int):
    from test_catalyst_local_intelligence import _apply_news, _news_change

    monkeypatch.setattr(local_module, "_utc_now", lambda: NOW)
    intelligence = _stack(tmp_path)
    etl = CatalystEtlRepository(intelligence.db_path)
    for start in range(1, count + 1, 250):
        _apply_news(
            etl,
            [
                _news_change(sequence, 1000 + sequence, available_at=NOW - timedelta(minutes=30))
                for sequence in range(start, min(count, start + 249) + 1)
            ],
            as_of=NOW - timedelta(minutes=20),
        )
    intelligence.reconcile()
    return intelligence, etl


def test_ingest_after_the_first_pass_reads_only_new_journal_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    small, _ = _journal_store(tmp_path / "small", monkeypatch, 40)
    large, _ = _journal_store(tmp_path / "large", monkeypatch, 800)
    small_inserted, small_steps = _ingest_steps(small)
    large_inserted, large_steps = _ingest_steps(large)
    assert small_inserted == large_inserted == 0
    # Twenty times the history, same work: the anti-join starts above the
    # watermark instead of walking the whole journal inside the write lock.
    assert large_steps == small_steps


def test_ingest_picks_up_new_changes_and_rescans_after_a_cursor_reset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_catalyst_local_intelligence import _apply_news, _news_change

    intelligence, etl = _journal_store(tmp_path, monkeypatch, 5)
    _apply_news(
        etl,
        [_news_change(6, 1006, available_at=NOW - timedelta(minutes=10))],
        as_of=NOW - timedelta(minutes=9),
    )
    intelligence.reconcile()
    with intelligence._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions"
        ).fetchone()[0] == 6
        # A revision missing below the watermark (the replay case after a
        # cursor reset) is not seen until the reset changes the stream state.
        connection.execute("DELETE FROM catalyst_local_news_revisions WHERE news_id=1002")
        connection.commit()
    intelligence.reconcile()
    with intelligence._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions WHERE news_id=1002"
        ).fetchone()[0] == 0
    etl.reset_cursor("news", error_code="test_reset")
    intelligence.reconcile()
    with intelligence._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions WHERE news_id=1002"
        ).fetchone()[0] == 1


def test_failed_reconcile_does_not_advance_the_ingest_watermark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_catalyst_local_intelligence import _apply_news, _news_change

    intelligence, etl = _journal_store(tmp_path, monkeypatch, 3)
    before = intelligence._ingest_watermark
    _apply_news(
        etl,
        [_news_change(4, 1004, available_at=NOW - timedelta(minutes=10))],
        as_of=NOW - timedelta(minutes=9),
    )

    def broken(*_args, **_kwargs):
        raise RuntimeError("publish failed")

    monkeypatch.setattr(intelligence, "_publish_completed_news", broken)
    with pytest.raises(RuntimeError, match="publish failed"):
        intelligence.reconcile()
    assert intelligence._ingest_watermark == before
    monkeypatch.undo()
    monkeypatch.setattr(local_module, "_utc_now", lambda: NOW)
    intelligence.reconcile()
    with intelligence._connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions WHERE news_id=1004"
        ).fetchone()[0] == 1


def test_reconcile_reuses_settled_projections_and_audits_across_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai_jobs import runtime as ai_runtime

    intelligence = _analyzed_store(tmp_path, monkeypatch)
    intelligence.reconcile()
    counts = {"public": 0, "validate": 0, "identity": 0}
    original_public = AIJobRepository.public
    original_validate = local_module.validate_result
    original_identity = ai_runtime.schema_identity

    def counted_public(row, *, cached=False):
        counts["public"] += 1
        return original_public(row, cached=cached)

    def counted_validate(*args, **kwargs):
        counts["validate"] += 1
        return original_validate(*args, **kwargs)

    def counted_identity(*args, **kwargs):
        counts["identity"] += 1
        return original_identity(*args, **kwargs)

    monkeypatch.setattr(AIJobRepository, "public", staticmethod(counted_public))
    monkeypatch.setattr(local_module, "validate_result", counted_validate)
    monkeypatch.setattr(ai_runtime, "schema_identity", counted_identity)
    for _ in range(3):
        intelligence.reconcile()
    # Nothing changed: no paid result is projected or validated again, and the
    # schema identity is resolved per model, not per job.
    assert counts["public"] == 0
    assert counts["validate"] == 0
    assert counts["identity"] <= 3 * 3


def test_rejected_results_are_revalidated_once_per_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_catalyst_local_intelligence import _finish_job, _news_result

    intelligence = _analyzed_store(tmp_path, monkeypatch)
    job = intelligence.request_analysis(304, force=False)
    rejected = _news_result(news_id=304, change_sequence=4, content_hash="hash-304-4")
    rejected["title_zh"] = "English only generated title"
    _finish_job(intelligence.ai_repository, job["job_id"], rejected)
    intelligence.reconcile()
    calls = 0
    original = local_module.validate_result

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(local_module, "validate_result", counted)
    intelligence.reconcile()
    first_round = calls
    intelligence.reconcile()
    intelligence.reconcile()
    assert calls == first_round
    with intelligence._connect() as connection:
        outcome = connection.execute(
            """SELECT outcome FROM catalyst_local_analysis_result_audit
               WHERE job_id=?""",
            (job["job_id"],),
        ).fetchall()
    assert [row[0] for row in outcome] == ["rejected"]
