from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.personal_config import PersonalConfig


LEGACY_RELEASE_DEADLINE = "the first release after Personal Edition activation"
SECRET_KEYS = {
    "OPENAI_API_KEY",
    "MACROLENS_URL",
    "MACROLENS_BASE_URL",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
    "FINNHUB_API_KEY",
    "DATA_DIR",
    "HOST_BIND",
    "PORT",
}


@dataclass(frozen=True)
class LegacyMigration:
    config: PersonalConfig
    secrets: dict[str, str]
    unmapped: dict[str, str]


def _boolean(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid legacy boolean: {value!r}")


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    raw = str(values.get(key, "")).strip()
    return int(raw) if raw else default


def migrate_legacy_environment(values: Mapping[str, str]) -> LegacyMigration:
    defaults = PersonalConfig()
    old_mode = str(values.get("CATALYST_MODE", "")).strip().lower()
    if old_mode in {"disabled", "off"}:
        catalyst_mode = "off"
    elif old_mode in {"display", "read"}:
        catalyst_mode = "read"
    elif old_mode in {"manual", "scheduled"}:
        catalyst_mode = old_mode
    else:
        enabled = _boolean(
            str(values.get("MACROLENS_ENABLED", "")),
            default=defaults.catalyst_sync_enabled,
        )
        catalyst_mode = "read" if enabled else "off"

    payload: dict[str, Any] = {
        "features": {
            "breakout_enabled": _boolean(
                str(values.get("BREAKOUT_RADAR_ENABLED", "")),
                default=defaults.features.breakout_enabled,
            ),
            "catalyst_mode": catalyst_mode,
        },
        "ai": {
            "model": str(values.get("OPENAI_MODEL", "")).strip()
            or defaults.ai.model,
            "reasoning": "max",
            "max_concurrency": 1,
            "daily_max_jobs": _integer(
                values, "OPENAI_DAILY_MAX_JOBS", defaults.ai.daily_max_jobs
            ),
            "execution_mode": "background",
        },
        "catalyst": {
            "sync_seconds": _integer(
                values,
                "MACROLENS_FEED_INTERVAL_SECONDS",
                defaults.catalyst.sync_seconds,
            ),
            "focus_seconds": _integer(
                values,
                "FOCUS_CONTEXT_REFRESH_SECONDS",
                defaults.catalyst.focus_seconds,
            ),
            "scheduled_times_et": defaults.catalyst.scheduled_times_et,
        },
        "breakout": {
            "regular_seconds": _integer(
                values,
                "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS",
                defaults.breakout.regular_seconds,
            ),
            "premarket_seconds": _integer(
                values,
                "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS",
                defaults.breakout.premarket_seconds,
            ),
            "closed_seconds": _integer(
                values,
                "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS",
                defaults.breakout.closed_seconds,
            ),
            "range_persistence_mode": str(
                values.get(
                    "RANGE_PERSISTENCE_MODE",
                    defaults.breakout.range_persistence_mode,
                )
            ).strip(),
        },
        "storage": {
            "retention_days": _integer(
                values, "RETENTION_DAYS", defaults.storage.retention_days
            ),
            "backup_keep": _integer(
                values, "BACKUP_KEEP", defaults.storage.backup_keep
            ),
        },
    }
    config = PersonalConfig.model_validate(payload)
    secrets = {
        ("MACROLENS_URL" if key == "MACROLENS_BASE_URL" else key): value
        for key, value in values.items()
        if key in SECRET_KEYS and str(value).strip()
    }
    mapped = SECRET_KEYS | {
        "CATALYST_MODE",
        "MACROLENS_ENABLED",
        "BREAKOUT_RADAR_ENABLED",
        "OPENAI_MODEL",
        "OPENAI_REASONING",
        "OPENAI_MAX_CONCURRENCY",
        "OPENAI_DAILY_MAX_JOBS",
        "OPENAI_EXECUTION_MODE",
        "MACROLENS_FEED_INTERVAL_SECONDS",
        "FOCUS_CONTEXT_REFRESH_SECONDS",
        "BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS",
        "BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS",
        "BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS",
        "RANGE_PERSISTENCE_MODE",
        "RETENTION_DAYS",
        "BACKUP_KEEP",
    }
    unmapped = {
        key: value
        for key, value in values.items()
        if key and key not in mapped and str(value).strip()
    }
    warnings.warn(
        "Legacy .env behavior settings are deprecated and will be removed after "
        f"{LEGACY_RELEASE_DEADLINE}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return LegacyMigration(config=config, secrets=secrets, unmapped=unmapped)
