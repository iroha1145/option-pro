from __future__ import annotations

import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.personal_config import PersonalConfig


LEGACY_RELEASE_DEADLINE = "Personal Edition 2.0"
SECRET_KEYS = {
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "MARKETDATA_TOKEN",
    "MASSIVE_API_KEY",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
}
MACHINE_KEYS = {
    "HOST_BIND",
    "PORT",
    "MACROLENS_URL",
    "ALLOWED_HOSTS",
    "TRUST_PROXY_HEADERS",
    "TRUSTED_PROXY_CIDRS",
    "DATA_DIR",
}
ALIASES = {
    "MARKETDATA_API_TOKEN": "MARKETDATA_TOKEN",
    "MACROLENS_BASE_URL": "MACROLENS_URL",
    "MACROLENS_INTERNAL_TOKEN": "INTERNAL_API_TOKEN",
}
REMOVED_KEYS = {
    "APP_AUTH_TOKEN",
    "MACROLENS_READ_KEY_ID",
    "MACROLENS_READ_SECRET",
    "MACROLENS_READ_PREVIOUS_SECRET",
    "MACROLENS_ACTION_KEY_ID",
    "MACROLENS_ACTION_SECRET",
    "MACROLENS_ACTION_PREVIOUS_SECRET",
    "MACROLENS_ACTION_NONCE_TTL_SECONDS",
    "MACROLENS_ACTION_CLOCK_SKEW_SECONDS",
    "MACROLENS_FOCUS_KEY_ID",
    "MACROLENS_FOCUS_SECRET",
    "MACROLENS_FOCUS_PREVIOUS_SECRET",
    "MACROLENS_FOCUS_NONCE_TTL_SECONDS",
    "MACROLENS_FOCUS_CLOCK_SKEW_SECONDS",
    "MACROLENS_FOCUS_ALLOWED_CIDRS",
    "MACROLENS_FOCUS_TRUSTED_PROXY_CIDRS",
}
DEPRECATED_KEYS = {
    "PUBLIC_READ_API_ENABLED",
    "ALLOW_INSECURE_PUBLIC_BIND",
    "DEPLOY_REQUIRE_AI",
    "DEPLOY_REQUIRE_CATALYST",
    "DEPLOY_REQUIRE_CATALYST_ACTIONS",
    "DEPLOY_REQUIRE_FOCUS_PRODUCER",
}
BEHAVIOR_KEYS = {
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
    "OPENAI_DAILY_BUDGET_USD",
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


@dataclass(frozen=True)
class LegacyMigration:
    config: PersonalConfig
    secrets: dict[str, str]
    machine: dict[str, str]
    mapped_keys: tuple[str, ...]
    deprecated_keys: tuple[str, ...]
    removed_keys: tuple[str, ...]
    conflicting_keys: tuple[str, ...]
    unmapped_keys: tuple[str, ...]
    requires_owner_password: bool
    warnings: tuple[str, ...]


class LegacyMigrationConflict(ValueError):
    def __init__(
        self,
        *,
        mapped_keys: tuple[str, ...],
        deprecated_keys: tuple[str, ...],
        removed_keys: tuple[str, ...],
        conflicting_keys: tuple[str, ...],
        unmapped_keys: tuple[str, ...],
        requires_owner_password: bool,
        warning_messages: tuple[str, ...],
    ) -> None:
        super().__init__(
            "conflicting legacy aliases: " + ", ".join(conflicting_keys)
        )
        self.mapped_keys = mapped_keys
        self.deprecated_keys = deprecated_keys
        self.removed_keys = removed_keys
        self.conflicting_keys = conflicting_keys
        self.unmapped_keys = unmapped_keys
        self.requires_owner_password = requires_owner_password
        self.warning_messages = warning_messages


def _present(values: Mapping[str, str], key: str) -> bool:
    return bool(str(values.get(key, "")).strip())


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


def _number(values: Mapping[str, str], key: str, default: float) -> float:
    raw = str(values.get(key, "")).strip()
    return float(raw) if raw else default


def _access_mode(values: Mapping[str, str]) -> str:
    explicit = str(values.get("ACCESS_MODE", "")).strip().lower()
    if explicit:
        if explicit not in {"private_network", "password"}:
            raise ValueError("ACCESS_MODE must be private_network or password")
        return explicit
    if _present(values, "APP_PASSWORD_HASH") or _present(values, "APP_AUTH_TOKEN"):
        return "password"
    return "private_network"


def _catalyst_mode(values: Mapping[str, str], defaults: PersonalConfig) -> str:
    if _boolean(str(values.get("HOT_CYCLE_SCHEDULE_ENABLED", "")), default=False):
        return "scheduled"
    manual = _boolean(
        str(values.get("HOT_CYCLE_MANUAL_ENABLED", "")), default=False
    ) or _boolean(str(values.get("NEWS_LLM_MANUAL_ENABLED", "")), default=False)
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


def _range_mode(values: Mapping[str, str], defaults: PersonalConfig) -> str:
    raw = str(
        values.get("RANGE_PERSISTENCE_MODE", defaults.breakout.range_persistence_mode)
    ).strip().lower()
    aliases = {"disabled": "off", "enabled": "active"}
    return aliases.get(raw, raw)


def _inventory(values: Mapping[str, str]) -> dict[str, Any]:
    populated = {key for key in values if key and _present(values, key)}
    conflicting: set[str] = set()
    for legacy, canonical in ALIASES.items():
        if (
            legacy in populated
            and canonical in populated
            and str(values[legacy]).strip() != str(values[canonical]).strip()
        ):
            conflicting.update((legacy, canonical))
    mapped_set = populated & (SECRET_KEYS | MACHINE_KEYS | BEHAVIOR_KEYS | set(ALIASES))
    deprecated_set = populated & (DEPRECATED_KEYS | set(ALIASES))
    removed_set = populated & REMOVED_KEYS
    recognized = (
        SECRET_KEYS
        | MACHINE_KEYS
        | BEHAVIOR_KEYS
        | set(ALIASES)
        | DEPRECATED_KEYS
        | REMOVED_KEYS
    )
    requires_owner_password = bool(
        _present(values, "APP_AUTH_TOKEN")
        and not _present(values, "APP_PASSWORD_HASH")
    )
    warning_messages = [
        "Legacy environment behavior settings are deprecated and will be removed "
        f"in {LEGACY_RELEASE_DEADLINE}."
    ]
    if requires_owner_password:
        warning_messages.append(
            "APP_AUTH_TOKEN was removed; configure APP_PASSWORD_HASH before startup."
        )
    return {
        "mapped_keys": tuple(sorted(mapped_set)),
        "deprecated_keys": tuple(sorted(deprecated_set)),
        "removed_keys": tuple(sorted(removed_set)),
        "conflicting_keys": tuple(sorted(conflicting)),
        "unmapped_keys": tuple(sorted(populated - recognized)),
        "requires_owner_password": requires_owner_password,
        "warnings": tuple(warning_messages),
    }


def _canonical_value(
    values: Mapping[str, str],
    canonical: str,
    legacy: str | None = None,
) -> str:
    value = str(values.get(canonical, "")).strip()
    if value:
        return value
    return str(values.get(legacy, "")).strip() if legacy else ""


def migrate_legacy_environment(values: Mapping[str, str]) -> LegacyMigration:
    from app.personal_config import PersonalConfig

    inventory = _inventory(values)
    if inventory["conflicting_keys"]:
        raise LegacyMigrationConflict(
            mapped_keys=inventory["mapped_keys"],
            deprecated_keys=inventory["deprecated_keys"],
            removed_keys=inventory["removed_keys"],
            conflicting_keys=inventory["conflicting_keys"],
            unmapped_keys=inventory["unmapped_keys"],
            requires_owner_password=inventory["requires_owner_password"],
            warning_messages=inventory["warnings"],
        )

    defaults = PersonalConfig()
    payload: dict[str, Any] = {
        "access": {
            "mode": _access_mode(values),
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
            "model": defaults.ai.model,
            "reasoning": "max",
            "max_concurrency": 1,
            "daily_max_jobs": _integer(
                values, "OPENAI_DAILY_MAX_JOBS", defaults.ai.daily_max_jobs
            ),
            "daily_budget_usd": _number(
                values,
                "OPENAI_DAILY_BUDGET_USD",
                defaults.ai.daily_budget_usd,
            ),
            "daily_token_limit": defaults.ai.daily_token_limit,
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
            "manual_force_reanalysis": defaults.catalyst.manual_force_reanalysis,
            "manual_refresh_cooldown_seconds": (
                defaults.catalyst.manual_refresh_cooldown_seconds
            ),
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
            "range_persistence_mode": _range_mode(values, defaults),
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
        key: value
        for key, value in {
            "OPENAI_API_KEY": _canonical_value(values, "OPENAI_API_KEY"),
            "FINNHUB_API_KEY": _canonical_value(values, "FINNHUB_API_KEY"),
            "MARKETDATA_TOKEN": _canonical_value(
                values, "MARKETDATA_TOKEN", "MARKETDATA_API_TOKEN"
            ),
            "INTERNAL_API_TOKEN": _canonical_value(
                values, "INTERNAL_API_TOKEN", "MACROLENS_INTERNAL_TOKEN"
            ),
            "APP_PASSWORD_HASH": _canonical_value(values, "APP_PASSWORD_HASH"),
        }.items()
        if value
    }
    machine = {
        key: value
        for key, value in {
            "HOST_BIND": _canonical_value(values, "HOST_BIND"),
            "PORT": _canonical_value(values, "PORT"),
            "MACROLENS_URL": _canonical_value(
                values, "MACROLENS_URL", "MACROLENS_BASE_URL"
            ),
            "ALLOWED_HOSTS": _canonical_value(values, "ALLOWED_HOSTS"),
            "TRUST_PROXY_HEADERS": _canonical_value(values, "TRUST_PROXY_HEADERS"),
            "TRUSTED_PROXY_CIDRS": _canonical_value(values, "TRUSTED_PROXY_CIDRS"),
            "DATA_DIR": _canonical_value(values, "DATA_DIR"),
        }.items()
        if value
    }
    for message in inventory["warnings"]:
        warnings.warn(message, DeprecationWarning, stacklevel=2)
    return LegacyMigration(
        config=config,
        secrets=secrets,
        machine=machine,
        mapped_keys=inventory["mapped_keys"],
        deprecated_keys=inventory["deprecated_keys"],
        removed_keys=inventory["removed_keys"],
        conflicting_keys=inventory["conflicting_keys"],
        unmapped_keys=inventory["unmapped_keys"],
        requires_owner_password=inventory["requires_owner_password"],
        warnings=inventory["warnings"],
    )


__all__ = [
    "ALIASES",
    "LegacyMigration",
    "LegacyMigrationConflict",
    "MACHINE_KEYS",
    "REMOVED_KEYS",
    "SECRET_KEYS",
    "migrate_legacy_environment",
]
