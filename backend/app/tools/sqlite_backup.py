from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator, Sequence


_SAFE_LABEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_HASH_CHUNK_BYTES = 1024 * 1024


class BackupError(RuntimeError):
    """Raised when a database cannot be backed up safely."""


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
    quick_check: str
    integrity_check: str
    foreign_key_violations: int
    removed_backups: tuple[str, ...]


def _validate_label(label: str) -> str:
    if not _SAFE_LABEL.fullmatch(label):
        raise BackupError(
            "database label must contain only letters, numbers, dot, dash or underscore"
        )
    return label


def _read_only_uri(path: Path) -> str:
    return f"{path.resolve().as_uri()}?mode=ro"


def _check_database(path: Path) -> tuple[str, str, int]:
    try:
        with closing(
            sqlite3.connect(_read_only_uri(path), uri=True, timeout=30.0)
        ) as connection:
            quick_rows = [str(row[0]) for row in connection.execute("PRAGMA quick_check")]
            integrity_rows = [
                str(row[0]) for row in connection.execute("PRAGMA integrity_check")
            ]
            foreign_key_violations = sum(
                1 for _row in connection.execute("PRAGMA foreign_key_check")
            )
    except sqlite3.Error as exc:
        raise BackupError(f"cannot validate SQLite backup {path}: {exc}") from exc

    if quick_rows != ["ok"]:
        raise BackupError(f"SQLite quick_check failed: {quick_rows!r}")
    if integrity_rows != ["ok"]:
        raise BackupError(f"SQLite integrity_check failed: {integrity_rows!r}")
    if foreign_key_violations:
        raise BackupError(
            f"SQLite foreign_key_check found {foreign_key_violations} violation(s)"
        )
    return "ok", "ok", foreign_key_violations


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
def _exclusive_backup_lock(
    destination: Path,
    label: str,
    *,
    timeout_seconds: float,
) -> Iterator[None]:
    if timeout_seconds < 0:
        raise BackupError("lock timeout must not be negative")
    lock_path = destination / f".{label}.backup.lock"
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
                        f"timed out waiting for backup lock: {lock_path}"
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


def prune_backups(destination: Path, label: str, keep: int) -> tuple[str, ...]:
    """Remove incomplete groups and keep the newest complete backup groups."""

    _validate_label(label)
    if keep < 1:
        raise BackupError("keep must be at least 1")

    database_paths = {
        path.name: path for path in destination.glob(f"{label}-*.sqlite3")
    }
    manifest_paths = {
        path.name.removesuffix(".json"): path
        for path in destination.glob(f"{label}-*.sqlite3.json")
    }
    checksum_paths = {
        path.name.removesuffix(".sha256"): path
        for path in destination.glob(f"{label}-*.sqlite3.sha256")
    }
    group_names = set(database_paths) | set(manifest_paths) | set(checksum_paths)
    removed: list[str] = []
    completed: list[Path] = []
    for group_name in group_names:
        group = (
            database_paths.get(group_name),
            manifest_paths.get(group_name),
            checksum_paths.get(group_name),
        )
        if all(path is not None and path.is_file() for path in group):
            completed.append(database_paths[group_name])
            continue

        for related_path in group:
            if related_path is None:
                continue
            try:
                related_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise BackupError(
                    f"cannot remove incomplete backup file {related_path}: {exc}"
                ) from exc
        if group_name in database_paths:
            removed.append(group_name)

    for backup_path in sorted(completed, reverse=True)[keep:]:
        for related_path in (
            backup_path,
            backup_path.with_suffix(backup_path.suffix + ".json"),
            backup_path.with_suffix(backup_path.suffix + ".sha256"),
        ):
            try:
                related_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise BackupError(
                    f"cannot remove expired backup file {related_path}: {exc}"
                ) from exc
        removed.append(backup_path.name)
    return tuple(sorted(removed))


def _remove_abandoned_temporary_files(destination: Path, label: str) -> None:
    patterns = (
        f".{label}-*.sqlite3.tmp",
        f".{label}-*.sqlite3.*.tmp",
    )
    for pattern in patterns:
        for path in destination.glob(pattern):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise BackupError(
                    f"cannot remove abandoned backup file {path}: {exc}"
                ) from exc


