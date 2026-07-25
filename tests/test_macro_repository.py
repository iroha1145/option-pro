"""Macro store durability: WAL, idempotent migration, atomic publication."""

from __future__ import annotations

import datetime as dt
import sqlite3

import pytest

from app.services.macro_conditions.models import (
    CompositeSnapshot,
    MacroError,
    SnapshotBundle,
)
from app.services.macro_conditions.registry import SCORING_VERSION
from app.services.macro_conditions.repository import (
    SCHEMA_CHECKSUM,
    SCHEMA_VERSION,
    MacroRepository,
    MacroSchemaError,
)
from app.services.macro_conditions.service import MacroConditionsService
from macro_fixtures import fixed_clock, seed_repository


SEED_START = dt.date(2019, 7, 1)
SEED_END = dt.date(2026, 7, 23)
AS_OF = "2026-07-24T22:30:00Z"


def _store(tmp_path) -> MacroRepository:
    return MacroRepository(tmp_path / "macro-conditions.db", clock=fixed_clock())


def _published(tmp_path) -> tuple[MacroRepository, MacroConditionsService]:
    repository = _store(tmp_path)
    seed_repository(repository, start=SEED_START, end=SEED_END)
    service = MacroConditionsService(repository, clock=fixed_clock())
    bundle, _summary = service.build_snapshot(as_of=AS_OF)
    assert bundle is not None
    repository.publish(bundle, run_id="mcr_test_seed")
    return repository, service


# ---------------------------------------------------------------------------
# 56-59 schema
# ---------------------------------------------------------------------------


