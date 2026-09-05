from __future__ import annotations

import argparse
import fcntl
import getpass
import json
import os
import re
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from dotenv import dotenv_values

from app.access import (
    hash_owner_password,
    owner_password_hash_is_valid,
)
from app.runtime_environment import load_runtime_environment


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SECRETS_PATH = REPOSITORY_ROOT / "secrets.env"
SECRET_KEYS = (
    "OPENAI_API_KEY",
    "FINNHUB_API_KEY",
    "MARKETDATA_TOKEN",
    "MASSIVE_API_KEY",
    "FMP_API_KEY",
    "FRED_API_KEY",
    "INTERNAL_API_TOKEN",
    "APP_PASSWORD_HASH",
)
_SAFE_VALUE = re.compile(r"^[!-~]+$")
_SAFE_HTTPS_AUTHORITY = re.compile(
    r"(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::[1-9][0-9]{0,4})?"
)
_UNSAFE_ENV_CHARACTERS = frozenset("#'\"\\$")
_UNSAFE_TOKEN_CHARACTERS = _UNSAFE_ENV_CHARACTERS
_REMOTE_SECRET_KEYS = frozenset(
    {"OPENAI_API_KEY", "FINNHUB_API_KEY", "INTERNAL_API_TOKEN"}
)
_OPENAI_VALIDATION_URL = "https://api.openai.com/v1/models"
_FINNHUB_VALIDATION_URL = "https://finnhub.io/api/v1/quote?symbol=AAPL"
_MACROLENS_HEALTH_PATH = "/internal/v1/health"
_VALIDATION_TIMEOUT_SECONDS = 3
_VALIDATION_READ_LIMIT_BYTES = 4096


def secrets_path() -> Path:
    return DEFAULT_SECRETS_PATH


