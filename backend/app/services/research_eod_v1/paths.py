"""Packaged research-eod paths. Runtime must not search the research/ tree."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REGISTRY_PATH = PACKAGE_DIR / "registry.json"


def ensure_reference_on_path() -> None:
    """No-op kept for call-site compatibility. Scoring imports the packaged module."""

    return None