def test_a_new_database_initialises_with_wal_and_a_recorded_schema(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    report = repository.integrity_report()
    assert report["journal_mode"] == "wal"
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == 0
    assert report["schema_versions"] == [SCHEMA_VERSION]


def test_migration_is_idempotent(tmp_path) -> None:
    repository = _store(tmp_path)
    for _ in range(4):
        repository.initialize()
    with repository.read() as connection:
        rows = connection.execute("SELECT version, checksum FROM macro_schema").fetchall()
    assert len(rows) == 1
    assert rows[0]["checksum"] == SCHEMA_CHECKSUM


def test_a_tampered_schema_checksum_is_refused(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    with sqlite3.connect(repository.path) as connection:
        connection.execute("UPDATE macro_schema SET checksum='0'*64")
        connection.commit()
    with pytest.raises(MacroSchemaError):
        repository.initialize()


def test_foreign_keys_are_enforced_on_both_connection_kinds(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    with repository.write() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with repository.read() as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1


def test_a_read_only_repository_refuses_to_write_or_migrate(tmp_path) -> None:
    writable = _store(tmp_path)
    writable.initialize()
    reader = MacroRepository(tmp_path / "macro-conditions.db", read_only=True)
    with pytest.raises(MacroSchemaError):
        reader.initialize()


def test_reading_a_missing_database_reports_store_unavailable(tmp_path) -> None:
    repository = MacroRepository(tmp_path / "absent.db", read_only=True)
    with pytest.raises(MacroError) as excinfo:
        repository.latest_composite()
    assert excinfo.value.code == "macro_store_unavailable"


def test_a_relative_path_is_refused(tmp_path) -> None:
    with pytest.raises(MacroSchemaError):
        MacroRepository("macro-conditions.db")


# ---------------------------------------------------------------------------
# 12-13 storage hygiene
# ---------------------------------------------------------------------------


def test_non_finite_values_can_never_be_stored(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    bundle = SnapshotBundle(
        as_of=AS_OF,
        scoring_version=SCORING_VERSION,
        factors=(),
        modules=(),
        composites=(
            CompositeSnapshot(
                snapshot_date=dt.date(2026, 7, 24),
                as_of=AS_OF,
                score=float("nan"),
                score_change_7d=None,
                confidence=None,
                regime=None,
                valid_module_count=7,
                data_through="2026-07-23",
                available_at=AS_OF,
                history_basis="latest_revised_backfill",
                status="ok",
                scoring_version=SCORING_VERSION,
            ),
        ),
    )
    with pytest.raises(MacroSchemaError):
        repository.publish(bundle, run_id="mcr_test")
    # Nothing partial was left behind.
    assert repository.latest_composite() is None


def test_a_candidate_from_another_scoring_version_is_refused(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    bundle = SnapshotBundle(
        as_of=AS_OF,
        scoring_version="optix-macro-score-v99",
        factors=(),
        modules=(),
        composites=(),
    )
    with pytest.raises(MacroSchemaError):
        repository.publish(bundle, run_id="mcr_test")


def test_sync_run_json_columns_are_bounded_and_stably_ordered(tmp_path) -> None:
    repository = _store(tmp_path)
    repository.initialize()
    repository.start_sync_run("mcr_1", "manual", started_at=AS_OF)
    repository.finish_sync_run(
        "mcr_1",
        status="degraded",
        data_through=dt.date(2026, 7, 23),
        series_succeeded=23,
        series_failed=1,
        error_codes=["fred_unavailable", "fred_unavailable", "etf_history_unavailable"],
        details={"z": 1, "a": 2},
        completed_at=AS_OF,
    )
    run = repository.latest_sync_run()
    assert run is not None
    assert run["status"] == "degraded"
    assert run["error_codes"] == ["etf_history_unavailable", "fred_unavailable"]
    with repository.read() as connection:
        raw = connection.execute(
            "SELECT details_json FROM macro_sync_runs WHERE run_id='mcr_1'"
        ).fetchone()[0]
    assert raw == '{"a":2,"z":1}'

    with pytest.raises(MacroSchemaError):
        repository.finish_sync_run(
            "mcr_1",
            status="degraded",
            data_through=None,
            series_succeeded=0,
            series_failed=0,
            error_codes=[],
            details={"blob": "x" * 200_000},
        )


# ---------------------------------------------------------------------------
# 60-61 failure isolation and atomic publication
# ---------------------------------------------------------------------------


def test_a_failed_refresh_keeps_the_previous_snapshot_readable(tmp_path) -> None:
    repository, service = _published(tmp_path)
    before = repository.latest_composite()
    assert before is not None

    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def fetch_many(self, *_args, **_kwargs):
            raise MacroError("fred_unavailable", "upstream down")

    broken = MacroConditionsService(
        repository,
        clock=fixed_clock(),
        fred_factory=lambda: _Boom(),
        proxy=_EmptyProxy(),
    )
    outcome = broken.refresh(trigger="manual")
    assert outcome["status"] in {"degraded", "succeeded"}

    after = repository.latest_composite()
    assert after is not None
    assert after["score"] == before["score"]
    assert after["snapshot_date"] == before["snapshot_date"]


class _EmptyProxy:
    def read(self, symbols=None, *, period="", periods=None):
        return {}, {}


def test_publication_replaces_rows_without_deleting_history(tmp_path) -> None:
    repository, service = _published(tmp_path)
    with repository.read() as connection:
        first = connection.execute(
            "SELECT COUNT(*) FROM macro_composite_snapshots"
        ).fetchone()[0]
    bundle, _summary = service.build_snapshot(as_of="2026-07-24T23:00:00Z")
    assert bundle is not None
    repository.publish(bundle, run_id="mcr_test")
    with repository.read() as connection:
        second = connection.execute(
            "SELECT COUNT(*) FROM macro_composite_snapshots"
        ).fetchone()[0]
        as_of_values = {
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT as_of FROM macro_composite_snapshots"
            )
        }
    # Upserted in place: the same snapshot dates, refreshed as_of, no row loss.
    assert second == first
    assert as_of_values == {"2026-07-24T23:00:00Z"}
    assert repository.integrity_report()["integrity_check"] == "ok"


def test_a_publication_error_rolls_back_the_whole_candidate(tmp_path) -> None:
    repository, service = _published(tmp_path)
    baseline = repository.latest_composite()
    assert baseline is not None
    bundle, _summary = service.build_snapshot(as_of="2026-07-25T00:00:00Z")
    assert bundle is not None
    # Poison the last composite row so the transaction fails after many inserts.
    poisoned = SnapshotBundle(
        as_of=bundle.as_of,
        scoring_version=bundle.scoring_version,
        factors=bundle.factors,
        modules=bundle.modules,
        composites=bundle.composites[:-1]
        + (
            CompositeSnapshot(
                snapshot_date=bundle.composites[-1].snapshot_date,
                as_of=bundle.as_of,
                score=float("inf"),
                score_change_7d=None,
                confidence=None,
                regime=None,
                valid_module_count=7,
                data_through="2026-07-23",
                available_at=bundle.as_of,
                history_basis="latest_revised_backfill",
                status="ok",
                scoring_version=SCORING_VERSION,
            ),
        ),
    )
    with pytest.raises(MacroSchemaError):
        repository.publish(poisoned, run_id="mcr_test")
    after = repository.latest_composite()
    assert after is not None
    assert after["as_of"] == baseline["as_of"]
    assert repository.integrity_report()["integrity_check"] == "ok"


# ---------------------------------------------------------------------------
# 62-63 integrity and backup inventory
# ---------------------------------------------------------------------------


def test_integrity_and_foreign_key_checks_pass_on_a_populated_store(tmp_path) -> None:
    repository, _service = _published(tmp_path)
    report = repository.integrity_report()
    assert report["integrity_check"] == "ok"
    assert report["foreign_key_violations"] == 0
    assert report["journal_mode"] == "wal"


def test_the_macro_database_is_part_of_the_data_path_inventory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    from app.data_paths import get_data_paths

    paths = get_data_paths()
    assert paths.macro_conditions_db == tmp_path / "macro-conditions.db"


def test_maintenance_and_retention_back_up_the_macro_database(tmp_path) -> None:
    import asyncio
    from types import SimpleNamespace

    from pydantic import SecretStr

    from app.worker.tasks import build_default_tasks

    for name in (
        "optix.db",
        "catalyst-cache.db",
        "ai-jobs.db",
        "optix-worker.db",
        "macro-conditions.db",
    ):
        with sqlite3.connect(tmp_path / name) as connection:
            connection.execute("CREATE TABLE sample(value INTEGER)")
            connection.commit()

    settings = SimpleNamespace(
        internal_api_token=SecretStr(""),
        macrolens_url="",
        macrolens_ca_bundle="",
        macrolens_cache_db_path=tmp_path / "catalyst-cache.db",
        macro_conditions_db_path=tmp_path / "macro-conditions.db",
        fred_api_key=SecretStr(""),
        openai_job_db_path=tmp_path / "ai-jobs.db",
        optix_worker_db_path=tmp_path / "optix-worker.db",
        optix_worker_lock_path=tmp_path / "optix-worker.lock",
        breakout_db_path=tmp_path / "optix.db",
        optix_backup_dir=tmp_path / "backups",
        personal_etl_enabled=False,
        massive_api_key="",
    )
    specs = build_default_tasks("macro-backup", settings=settings)
    maintenance = next(spec for spec in specs if spec.name == "maintenance")
    assert "macro-conditions" in maintenance.runner.databases
    result = asyncio.run(maintenance.runner())
    assert "macro-conditions" in result.details["backed_up"]
    produced = sorted(path.name for path in (tmp_path / "backups").glob("*.sqlite3"))
    assert any(name.startswith("macro-conditions-") for name in produced)


# ---------------- schema v2 migration (incremental review P1/P2) ----------------

_V1_ETF_TABLE = """
    CREATE TABLE macro_etf_observations (
        symbol TEXT NOT NULL,
        observation_date TEXT NOT NULL,
        adjusted_close REAL NOT NULL,
        provider TEXT NOT NULL,
        data_through TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        available_at TEXT NOT NULL,
        history_basis TEXT NOT NULL
            CHECK(history_basis IN ('latest_revised_backfill','local_point_in_time')),
        PRIMARY KEY(symbol, observation_date, provider, available_at)
    )
"""


def _v1_database(path, rows):
    """A database in the shape the previous release wrote."""

    import sqlite3

    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(_V1_ETF_TABLE)
    connection.executemany(
        """INSERT INTO macro_etf_observations(
               symbol,observation_date,adjusted_close,provider,
               data_through,fetched_at,available_at,history_basis
           ) VALUES(?,?,?,?,?,?,?,?)""",
        rows,
    )
    connection.commit()
    connection.close()


def test_v1_etf_duplicates_collapse_without_losing_first_visibility(tmp_path) -> None:
    """The v1 key was the write-time stamp, so identical prices piled up.

    ``INSERT OR IGNORE`` never ignored anything: 8 symbols x ~252 sessions,
    rewritten twice a day. The migration collapses them on the value, and keeps
    the earliest stamp as first_seen_at -- discarding that would move the
    point-in-time visibility of history that is already stored, which is the one
    thing this table exists to preserve.
    """

    from app.services.macro_conditions.repository import MacroRepository

    database = tmp_path / "macro-conditions.db"
    _v1_database(
        database,
        [
            # Same price seen on three separate refreshes.
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-20", "2026-07-20T22:00:00Z",
             "2026-07-20T22:00:00Z", "local_point_in_time"),
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-21", "2026-07-21T22:00:00Z",
             "2026-07-21T22:00:00Z", "local_point_in_time"),
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-22", "2026-07-22T22:00:00Z",
             "2026-07-22T22:00:00Z", "local_point_in_time"),
            # A genuine restatement of the same session must survive as its own row.
            ("SPY", "2026-07-20", 501.5, "yahoo", "2026-07-23", "2026-07-23T22:00:00Z",
             "2026-07-23T22:00:00Z", "local_point_in_time"),
            ("HYG", "2026-07-20", 78.25, "yahoo", "2026-07-20", "2026-07-20T22:00:00Z",
             "2026-07-20T22:00:00Z", "local_point_in_time"),
        ],
    )

    repository = MacroRepository(database)
    repository.initialize()

    with repository.read() as connection:
        rows = connection.execute(
            """SELECT symbol,observation_date,adjusted_close,first_seen_at,last_seen_at
               FROM macro_etf_observations ORDER BY symbol, adjusted_close"""
        ).fetchall()
    collapsed = [dict(row) for row in rows]

    assert len(collapsed) == 3, f"five v1 rows should collapse to three: {collapsed}"
    spy_500 = next(r for r in collapsed if r["symbol"] == "SPY" and r["adjusted_close"] == 500.0)
    assert spy_500["first_seen_at"] == "2026-07-20T22:00:00Z", (
        "the earliest sighting is when that price first became visible"
    )
    assert spy_500["last_seen_at"] == "2026-07-22T22:00:00Z"
    # The restatement is a separate revision, not a duplicate.
    assert any(r["adjusted_close"] == 501.5 for r in collapsed)
    assert any(r["symbol"] == "HYG" for r in collapsed)

    # active_etf picks the newest revision and reports its first-visible time.
    active = repository.active_etf("SPY")
    assert [row["adjusted_close"] for row in active] == [501.5]
    assert active[0]["available_at"] == "2026-07-23T22:00:00Z"


def test_migration_is_idempotent_and_refuses_to_empty_the_table(tmp_path) -> None:
    from app.services.macro_conditions.repository import MacroRepository

    database = tmp_path / "macro-conditions.db"
    _v1_database(
        database,
        [
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-20", "2026-07-20T22:00:00Z",
             "2026-07-20T22:00:00Z", "local_point_in_time"),
        ],
    )
    repository = MacroRepository(database)
    repository.initialize()
    repository.initialize()
    repository.initialize()

    with repository.read() as connection:
        count = connection.execute(
            "SELECT COUNT(*) AS n FROM macro_etf_observations"
        ).fetchone()["n"]
        leftovers = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='macro_etf_observations_v1'"
        ).fetchall()
    assert count == 1
    assert leftovers == [], "the v1 table must not be left behind"


