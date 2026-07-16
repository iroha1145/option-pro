from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from pathlib import Path

from dotenv import dotenv_values

from app.access import (
    hash_owner_password,
    owner_password_hash_is_valid,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SECRETS_PATH = REPOSITORY_ROOT / "secrets.env"
SECRET_KEYS = (
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "MARKETDATA_TOKEN",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
)
_SAFE_VALUE = re.compile(r"^[!-~]+$")
_UNSAFE_ENV_CHARACTERS = frozenset("#'\"\\")


def secrets_path() -> Path:
    return DEFAULT_SECRETS_PATH


def _read_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise ValueError("secrets.env must not be a symbolic link")
    if not path.is_file():
        raise ValueError("secrets.env is not a regular file")
    values = {
        key: str(value or "")
        for key, value in dotenv_values(path).items()
        if key in SECRET_KEYS and str(value or "")
    }
    return values


def _serialize(values: dict[str, str]) -> bytes:
    lines = [f"{key}={values[key]}\n" for key in SECRET_KEYS if values.get(key)]
    return "".join(lines).encode("utf-8")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write(values: dict[str, str], path: Path) -> Path | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Path | None = None
    if path.exists():
        backup = path.with_name(f"{path.name}.bak.{time.time_ns()}")
        shutil.copyfile(path, backup)
        os.chmod(backup, 0o600)
        with backup.open("rb") as handle:
            os.fsync(handle.fileno())

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        payload = _serialize(values)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        _sync_directory(path.parent)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return backup


def _read_secret() -> str:
    if sys.stdin.isatty():
        value = getpass.getpass("Secret value: ")
    else:
        value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        raise ValueError("secret value cannot be empty")
    return value


def _normalized_value(key: str, value: str) -> str:
    if key == "APP_PASSWORD_HASH":
        if owner_password_hash_is_valid(value):
            return value
        return hash_owner_password(value)
    if len(value) > 8192 or not _SAFE_VALUE.fullmatch(value):
        raise ValueError("secret value must use non-whitespace printable characters")
    if any(character in value for character in _UNSAFE_ENV_CHARACTERS):
        raise ValueError("secret value contains characters unsupported by secrets.env")
    return value


def _format_valid(key: str, value: str) -> bool:
    if key == "APP_PASSWORD_HASH":
        return owner_password_hash_is_valid(value)
    if not (8 <= len(value) <= 8192) or not _SAFE_VALUE.fullmatch(value):
        return False
    if any(character in value for character in _UNSAFE_ENV_CHARACTERS):
        return False
    if key == "OPENAI_API_KEY" and not value.startswith("sk-"):
        return False
    return True


def status_report(path: Path) -> dict[str, dict[str, bool]]:
    values = _read_values(path)
    return {
        key: {"configured": bool(values.get(key))}
        for key in SECRET_KEYS
    }


def validate_report(path: Path) -> dict[str, object]:
    values = _read_values(path)
    return {
        "file": {
            "exists": path.exists(),
            "permission_0600": (
                not path.exists()
                or stat.S_IMODE(path.stat().st_mode) == 0o600
            ),
        },
        "secrets": {
            key: {
                "configured": bool(values.get(key)),
                "format_valid": bool(values.get(key))
                and _format_valid(key, values[key]),
            }
            for key in SECRET_KEYS
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage server-only Personal Edition secrets.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    set_parser = commands.add_parser("set")
    set_parser.add_argument("key", choices=SECRET_KEYS)
    remove_parser = commands.add_parser("remove")
    remove_parser.add_argument("key", choices=SECRET_KEYS)
    commands.add_parser("validate")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    path = secrets_path()
    try:
        if arguments.command == "status":
            print(json.dumps(status_report(path), sort_keys=True))
            return 0
        if arguments.command == "validate":
            report = validate_report(path)
            print(json.dumps(report, sort_keys=True))
            secret_report = report["secrets"]
            file_report = report["file"]
            return 0 if (
                isinstance(secret_report, dict)
                and isinstance(file_report, dict)
                and file_report["permission_0600"]
                and all(
                    not item["configured"] or item["format_valid"]
                    for item in secret_report.values()
                )
            ) else 1

        values = _read_values(path)
        key = arguments.key
        if arguments.command == "set":
            values[key] = _normalized_value(key, _read_secret())
        else:
            values.pop(key, None)
        atomic_write(values, path)
        print(json.dumps({"key": key, "configured": arguments.command == "set"}))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Secret update failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
