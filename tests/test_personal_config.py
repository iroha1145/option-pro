from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.legacy_env_adapter import LegacyMigrationConflict, migrate_legacy_environment
from app.personal_config import AIConfig, AccessConfig, load_personal_config
from app.tools.migrate_personal_config import migrate


def test_repository_personal_config_freezes_paid_runtime() -> None:
    config = load_personal_config()

    assert config.ai.model == "gpt-5.6-terra"
    assert config.ai.reasoning == "max"
    assert config.ai.max_concurrency == 1
    assert config.ai.daily_max_jobs == 0
    assert config.ai.daily_budget_usd == 0.0
    assert config.ai.daily_token_limit == 10_000_000
    assert config.ai.execution_mode == "background"
    assert config.access.mode == "private_network"
    assert config.public_home.poll_seconds == 30
    assert config.public_home.watchlist_seconds == 1800
    assert config.public_home.indices_seconds == 300
    assert config.public_home.overview_seconds == 300
    assert config.public_home.chart_seconds == 300
    assert config.public_home.signals_seconds == 900
    assert config.public_home.earnings_seconds == 21_600
    assert config.public_home.unusual_seconds == 1800


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("model", "gpt-5.6-luna"),
        ("reasoning", "high"),
        ("max_concurrency", 2),
        ("execution_mode", "worker_sync"),
    ],
)
def test_paid_runtime_rejects_personal_edition_drift(field: str, value: object) -> None:
    payload = AIConfig().model_dump()
    payload[field] = value

    with pytest.raises(ValidationError):
        AIConfig.model_validate(payload)


@pytest.mark.parametrize(
    "network",
    ["0.0.0.0/0", "203.0.113.0/24", "8.8.8.0/24", "2001:db8::/32"],
)
def test_private_access_networks_reject_public_or_ambiguous_ranges(network: str) -> None:
    with pytest.raises(ValidationError):
        AccessConfig(allowed_private_cidrs=[network])


def test_legacy_environment_is_reduced_to_typed_config_and_small_runtime_env() -> None:
    with pytest.warns(DeprecationWarning):
        migration = migrate_legacy_environment(
            {
                "OPENAI_API_KEY": "secret-value",
                "OPENAI_MODEL": "gpt-5.6-terra",
                "OPENAI_REASONING": "low",
                "OPENAI_MAX_CONCURRENCY": "8",
                "BREAKOUT_RADAR_ENABLED": "true",
                "CATALYST_MODE": "scheduled",
                "MACROLENS_FEED_INTERVAL_SECONDS": "240",
                "MARKETDATA_API_TOKEN": "market-secret",
                "MACROLENS_BASE_URL": "https://macrolens.example",
                "MACROLENS_INTERNAL_TOKEN": "legacy-token",
                "UNMAPPED_SWITCH": "legacy",
            }
        )

    assert migration.config.ai.reasoning == "max"
    assert migration.config.ai.max_concurrency == 1
    assert migration.config.features.catalyst_mode == "scheduled"
    assert migration.config.catalyst.sync_seconds == 240
    assert migration.secrets == {
        "OPENAI_API_KEY": "secret-value",
        "MARKETDATA_TOKEN": "market-secret",
        "INTERNAL_API_TOKEN": "legacy-token",
    }
    assert migration.machine == {"MACROLENS_URL": "https://macrolens.example"}
    assert migration.unmapped_keys == ("UNMAPPED_SWITCH",)
    assert migration.deprecated_keys == (
        "MACROLENS_BASE_URL",
        "MACROLENS_INTERNAL_TOKEN",
        "MARKETDATA_API_TOKEN",
    )
    assert migration.requires_owner_password is False


def test_legacy_macrolens_token_name_is_normalized_for_personal_runtime() -> None:
    with pytest.warns(DeprecationWarning):
        migration = migrate_legacy_environment(
            {
                "MACROLENS_URL": "https://legacy-macrolens.example",
                "MACROLENS_INTERNAL_TOKEN": "legacy-token",
            }
        )

    assert migration.secrets == {"INTERNAL_API_TOKEN": "legacy-token"}
    assert migration.machine == {"MACROLENS_URL": "https://legacy-macrolens.example"}
    assert migration.unmapped_keys == ()
    assert migration.deprecated_keys == ("MACROLENS_INTERNAL_TOKEN",)


def test_migration_command_writes_private_secrets_and_review_report(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_text(
        "OPENAI_API_KEY=secret-value\n"
        "APP_AUTH_TOKEN=old-browser-secret\n"
        "OPENAI_MODEL=gpt-5.6-terra\n"
        "OPENAI_REASONING=low\n"
        "MARKETDATA_API_TOKEN=market-secret\n"
        "MACROLENS_BASE_URL=https://macrolens.example\n"
        "HOST_BIND=127.0.0.1\n"
        "PORT=2000\n"
        "ALLOWED_HOSTS=127.0.0.1\n"
        "TRUST_PROXY_HEADERS=false\n"
        "TRUSTED_PROXY_CIDRS=127.0.0.1/32\n"
        "DATA_DIR=/data\n"
        "UNMAPPED_SWITCH=legacy\n",
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning):
        config_path, secrets_path, machine_path, report_path = migrate(
            source, tmp_path / "out"
        )

    migrated = load_personal_config(config_path)
    assert migrated.ai.reasoning == "max"
    assert migrated.ai.max_concurrency == 1
    assert secrets_path.read_text(encoding="utf-8") == (
        "OPENAI_API_KEY=secret-value\n"
        "MARKETDATA_TOKEN=market-secret\n"
    )
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
    machine = machine_path.read_text(encoding="utf-8")
    assert 'MACROLENS_URL="https://macrolens.example"' in machine
    assert 'DATA_DIR="/data"' in machine
    assert stat.S_IMODE(machine_path.stat().st_mode) == 0o600
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(report) == {
        "mapped_keys",
        "deprecated_keys",
        "removed_keys",
        "conflicting_keys",
        "unmapped_keys",
        "requires_owner_password",
        "warnings",
    }
    assert report["conflicting_keys"] == []
    assert report["requires_owner_password"] is True
    assert report["unmapped_keys"] == ["UNMAPPED_SWITCH"]
    assert report["removed_keys"] == [
        {"key": "APP_AUTH_TOKEN", "status": "removed_by_personal_edition"}
    ]
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert "old-browser-secret" not in config_path.read_text(encoding="utf-8")
    assert "old-browser-secret" not in secrets_path.read_text(encoding="utf-8")
    assert "old-browser-secret" not in report_path.read_text(encoding="utf-8")


def test_migration_fails_closed_when_canonical_and_legacy_aliases_conflict(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_text(
        "MACROLENS_URL=https://new.example\n"
        "MACROLENS_BASE_URL=https://old.example\n",
        encoding="utf-8",
    )
    output = tmp_path / "out"

    with pytest.raises(LegacyMigrationConflict):
        migrate(source, output)

    report_path = output / "migration-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["conflicting_keys"] == ["MACROLENS_BASE_URL", "MACROLENS_URL"]
    assert not (output / "personal.toml").exists()
    serialized = report_path.read_text(encoding="utf-8")
    assert "https://new.example" not in serialized
    assert "https://old.example" not in serialized
