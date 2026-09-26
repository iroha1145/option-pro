from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path


_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]{1,16}$")
_HASH_CHUNK_BYTES = 1024 * 1024
_MANIFEST_SCHEMA_VERSION = 1
_MAX_MANIFEST_BYTES = 64 * 1024
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
DATABASE_SUFFIX = ".sqlite3"
# Retention tiers. Every copy of the last 24 hours stays eligible, older
# copies keep the newest one per UTC day, and copies older than a week keep
# the newest one per ISO week. ``keep`` caps each label and drops the oldest
# eligible copy first, so the disk bound stays keep x database size.
_RECENT_TIER = timedelta(hours=24)
_DAILY_TIER = timedelta(days=7)
# A copy must leave this much space for the live databases and snapshots that
# share the volume; filling the disk breaks their writes, not just the backup.
_FREE_SPACE_RESERVE_BYTES = 256 * 1024 * 1024
QUARANTINE_DIRECTORY = "quarantine"


class BackupError(RuntimeError):
    """Raised when a database cannot be backed up safely."""

    def __init__(self, message: str, *, code: str = "backup_failed") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class BackupResult:
    label: str
    source: str
    backup: str
    manifest: str
    checksum_file: str
    created_at: str
    size_bytes: int
    sha256: str
    # None for plain-file backups, which have no SQLite structure to check.
    integrity_check: str | None
    foreign_key_violations: int | None
    removed_backups: tuple[str, ...]
    quarantined: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CompleteBackup:
    backup_path: Path
    manifest_path: Path
    checksum_path: Path
    created_at: datetime


@dataclass(frozen=True)
class _ManifestBackup:
    backup_path: Path
    manifest_path: Path
    checksum_path: Path
    created_at: datetime
    expected_sha256: str


@dataclass(frozen=True)
class _PruneOutcome:
    removed: tuple[str, ...]
    quarantined: tuple[str, ...]


