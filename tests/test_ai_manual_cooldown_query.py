from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository


NOW = datetime(2026, 10, 12, 8, 20, tzinfo=timezone.utc)
# Kept independently from the implementation to compare with main 43563c2f.
OLD_SQL = """
SELECT MAX(COALESCE(j.completed_at,j.submission_started_at))
FROM ai_job_sources AS s CROSS JOIN ai_jobs AS j ON j.job_id=s.job_id
WHERE s.submission_source='manual' AND j.submission_started_at IS NOT NULL
AND j.status IN ('completed','failed','cancelled','insufficient_context','budget_blocked')
AND (?1 IS NULL OR j.job_id<>?1)
"""


def database():
    conn = sqlite3.connect(":memory:")
    conn.executescript("""
        CREATE TABLE ai_jobs(job_id TEXT PRIMARY KEY,status TEXT,
            submission_started_at TEXT,completed_at TEXT,payload_json TEXT,
            next_attempt_at TEXT,priority INTEGER DEFAULT 50,created_at TEXT,
            job_type TEXT DEFAULT 'earnings_impact',error_code TEXT,
            updated_at TEXT,retry_of_job_id TEXT);
        CREATE TABLE ai_job_sources(job_id TEXT PRIMARY KEY,submission_source TEXT);
    """)
    conn.executescript(storage._MANUAL_COOLDOWN_INDEX_SQL)
    # Include every existing competing ai_jobs index, especially the status
    # index that SQLite 3.40 preferred over the unforced timestamp index.
    for sql in (storage._AI_JOBS_INDEX_SQL, storage._PAYLOAD_LOOKUP_INDEX_SQL,
                storage._BUDGET_READ_INDEX_SQL, storage._RETRY_LINEAGE_INDEX_SQL):
        conn.executescript(sql)
    return conn


def insert(conn, job_id, status="completed", submitted="2026-10-12T08:19:50Z",
           completed=None, lane="manual"):
    conn.execute("INSERT INTO ai_jobs(job_id,status,submission_started_at,completed_at,payload_json) VALUES(?,?,?,?,?)",
                 (job_id, status, submitted, completed, '{"body":"' + "x" * 4096 + '"}'))
    if lane is not None:
        conn.execute("INSERT INTO ai_job_sources VALUES(?,?)", (job_id, lane))


def old_result(conn, excluded, seconds=30):
    if seconds <= 0:
        return None
    value = storage._parse_time(conn.execute(OLD_SQL, (excluded,)).fetchone()[0])
    if value is None:
        return None
    until = value + timedelta(seconds=seconds)
    return until if until > NOW else None


def assert_same(conn, excluded=None, seconds=30):
    kwargs = dict(now_dt=NOW, cooldown_seconds=seconds, exclude_job_id=excluded)
    try:
        expected = old_result(conn, excluded, seconds)
    except (ValueError, TypeError) as exc:
        # Invalid and timezone-naive legacy strings keep the old error behavior.
        with pytest.raises(type(exc)):
            AIJobRepository._manual_cooldown_until(conn, **kwargs)
    else:
        assert AIJobRepository._manual_cooldown_until(conn, **kwargs) == expected


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled",
    "insufficient_context", "budget_blocked", "pending", "queued", "in_progress"])
@pytest.mark.parametrize("lane", ["manual", "scheduled", None])
def test_status_source_submission_and_exclusion_semantics(status, lane):
    with database() as conn:
        insert(conn, "older", completed="2026-10-12T08:19:35Z")
        insert(conn, "latest", status=status, completed="2026-10-12T08:20:15Z", lane=lane)
        # A local completion without a paid submission must never trigger cooldown.
        insert(conn, "unpaid", submitted=None, completed="2099-01-01T00:00:00Z")
        for excluded in (None, "latest", "older", "nonexistent"):
            for seconds in (-1, 0, 30):
                assert_same(conn, excluded, seconds)


@pytest.mark.parametrize("value", [None, "", "2026-10-12T08:19:30Z",
    "2026-10-12T08:19:30.000001Z", "2026-10-12T08:20:00+00:00",
    "2026-10-12T10:20:00+02:00", "2099-01-01T00:00:00Z",
    "2026-10-12T08:20:00", "not-a-date"])
def test_legacy_timestamp_formats_keep_lexical_max_and_parse_behavior(value):
    with database() as conn:
        insert(conn, "a", completed=value)
        insert(conn, "b", completed="2026-10-12T08:19:29.9Z")
        assert_same(conn)
        assert_same(conn, "a")
        assert_same(conn, "b")


@pytest.mark.parametrize("scheduled_count", [63, 64, 65, 1000])
def test_fallback_beyond_recent_candidates_and_boundary_exclusion(scheduled_count):
    with database() as conn:
        insert(conn, "manual", completed="2026-10-12T08:19:59Z")
        insert(conn, "tie", completed="2026-10-12T08:19:59Z")
        for i in range(scheduled_count):
            insert(conn, f"scheduled-{i}", completed="2099-01-01T00:00:00Z", lane="scheduled")
        assert_same(conn)
        assert_same(conn, "manual")
        assert_same(conn, "tie")
        assert_same(conn, "scheduled-0")


