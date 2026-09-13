"""Experiment registry. Every trial is appended; best-looking rows are not deleted."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.services.research.protocol import FROZEN_PROTOCOL, protocol_hash


def default_registry_path(root: str | Path) -> Path:
    return Path(root) / "experiment_registry.jsonl"


def append_trial(root: str | Path, record: Mapping[str, Any]) -> Path:
    path = default_registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "protocol_version": FROZEN_PROTOCOL["protocol_version"],
        "protocol_hash": protocol_hash(),
        **dict(record),
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True, sort_keys=True) + "\n")
    return path


def load_trials(root: str | Path) -> list[dict[str, Any]]:
    path = default_registry_path(root)
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
