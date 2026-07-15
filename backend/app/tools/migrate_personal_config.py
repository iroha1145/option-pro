from __future__ import annotations

import argparse
import json
from pathlib import Path

from dotenv import dotenv_values

from app.legacy_env_adapter import migrate_legacy_environment
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
execution_mode = "background"

[catalyst]
sync_seconds = {config.catalyst.sync_seconds}
focus_seconds = {config.catalyst.focus_seconds}
scheduled_times_et = [{times}]

[breakout]
regular_seconds = {config.breakout.regular_seconds}
premarket_seconds = {config.breakout.premarket_seconds}
closed_seconds = {config.breakout.closed_seconds}
range_persistence_mode = {_toml_string(config.breakout.range_persistence_mode)}

[storage]
retention_days = {config.storage.retention_days}
backup_keep = {config.storage.backup_keep}
'''


def migrate(source: Path, output_directory: Path) -> tuple[Path, Path, Path]:
    if not source.is_file():
        raise FileNotFoundError(f"legacy environment file not found: {source}")
    parsed = {
        key: str(value or "")
        for key, value in dotenv_values(source).items()
        if key
    }
    result = migrate_legacy_environment(parsed)
    output_directory.mkdir(parents=True, exist_ok=True)
    config_path = output_directory / "personal.toml"
    secrets_path = output_directory / "secrets.env"
    report_path = output_directory / "unmapped-env.json"
    config_path.write_text(_toml(result.config), encoding="utf-8")
    atomic_write(result.secrets, secrets_path)
    report_path.write_text(
        json.dumps(
            {
                "deprecated_keys": list(result.deprecated_keys),
                "unmapped_keys": list(result.unmapped_keys),
                "requires_owner_password": result.requires_owner_password,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n",
        encoding="utf-8",
    )
    return config_path, secrets_path, report_path


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
