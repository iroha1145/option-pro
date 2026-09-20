"""Load the packaged registry.json without sys.path or research/ directories."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.research_eod_v1.paths import REGISTRY_PATH
from app.services.research_eod_v1.registry_scoring import load_registry as _load


def load_registry(path: Path | None = None) -> dict[str, Any]:
    return _load(path or REGISTRY_PATH)
