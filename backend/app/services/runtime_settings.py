"""Versioned storage for owner-editable, non-secret runtime settings.

The document intentionally contains a small allowlist of operational values.
Credentials, access controls, provider endpoints, and other secrets remain in
their existing environment-backed configuration and cannot enter this store.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
)

from app.data_paths import get_data_paths
from app.personal_config import (
    HOURLY_ANALYSIS_TIMES_ET,
    PersonalConfig,
    get_personal_config,
)


_MAX_DOCUMENT_BYTES = 256 * 1024
_BASELINE_UPDATED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
_LEGACY_CATALYST_ACTION_FIELDS = frozenset(
    {"manual_force_reanalysis", "manual_refresh_enabled"}
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


def _normalize_scheduled_times(values: Sequence[str]) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("scheduled times must use HH:MM")
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("scheduled times must be valid clock times")
        item = f"{hour:02d}:{minute:02d}"
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


class RuntimeAISettingsV1(_StrictModel):
    """The exact AI settings accepted by the previous release."""

    daily_max_jobs: int = Field(default=4, ge=1, le=4)
    daily_budget_usd: float = Field(
        default=2.0,
        ge=0.01,
        le=100,
        multiple_of=0.01,
    )
    manual_analysis_enabled: bool = False
    manual_analysis_cooldown_seconds: int = Field(default=30, ge=0, le=86_400)


class RuntimeCatalystSettingsV1(_StrictModel):
    """The exact Catalyst settings accepted by the previous release."""

    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
    manual_refresh_cooldown_seconds: int = Field(default=30, ge=0, le=3_600)
    scheduled_analysis_enabled: bool = False
    scheduled_times_et: tuple[str, ...] = Field(
        default=("08:00", "12:00", "16:00"),
        min_length=1,
        max_length=8,
    )

    @field_validator("scheduled_times_et")
    @classmethod
    def validate_scheduled_times(cls, values: Sequence[str]) -> tuple[str, ...]:
        return _normalize_scheduled_times(values)


class RuntimeSettingsV1(_StrictModel):
    ai: RuntimeAISettingsV1 = Field(default_factory=RuntimeAISettingsV1)
    catalyst: RuntimeCatalystSettingsV1 = Field(
        default_factory=RuntimeCatalystSettingsV1
    )


class RuntimeAISettings(_StrictModel):
    # Compatibility fields: zero disables the former count and dollar gates.
    daily_max_jobs: int = Field(default=0, ge=0, le=100_000)
    daily_budget_usd: float = Field(default=0.0, ge=0, le=10_000, multiple_of=0.01)
    daily_token_limit: int = Field(
        default=10_000_000,
        ge=102_400,
        le=100_000_000,
    )
    manual_analysis_enabled: bool = False
    manual_analysis_cooldown_seconds: int = Field(default=30, ge=0, le=86_400)


class RuntimeCatalystSettings(_StrictModel):
    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
    manual_refresh_cooldown_seconds: int = Field(default=30, ge=0, le=3_600)
    scheduled_analysis_enabled: bool = False
    scheduled_times_et: tuple[str, ...] = Field(
        default=HOURLY_ANALYSIS_TIMES_ET,
        min_length=1,
        max_length=24,
    )

    @field_validator("scheduled_times_et")
    @classmethod
    def validate_scheduled_times(cls, values: Sequence[str]) -> tuple[str, ...]:
        return _normalize_scheduled_times(values)


class RuntimeSettings(_StrictModel):
    ai: RuntimeAISettings = Field(default_factory=RuntimeAISettings)
    catalyst: RuntimeCatalystSettings = Field(default_factory=RuntimeCatalystSettings)


class RuntimeAISettingsPatch(_StrictModel):
    daily_max_jobs: Optional[int] = Field(default=None, ge=0, le=100_000)
    daily_budget_usd: Optional[float] = Field(
        default=None,
        ge=0,
        le=10_000,
        multiple_of=0.01,
    )
    daily_token_limit: Optional[int] = Field(
        default=None,
        ge=102_400,
        le=100_000_000,
    )
    manual_analysis_enabled: Optional[bool] = None
    manual_analysis_cooldown_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=86_400,
    )


class RuntimeCatalystSettingsPatch(_StrictModel):
    sync_seconds: Optional[int] = Field(default=None, ge=30, le=86_400)
    focus_seconds: Optional[int] = Field(default=None, ge=300, le=86_400)
    manual_refresh_cooldown_seconds: Optional[int] = Field(
        default=None,
        ge=0,
        le=3_600,
    )
    scheduled_analysis_enabled: Optional[bool] = None
    scheduled_times_et: Optional[tuple[str, ...]] = Field(
        default=None,
        min_length=1,
        max_length=24,
    )

    @field_validator("scheduled_times_et")
    @classmethod
    def validate_scheduled_times(
        cls,
        values: Optional[Sequence[str]],
    ) -> Optional[tuple[str, ...]]:
        if values is None:
            return None
        return _normalize_scheduled_times(values)


class RuntimeSettingsPatch(_StrictModel):
    ai: Optional[RuntimeAISettingsPatch] = None
    catalyst: Optional[RuntimeCatalystSettingsPatch] = None


class RuntimeSettingsDocument(_StrictModel):
    schema_version: Literal[2] = 2
    version: int = Field(ge=1)
    updated_at: AwareDatetime
    settings: RuntimeSettings


class RuntimeSettingsDocumentV1(_StrictModel):
    """Schema-v1 document retained solely for forward migration and recovery."""

    schema_version: Literal[1] = 1
    version: int = Field(ge=1)
    updated_at: AwareDatetime
    settings: RuntimeSettingsV1


class RuntimeSettingsRevision(_StrictModel):
    version: int = Field(ge=1)
    updated_at: AwareDatetime
    current: bool


class RuntimeSettingsError(RuntimeError):
    """Base error that is safe to translate to a generic API response."""


class RuntimeSettingsStorageError(RuntimeSettingsError):
    pass


class RuntimeSettingsValidationError(RuntimeSettingsError):
    pass


class RuntimeSettingsVersionConflict(RuntimeSettingsError):
    def __init__(self, current_version: int) -> None:
        super().__init__("runtime settings version conflict")
        self.current_version = current_version


class RuntimeSettingsRevisionNotFound(RuntimeSettingsError):
    pass


def runtime_settings_from_personal_config(config: PersonalConfig) -> RuntimeSettings:
    """Build safe defaults from the checked-in personal configuration."""

    return RuntimeSettings(
        ai=RuntimeAISettings(
            daily_max_jobs=config.ai.daily_max_jobs,
            daily_budget_usd=config.ai.daily_budget_usd,
            daily_token_limit=config.ai.daily_token_limit,
            manual_analysis_enabled=config.catalyst_manual_enabled,
        ),
        catalyst=RuntimeCatalystSettings(
            sync_seconds=config.catalyst.sync_seconds,
            focus_seconds=config.catalyst.focus_seconds,
            manual_refresh_cooldown_seconds=(
                config.catalyst.manual_refresh_cooldown_seconds
            ),
            scheduled_analysis_enabled=config.catalyst_scheduled_enabled,
            scheduled_times_et=tuple(config.catalyst.scheduled_times_et),
        ),
    )


def _merge_patch(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            base[key] = _merge_patch(dict(base[key]), value)
        else:
            base[key] = value
    return base


def _drop_legacy_catalyst_action_fields(payload: Any) -> Any:
    """Accept old persisted documents without reviving removed action gates."""

    if not isinstance(payload, dict):
        return payload
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        return payload
    catalyst = settings.get("catalyst")
    if not isinstance(catalyst, dict) or not any(
        field in catalyst for field in _LEGACY_CATALYST_ACTION_FIELDS
    ):
        return payload

    migrated = dict(payload)
    migrated_settings = dict(settings)
    migrated_catalyst = dict(catalyst)
    for field in _LEGACY_CATALYST_ACTION_FIELDS:
        migrated_catalyst.pop(field, None)
    migrated_settings["catalyst"] = migrated_catalyst
    migrated["settings"] = migrated_settings
    return migrated


def _migrate_v1_document(
    document: RuntimeSettingsDocumentV1,
) -> RuntimeSettingsDocument:
    """Convert v1 limits to the Token policy without changing owner switches."""

    legacy_ai = document.settings.ai
    legacy_catalyst = document.settings.catalyst
    return RuntimeSettingsDocument(
        version=document.version,
        updated_at=document.updated_at,
        settings=RuntimeSettings(
            ai=RuntimeAISettings(
                daily_max_jobs=0,
                daily_budget_usd=0.0,
                daily_token_limit=10_000_000,
                manual_analysis_enabled=legacy_ai.manual_analysis_enabled,
                manual_analysis_cooldown_seconds=(
                    legacy_ai.manual_analysis_cooldown_seconds
                ),
            ),
            catalyst=RuntimeCatalystSettings(
                sync_seconds=legacy_catalyst.sync_seconds,
                focus_seconds=legacy_catalyst.focus_seconds,
                manual_refresh_cooldown_seconds=(
                    legacy_catalyst.manual_refresh_cooldown_seconds
                ),
                scheduled_analysis_enabled=(
                    legacy_catalyst.scheduled_analysis_enabled
                ),
                scheduled_times_et=legacy_catalyst.scheduled_times_et,
            ),
        ),
    )


class RuntimeSettingsStore:
    """Atomic JSON store with optimistic versions and bounded backups."""

    def __init__(
        self,
        path: Path,
        *,
        defaults: RuntimeSettings | None = None,
        backup_keep: int = 7,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 1 <= backup_keep <= 100:
            raise ValueError("backup_keep must be between 1 and 100")
        self.path = path.expanduser().resolve()
        self.backup_dir = self.path.parent / f".{self.path.name}.backups"
        self.lock_path = self.path.parent / f".{self.path.name}.lock"
        self.pre_v2_snapshot_path = self.path.parent / f"{self.path.name}.pre-v2"
        self.defaults = defaults or RuntimeSettings()
        self.backup_keep = backup_keep
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise RuntimeSettingsStorageError("runtime settings clock must be timezone-aware")
        return value.astimezone(timezone.utc)

    def _default_document(self) -> RuntimeSettingsDocument:
        return RuntimeSettingsDocument(
            version=1,
            # Version 1 is a deterministic baseline, not a user write. Keeping
            # its timestamp stable also makes first-write crash recovery safe
            # across multiple web or worker processes.
            updated_at=_BASELINE_UPDATED_AT,
            settings=self.defaults,
        )

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            stream = self.lock_path.open("a+b")
            os.chmod(self.lock_path, 0o600)
        except OSError as exc:
            raise RuntimeSettingsStorageError("runtime settings lock cannot be opened") from exc
        try:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            except OSError as exc:
                raise RuntimeSettingsStorageError(
                    "runtime settings lock cannot be acquired"
                ) from exc
            yield
        finally:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
            finally:
                stream.close()

    def _read_bounded_bytes(self, path: Path) -> bytes:
        try:
            if path.stat().st_size > _MAX_DOCUMENT_BYTES:
                raise RuntimeSettingsStorageError("runtime settings document is too large")
            raw = path.read_bytes()
            if len(raw) > _MAX_DOCUMENT_BYTES:
                raise RuntimeSettingsStorageError("runtime settings document is too large")
            return raw
        except RuntimeSettingsStorageError:
            raise
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings document cannot be read"
            ) from exc

    def _read_document_with_source(
        self,
        path: Path,
    ) -> tuple[RuntimeSettingsDocument, Literal[1, 2], bytes]:
        try:
            raw = self._read_bounded_bytes(path)
            payload = json.loads(raw)
            payload = _drop_legacy_catalyst_action_fields(payload)
            schema_version = (
                payload.get("schema_version")
                if isinstance(payload, dict)
                else None
            )
            if schema_version == 1:
                legacy = RuntimeSettingsDocumentV1.model_validate(payload)
                return _migrate_v1_document(legacy), 1, raw
            if schema_version == 2:
                return RuntimeSettingsDocument.model_validate(payload), 2, raw
            raise ValueError("unsupported runtime settings schema")
        except RuntimeSettingsStorageError:
            raise
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            ValidationError,
            ValueError,
        ) as exc:
            raise RuntimeSettingsStorageError("runtime settings document is invalid") from exc

    def _read_document(self, path: Path) -> RuntimeSettingsDocument:
        document, _source_schema, _raw = self._read_document_with_source(path)
        return document

    def _path_exists(self, path: Path) -> bool:
        try:
            path.stat()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings path cannot be inspected"
            ) from exc

    def _read_current_unlocked(self) -> RuntimeSettingsDocument:
        if not self._path_exists(self.path):
            return self._default_document()
        return self._read_document(self.path)

    def _read_current_with_source_unlocked(
        self,
    ) -> tuple[RuntimeSettingsDocument, Literal[1, 2], bytes | None]:
        if not self._path_exists(self.path):
            return self._default_document(), 2, None
        return self._read_document_with_source(self.path)

    def read(self) -> RuntimeSettingsDocument:
        # os.replace makes the current document atomic for lock-free readers.
        return self._read_current_unlocked()

    def _fsync_directory(self, directory: Path) -> None:
        try:
            descriptor = os.open(directory, os.O_RDONLY)
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings directory cannot be opened"
            ) from exc
        try:
            os.fsync(descriptor)
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings directory cannot be synced"
            ) from exc
        finally:
            os.close(descriptor)

    def _atomic_write_bytes(self, path: Path, content: bytes) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.",
                suffix=".tmp",
                dir=path.parent,
            )
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings temporary file cannot be created"
            ) from exc

        temporary_path = Path(temporary_name)
        descriptor_open = True
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor_open = False
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
            self._fsync_directory(path.parent)
        except OSError as exc:
            raise RuntimeSettingsStorageError(
                "runtime settings document cannot be written"
            ) from exc
        finally:
            if descriptor_open:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _atomic_write(self, path: Path, document: RuntimeSettingsDocument) -> None:
        serialized = json.dumps(
            document.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8") + b"\n"
        self._atomic_write_bytes(path, serialized)

    def _preserve_pre_v2_snapshot(self, raw_v1: bytes) -> None:
        """Create the immutable, byte-for-byte v1 recovery snapshot once."""

        if self._path_exists(self.pre_v2_snapshot_path):
            existing = self._read_bounded_bytes(self.pre_v2_snapshot_path)
            if existing != raw_v1:
                raise RuntimeSettingsStorageError(
                    "runtime settings pre-v2 snapshot collision"
                )
            try:
                os.chmod(self.pre_v2_snapshot_path, 0o600)
            except OSError as exc:
                raise RuntimeSettingsStorageError(
                    "runtime settings pre-v2 snapshot permissions cannot be secured"
                ) from exc
            return
        self._atomic_write_bytes(self.pre_v2_snapshot_path, raw_v1)

    def restore_pre_v2_snapshot(self) -> RuntimeSettingsDocumentV1:
        """Atomically restore the original v1 bytes before an application rollback.

        Call this after stopping writers and before starting a schema-v1 release.
        Schema-v2 history files remain available in ``backup_dir`` for recovery,
        but an old release must not use its history endpoint until that directory
        has been moved aside.
        """

        with self._exclusive_lock():
            if not self._path_exists(self.pre_v2_snapshot_path):
                raise RuntimeSettingsRevisionNotFound(
                    "runtime settings pre-v2 snapshot not found"
                )
            raw = self._read_bounded_bytes(self.pre_v2_snapshot_path)
            try:
                payload = json.loads(raw)
                payload = _drop_legacy_catalyst_action_fields(payload)
                legacy = RuntimeSettingsDocumentV1.model_validate(payload)
            except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
                raise RuntimeSettingsStorageError(
                    "runtime settings pre-v2 snapshot is invalid"
                ) from exc
            self._atomic_write_bytes(self.path, raw)
            return legacy

    def _backup_path(self, version: int) -> Path:
        return self.backup_dir / f"runtime-settings.v{version}.json"

    def _archive(self, document: RuntimeSettingsDocument) -> None:
        destination = self._backup_path(document.version)
        if self._path_exists(destination):
            archived = self._read_document(destination)
            if archived != document:
                raise RuntimeSettingsStorageError("runtime settings backup version collision")
            return
        self._atomic_write(destination, document)

    def _backup_documents_unlocked(self) -> list[RuntimeSettingsDocument]:
        if not self._path_exists(self.backup_dir):
            return []
        documents = [
            self._read_document(path)
            for path in self.backup_dir.glob("runtime-settings.v*.json")
            if path.is_file()
        ]
        versions = [item.version for item in documents]
        if len(versions) != len(set(versions)):
            raise RuntimeSettingsStorageError("runtime settings backup versions are duplicated")
        return sorted(documents, key=lambda item: item.version, reverse=True)

    def _prune_backups(self) -> None:
        for document in self._backup_documents_unlocked()[self.backup_keep :]:
            try:
                self._backup_path(document.version).unlink()
            except OSError as exc:
                raise RuntimeSettingsStorageError(
                    "runtime settings backup cannot be pruned"
                ) from exc
        if self._path_exists(self.backup_dir):
            self._fsync_directory(self.backup_dir)

    def history(self) -> list[RuntimeSettingsRevision]:
        with self._exclusive_lock():
            current = self._read_current_unlocked()
            revisions = [
                RuntimeSettingsRevision(
                    version=current.version,
                    updated_at=current.updated_at,
                    current=True,
                )
            ]
            revisions.extend(
                RuntimeSettingsRevision(
                    version=document.version,
                    updated_at=document.updated_at,
                    current=False,
                )
                for document in self._backup_documents_unlocked()
                if document.version != current.version
            )
            return sorted(revisions, key=lambda item: item.version, reverse=True)

    def update(
        self,
        patch: RuntimeSettingsPatch,
        *,
        expected_version: int,
    ) -> RuntimeSettingsDocument:
        with self._exclusive_lock():
            current, source_schema, raw_current = (
                self._read_current_with_source_unlocked()
            )
            if expected_version != current.version:
                raise RuntimeSettingsVersionConflict(current.version)

            patch_payload = patch.model_dump(exclude_unset=True, mode="python")
            if not patch_payload:
                return current
            merged = _merge_patch(
                current.settings.model_dump(mode="python"),
                patch_payload,
            )
            try:
                settings = RuntimeSettings.model_validate(merged)
            except ValidationError as exc:
                raise RuntimeSettingsValidationError("runtime settings patch is invalid") from exc
            if settings == current.settings:
                return current

            updated = RuntimeSettingsDocument(
                version=current.version + 1,
                updated_at=self._now(),
                settings=settings,
            )
            if source_schema == 1:
                if raw_current is None:  # pragma: no cover - defensive invariant
                    raise RuntimeSettingsStorageError(
                        "runtime settings v1 source bytes are unavailable"
                    )
                self._preserve_pre_v2_snapshot(raw_current)
            self._archive(current)
            self._atomic_write(self.path, updated)
            self._prune_backups()
            return updated

    def rollback(
        self,
        target_version: int,
        *,
        expected_version: int,
    ) -> RuntimeSettingsDocument:
        with self._exclusive_lock():
            current, source_schema, raw_current = (
                self._read_current_with_source_unlocked()
            )
            if expected_version != current.version:
                raise RuntimeSettingsVersionConflict(current.version)
            if target_version >= current.version:
                raise RuntimeSettingsRevisionNotFound("rollback target must be older")

            target_path = self._backup_path(target_version)
            if not self._path_exists(target_path):
                raise RuntimeSettingsRevisionNotFound("runtime settings revision not found")
            target = self._read_document(target_path)
            if target.version != target_version:
                raise RuntimeSettingsStorageError("runtime settings backup version mismatch")

            restored = RuntimeSettingsDocument(
                version=current.version + 1,
                updated_at=self._now(),
                settings=target.settings,
            )
            if source_schema == 1:
                if raw_current is None:  # pragma: no cover - defensive invariant
                    raise RuntimeSettingsStorageError(
                        "runtime settings v1 source bytes are unavailable"
                    )
                self._preserve_pre_v2_snapshot(raw_current)
            self._archive(current)
            self._atomic_write(self.path, restored)
            self._prune_backups()
            return restored


@lru_cache(maxsize=1)
def get_runtime_settings_store() -> RuntimeSettingsStore:
    config = get_personal_config()
    return RuntimeSettingsStore(
        get_data_paths().runtime_settings,
        defaults=runtime_settings_from_personal_config(config),
        backup_keep=config.storage.backup_keep,
    )


def get_effective_runtime_settings(
    store: Optional[RuntimeSettingsStore] = None,
) -> RuntimeSettings:
    """Read the validated settings currently effective for web and workers.

    This function intentionally performs a fresh atomic read on every call so
    long-running processes observe an owner update without restarting. Invalid
    or unreadable persisted data raises ``RuntimeSettingsStorageError`` instead
    of silently re-enabling a disabled paid action.
    """

    return (store or get_runtime_settings_store()).read().settings