def _backup_database_locked(
    source: Path,
    destination: Path,
    *,
    label: str | None = None,
    keep: int = 7,
    created_at: datetime | None = None,
) -> BackupResult:
    """Create and verify one online SQLite backup, then apply retention."""

    source = source.expanduser().resolve()
    if not source.is_file():
        raise BackupError(f"SQLite source does not exist or is not a file: {source}")
    if keep < 1:
        raise BackupError("keep must be at least 1")

    backup_label = _validate_label(label or source.stem)
    try:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise BackupError(f"cannot create backup directory {destination}: {exc}") from exc
    destination = destination.resolve()
    if not destination.is_dir():
        raise BackupError(f"backup destination is not a directory: {destination}")

    timestamp = created_at or datetime.now(UTC)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    timestamp = timestamp.astimezone(UTC)
    filename_timestamp = timestamp.strftime("%Y%m%dT%H%M%S.%fZ")
    backup_path = destination / (
        f"{backup_label}-{filename_timestamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    )
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")

    temporary_path: Path | None = None
    published_paths: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination,
            prefix=f".{backup_label}-",
            suffix=".sqlite3.tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)

        try:
            with closing(
                sqlite3.connect(_read_only_uri(source), uri=True, timeout=30.0)
            ) as source_connection:
                with closing(
                    sqlite3.connect(temporary_path, timeout=30.0)
                ) as target_connection:
                    source_connection.backup(target_connection, pages=256, sleep=0.05)
        except sqlite3.Error as exc:
            raise BackupError(f"SQLite Backup API failed for {source}: {exc}") from exc

        os.chmod(temporary_path, 0o600)
        quick_check, integrity_check, foreign_key_violations = _check_database(
            temporary_path
        )
        digest = _sha256(temporary_path)
        size_bytes = temporary_path.stat().st_size

        os.replace(temporary_path, backup_path)
        temporary_path = None
        published_paths.append(backup_path)

        manifest_payload = {
            "label": backup_label,
            "source": str(source),
            "backup": backup_path.name,
            "created_at": timestamp.isoformat().replace("+00:00", "Z"),
            "size_bytes": size_bytes,
            "sha256": digest,
            "quick_check": quick_check,
            "integrity_check": integrity_check,
            "foreign_key_violations": foreign_key_violations,
        }
        _atomic_write_text(
            manifest_path,
            json.dumps(manifest_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
        )
        published_paths.append(manifest_path)
        _atomic_write_text(checksum_path, f"{digest}  {backup_path.name}\n")
        published_paths.append(checksum_path)

        removed = prune_backups(destination, backup_label, keep)
        return BackupResult(
            label=backup_label,
            source=str(source),
            backup=str(backup_path),
            manifest=str(manifest_path),
            checksum_file=str(checksum_path),
            created_at=manifest_payload["created_at"],
            size_bytes=size_bytes,
            sha256=digest,
            quick_check=quick_check,
            integrity_check=integrity_check,
            foreign_key_violations=foreign_key_violations,
            removed_backups=removed,
        )
    except (OSError, BackupError) as exc:
        for path in reversed(published_paths):
            path.unlink(missing_ok=True)
        if isinstance(exc, BackupError):
            raise
        raise BackupError(f"cannot create backup for {source}: {exc}") from exc
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


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

    resolved_source = source.expanduser().resolve()
    if not resolved_source.is_file():
        raise BackupError(
            f"SQLite source does not exist or is not a file: {resolved_source}"
        )
    if keep < 1:
        raise BackupError("keep must be at least 1")
    backup_label = _validate_label(label or resolved_source.stem)
    try:
        destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise BackupError(f"cannot create backup directory {destination}: {exc}") from exc
    resolved_destination = destination.resolve()
    if not resolved_destination.is_dir():
        raise BackupError(f"backup destination is not a directory: {resolved_destination}")

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
        help="completed backups to retain per database label (default: 7)",
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
