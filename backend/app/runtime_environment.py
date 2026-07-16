"""Load repository runtime files before modules inspect ``os.environ``.

The Personal Edition keeps three distinct files: legacy-compatible ``.env``,
host-specific ``machine.env`` and server-only ``secrets.env``. Values exported
by the process always win. The canonical files override legacy ``.env`` values
only for the seven machine fields or five secrets that belong to them.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping, Sequence
from pathlib import Path

from dotenv import dotenv_values


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ROOT_ENV_FILE = REPOSITORY_ROOT / ".env"
MACHINE_ENV_FILE = REPOSITORY_ROOT / "machine.env"
SECRETS_ENV_FILE = REPOSITORY_ROOT / "secrets.env"
RUNTIME_ENV_FILES = (ROOT_ENV_FILE, MACHINE_ENV_FILE, SECRETS_ENV_FILE)
MACHINE_ENV_KEYS = frozenset(
    {
        "HOST_BIND",
        "PORT",
        "MACROLENS_URL",
        "ALLOWED_HOSTS",
        "TRUST_PROXY_HEADERS",
        "TRUSTED_PROXY_CIDRS",
        "DATA_DIR",
    }
)
SECRET_ENV_KEYS = frozenset(
    {
        "OPENAI_API_KEY",
        "FINNHUB_API_KEY",
        "MARKETDATA_TOKEN",
        "INTERNAL_API_TOKEN",
        "APP_PASSWORD_HASH",
    }
)


def _key_belongs_to_file(path: Path, key: str) -> bool:
    """Enforce the canonical machine/secret file boundary during loading."""

    if path.name == MACHINE_ENV_FILE.name:
        return key in MACHINE_ENV_KEYS
    if path.name == SECRETS_ENV_FILE.name:
        return key in SECRET_ENV_KEYS
    return True


def load_runtime_environment(
    paths: Sequence[Path] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
) -> tuple[Path, ...]:
    """Merge runtime files without ever overriding exported process values."""

    selected_paths = RUNTIME_ENV_FILES if paths is None else paths
    target = os.environ if environ is None else environ
    exported_keys = set(target)
    merged: dict[str, str] = {}
    loaded: list[Path] = []
    for path in selected_paths:
        if not path.is_file():
            continue
        values = dotenv_values(path, interpolate=False)
        loaded.append(path)
        for key, value in values.items():
            if key and value is not None and _key_belongs_to_file(path, key):
                merged[key] = str(value)
    for key, value in merged.items():
        if key not in exported_keys:
            target[key] = value
    return tuple(loaded)


__all__ = [
    "MACHINE_ENV_FILE",
    "MACHINE_ENV_KEYS",
    "REPOSITORY_ROOT",
    "ROOT_ENV_FILE",
    "RUNTIME_ENV_FILES",
    "SECRETS_ENV_FILE",
    "SECRET_ENV_KEYS",
    "load_runtime_environment",
]