def _read_values(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError("secrets.env must not be a symbolic link")
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("secrets.env is not a regular file")
    values = {
        key: str(value or "")
        for key, value in dotenv_values(path, interpolate=False).items()
        if key in SECRET_KEYS and str(value or "")
    }
    return values


def _serialize(values: dict[str, str]) -> bytes:
    lines: list[str] = []
    for key in SECRET_KEYS:
        value = values.get(key, "")
        if not value:
            continue
        # Compose interpolates unquoted dollar signs in env_file values. Owner
        # password hashes use dollar-delimited fields, so keep this value
        # single-quoted. python-dotenv and Compose both remove the quotes while
        # preserving the hash byte for byte.
        serialized = f"'{value}'" if key == "APP_PASSWORD_HASH" else value
        lines.append(f"{key}={serialized}\n")
    return "".join(lines).encode("utf-8")


def _sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise ValueError("secrets.env directory must be a real directory")


def _lock_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.lock")


@contextmanager
def _exclusive_secret_lock(path: Path) -> Iterator[None]:
    """Hold one same-directory 0600 lock across a complete Secret update."""

    _ensure_private_directory(path)
    lock_path = _lock_path(path)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(
            lock_path,
            os.O_RDWR | os.O_CREAT | os.O_EXCL | no_follow,
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
    except FileExistsError:
        descriptor = os.open(lock_path, os.O_RDWR | no_follow)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("Secret update lock is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("Secret update lock permissions must be 0600")
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        # A path replacement after open would split writers across two inodes.
        # Verify the locked descriptor is still the directory entry every time
        # before entering the read-modify-write transaction.
        visible = os.stat(lock_path, follow_symlinks=False)
        if (
            not stat.S_ISREG(visible.st_mode)
            or visible.st_dev != metadata.st_dev
            or visible.st_ino != metadata.st_ino
        ):
            raise ValueError("Secret update lock changed while being acquired")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _private_temporary_file(path: Path, *, purpose: str) -> tuple[int, Path]:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.{purpose}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
    except BaseException:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)
        raise
    return descriptor, temporary


def _open_private_regular_file(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("secrets.env is not a regular file")
        if stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError("secrets.env permissions must be 0600 before update")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _atomic_private_backup(path: Path) -> Path:
    """Expose a complete 0600 backup in one filesystem operation."""

    source_descriptor = _open_private_regular_file(path)
    backup_descriptor, temporary = _private_temporary_file(
        path,
        purpose="backup",
    )
    try:
        with os.fdopen(source_descriptor, "rb", closefd=True) as source:
            with os.fdopen(backup_descriptor, "wb", closefd=True) as target:
                while True:
                    chunk = source.read(64 * 1024)
                    if not chunk:
                        break
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())

        backup: Path | None = None
        for _attempt in range(64):
            candidate = path.with_name(
                f"{path.name}.bak.{time.time_ns()}"
            )
            try:
                # A hard link makes the already-fsynced inode visible at once;
                # unlike copyfile, readers can never observe a partial backup.
                os.link(
                    temporary,
                    candidate,
                    follow_symlinks=False,
                )
            except FileExistsError:
                continue
            backup = candidate
            break
        if backup is None:
            raise FileExistsError("could not allocate a unique Secret backup")
        temporary.unlink()
        _sync_directory(path.parent)
        return backup
    except BaseException:
        try:
            os.close(source_descriptor)
        except OSError:
            pass
        try:
            os.close(backup_descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _atomic_write_locked(values: dict[str, str], path: Path) -> Path | None:
    """Write while the caller holds ``_exclusive_secret_lock``."""

    payload = _serialize(values)
    backup: Path | None = None
    if path.exists() or path.is_symlink():
        backup = _atomic_private_backup(path)

    descriptor, temporary = _private_temporary_file(path, purpose="write")
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise
    return backup


def atomic_write(values: dict[str, str], path: Path) -> Path | None:
    with _exclusive_secret_lock(path):
        return _atomic_write_locked(values, path)


def _read_private_values_for_update(path: Path) -> dict[str, str]:
    if path.is_symlink():
        raise ValueError("secrets.env must not be a symbolic link")
    if not path.exists():
        return {}
    descriptor = _open_private_regular_file(path)
    with os.fdopen(descriptor, "r", encoding="utf-8", closefd=True) as stream:
        return {
            key: str(value or "")
            for key, value in dotenv_values(
                stream=stream,
                interpolate=False,
            ).items()
            if key in SECRET_KEYS and str(value or "")
        }


def _mutate_secret(
    path: Path,
    key: str,
    value: str | None,
) -> Path | None:
    """Serialize CLI set/remove operations without reading outside the lock."""

    if key not in SECRET_KEYS:
        raise ValueError("unsupported Secret key")
    with _exclusive_secret_lock(path):
        values = _read_private_values_for_update(path)
        if value is None:
            values.pop(key, None)
        else:
            values[key] = value
        return _atomic_write_locked(values, path)


def _read_secret() -> str:
    if sys.stdin.isatty():
        value = getpass.getpass("Secret value: ")
    else:
        value = sys.stdin.readline().rstrip("\r\n")
    if not value:
        raise ValueError("secret value cannot be empty")
    return value


def _fred_key_shape_valid(value: str) -> bool:
    """FRED issues exactly 32 lower-case alphanumeric characters."""

    return len(value) == 32 and value.isalnum() and value == value.lower()


#: Secrets whose exact shape the issuing service documents. These are enforced at
#: *set* time, not only by ``validate``: a hidden prompt echoes nothing, so a
#: double-paste silently stores 64 or 96 characters and the mistake only surfaces
#: later as an upstream rejection. Failing immediately, with the observed length,
#: is far cheaper to diagnose. The message never contains the value itself.
_EXACT_SHAPES: dict[str, tuple[object, str]] = {
    "FRED_API_KEY": (
        _fred_key_shape_valid,
        "FRED_API_KEY must be exactly 32 lower-case alphanumeric characters",
    ),
}


def _require_exact_shape(key: str, value: str) -> None:
    entry = _EXACT_SHAPES.get(key)
    if entry is None:
        return
    predicate, message = entry
    if not predicate(value):  # type: ignore[operator]
        # Reporting the observed length turns "it did not work" into an obvious
        # double-paste; the secret itself is still never echoed.
        raise ValueError(f"{message} (received {len(value)} characters)")


def _normalized_value(key: str, value: str) -> str:
    if key == "APP_PASSWORD_HASH":
        if owner_password_hash_is_valid(value):
            return value
        return hash_owner_password(value)
    if len(value) > 8192 or not _SAFE_VALUE.fullmatch(value):
        raise ValueError("secret value must use non-whitespace printable characters")
    if any(character in value for character in _UNSAFE_TOKEN_CHARACTERS):
        raise ValueError("secret value contains characters unsupported by secrets.env")
    _require_exact_shape(key, value)
    return value


def _format_valid(key: str, value: str) -> bool:
    if key == "APP_PASSWORD_HASH":
        return owner_password_hash_is_valid(value)
    if not (8 <= len(value) <= 8192) or not _SAFE_VALUE.fullmatch(value):
        return False
    if any(character in value for character in _UNSAFE_TOKEN_CHARACTERS):
        return False
    if key == "OPENAI_API_KEY" and not value.startswith("sk-"):
        return False
    entry = _EXACT_SHAPES.get(key)
    if entry is not None and not entry[0](value):  # type: ignore[operator]
        return False
    return True


def status_report(path: Path) -> dict[str, dict[str, bool]]:
    values = _read_values(path)
    return {
        key: {"configured": bool(values.get(key))}
        for key in SECRET_KEYS
    }


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request,
        file_pointer,
        code,
        message,
        headers,
        new_url,
    ):
        return None


def _open_validation_request(request: urllib.request.Request):
    """Open one validation request without environment proxies or redirects."""

    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects(),
    )
    return opener.open(request, timeout=_VALIDATION_TIMEOUT_SECONDS)


def _validation_state(
    *,
    checked: bool,
    ok: bool,
    reason: str,
    status: int | None = None,
) -> dict[str, bool | int | str]:
    result: dict[str, bool | int | str] = {
        "connection_checked": checked,
        "connection_skipped": not checked,
        "connection_ok": ok,
        "reason": reason,
    }
    if status is not None:
        result["http_status"] = status
    return result


def _reason_for_http_status(status: int) -> str:
    if 300 <= status < 400:
        return "redirect_rejected"
    if status in {401, 403}:
        return "authentication_failed"
    if status == 404:
        return "endpoint_not_found"
    if status == 429:
        return "rate_limited"
    if 500 <= status < 600:
        return "service_unavailable"
    return "request_rejected"


def _validate_connection(
    url: str,
    headers: dict[str, str],
) -> dict[str, bool | int | str]:
    finnhub_key = headers.get("X-Finnhub-Token", "")
    if finnhub_key:
        from app.services.finnhub_budget import reserve_finnhub_request

        if not reserve_finnhub_request(finnhub_key, timeout=0):
            return _validation_state(checked=False, ok=False, reason="rate_limited")
    try:
        request = urllib.request.Request(
            url,
            headers=headers,
            method="GET",
        )
        with _open_validation_request(request) as response:
            status = int(response.getcode())
            if not 100 <= status <= 599:
                raise ValueError("invalid HTTP status")
            response.read(_VALIDATION_READ_LIMIT_BYTES)
    except urllib.error.HTTPError as exc:
        if finnhub_key and exc.code == 429:
            from app.services.finnhub_budget import mark_finnhub_rate_limited

            mark_finnhub_rate_limited(finnhub_key, retry_after=(exc.headers or {}).get("Retry-After", 60))
        try:
            status = int(exc.code)
            if not 100 <= status <= 599:
                raise ValueError("invalid HTTP status")
        except (TypeError, ValueError):
            return _validation_state(
                checked=True,
                ok=False,
                reason="connection_failed",
            )
        finally:
            try:
                exc.close()
            except Exception:
                pass
        return _validation_state(
            checked=True,
            ok=False,
            reason=_reason_for_http_status(status),
            status=status,
        )
    except Exception:
        # Validation output deliberately discards network exception details.
        return _validation_state(
            checked=True,
            ok=False,
            reason="connection_failed",
        )

    if 200 <= status < 300:
        return _validation_state(
            checked=True,
            ok=True,
            reason="reachable",
            status=status,
        )
    return _validation_state(
        checked=True,
        ok=False,
        reason=_reason_for_http_status(status),
        status=status,
    )


def _macrolens_health_url() -> tuple[str | None, str | None]:
    environment = dict(os.environ)
    try:
        load_runtime_environment(
            tuple(
                REPOSITORY_ROOT / filename
                for filename in (".env", "machine.env", "secrets.env")
            ),
            environ=environment,
        )
    except Exception:
        return None, "macrolens_url_missing"
    origin = environment.get("MACROLENS_URL", "")
    if not origin:
        return None, "macrolens_url_missing"
    if (
        any(character.isspace() or ord(character) < 32 for character in origin)
        or "?" in origin
        or "#" in origin
    ):
        return None, "macrolens_url_invalid"
    try:
        parsed = urllib.parse.urlsplit(origin)
        port = parsed.port
    except ValueError:
        return None, "macrolens_url_invalid"
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or not _SAFE_HTTPS_AUTHORITY.fullmatch(parsed.netloc)
        or (port is not None and not 1 <= port <= 65535)
    ):
        return None, "macrolens_url_invalid"
    return f"https://{parsed.netloc}{_MACROLENS_HEALTH_PATH}", None


