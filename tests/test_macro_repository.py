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
    repository.publish(bundle)
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
        repository.publish(bundle)
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
        repository.publish(bundle)


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
    def read(self, symbols=None, *, period=""):
        return {}, {}


def test_publication_replaces_rows_without_deleting_history(tmp_path) -> None:
    repository, service = _published(tmp_path)
    with repository.read() as connection:
        first = connection.execute(
            "SELECT COUNT(*) FROM macro_composite_snapshots"
        ).fetchone()[0]
    bundle, _summary = service.build_snapshot(as_of="2026-07-24T23:00:00Z")
    assert bundle is not None
    repository.publish(bundle)
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
        repository.publish(poisoned)
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
