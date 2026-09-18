from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from app.services.research_eod_v1.paths import (
    COMPOSITE_MANIFEST_PATH,
    ETF_SUBASSET_MANIFEST_PATH,
    EXPERIMENT_MANIFEST_PATH,
    REFERENCE_DIR,
    REFERENCE_REGISTRY_PATH,
    REGISTRY_PATH,
)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def load_registry(path: Path | None = None) -> dict[str, Any]:
    if str(REFERENCE_DIR) not in sys.path:
        sys.path.insert(0, str(REFERENCE_DIR))
    from registry import load_registry as _load

    return _load(path or REGISTRY_PATH)


def load_experiment_manifest() -> dict[str, Any]:
    return _read_json(EXPERIMENT_MANIFEST_PATH)


def load_etf_subasset_manifest() -> dict[str, Any]:
    return _read_json(ETF_SUBASSET_MANIFEST_PATH)


def load_composite_manifest() -> dict[str, Any]:
    return _read_json(COMPOSITE_MANIFEST_PATH)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def config_hash() -> str:
    payload = "\n".join(
        [
            f"{path.name}:{file_sha256(path)}"
            for path in (
                REGISTRY_PATH,
                EXPERIMENT_MANIFEST_PATH,
                ETF_SUBASSET_MANIFEST_PATH,
                COMPOSITE_MANIFEST_PATH,
                REFERENCE_REGISTRY_PATH,
            )
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