def validate_report(path: Path) -> dict[str, object]:
    values = _read_values(path)
    file_exists = path.exists()
    permission_0600 = (
        not file_exists
        or stat.S_IMODE(path.stat().st_mode) == 0o600
    )
    secret_report: dict[str, dict[str, bool | int | str]] = {}
    for key in SECRET_KEYS:
        value = values.get(key, "")
        configured = bool(value)
        format_valid = configured and _format_valid(key, value)
        item: dict[str, bool | int | str] = {
            "configured": configured,
            "format_valid": format_valid,
        }
        if not configured:
            item.update(
                _validation_state(
                    checked=False,
                    ok=False,
                    reason="not_configured",
                )
            )
        elif not permission_0600:
            item.update(
                _validation_state(
                    checked=False,
                    ok=False,
                    reason="file_permissions_invalid",
                )
            )
        elif not format_valid:
            item.update(
                _validation_state(
                    checked=False,
                    ok=False,
                    reason="format_invalid",
                )
            )
        elif key not in _REMOTE_SECRET_KEYS:
            item.update(
                _validation_state(
                    checked=False,
                    ok=True,
                    reason="local_validation_only",
                )
            )
        elif key == "OPENAI_API_KEY":
            item.update(
                _validate_connection(
                    _OPENAI_VALIDATION_URL,
                    {"Authorization": f"Bearer {value}"},
                )
            )
        elif key == "FINNHUB_API_KEY":
            item.update(
                _validate_connection(
                    _FINNHUB_VALIDATION_URL,
                    {"X-Finnhub-Token": value},
                )
            )
        else:
            url, unsafe_reason = _macrolens_health_url()
            if url is None:
                item.update(
                    _validation_state(
                        checked=False,
                        ok=False,
                        reason=unsafe_reason or "macrolens_url_invalid",
                    )
                )
            else:
                item.update(
                    _validate_connection(
                        url,
                        {"Authorization": f"Bearer {value}"},
                    )
                )
        secret_report[key] = item

    return {
        "file": {
            "exists": file_exists,
            "permission_0600": permission_0600,
        },
        "secrets": secret_report,
    }