def measured(conn, operation):
    steps = 0

    def tick():
        nonlocal steps
        steps += 1
        return 0

    conn.set_progress_handler(tick, 1)
    try:
        result = operation()
    finally:
        conn.set_progress_handler(None, 0)
    return result, steps


@pytest.mark.parametrize("distribution", ["manual", "mixed", "scheduled", "old_manual", "old_many_manual"])
def test_large_history_query_plan_and_bounded_work(distribution):
    with database() as conn:
        jobs, sources = [], []
        for i in range(20_000):
            stamp = storage._iso(NOW - timedelta(seconds=20_000 - i))
            jobs.append((str(i), "completed", stamp, stamp, '{"body":"' + "x" * 4096 + '"}'))
            lane = "manual" if distribution == "manual" or (distribution == "mixed" and i % 2) or (distribution == "old_manual" and i == 0) or (distribution == "old_many_manual" and i < 19_800) else "scheduled"
            sources.append((str(i), lane))
        conn.executemany("INSERT INTO ai_jobs(job_id,status,submission_started_at,completed_at,payload_json) VALUES(?,?,?,?,?)", jobs)
        conn.executemany("INSERT INTO ai_job_sources VALUES(?,?)", sources)
        conn.execute("ANALYZE")
        plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + storage._MANUAL_COOLDOWN_SQL, (None,))]
        assert any("idx_ai_jobs_manual_cooldown" in detail for detail in plan), plan
        assert not any("idx_ai_jobs_due" in detail for detail in plan), plan
        assert not any("TEMP B-TREE" in row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + storage._MANUAL_COOLDOWN_SQL, (None,))
                       if row[1] == plan_parent(conn)), plan
        assert not any(detail == "SCAN j" for detail in plan), plan
        assert any("COVERING INDEX idx_ai_job_sources_lane_job" in detail for detail in plan), plan
        # Compare with the old query before adding the fallback cover index.
        conn.execute("DROP INDEX idx_ai_jobs_manual_paid_cover")
        before, old_steps = measured(conn, lambda: old_result(conn, None))
        cover_statement = next(statement for statement in storage._statements(storage._MANUAL_COOLDOWN_INDEX_SQL)
                               if "idx_ai_jobs_manual_paid_cover" in statement)
        conn.execute(cover_statement)
        fallback_plan = [row[3] for row in conn.execute("EXPLAIN QUERY PLAN " + storage._MANUAL_COOLDOWN_FALLBACK_SQL, (None,))]
        assert any("idx_ai_jobs_manual_paid_cover" in detail for detail in fallback_plan), fallback_plan
        # SQLite calls this USING INDEX because status is proved by its partial
        # predicate. Inspect bytecode as well: there must be no table Column
        # opcode that would load ai_jobs payload pages.
        root = conn.execute("SELECT rootpage FROM sqlite_master WHERE name='ai_jobs'").fetchone()[0]
        bytecode = conn.execute("EXPLAIN " + storage._MANUAL_COOLDOWN_FALLBACK_SQL, (None,)).fetchall()
        table_cursors = {row[2] for row in bytecode if row[1] == "OpenRead" and row[3] == root}
        assert not any(row[1] == "Column" and row[2] in table_cursors for row in bytecode)
        after, new_steps = measured(conn, lambda: AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30))
        assert after == before
        # Recent manual histories take the fast path. Old manual histories
        # use index lookups without loading payload pages. Sparse/absent manual
        # histories cost a fixed 64 slice plus their small manual set.
        if distribution == "old_many_manual":
            assert new_steps < old_steps, (distribution, old_steps, new_steps)
        else:
            assert new_steps < 4000, (distribution, old_steps, new_steps, plan)
        if distribution in {"manual", "mixed"}:
            assert new_steps * 50 < old_steps
        print(dict(distribution=distribution, old_steps=old_steps, new_steps=new_steps, plan=plan, fallback_plan=fallback_plan))


