from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.data_paths import get_data_paths
from app.services.breakouts.config import BreakoutSettings
from app.services.catalysts.config import CatalystSettings


def test_data_dir_owns_the_complete_runtime_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    paths = get_data_paths()

    assert paths.root == tmp_path
    assert paths.ai_jobs_db == tmp_path / "ai-jobs.db"
    assert paths.catalyst_cache_db == tmp_path / "catalyst-cache.db"
    assert paths.optix_db == tmp_path / "optix.db"
    assert paths.worker_db == tmp_path / "optix-worker.db"
    assert paths.worker_lock == tmp_path / "optix-worker.lock"
    assert paths.watchlist_snapshot == tmp_path / "watchlist-snapshot-v1.json"
    assert paths.strength_snapshot == tmp_path / "strength-snapshot-v1.json"
    assert paths.backups_dir == tmp_path / "backups"
    assert paths.runtime_settings == tmp_path / "runtime-settings.json"


def test_independent_path_environment_variables_are_ignored(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_JOB_DB_PATH", "/should-not-be-used/ai.db")
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", "/should-not-be-used/catalyst.db")
    monkeypatch.setenv("BREAKOUT_DB_PATH", "/should-not-be-used/optix.db")

    assert Settings(_env_file=None).openai_job_db_path == tmp_path / "ai-jobs.db"
    assert (
        CatalystSettings(_env_file=None).cache_db_path
        == tmp_path / "catalyst-cache.db"
    )
    assert BreakoutSettings(_env_file=None).db_path == tmp_path / "optix.db"


def test_programmatic_path_overrides_remain_available_to_tests(tmp_path) -> None:
    assert (
        Settings(
            _env_file=None,
            openai_job_db_path=tmp_path / "test-ai.db",
        ).openai_job_db_path
        == tmp_path / "test-ai.db"
    )
    assert (
        CatalystSettings(
            _env_file=None,
            cache_db_path=tmp_path / "test-catalyst.db",
        ).cache_db_path
        == tmp_path / "test-catalyst.db"
    )
    assert (
        BreakoutSettings(
            _env_file=None,
            db_path=tmp_path / "test-optix.db",
        ).db_path
        == tmp_path / "test-optix.db"
    )


@pytest.mark.parametrize("value", ["relative", Path("/data/../tmp")])
def test_data_dir_rejects_noncanonical_roots(value, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(value))

    with pytest.raises(ValueError, match="DATA_DIR"):
        get_data_paths()