def _validation_succeeded(report: dict[str, object]) -> bool:
    file_report = report.get("file")
    secret_report = report.get("secrets")
    if not isinstance(file_report, dict) or not file_report.get("permission_0600"):
        return False
    if not isinstance(secret_report, dict):
        return False
    for key, raw_item in secret_report.items():
        if not isinstance(raw_item, dict):
            return False
        if not raw_item.get("configured"):
            continue
        if not raw_item.get("format_valid"):
            return False
        if key in _REMOTE_SECRET_KEYS and not (
            raw_item.get("connection_checked")
            and raw_item.get("connection_ok")
        ):
            return False
    return True


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage server-only Personal Edition secrets.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    set_parser = commands.add_parser("set")
    set_parser.add_argument("key")
    remove_parser = commands.add_parser("remove")
    remove_parser.add_argument("key")
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
            return 0 if _validation_succeeded(report) else 1

        key = arguments.key
        if key not in SECRET_KEYS:
            raise ValueError("unsupported Secret key")
        if arguments.command == "set":
            value = _normalized_value(key, _read_secret())
        else:
            value = None
        _mutate_secret(path, key, value)
        print(json.dumps({"key": key, "configured": arguments.command == "set"}))
        return 0
    except (OSError, ValueError) as exc:
        print(f"Secret update failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