def test_reseeing_a_price_moves_last_seen_at_and_writes_no_new_row(tmp_path) -> None:
    """The whole point of the rekey: a refresh that changes nothing writes nothing."""

    from datetime import date

    from app.services.macro_conditions.models import EtfObservation
    from app.services.macro_conditions.repository import (
        HISTORY_BASIS_LOCAL,
        MacroRepository,
    )

    repository = MacroRepository(tmp_path / "macro-conditions.db")
    repository.initialize()
    observation = EtfObservation(
        symbol="SPY",
        observation_date=date(2026, 7, 20),
        adjusted_close=500.0,
        provider="yahoo",
    )

    first = repository.record_etf_observations(
        [observation],
        data_through=date(2026, 7, 20),
        history_basis=HISTORY_BASIS_LOCAL,
        observed_at="2026-07-20T22:00:00Z",
    )
    assert first["inserted"] == 1

    second = repository.record_etf_observations(
        [observation],
        data_through=date(2026, 7, 21),
        history_basis=HISTORY_BASIS_LOCAL,
        observed_at="2026-07-21T22:00:00Z",
    )
    assert second["inserted"] == 0, "an unchanged price must not create a row"
    assert second["unchanged"] == 1

    with repository.read() as connection:
        row = connection.execute(
            "SELECT first_seen_at,last_seen_at FROM macro_etf_observations"
        ).fetchone()
    assert row["first_seen_at"] == "2026-07-20T22:00:00Z"
    assert row["last_seen_at"] == "2026-07-21T22:00:00Z"

    # A different price is a revision and does get its own row.
    revised = repository.record_etf_observations(
        [
            EtfObservation(
                symbol="SPY",
                observation_date=date(2026, 7, 20),
                adjusted_close=501.5,
                provider="yahoo",
            )
        ],
        data_through=date(2026, 7, 22),
        history_basis=HISTORY_BASIS_LOCAL,
        observed_at="2026-07-22T22:00:00Z",
    )
    assert revised["inserted"] == 1