def _validate_label(label: str) -> str:
    if not _SAFE_LABEL.fullmatch(label):
        raise BackupError(
            "database label must contain only letters, numbers, dot, dash or underscore"
        )
    return label


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _check_database(path: Path) -> tuple[str, int]:
    # integrity_check already performs every check quick_check does; running
    # both read each multi-GB copy one extra time.
    try:
        with closing(
            sqlite3.connect(_read_only_uri(path), uri=True, timeout=30.0)
        ) as connection:
            integrity_rows = [
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            ]
            foreign_key_violations = sum(
                1 for _row in connection.execute("PRAGMA foreign_key_check")
            )
    except sqlite3.Error as exc:
        raise BackupError(f"cannot validate SQLite backup {path}: {exc}") from exc

    if integrity_rows != ["ok"]:
        raise BackupError(f"SQLite integrity_check failed: {integrity_rows!r}")
    if foreign_key_violations:
        raise BackupError(
            f"SQLite foreign_key_check found {foreign_key_violations} violation(s)"
        )
    return "ok", foreign_key_violations


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_HASH_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write_text(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@contextmanager
def _exclusive_file_lock(lock_path: Path, *, timeout_seconds: float) -> Iterator[None]:
    if timeout_seconds < 0:
        raise BackupError("lock timeout must not be negative")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BackupError(f"cannot open backup lock {lock_path}: {exc}") from exc

    acquired = False
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError as exc:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackupError(
                        f"timed out waiting for backup lock: {lock_path}",
                        code="backup_lock_timeout",
                    ) from exc
                time.sleep(min(0.05, remaining))
            except OSError as exc:
                raise BackupError(f"cannot acquire backup lock {lock_path}: {exc}") from exc
        yield
    finally:
        if acquired:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(descriptor)


@contextmanager
def _exclusive_backup_lock(
    destination: Path,
    label: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    with _exclusive_file_lock(
        destination / f".{label}.backup.lock",
        timeout_seconds=timeout_seconds,
    ):
        yield


def _retention_lock(destination: Path, *, timeout_seconds: float):
    return _exclusive_file_lock(
        destination / ".sqlite-backup-retention.lock",
        timeout_seconds=timeout_seconds,
    )


def _backup_name_pattern(label: str, suffix: str = DATABASE_SUFFIX) -> re.Pattern[str]:
    return re.compile(
        rf"^{re.escape(label)}-(?P<timestamp>\d{{8}}T\d{{6}}\.\d{{6}}Z)-"
        rf"[0-9a-f]{{8}}{re.escape(suffix)}$"
    )


def _filename_created_at(name_pattern: re.Pattern[str], backup_name: str) -> datetime | None:
    match = name_pattern.fullmatch(backup_name)
    if match is None:
        return None
    try:
        return datetime.strptime(
            match.group("timestamp"),
            "%Y%m%dT%H%M%S.%fZ",
        ).replace(tzinfo=UTC)
    except ValueError:
        return None


def _parse_created_at(raw_value: object, manifest_path: Path) -> datetime:
    if not isinstance(raw_value, str) or not raw_value.endswith("Z"):
        raise BackupError(f"invalid created_at in backup manifest {manifest_path}")
    try:
        parsed = datetime.fromisoformat(raw_value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise BackupError(
            f"invalid created_at in backup manifest {manifest_path}"
        ) from exc
    return parsed.astimezone(UTC)


def _load_manifest_backup(
    destination: Path,
    manifest_path: Path,
    *,
    label: str,
    expected_backup_name: str,
    filename_created_at: datetime,
) -> _ManifestBackup:
    try:
        manifest_size = manifest_path.stat().st_size
    except OSError as exc:
        raise BackupError(f"cannot inspect backup manifest {manifest_path}: {exc}") from exc
    if manifest_size > _MAX_MANIFEST_BYTES:
        raise BackupError(f"backup manifest is too large: {manifest_path}")

    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"invalid backup manifest {manifest_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BackupError(f"invalid backup manifest object: {manifest_path}")

    required_fields = {
        "schema_version",
        "label",
        "created_at",
        "source",
        "backup",
        "size_bytes",
        "sha256",
    }
    if not required_fields.issubset(payload):
        raise BackupError(f"backup manifest is missing required fields: {manifest_path}")
    if payload["schema_version"] != _MANIFEST_SCHEMA_VERSION:
        raise BackupError(f"unsupported backup manifest version: {manifest_path}")
    if payload["label"] != label:
        raise BackupError(f"backup manifest label does not match its filename: {manifest_path}")
    if payload["backup"] != expected_backup_name:
        raise BackupError(f"backup manifest filename does not match: {manifest_path}")
    if not isinstance(payload["source"], str) or not payload["source"]:
        raise BackupError(f"backup manifest source is invalid: {manifest_path}")
    if (
        not isinstance(payload["size_bytes"], int)
        or isinstance(payload["size_bytes"], bool)
        or payload["size_bytes"] < 0
    ):
        raise BackupError(f"backup manifest size is invalid: {manifest_path}")
    digest = payload["sha256"]
    if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
        raise BackupError(f"backup manifest checksum is invalid: {manifest_path}")

    backup_path = destination / expected_backup_name
    checksum_path = destination / f"{expected_backup_name}.sha256"
    if not backup_path.is_file():
        raise BackupError(f"backup manifest does not have its database file: {manifest_path}")
    try:
        actual_size = backup_path.stat().st_size
    except OSError as exc:
        raise BackupError(f"cannot validate backup file {backup_path}: {exc}") from exc
    if actual_size != payload["size_bytes"]:
        raise BackupError(f"backup size does not match its manifest: {backup_path}")

    created_at = _parse_created_at(payload["created_at"], manifest_path)
    if created_at != filename_created_at:
        raise BackupError(f"backup timestamp does not match its manifest: {manifest_path}")
    return _ManifestBackup(
        backup_path=backup_path,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        created_at=created_at,
        expected_sha256=digest,
    )


def _load_complete_backup(
    destination: Path,
    manifest_path: Path,
    *,
    label: str,
    expected_backup_name: str,
    filename_created_at: datetime,
) -> _CompleteBackup:
    manifest_backup = _load_manifest_backup(
        destination,
        manifest_path,
        label=label,
        expected_backup_name=expected_backup_name,
        filename_created_at=filename_created_at,
    )
    if not manifest_backup.checksum_path.is_file():
        raise BackupError(
            f"backup manifest does not have its checksum file: {manifest_path}"
        )
    try:
        checksum_text = manifest_backup.checksum_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise BackupError(
            f"cannot validate backup checksum {manifest_backup.checksum_path}: {exc}"
        ) from exc
    if checksum_text != (
        f"{manifest_backup.expected_sha256}  {expected_backup_name}\n"
    ):
        raise BackupError(
            "backup checksum file does not match its manifest: "
            f"{manifest_backup.checksum_path}"
        )
    return _CompleteBackup(
        backup_path=manifest_backup.backup_path,
        manifest_path=manifest_backup.manifest_path,
        checksum_path=manifest_backup.checksum_path,
        created_at=manifest_backup.created_at,
    )


def _retained_backups(
    backups: Sequence[_CompleteBackup],
    *,
    reference: datetime,
    keep: int,
    protect: str | None = None,
) -> list[_CompleteBackup]:
    eligible: list[_CompleteBackup] = []
    seen_days: set[object] = set()
    seen_weeks: set[object] = set()
    # The copy just published comes first: after the clock steps back, older
    # copies can carry later timestamps and would otherwise push it out.
    for item in sorted(
        backups,
        key=lambda value: (
            value.backup_path.name == protect,
            value.created_at,
            value.backup_path.name,
        ),
        reverse=True,
    ):
        age = reference - item.created_at
        if age < _RECENT_TIER:
            eligible.append(item)
            continue
        bucket, seen = (
            (item.created_at.date(), seen_days)
            if age < _DAILY_TIER
            else (item.created_at.isocalendar()[:2], seen_weeks)
        )
        if bucket in seen:
            continue
        seen.add(bucket)
        eligible.append(item)
    return eligible[:keep]


_PENDING_NAME = ".pending-copy"


def _room_for_pending(
    completed: Sequence[_CompleteBackup],
    *,
    pending: datetime,
    keep: int,
) -> list[_CompleteBackup]:
    """Copies to delete so one copy made at ``pending`` fits under ``keep``.

    Only copies the post-publish pass would drop anyway are chosen, and never
    more than the one free slot needs. Once a failed attempt has made room,
    later attempts delete nothing, so a streak of failures cannot thin the
    history that still has no replacement.
    """

    budget = max(0, len(completed) - max(keep - 1, 1))
    if not budget:
        return []
    placeholder = _CompleteBackup(
        backup_path=Path(_PENDING_NAME),
        manifest_path=Path(_PENDING_NAME),
        checksum_path=Path(_PENDING_NAME),
        created_at=pending,
    )
    survivors = {
        item.backup_path.name
        for item in _retained_backups(
            [*completed, placeholder],
            reference=pending,
            keep=keep,
            protect=_PENDING_NAME,
        )
    }
    dropped = sorted(
        (item for item in completed if item.backup_path.name not in survivors),
        key=lambda item: (item.created_at, item.backup_path.name),
    )
    return dropped[:budget]


def _quarantine(destination: Path, paths: Sequence[Path]) -> tuple[str, ...]:
    if not paths:
        return ()
    target = destination / QUARANTINE_DIRECTORY
    moved: list[str] = []
    try:
        target.mkdir(mode=0o700, exist_ok=True)
        for path in paths:
            try:
                os.replace(path, target / path.name)
            except FileNotFoundError:
                continue
            moved.append(path.name)
    except OSError as exc:
        raise BackupError(
            f"cannot quarantine orphaned backup metadata in {destination}: {exc}"
        ) from exc
    return tuple(sorted(moved))


def _prune_backups_locked(
    destination: Path,
    label: str,
    keep: int,
    *,
    suffix: str = DATABASE_SUFFIX,
    reference: datetime | None = None,
    protect: str | None = None,
    pending: datetime | None = None,
) -> _PruneOutcome:
    """Validate the complete target-label inventory before changing any files.

    ``reference`` anchors the retention tiers; it defaults to the newest
    complete copy, so time passing without new backups never thins history.
    ``protect`` names a copy that always survives, the one just published.
    ``pending`` only makes room for a copy about to be created at that time.
    """

    name_pattern = _backup_name_pattern(label, suffix)
    manifest_suffix = f"{suffix}.json"
    checksum_suffix = f"{suffix}.sha256"
    related_paths: dict[str, dict[str, Path]] = {}
    for path in destination.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith(manifest_suffix):
            backup_name = path.name.removesuffix(".json")
            kind = "manifest"
        elif path.name.endswith(checksum_suffix):
            backup_name = path.name.removesuffix(".sha256")
            kind = "checksum"
        elif path.name.endswith(suffix):
            backup_name = path.name
            kind = "backup"
        else:
            continue
        filename_created_at = _filename_created_at(name_pattern, backup_name)
        if filename_created_at is None:
            continue
        related_paths.setdefault(backup_name, {})[kind] = path

    completed: list[_CompleteBackup] = []
    missing_checksums: list[_ManifestBackup] = []
    incomplete_paths: list[Path] = []
    orphaned_paths: list[Path] = []
    for backup_name, group in related_paths.items():
        filename_created_at = _filename_created_at(name_pattern, backup_name)
        if filename_created_at is None:
            continue
        if "manifest" not in group:
            incomplete_paths.extend(group.values())
            continue
        if "backup" not in group:
            # The copy was removed by hand but its manifest stayed. Failing
            # the label here would block every later backup of it forever.
            orphaned_paths.extend(group.values())
            continue
        manifest_path = group["manifest"]
        if "checksum" in group:
            complete_backup = _load_complete_backup(
                destination,
                manifest_path,
                label=label,
                expected_backup_name=backup_name,
                filename_created_at=filename_created_at,
            )
            completed.append(complete_backup)
            continue

        manifest_backup = _load_manifest_backup(
            destination,
            manifest_path,
            label=label,
            expected_backup_name=backup_name,
            filename_created_at=filename_created_at,
        )
        try:
            actual_digest = _sha256(manifest_backup.backup_path)
        except OSError as exc:
            raise BackupError(
                f"cannot verify backup without checksum {manifest_backup.backup_path}: {exc}"
            ) from exc
        if actual_digest != manifest_backup.expected_sha256:
            raise BackupError(
                "backup without checksum does not match its manifest: "
                f"{manifest_backup.backup_path}"
            )
        missing_checksums.append(manifest_backup)
        completed.append(
            _CompleteBackup(
                backup_path=manifest_backup.backup_path,
                manifest_path=manifest_backup.manifest_path,
                checksum_path=manifest_backup.checksum_path,
                created_at=manifest_backup.created_at,
            )
        )

    for manifest_backup in missing_checksums:
        try:
            _atomic_write_text(
                manifest_backup.checksum_path,
                f"{manifest_backup.expected_sha256}  "
                f"{manifest_backup.backup_path.name}\n",
            )
        except OSError as exc:
            raise BackupError(
                f"cannot restore backup checksum {manifest_backup.checksum_path}: {exc}"
            ) from exc

    anchor = reference or max(
        (item.created_at for item in completed),
        default=None,
    )
    if pending is not None:
        expired = _room_for_pending(completed, pending=pending, keep=keep)
    else:
        retained = (
            {
                item.backup_path.name
                for item in _retained_backups(
                    completed,
                    reference=anchor,
                    keep=keep,
                    protect=protect,
                )
            }
            if anchor is not None
            else set()
        )
        expired = sorted(
            (item for item in completed if item.backup_path.name not in retained),
            key=lambda item: (item.created_at, item.backup_path.name),
            reverse=True,
        )
    removed: list[str] = []
    for path in incomplete_paths:
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError(f"cannot remove incomplete backup file {path}: {exc}") from exc
        if path.name.endswith(suffix):
            removed.append(path.name)

    for group in expired:
        for related_path in (
            group.manifest_path,
            group.checksum_path,
            group.backup_path,
        ):
            try:
                related_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise BackupError(
                    f"cannot remove expired backup file {related_path}: {exc}"
                ) from exc
        removed.append(group.backup_path.name)
    quarantined = _quarantine(destination, orphaned_paths)
    return _PruneOutcome(removed=tuple(sorted(removed)), quarantined=quarantined)


def latest_backup_at(
    destination: Path,
    label: str,
    *,
    suffix: str = DATABASE_SUFFIX,
) -> datetime | None:
    """Creation time of the newest committed copy of ``label``, if any.

    The manifest is written last, so its name alone marks a finished copy.
    """

    name_pattern = _backup_name_pattern(label, suffix)
    try:
        names = [path.name for path in destination.glob(f"{label}-*{suffix}.json")]
    except OSError:
        return None
    times = [
        created_at
        for name in names
        if (created_at := _filename_created_at(name_pattern, name.removesuffix(".json")))
        is not None
    ]
    return max(times, default=None)


def prune_backups(
    destination: Path,
    label: str,
    keep: int,
    *,
    lock_timeout_seconds: float = 30.0,
) -> tuple[str, ...]:
    """Apply tiered retention to exact-label groups under the directory lock."""

    _validate_label(label)
    if keep < 1:
        raise BackupError("keep must be at least 1")
    with _retention_lock(destination, timeout_seconds=lock_timeout_seconds):
        return _prune_backups_locked(destination, label, keep).removed


_SQLITE_SIDECARS = ("-wal", "-shm", "-journal")


def _remove_sidecars(path: Path) -> None:
    for sidecar in _SQLITE_SIDECARS:
        path.with_name(f"{path.name}{sidecar}").unlink(missing_ok=True)


def _remove_abandoned_temporary_files(
    destination: Path,
    label: str,
    suffix: str = DATABASE_SUFFIX,
) -> None:
    pattern = re.compile(
        rf"^\.{re.escape(label)}\.backup-v1\.[A-Za-z0-9_-]+{re.escape(suffix)}\.tmp"
        r"(?:-wal|-shm|-journal)?$"
    )
    for path in destination.glob(f".{label}.backup-v1.*{suffix}.tmp*"):
        if not pattern.fullmatch(path.name):
            continue
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise BackupError(f"cannot remove abandoned backup file {path}: {exc}") from exc


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _ensure_free_space(destination: Path, required_bytes: int, *, label: str) -> None:
    try:
        free = _free_bytes(destination)
    except OSError as exc:
        raise BackupError(f"cannot read free space of {destination}: {exc}") from exc
    needed = max(0, int(required_bytes)) + _FREE_SPACE_RESERVE_BYTES
    if free < needed:
        raise BackupError(
            f"not enough free space to back up {label}: "
            f"{free} bytes free, {needed} bytes needed",
            code="backup_insufficient_space",
        )


def _database_bytes(source: Path) -> int:
    """Upper bound of a page copy: the main file plus pages still in the WAL."""

    size = source.stat().st_size
    try:
        size += source.with_name(f"{source.name}-wal").stat().st_size
    except FileNotFoundError:
        pass
    return size


def _copy_database(
    source: Path,
    target: Path,
    *,
    progress: Callable[[int, int, int], object] | None = None,
) -> None:
    # One step copies every page inside a single read transaction. A WAL
    # source keeps accepting writes, and no commit can land between steps and
    # restart the copy from the first page: with 256-page steps a writer
    # committing every 20 ms kept the copy from ever finishing.
    try:
        with closing(
            sqlite3.connect(_read_only_uri(source), uri=True, timeout=30.0)
        ) as source_connection:
            with closing(sqlite3.connect(target, timeout=30.0)) as target_connection:
                source_connection.backup(
                    target_connection,
                    pages=-1,
                    progress=progress,
                )
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite Backup API failed for {source}: {exc}") from exc


def _copy_file(source: Path, target: Path) -> str:
    # A single descriptor pins one inode, so an atomic replacement of the
    # source during the copy cannot mix old and new bytes.
    digest = hashlib.sha256()
    try:
        with source.open("rb") as reader, target.open("wb") as writer:
            for chunk in iter(lambda: reader.read(_HASH_CHUNK_BYTES), b""):
                digest.update(chunk)
                writer.write(chunk)
            writer.flush()
            os.fsync(writer.fileno())
    except OSError as exc:
        raise BackupError(f"cannot copy {source}: {exc}") from exc
    return digest.hexdigest()


def _prepare_destination(destination: Path) -> Path:
    try:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise BackupError(f"cannot create backup directory {destination}: {exc}") from exc
    resolved = destination.resolve()
    if not resolved.is_dir():
        raise BackupError(f"backup destination is not a directory: {resolved}")
    return resolved


def _resolve_source(source: Path) -> Path:
    resolved = source.expanduser().resolve()
    if not resolved.is_file():
        raise BackupError(f"backup source does not exist or is not a file: {resolved}")
    return resolved


def _publish_backup_locked(
    source: Path,
    destination: Path,
    *,
    label: str,
    keep: int,
    suffix: str,
    created_at: datetime | None,
    lock_timeout_seconds: float,
    required_bytes: int,
    copy: Callable[[Path], dict[str, object]],
) -> BackupResult:
    """Free a slot, copy, verify, publish, then enforce retention.

    ``copy`` fills the temporary path and returns the verification fields
    recorded in the manifest; it raises BackupError when verification fails.
    """

    timestamp = created_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = destination / (
        f"{label}-{filename_timestamp}-{uuid.uuid4().hex[:8]}{suffix}"
    )
    manifest_path = backup_path.with_name(f"{backup_path.name}.json")
    checksum_path = backup_path.with_name(f"{backup_path.name}.sha256")

    # Make room before copying: a full disk otherwise fails every copy while
    # the expired copies that would free it are never reached. The newest
    # existing copy always survives until the new one has been verified.
    with _retention_lock(destination, timeout_seconds=lock_timeout_seconds):
        before = _prune_backups_locked(
            destination,
            label,
            keep,
            suffix=suffix,
            pending=timestamp,
        )
    _ensure_free_space(destination, required_bytes, label=label)

    temporary_path: Path | None = None
    published_paths: list[Path] = []
    committed = False
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination,
            prefix=f".{label}.backup-v1.",
            suffix=f"{suffix}.tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)

        verification = copy(temporary_path)
        os.chmod(temporary_path, 0o600)
        digest = _sha256(temporary_path)
        size_bytes = temporary_path.stat().st_size

        manifest_payload: dict[str, object] = {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "label": label,
            "source": str(source),
            "backup": backup_path.name,
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "size_bytes": size_bytes,
            "sha256": digest,
            **verification,
        }
        with _retention_lock(destination, timeout_seconds=lock_timeout_seconds):
            os.replace(temporary_path, backup_path)
            temporary_path = None
            published_paths.append(backup_path)
            _atomic_write_text(checksum_path, f"{digest}  {backup_path.name}\n")
            published_paths.append(checksum_path)
            _atomic_write_text(
                manifest_path,
                json.dumps(
                    manifest_payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
            )
            published_paths.append(manifest_path)
            committed = True
            after = _prune_backups_locked(
                destination,
                label,
                keep,
                suffix=suffix,
                reference=timestamp,
                protect=backup_path.name,
            )
        integrity_check = verification.get("integrity_check")
        foreign_key_violations = verification.get("foreign_key_violations")
        return BackupResult(
            label=label,
            source=str(source),
            backup=str(backup_path),
            manifest=str(manifest_path),
            checksum_file=str(checksum_path),
            created_at=str(manifest_payload["created_at"]),
            size_bytes=size_bytes,
            sha256=digest,
            integrity_check=(
                str(integrity_check) if integrity_check is not None else None
            ),
            foreign_key_violations=(
                int(foreign_key_violations)
                if isinstance(foreign_key_violations, int)
                else None
            ),
            removed_backups=tuple(sorted(set(before.removed + after.removed))),
            quarantined=tuple(sorted(set(before.quarantined + after.quarantined))),
        )
    except (OSError, BackupError) as exc:
        if not committed:
            for path in reversed(published_paths):
                path.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"cannot create backup for {source}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
            _remove_sidecars(temporary_path)


def _backup_database_locked(
    source: Path,
    destination: Path,
    *,
    label: str | None = None,
    keep: int = 7,
    created_at: datetime | None = None,
    lock_timeout_seconds: float = 30.0,
) -> BackupResult:
    """Create and verify one online SQLite backup, then apply retention."""

    source = _resolve_source(source)
    if keep < 1:
        raise BackupError("keep must be at least 1")
    backup_label = _validate_label(label or source.stem)
    destination = _prepare_destination(destination)

    def copy(temporary_path: Path) -> dict[str, object]:
        _copy_database(source, temporary_path)
        integrity_check, foreign_key_violations = _check_database(temporary_path)
        # Checking a WAL-format copy read-only leaves empty -wal and -shm
        # files; they would outlive the rename and pile up forever.
        _remove_sidecars(temporary_path)
        return {
            "integrity_check": integrity_check,
            "foreign_key_violations": foreign_key_violations,
        }

    try:
        required_bytes = _database_bytes(source)
    except OSError as exc:
        raise BackupError(f"cannot inspect SQLite source {source}: {exc}") from exc
    return _publish_backup_locked(
        source,
        destination,
        label=backup_label,
        keep=keep,
        suffix=DATABASE_SUFFIX,
        created_at=created_at,
        lock_timeout_seconds=lock_timeout_seconds,
        required_bytes=required_bytes,
        copy=copy,
    )


def backup_database(
    source: Path,
    destination: Path,
    *,
    label: str | None = None,
    keep: int = 7,
    created_at: datetime | None = None,
    lock_timeout_seconds: float = 30.0,
) -> BackupResult:
    """Serialize creation and retention for one database label."""

    resolved_source = _resolve_source(source)
    if keep < 1:
        raise BackupError("keep must be at least 1")
    backup_label = _validate_label(label or resolved_source.stem)
    resolved_destination = _prepare_destination(destination)

    with _exclusive_backup_lock(
        resolved_destination,
        backup_label,
        timeout_seconds=lock_timeout_seconds,
    ):
        _remove_abandoned_temporary_files(resolved_destination, backup_label)
        return _backup_database_locked(
            resolved_source,
            resolved_destination,
            label=backup_label,
            keep=keep,
            created_at=created_at,
            lock_timeout_seconds=lock_timeout_seconds,
        )


def file_backup_suffix(source: Path) -> str:
    """Suffix of plain-file copies; keeps the source's own when it is safe."""

    if _SAFE_SUFFIX.fullmatch(source.suffix) and source.suffix != DATABASE_SUFFIX:
        return source.suffix
    return ".bak"


def backup_file(
    source: Path,
    destination: Path,
    *,
    label: str | None = None,
    keep: int = 7,
    created_at: datetime | None = None,
    lock_timeout_seconds: float = 30.0,
) -> BackupResult:
    """Back up one regular file with the same manifest, checksum and retention."""

    resolved_source = _resolve_source(source)
    if keep < 1:
        raise BackupError("keep must be at least 1")
    backup_label = _validate_label(label or resolved_source.stem)
    suffix = file_backup_suffix(resolved_source)
    resolved_destination = _prepare_destination(destination)

    def copy(temporary_path: Path) -> dict[str, object]:
        copied = _copy_file(resolved_source, temporary_path)
        if _sha256(temporary_path) != copied:
            raise BackupError(f"file backup of {resolved_source} did not verify")
        return {}

    try:
        required_bytes = resolved_source.stat().st_size
    except OSError as exc:
        raise BackupError(f"cannot inspect backup source {resolved_source}: {exc}") from exc
    with _exclusive_backup_lock(
        resolved_destination,
        backup_label,
        timeout_seconds=lock_timeout_seconds,
    ):
        _remove_abandoned_temporary_files(resolved_destination, backup_label, suffix)
        return _publish_backup_locked(
            resolved_source,
            resolved_destination,
            label=backup_label,
            keep=keep,
            suffix=suffix,
            created_at=created_at,
            lock_timeout_seconds=lock_timeout_seconds,
            required_bytes=required_bytes,
            copy=copy,
        )


def parse_database_spec(spec: str) -> tuple[str, Path]:
    if "=" in spec:
        label, raw_path = spec.split("=", 1)
    else:
        raw_path = spec
        label = Path(raw_path).stem
    if not raw_path.strip():
        raise argparse.ArgumentTypeError("database path cannot be empty")
    try:
        return _validate_label(label), Path(raw_path)
    except BackupError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create verified online backups with the SQLite Backup API."
    )
    parser.add_argument(
        "--database",
        action="append",
        required=True,
        metavar="LABEL=PATH",
        help="database to back up; repeat for multiple databases",
    )
    parser.add_argument(
        "--destination",
        type=Path,
        required=True,
        help="directory that receives backups and checksum manifests",
    )
    parser.add_argument(
        "--keep",
        type=int,
        default=7,
        help=(
            "maximum backups retained per database label; older copies are "
            "thinned to one per day and one per week first (default: 7)"
        ),
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        type=float,
        default=30.0,
        help="seconds to wait for another backup of the same label (default: 30)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if arguments.keep < 1:
        parser.error("--keep must be at least 1")
    if arguments.lock_timeout_seconds < 0:
        parser.error("--lock-timeout-seconds must not be negative")

    database_specs: list[tuple[str, Path]] = []
    labels: set[str] = set()
    for raw_spec in arguments.database:
        try:
            label, path = parse_database_spec(raw_spec)
        except argparse.ArgumentTypeError as exc:
            parser.error(str(exc))
        if label in labels:
            parser.error(f"duplicate database label: {label}")
        labels.add(label)
        database_specs.append((label, path))

    results: list[BackupResult] = []
    errors: list[dict[str, str]] = []
    for label, path in database_specs:
        try:
            results.append(
                backup_database(
                    path,
                    arguments.destination,
                    label=label,
                    keep=arguments.keep,
                    lock_timeout_seconds=arguments.lock_timeout_seconds,
                )
            )
        except BackupError as exc:
            errors.append({"label": label, "source": str(path), "error": str(exc)})

    payload = {
        "ok": not errors,
        "backups": [asdict(result) for result in results],
        "errors": errors,
    }
    output = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    print(output, file=sys.stderr if errors else sys.stdout)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
