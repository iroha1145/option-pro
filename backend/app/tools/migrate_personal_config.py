from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values

from app.legacy_env_adapter import LegacyMigrationConflict, migrate_legacy_environment
from app.personal_config import PersonalConfig
from app.tools.personal_secrets import atomic_write


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _toml(config: PersonalConfig) -> str:
    times = ", ".join(_toml_string(value) for value in config.catalyst.scheduled_times_et)
    private_cidrs = ", ".join(
        _toml_string(value) for value in config.access.allowed_private_cidrs
    )
    return f'''[access]
mode = {_toml_string(config.access.mode)}
allowed_private_cidrs = [{private_cidrs}]

[features]
breakout_enabled = {str(config.features.breakout_enabled).lower()}
catalyst_mode = {_toml_string(config.features.catalyst_mode)}

[ai]
model = {_toml_string(config.ai.model)}
reasoning = "max"
max_concurrency = 1
daily_max_jobs = {config.ai.daily_max_jobs}
daily_budget_usd = {config.ai.daily_budget_usd}
execution_mode = "background"

[catalyst]
sync_seconds = {config.catalyst.sync_seconds}
focus_seconds = {config.catalyst.focus_seconds}
scheduled_times_et = [{times}]
manual_force_reanalysis = {str(config.catalyst.manual_force_reanalysis).lower()}
manual_refresh_cooldown_seconds = {config.catalyst.manual_refresh_cooldown_seconds}

[breakout]
regular_seconds = {config.breakout.regular_seconds}
premarket_seconds = {config.breakout.premarket_seconds}
closed_seconds = {config.breakout.closed_seconds}
range_persistence_mode = {_toml_string(config.breakout.range_persistence_mode)}

[storage]
retention_days = {config.storage.retention_days}
backup_keep = {config.storage.backup_keep}
'''


def _atomic_private_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _environment_text(values: dict[str, str], order: tuple[str, ...]) -> str:
    return "".join(
        f"{key}={json.dumps(values[key], ensure_ascii=False)}\n"
        for key in order
        if values.get(key)
    )


def _report_payload(result: object) -> dict[str, object]:
    warning_messages = getattr(result, "warnings", None)
    if warning_messages is None:
        warning_messages = getattr(result, "warning_messages", ())
    return {
        "mapped_keys": list(getattr(result, "mapped_keys", ())),
        "deprecated_keys": list(getattr(result, "deprecated_keys", ())),
        "removed_keys": [
            {"key": key, "status": "removed_by_personal_edition"}
            for key in getattr(result, "removed_keys", ())
        ],
        "conflicting_keys": list(getattr(result, "conflicting_keys", ())),
        "unmapped_keys": list(getattr(result, "unmapped_keys", ())),
        "requires_owner_password": bool(
            getattr(result, "requires_owner_password", False)
        ),
        "warnings": list(warning_messages),
    }


def _write_report(path: Path, result: object) -> None:
    _atomic_private_text(
        path,
        json.dumps(
            _report_payload(result),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    )


def migrate(source: Path, output_directory: Path) -> tuple[Path, Path, Path, Path]:
    if not source.is_file():
        raise FileNotFoundError(f"legacy environment file not found: {source}")
    parsed = {
        key: str(value or "")
        for key, value in dotenv_values(source).items()
        if key
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    config_path = output_directory / "personal.toml"
    secrets_path = output_directory / "secrets.env"
    machine_path = output_directory / "machine.env"
    report_path = output_directory / "migration-report.json"
    try:
        result = migrate_legacy_environment(parsed)
    except LegacyMigrationConflict as exc:
        _write_report(report_path, exc)
        raise
    config_path.write_text(_toml(result.config), encoding="utf-8")
    atomic_write(result.secrets, secrets_path)
    _atomic_private_text(
        machine_path,
        _environment_text(
            result.machine,
            (
                "HOST_BIND",
                "PORT",
                "MACROLENS_URL",
                "ALLOWED_HOSTS",
                "TRUST_PROXY_HEADERS",
                "TRUSTED_PROXY_CIDRS",
                "DATA_DIR",
            ),
        ),
    )
    _write_report(report_path, result)
    return config_path, secrets_path, machine_path, report_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a legacy Option Pro .env into Personal Edition files."
    )
    parser.add_argument("source", type=Path, help="legacy .env path")
    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path("config/migrated"),
    )
    arguments = parser.parse_args()
    try:
        paths = migrate(arguments.source, arguments.output_directory)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps({"created": [str(path) for path in paths]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