def test_publication_log_is_append_only_and_survives_a_republish(tmp_path) -> None:
    """The snapshot tables are a current view; this log is the historical record.

    Without it the store can say what a past date recomputes to today, but not
    what was published on that date -- and a walk-forward test that asks the
    second question and receives the first is silently fed revisions that were
    not visible at the time.
    """

    repository, service = _published(tmp_path)

    with repository.read() as connection:
        first = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM macro_snapshot_publications ORDER BY published_at"
            ).fetchall()
        ]
    assert len(first) == 1, (
        f"one publication per run, not one per history grid date: {len(first)}"
    )
    assert first[0]["run_id"], "a publication belongs to a sync run"
    assert first[0]["composite_payload"].startswith("{")

    # Publish the same bundle again: a second run of the same day.
    bundle, _summary = service.build_snapshot(as_of=AS_OF)
    assert bundle is not None
    repository.publish(bundle, run_id="mcr_test_second")

    with repository.read() as connection:
        second = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM macro_snapshot_publications ORDER BY published_at"
            ).fetchall()
        ]
    assert len(second) > len(first), "a republish appends rather than overwrites"
    assert second[0]["publication_id"] == first[0]["publication_id"]
    assert second[0]["composite_payload"] == first[0]["composite_payload"], (
        "an earlier publication must never be rewritten"
    )


