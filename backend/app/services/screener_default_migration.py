"""Idempotent admin-default migration onto eod_limited_v1.

Init-frozen ``production`` leftovers from the previous system default are
moved to the new screener default. Explicit A0 locks and a completed
rollback are left alone. User view preferences are never rewritten.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.algorithm_modes import (
    A0_ALGORITHM,
    DEFAULT_SCREENER_ALGORITHM,
    EOD_LIMITED_V1,
    PRODUCTION_ALGORITHM,
)
from app.services.runtime_settings import (
    RuntimeAlgorithmSettingsPatch,
    RuntimeSettingsPatch,
    RuntimeSettingsStore,
    RuntimeSettingsVersionConflict,
)

MIGRATION_ID = "screener_default_to_eod_limited_v1"
RECORD_NAME = "screener-default-to-eod-limited-v1.json"
ENV_DISABLE = "OPTIX_SCREENER_DEFAULT_MIGRATION"

STATUS_APPLIED = "migrated_init_frozen_production"
STATUS_ALREADY_NEW = "already_new_default"
STATUS_ADMIN_LOCKED = "admin_locked_non_default"
STATUS_DISABLED = "migration_disabled"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_ROLLBACK_SKIPPED = "rollback_skipped_admin_changed"

_SEEN_PATHS: set[str] = set()


def migration_record_path(store: RuntimeSettingsStore) -> Path:
    return store.path.parent / RECORD_NAME


def _now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_record(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    encoded = json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return record


def _disabled() -> bool:
    raw = os.environ.get(ENV_DISABLE, "1").strip().lower()
    return raw in {"0", "false", "off", "no"}


def apply_screener_default_migration(store: RuntimeSettingsStore) -> dict[str, Any]:
    """Move init-frozen admin production defaults onto the new EOD default."""

    path = migration_record_path(store)
    cache_key = str(path)
    if cache_key in _SEEN_PATHS:
        existing = _read_record(path)
        if existing is not None:
            return existing
    existing = _read_record(path)
    if existing is not None:
        _SEEN_PATHS.add(cache_key)
        return existing

    document = store.read()
    current = document.settings.algorithms.screener_ranking_algorithm
    persisted = store.path.exists()

    if _disabled():
        record = {
            "id": MIGRATION_ID,
            "status": STATUS_DISABLED,
            "from_algorithm": current,
            "to_algorithm": DEFAULT_SCREENER_ALGORITHM,
            "scope": "admin_screener_ranking_algorithm",
            "recorded_at": _now_text(),
        }
        if persisted:
            _write_record(path, record)
            _SEEN_PATHS.add(cache_key)
        return record

    if current == EOD_LIMITED_V1:
        record = {
            "id": MIGRATION_ID,
            "status": STATUS_ALREADY_NEW,
            "from_algorithm": current,
            "to_algorithm": EOD_LIMITED_V1,
            "scope": "admin_screener_ranking_algorithm",
            "recorded_at": _now_text(),
        }
        if persisted:
            _write_record(path, record)
            _SEEN_PATHS.add(cache_key)
        return record

    if current == A0_ALGORITHM:
        record = {
            "id": MIGRATION_ID,
            "status": STATUS_ADMIN_LOCKED,
            "from_algorithm": current,
            "to_algorithm": current,
            "scope": "admin_screener_ranking_algorithm",
            "recorded_at": _now_text(),
            "skip_reason": "admin_locked_a0",
        }
        _write_record(path, record)
        _SEEN_PATHS.add(cache_key)
        return record

    if current != PRODUCTION_ALGORITHM:
        record = {
            "id": MIGRATION_ID,
            "status": STATUS_ADMIN_LOCKED,
            "from_algorithm": current,
            "to_algorithm": current,
            "scope": "admin_screener_ranking_algorithm",
            "recorded_at": _now_text(),
            "skip_reason": "admin_locked_other",
        }
        _write_record(path, record)
        _SEEN_PATHS.add(cache_key)
        return record

    if not persisted:
        return {
            "id": MIGRATION_ID,
            "status": STATUS_ALREADY_NEW,
            "from_algorithm": DEFAULT_SCREENER_ALGORITHM,
            "to_algorithm": DEFAULT_SCREENER_ALGORITHM,
            "scope": "admin_screener_ranking_algorithm",
            "recorded_at": _now_text(),
            "skip_reason": "unpersisted_uses_code_default",
        }

    try:
        updated = store.update(
            RuntimeSettingsPatch(
                algorithms=RuntimeAlgorithmSettingsPatch(
                    screener_ranking_algorithm=EOD_LIMITED_V1,
                )
            ),
            expected_version=document.version,
        )
    except RuntimeSettingsVersionConflict:
        latest = store.read()
        latest_algo = latest.settings.algorithms.screener_ranking_algorithm
        if latest_algo == EOD_LIMITED_V1:
            record = {
                "id": MIGRATION_ID,
                "status": STATUS_APPLIED,
                "from_algorithm": PRODUCTION_ALGORITHM,
                "to_algorithm": EOD_LIMITED_V1,
                "scope": "admin_screener_ranking_algorithm",
                "previous_version": document.version,
                "result_version": latest.version,
                "applied_at": _now_text(),
                "note": "applied_by_peer",
            }
            _write_record(path, record)
            _SEEN_PATHS.add(cache_key)
            return record
        raise

    record = {
        "id": MIGRATION_ID,
        "status": STATUS_APPLIED,
        "from_algorithm": PRODUCTION_ALGORITHM,
        "to_algorithm": EOD_LIMITED_V1,
        "scope": "admin_screener_ranking_algorithm",
        "previous_version": document.version,
        "result_version": updated.version,
        "applied_at": _now_text(),
    }
    _write_record(path, record)
    _SEEN_PATHS.add(cache_key)
    return record


def rollback_screener_default_migration(store: RuntimeSettingsStore) -> dict[str, Any]:
    """Restore the previous admin screener default and prevent a silent re-apply."""

    path = migration_record_path(store)
    record = _read_record(path)
    if record is None or record.get("status") != STATUS_APPLIED:
        return record or {
            "id": MIGRATION_ID,
            "status": "rollback_not_applicable",
            "recorded_at": _now_text(),
        }

    document = store.read()
    current = document.settings.algorithms.screener_ranking_algorithm
    previous = str(record.get("from_algorithm") or PRODUCTION_ALGORITHM)
    if current != EOD_LIMITED_V1:
        record = {
            **record,
            "status": STATUS_ROLLBACK_SKIPPED,
            "rolled_back_at": _now_text(),
            "skip_reason": "admin_changed_after_migration",
            "current_algorithm": current,
        }
        _write_record(path, record)
        _SEEN_PATHS.add(str(path))
        return record

    restored = store.update(
        RuntimeSettingsPatch(
            algorithms=RuntimeAlgorithmSettingsPatch(
                screener_ranking_algorithm=previous,  # type: ignore[arg-type]
            )
        ),
        expected_version=document.version,
    )
    record = {
        **record,
        "status": STATUS_ROLLED_BACK,
        "rolled_back_at": _now_text(),
        "rollback_version": restored.version,
        "restored_algorithm": previous,
    }
    _write_record(path, record)
    _SEEN_PATHS.add(str(path))
    return record
