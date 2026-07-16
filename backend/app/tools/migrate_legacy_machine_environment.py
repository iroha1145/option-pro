"""Safely copy legacy machine settings without importing host dependencies."""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

from app.legacy_env_adapter import ALIASES, MACHINE_KEYS

MACHINE_KEY_ORDER = (
    "HOST_BIND",
    "PORT",
    "MACROLENS_URL",
    "ALLOWED_HOSTS",
    "TRUST_PROXY_HEADERS",
    "TRUSTED_PROXY_CIDRS",
    "DATA_DIR",
)
MACHINE_ALIASES = {
    legacy: canonical
    for legacy, canonical in ALIASES.items()
    if canonical in MACHINE_KEYS
}


class MigrationError(Exception):
    def __init__(self, key: str) -> None:
        self.key = key


def _decode_value(raw: str, key: str) -> str:
    text = raw.strip()
    if not text:
        return ""
    if text[0] in {"'", '"'}:
        quote = text[0]
        escaped = False
        closing = None
        for index, character in enumerate(text[1:], start=1):
            if character == quote and not escaped:
                closing = index
                break
            escaped = character == "\\" and not escaped
            if character != "\\":
                escaped = False
        if closing is None:
            raise MigrationError(key)
        tail = text[closing + 1 :].strip()
        if tail and not tail.startswith("#"):
            raise MigrationError(key)
        try:
            value = ast.literal_eval(text[: closing + 1])
        except (SyntaxError, ValueError):
            raise MigrationError(key) from None
        if not isinstance(value, str):
            raise MigrationError(key)
    else:
        value = re.split(r"\s+#", text, maxsplit=1)[0].rstrip()
    if (
        "\0" in value
        or "\r" in value
        or "\n" in value
        or any(ord(character) < 32 for character in value)
        or "$" in value
    ):
        raise MigrationError(key)
    return value


def _read_environment(path: Path, *, required: bool) -> dict[str, str]:
    if not path.exists():
        if required:
            raise OSError(path)
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", line)
        if match is None:
            continue
        key = match.group(1)
        if key not in MACHINE_KEYS and key not in MACHINE_ALIASES:
            continue
        remainder = line[match.end() :].lstrip()
        if not remainder.startswith("="):
            raise MigrationError(key)
        values[key] = _decode_value(remainder[1:], key)
    return values


def _merge_legacy_sources(*sources: dict[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for source in sources:
        for key, value in source.items():
            previous = merged.get(key)
            if previous and value and previous.strip() != value.strip():
                raise MigrationError(key)
            if key not in merged or value:
                merged[key] = value
    return merged


def _canonical_machine_values(
    legacy: dict[str, str],
    defaults: dict[str, str],
) -> dict[str, str]:
    if set(MACHINE_KEY_ORDER) != MACHINE_KEYS:
        raise RuntimeError("machine key inventory is inconsistent")
    missing_defaults = MACHINE_KEYS - defaults.keys()
    if missing_defaults:
        raise MigrationError(sorted(missing_defaults)[0])

    values = {key: defaults[key] for key in MACHINE_KEY_ORDER}
    for key in MACHINE_KEY_ORDER:
        if key in legacy:
            values[key] = legacy[key]

    for legacy_key, canonical_key in MACHINE_ALIASES.items():
        legacy_value = legacy.get(legacy_key, "").strip()
        canonical_value = legacy.get(canonical_key, "").strip()
        if legacy_value and canonical_value and legacy_value != canonical_value:
            raise MigrationError(canonical_key)
        if legacy_value and not canonical_value:
            values[canonical_key] = legacy[legacy_key]
    return values


def migrate(
    root_source: Path,
    secret_source: Path,
    defaults_source: Path,
    destination: Path,
) -> None:
    legacy = _merge_legacy_sources(
        _read_environment(root_source, required=True),
        _read_environment(secret_source, required=False),
    )
    defaults = _read_environment(defaults_source, required=True)
    values = _canonical_machine_values(legacy, defaults)

    payload = "".join(
        f"{key}={json.dumps(values[key], ensure_ascii=False)}\n"
        for key in MACHINE_KEY_ORDER
    )
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 4:
        print("旧配置迁移需要两个源文件、默认模板和目标文件。", file=sys.stderr)
        return 2
    try:
        migrate(*(Path(argument) for argument in arguments))
    except MigrationError as exc:
        print(
            f"无法安全迁移旧 .env 中的 {exc.key}；machine.env 未生成。",
            file=sys.stderr,
        )
        return 1
    except (OSError, UnicodeError):
        print("无法安全读取旧 .env；machine.env 未生成。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