def test_registered_migration_preserves_nonempty_rows_and_rejects_corruption(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    repo.initialize()
    version, digest = runtime.schema_identity("earnings_impact")
    for lane in ("manual", "scheduled"):
        public, _ = repo.create_job(
            job_type="earnings_impact", payload={"ticker": "AAPL", "name": "Apple"},
            model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
            prompt_version=lane, schema_version=version, schema_sha256=digest,
            submission_source=lane, max_queued=20,
        )
        with sqlite3.connect(repo.path) as conn:
            conn.execute("""UPDATE ai_jobs SET status='completed',submission_started_at=?,
                         completed_at=?,budget_charge_microusd=100,usage_total_tokens=500
                         WHERE job_id=?""",
                         (storage._iso(NOW-timedelta(minutes=1)), storage._iso(NOW-timedelta(seconds=10)), public["job_id"]))
    with sqlite3.connect(repo.path) as conn:
        conn.execute("DROP INDEX idx_ai_jobs_manual_cooldown")
        conn.execute("DROP INDEX idx_ai_job_sources_lane_job")
        conn.execute("DROP INDEX idx_ai_jobs_manual_paid_cover")
        conn.execute("DELETE FROM ai_job_schema WHERE version=?", (storage._MANUAL_COOLDOWN_INDEX_VERSION,))
        old_registry = conn.execute("SELECT * FROM ai_job_schema ORDER BY version").fetchall()
        old_jobs = conn.execute("SELECT * FROM ai_jobs").fetchall()
        old_sources = conn.execute("SELECT * FROM ai_job_sources ORDER BY job_id").fetchall()
        assert len(old_jobs) == len(old_sources) == 2
    repo.initialize()
    repo.initialize()
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT * FROM ai_jobs").fetchall() == old_jobs
        assert conn.execute("SELECT * FROM ai_job_sources ORDER BY job_id").fetchall() == old_sources
        assert conn.execute("SELECT * FROM ai_job_schema WHERE version<>? ORDER BY version", (storage._MANUAL_COOLDOWN_INDEX_VERSION,)).fetchall() == old_registry
        assert conn.execute("SELECT checksum FROM ai_job_schema WHERE version=?", (storage._MANUAL_COOLDOWN_INDEX_VERSION,)).fetchone() == (storage._MANUAL_COOLDOWN_INDEX_CHECKSUM,)
        conn.execute("UPDATE ai_job_schema SET checksum='corrupt' WHERE version=?", (storage._MANUAL_COOLDOWN_INDEX_VERSION,))
    with pytest.raises(RuntimeError, match="ai_job_manual_cooldown_index_checksum_mismatch"):
        repo.initialize()


def test_old_store_without_indexes_reads_real_fallback_without_writing(tmp_path):
    path = tmp_path / "old.db"
    with database() as conn, sqlite3.connect(path) as target:
        insert(conn, "manual", completed="2026-10-12T08:19:59Z")
        for i in range(70):
            insert(conn, f"scheduled-{i}", completed="2099-01-01T00:00:00Z", lane="scheduled")
        for name in ("idx_ai_jobs_manual_cooldown", "idx_ai_job_sources_lane_job", "idx_ai_jobs_manual_paid_cover"):
            conn.execute(f"DROP INDEX {name}")
        conn.commit()
        expected = old_result(conn, None)
        conn.backup(target)
    repo = AIJobRepository(path)
    with repo._connect_read_only() as conn:
        statements = []
        conn.set_trace_callback(statements.append)
        version = conn.execute("PRAGMA schema_version").fetchone()[0]
        assert AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30) == expected
        assert any("SELECT MAX(COALESCE" in statement for statement in statements)
        assert AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30, exclude_job_id="manual") is None
        assert conn.total_changes == 0
        assert conn.execute("PRAGMA schema_version").fetchone()[0] == version
        assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 71
        assert conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('idx_ai_jobs_manual_cooldown','idx_ai_job_sources_lane_job','idx_ai_jobs_manual_paid_cover')").fetchone()[0] == 0


def plan_parent(conn):
    return next(row[0] for row in conn.execute("EXPLAIN QUERY PLAN " + storage._MANUAL_COOLDOWN_SQL, (None,))
                if row[3] == "CO-ROUTINE recent")


@pytest.mark.parametrize("missing", ["ai_jobs", "ai_job_sources"])
def test_missing_table_error_is_not_hidden(missing):
    with database() as conn:
        conn.execute(f"DROP TABLE {missing}")
        with pytest.raises(sqlite3.OperationalError, match=f"no such table: {missing}"):
            AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30)


def test_unusable_named_index_error_is_not_hidden():
    with database() as conn:
        conn.execute("DROP INDEX idx_ai_jobs_manual_cooldown")
        conn.execute("CREATE INDEX idx_ai_jobs_manual_cooldown ON ai_jobs(job_id) WHERE status='pending'")
        with pytest.raises(sqlite3.OperationalError, match="no query solution"):
            AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30)


def test_sql_error_is_not_hidden(monkeypatch):
    with database() as conn:
        monkeypatch.setattr(storage, "_MANUAL_COOLDOWN_SQL", "SELECT FROM ai_jobs")
        with pytest.raises(sqlite3.OperationalError, match="syntax error"):
            AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30)


def test_locked_database_error_is_not_hidden(tmp_path):
    path = tmp_path / "locked.db"
    with database() as source, sqlite3.connect(path) as target:
        insert(source, "manual")
        source.commit()
        source.backup(target)
    with sqlite3.connect(path) as writer, sqlite3.connect(path, timeout=0) as reader:
        writer.execute("BEGIN EXCLUSIVE")
        reader.execute("PRAGMA busy_timeout=0")
        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            AIJobRepository._manual_cooldown_until(reader, now_dt=NOW, cooldown_seconds=30)
        writer.rollback()


def test_corrupt_database_error_is_not_hidden(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"not a SQLite database" * 32)
    with AIJobRepository(path)._connect_read_only() as conn:
        with pytest.raises(sqlite3.DatabaseError, match="file is not a database"):
            AIJobRepository._manual_cooldown_until(conn, now_dt=NOW, cooldown_seconds=30)
