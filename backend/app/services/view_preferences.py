"""Per-principal view preferences for optional production algorithms.

Explicit saved choices survive admin default changes. ``follow_default``
means the request should take the current admin/system default.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.data_paths import get_data_paths
from app.services.algorithm_modes import (
    A0_ALGORITHM,
    FOLLOW_DEFAULT,
    PRODUCTION_ALGORITHM,
    T1_ALGORITHM,
    canonicalize_radar_algorithm,
    canonicalize_screener_algorithm,
)


_MAX_DOCUMENT_BYTES = 64 * 1024


@dataclass(frozen=True)
class ViewPreferences:
    screener_ranking_algorithm: str = FOLLOW_DEFAULT
    radar_sort_algorithm: str = FOLLOW_DEFAULT

    def as_dict(self) -> dict[str, str]:
        return {
            "screener_ranking_algorithm": self.screener_ranking_algorithm,
            "radar_sort_algorithm": self.radar_sort_algorithm,
        }


def default_view_preferences() -> ViewPreferences:
    return ViewPreferences()


def _normalize_choice(family: str, value: Any) -> str:
    if family == "screener":
        parsed = canonicalize_screener_algorithm(value, allow_follow=True)
    else:
        parsed = canonicalize_radar_algorithm(value, allow_follow=True)
    return parsed or FOLLOW_DEFAULT


def normalize_view_preferences(value: Any) -> ViewPreferences:
    payload = value if isinstance(value, dict) else {}
    return ViewPreferences(
        screener_ranking_algorithm=_normalize_choice(
            "screener",
            payload.get("screener_ranking_algorithm"),
        ),
        radar_sort_algorithm=_normalize_choice(
            "radar",
            payload.get("radar_sort_algorithm"),
        ),
    )


class ViewPreferenceStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or get_data_paths().root / "view-preferences.json"

    def _read_document(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return {"version": 1, "principals": {}}
        except OSError:
            return {"version": 1, "principals": {}}
        if len(raw) > _MAX_DOCUMENT_BYTES:
            return {"version": 1, "principals": {}}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            return {"version": 1, "principals": {}}
        if not isinstance(payload, dict):
            return {"version": 1, "principals": {}}
        principals = payload.get("principals")
        if not isinstance(principals, dict):
            principals = {}
        return {"version": 1, "principals": principals}

    def read(self, principal: str) -> ViewPreferences:
        key = str(principal or "").strip()
        if not key:
            return default_view_preferences()
        document = self._read_document()
        return normalize_view_preferences(document["principals"].get(key))

    def write(self, principal: str, preferences: ViewPreferences) -> ViewPreferences:
        key = str(principal or "").strip()
        if not key:
            raise ValueError("view preference principal is required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = self._read_document()
        principals = dict(document.get("principals") or {})
        principals[key] = {
            **preferences.as_dict(),
            "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        payload = {"version": 1, "principals": principals}
        encoded = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        fd, tmp = tempfile.mkstemp(prefix="view-preferences.", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return preferences


def get_view_preference_store() -> ViewPreferenceStore:
    return ViewPreferenceStore()


def principal_for_request(*, is_owner: bool, account_id: str | None) -> str | None:
    if is_owner:
        return "owner"
    if account_id:
        return f"account:{account_id}"
    return None


# Keep unused imports referenced for tests that patch algorithm IDs.
_ = (PRODUCTION_ALGORITHM, A0_ALGORITHM, T1_ALGORITHM)
