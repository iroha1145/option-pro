from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.legacy_env_adapter import migrate_legacy_environment
from app.personal_config import AIConfig, AccessConfig, load_personal_config
from app.tools.migrate_personal_config import migrate


def test_repository_personal_config_freezes_paid_runtime() -> None:
    config = load_personal_config()

    assert config.ai.model == "gpt-5.6-terra"
    assert config.ai.reasoning == "max"
    assert config.ai.max_concurrency == 1
    assert config.ai.daily_max_jobs == 4
    assert config.ai.execution_mode == "background"
    assert config.access.mode == "private_network"


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
                "UNMAPPED_SWITCH": "legacy",
            }
        )

    assert migration.config.ai.reasoning == "max"
    assert migration.config.ai.max_concurrency == 1
    assert migration.config.features.catalyst_mode == "scheduled"
    assert migration.config.catalyst.sync_seconds == 240
    assert migration.secrets == {"OPENAI_API_KEY": "secret-value"}
    assert migration.unmapped_keys == ("UNMAPPED_SWITCH",)
    assert migration.deprecated_keys == ()
    assert migration.requires_owner_password is False


def test_migration_command_writes_private_secrets_and_review_report(
    tmp_path: Path,
) -> None:
    source = tmp_path / ".env"
    source.write_text(
        "OPENAI_API_KEY=secret-value\n"
        "APP_AUTH_TOKEN=old-browser-secret\n"
        "OPENAI_MODEL=gpt-5.6-terra\n"
        "OPENAI_REASONING=low\n"
        "UNMAPPED_SWITCH=legacy\n",
        encoding="utf-8",
    )

    with pytest.warns(DeprecationWarning):
        config_path, secrets_path, report_path = migrate(source, tmp_path / "out")

    migrated = load_personal_config(config_path)
    assert migrated.ai.reasoning == "max"
    assert migrated.ai.max_concurrency == 1
    assert secrets_path.read_text(encoding="utf-8") == (
        "OPENAI_API_KEY=secret-value\n"
    )
    assert stat.S_IMODE(secrets_path.stat().st_mode) == 0o600
    assert json.loads(report_path.read_text(encoding="utf-8")) == {
        "deprecated_keys": ["APP_AUTH_TOKEN"],
        "requires_owner_password": True,
        "unmapped_keys": ["UNMAPPED_SWITCH"],
    }
    assert "old-browser-secret" not in config_path.read_text(encoding="utf-8")
    assert "old-browser-secret" not in secrets_path.read_text(encoding="utf-8")
    assert "old-browser-secret" not in report_path.read_text(encoding="utf-8")
