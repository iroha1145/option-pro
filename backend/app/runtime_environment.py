"""Load repository runtime files before modules inspect ``os.environ``.

The Personal Edition keeps three distinct files: legacy-compatible ``.env``,
host-specific ``machine.env`` and server-only ``secrets.env``.  Values exported
by the process always win; among files, later files win so canonical machine and
secret settings cannot be shadowed by stale legacy entries.
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
        values = dotenv_values(path)
        loaded.append(path)
        for key, value in values.items():
            if key and value is not None:
                merged[key] = str(value)
    for key, value in merged.items():
        if key not in exported_keys:
            target[key] = value
    return tuple(loaded)


__all__ = [
    "MACHINE_ENV_FILE",
    "REPOSITORY_ROOT",
    "ROOT_ENV_FILE",
    "RUNTIME_ENV_FILES",
    "SECRETS_ENV_FILE",
    "load_runtime_environment",
]