def test_merged_rows_keep_the_basis_of_their_earliest_sighting(tmp_path) -> None:
    """The same price under two bases is one price, and the label must follow the stamp.

    A close can be recorded once by the ten-year backfill and again by an
    incremental refresh. Those are not two prices. The merged row keeps the
    earliest ``available_at`` as ``first_seen_at``, so it has to keep that
    sighting's ``history_basis`` too -- otherwise a backfill-derived visibility
    gets labelled as locally observed, which is exactly the distinction the
    point-in-time design rests on. Deciding by insert order would be
    non-deterministic on top of being wrong.
    """

    from app.services.macro_conditions.repository import MacroRepository

    database = tmp_path / "macro-conditions.db"
    _v1_database(
        database,
        [
            # Backfill saw it first...
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-20", "2026-07-20T22:00:00Z",
             "2026-07-20T22:00:00Z", "latest_revised_backfill"),
            # ...and an incremental refresh re-read the identical close later.
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-22", "2026-07-22T22:00:00Z",
             "2026-07-22T22:00:00Z", "local_point_in_time"),
            # A close only ever seen live keeps its local label.
            ("SPY", "2026-07-21", 502.0, "yahoo", "2026-07-22", "2026-07-22T22:00:00Z",
             "2026-07-22T22:00:00Z", "local_point_in_time"),
        ],
    )

    MacroRepository(database).initialize()

    import sqlite3

    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    merged = {
        row["observation_date"]: dict(row)
        for row in connection.execute(
            "SELECT observation_date,history_basis,first_seen_at,last_seen_at"
            " FROM macro_etf_observations"
        )
    }
    connection.close()

    assert len(merged) == 2
    backfilled = merged["2026-07-20"]
    assert backfilled["history_basis"] == "latest_revised_backfill", (
        "the earliest sighting was a backfill, so its visibility is backfill-derived"
    )
    assert backfilled["first_seen_at"] == "2026-07-20T22:00:00Z"
    assert backfilled["last_seen_at"] == "2026-07-22T22:00:00Z"
    assert merged["2026-07-21"]["history_basis"] == "local_point_in_time"


def test_reads_work_against_a_v1_table_before_the_worker_migrates(tmp_path) -> None:
    """The API ships with the migration but does not run it.

    initialize() is only called inside refresh(), which lives in the worker. The
    API process opens the same database read-only, so between a deploy and the
    worker next macro run a v2-only read path would meet a v1 table and the
    macro panel would report unavailable for hours. Reading whichever shape is
    on disk removes that window rather than assuming a container start order.
    """

    from app.services.macro_conditions.repository import MacroRepository

    database = tmp_path / "macro-conditions.db"
    _v1_database(
        database,
        [
            ("SPY", "2026-07-20", 500.0, "yahoo", "2026-07-20", "2026-07-20T22:00:00Z",
             "2026-07-20T22:00:00Z", "local_point_in_time"),
            ("SPY", "2026-07-21", 502.0, "yahoo", "2026-07-21", "2026-07-21T22:00:00Z",
             "2026-07-21T22:00:00Z", "local_point_in_time"),
        ],
    )

    # No initialize(): exactly the state the API sees straight after a deploy.
    reader = MacroRepository(database, read_only=True)
    rows = reader.active_etf("SPY")

    assert [row["adjusted_close"] for row in rows] == [500.0, 502.0]
    assert rows[0]["available_at"] == "2026-07-20T22:00:00Z"

    # And the same reader keeps working once the worker has migrated.
    MacroRepository(database).initialize()
    migrated = MacroRepository(database, read_only=True).active_etf("SPY")
    assert [row["adjusted_close"] for row in migrated] == [500.0, 502.0]
    assert migrated[0]["available_at"] == "2026-07-20T22:00:00Z"
