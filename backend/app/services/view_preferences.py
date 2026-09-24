"""Per-principal view preferences for optional production algorithms.

Explicit saved choices survive admin default changes. ``follow_default``
means the request should take the current admin/system default.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.data_paths import get_data_paths
from app.services.algorithm_modes import (
    FOLLOW_DEFAULT,
    canonicalize_radar_algorithm,
    canonicalize_screener_algorithm,
)


# AccountStore supports 2,000 customer accounts. Allow one additional owner
# principal and enough room for every principal's two choices plus metadata.
_MAX_PRINCIPALS = 2_001
_MAX_DOCUMENT_BYTES = 1024 * 1024


class ViewPreferenceStorageError(RuntimeError):
    """The preference document could not be read or safely replaced."""


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
        self.lock_path = self.path.parent / f".{self.path.name}.lock"

    @staticmethod
    def _empty_document() -> dict[str, Any]:
        return {"version": 1, "principals": {}}

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = self.lock_path.open("a+b")
            os.chmod(self.lock_path, 0o600)
        except OSError as exc:
            raise ViewPreferenceStorageError(
                "view preference lock cannot be opened"
            ) from exc
        try:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise ViewPreferenceStorageError(
                    "view preference lock cannot be acquired"
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _read_document(self) -> dict[str, Any]:
        try:
            if self.path.stat().st_size > _MAX_DOCUMENT_BYTES:
                raise ViewPreferenceStorageError(
                    "view preference document is too large"
                )
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return self._empty_document()
        except ViewPreferenceStorageError:
            raise
        except OSError as exc:
            raise ViewPreferenceStorageError(
                "view preference document cannot be read"
            ) from exc
        if len(raw) > _MAX_DOCUMENT_BYTES:
            raise ViewPreferenceStorageError("view preference document is too large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ViewPreferenceStorageError(
                "view preference document is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise ViewPreferenceStorageError("view preference document is invalid")
        principals = payload.get("principals")
        if not isinstance(principals, dict):
            raise ViewPreferenceStorageError("view preference document is invalid")
        if len(principals) > _MAX_PRINCIPALS:
            raise ViewPreferenceStorageError(
                "view preference principal capacity exceeded"
            )
        return {"version": 1, "principals": principals}

    def read(self, principal: str) -> ViewPreferences:
        key = str(principal or "").strip()
        if not key:
            return default_view_preferences()
        document = self._read_document()
        return normalize_view_preferences(document["principals"].get(key))

    def write(self, principal: str, preferences: ViewPreferences) -> ViewPreferences:
        return self.patch(principal, preferences.as_dict())

    def patch(
        self,
        principal: str,
        updates: Mapping[str, Any],
    ) -> ViewPreferences:
        key = str(principal or "").strip()
        if not key:
            raise ValueError("view preference principal is required")
        with self._exclusive_lock():
            document = self._read_document()
            principals = dict(document["principals"])
            if key not in principals and len(principals) >= _MAX_PRINCIPALS:
                raise ViewPreferenceStorageError(
                    "view preference principal capacity exceeded"
                )
            current = normalize_view_preferences(principals.get(key)).as_dict()
            current.update(dict(updates))
            preferences = normalize_view_preferences(current)
            principals[key] = {
                **preferences.as_dict(),
                "updated_at": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            }
            payload = {"version": 1, "principals": principals}
            encoded = json.dumps(
                payload,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(encoded) > _MAX_DOCUMENT_BYTES:
                raise ViewPreferenceStorageError(
                    "view preference document is too large"
                )
            tmp: str | None = None
            try:
                fd, tmp = tempfile.mkstemp(
                    prefix="view-preferences.",
                    dir=str(self.path.parent),
                )
                with os.fdopen(fd, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(tmp, self.path)
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError as exc:
                raise ViewPreferenceStorageError(
                    "view preference document cannot be saved"
                ) from exc
            finally:
                if tmp is not None and os.path.exists(tmp):
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
