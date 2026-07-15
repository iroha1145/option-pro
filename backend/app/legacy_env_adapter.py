from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.personal_config import PersonalConfig


LEGACY_RELEASE_DEADLINE = "Personal Edition 2.0"
SECRET_KEYS = {
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
    "DATA_DIR",
}
DEPRECATED_ACCESS_KEYS = {
    "PUBLIC_READ_API_ENABLED",
    "APP_AUTH_TOKEN",
    "MACROLENS_ACTION_KEY_ID",
    "MACROLENS_ACTION_SECRET",
    "DEPLOY_REQUIRE_AI",
    "DEPLOY_REQUIRE_CATALYST",
    "DEPLOY_REQUIRE_CATALYST_ACTIONS",
    "DEPLOY_REQUIRE_FOCUS_PRODUCER",
}


@dataclass(frozen=True)
class LegacyMigration:
    config: PersonalConfig
    secrets: dict[str, str]
    deprecated_keys: tuple[str, ...]
    unmapped_keys: tuple[str, ...]
    requires_owner_password: bool


def _boolean(value: str, *, default: bool) -> bool:
    normalized = value.strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("invalid legacy boolean value")


def _integer(values: Mapping[str, str], key: str, default: int) -> int:
    raw = str(values.get(key, "")).strip()
    return int(raw) if raw else default


def _access_mode(values: Mapping[str, str]) -> str:
    explicit = str(values.get("ACCESS_MODE", "")).strip().lower()
    if explicit:
        if explicit not in {"private_network", "password"}:
            raise ValueError("ACCESS_MODE must be private_network or password")
        return explicit
    if str(values.get("APP_PASSWORD_HASH", "")).strip():
        return "password"
    if str(values.get("APP_AUTH_TOKEN", "")).strip():
        return "password"
    return "private_network"


def _catalyst_mode(values: Mapping[str, str], defaults: PersonalConfig) -> str:
    if _boolean(
        str(values.get("HOT_CYCLE_SCHEDULE_ENABLED", "")),
        default=False,
    ):
        return "scheduled"
    manual = _boolean(
        str(values.get("HOT_CYCLE_MANUAL_ENABLED", "")),
        default=False,
    ) or _boolean(
        str(values.get("NEWS_LLM_MANUAL_ENABLED", "")),
        default=False,
    )
    if manual:
        return "manual"
    old_mode = str(values.get("CATALYST_MODE", "")).strip().lower()
    if old_mode in {"disabled", "off"}:
        return "off"
    if old_mode in {"display", "read"}:
        return "read"
    if old_mode in {"manual", "scheduled"}:
        return old_mode
    enabled = _boolean(
        str(values.get("MACROLENS_ENABLED", "")),
        default=defaults.catalyst_sync_enabled,
    )
    return "read" if enabled else "off"


def migrate_legacy_environment(values: Mapping[str, str]) -> LegacyMigration:
    defaults = PersonalConfig()
    access_mode = _access_mode(values)
    payload: dict[str, Any] = {
        "access": {
            "mode": access_mode,
            "allowed_private_cidrs": defaults.access.allowed_private_cidrs,
        },
        "features": {
            "breakout_enabled": _boolean(
                str(values.get("BREAKOUT_RADAR_ENABLED", "")),
                default=defaults.features.breakout_enabled,
            ),
            "catalyst_mode": _catalyst_mode(values, defaults),
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
        key: str(value)
        for key, value in values.items()
        if key in SECRET_KEYS and str(value).strip()
    }
    mapped = SECRET_KEYS | DEPRECATED_ACCESS_KEYS | {
        "ACCESS_MODE",
        "CATALYST_MODE",
        "MACROLENS_ENABLED",
        "NEWS_LLM_MANUAL_ENABLED",
        "HOT_CYCLE_MANUAL_ENABLED",
        "HOT_CYCLE_SCHEDULE_ENABLED",
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
    deprecated_keys = tuple(
        sorted(
            key
            for key in DEPRECATED_ACCESS_KEYS
            if str(values.get(key, "")).strip()
        )
    )
    unmapped_keys = tuple(
        sorted(
            key
            for key, value in values.items()
            if key and key not in mapped and str(value).strip()
        )
    )
    warnings.warn(
        "Legacy .env behavior settings are deprecated and will be removed in "
        f"{LEGACY_RELEASE_DEADLINE}.",
        DeprecationWarning,
        stacklevel=2,
    )
    return LegacyMigration(
        config=config,
        secrets=secrets,
        deprecated_keys=deprecated_keys,
        unmapped_keys=unmapped_keys,
        requires_owner_password=bool(
            str(values.get("APP_AUTH_TOKEN", "")).strip()
            and not str(values.get("APP_PASSWORD_HASH", "")).strip()
        ),
    )
