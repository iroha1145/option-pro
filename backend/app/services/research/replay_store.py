"""Atomic, idempotent replay artifacts with a run-identity manifest.

A day or ticker is one commit: rows and the completion marker live in the
same shard file. A truncated JSONL line cannot rewind completed shards.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from app.services.research.run_identity import (
    RunIdentityError,
    assert_compatible_identity,
)


_SAFE_KEY = re.compile(r"[^A-Za-z0-9._-]+")


def partial_paths(out: Path) -> tuple[Path, Path]:
    return (
        out.with_name(out.stem + ".partial-days.jsonl"),
        out.with_name(out.stem + ".partial-rows.jsonl"),
    )


def store_dir(out: Path) -> Path:
    return out.with_name(out.stem + ".store")


def atomic_write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=True, default=str) + "\n"
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read JSONL and skip a truncated or corrupt tail line."""

    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def upsert_by_key(
    rows: Iterable[Mapping[str, Any]],
    *,
    key_fn: Callable[[Mapping[str, Any]], tuple[Any, ...]],
) -> list[dict[str, Any]]:
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        merged[key_fn(row)] = dict(row)
    return list(merged.values())


def completed_sessions(path: Path, *, key: str = "signal_date") -> set[str]:
    return {
        str(row[key])
        for row in read_jsonl(path)
        if isinstance(row, Mapping) and row.get(key)
    }


def reset_partials(*paths: Path) -> None:
    for path in paths:
        if path.is_file():
            path.unlink()


def _safe_key(key: str) -> str:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    cleaned = _SAFE_KEY.sub("_", key).strip("._") or "key"
    return f"{cleaned[:80]}-{digest}"


class ReplayStore:
    def __init__(self, out: Path):
        self.out = Path(out)
        self.dir = store_dir(self.out)
        self.manifest_path = self.dir / "manifest.json"
        self.shards_dir = self.dir / "shards"

    def initialize(self, identity: Mapping[str, Any]) -> None:
        if self.dir.exists():
            for path in self.shards_dir.glob("*.json"):
                path.unlink()
            for path in self.shards_dir.glob("*.tmp"):
                path.unlink()
        self.shards_dir.mkdir(parents=True, exist_ok=True)
        days_path, rows_path = partial_paths(self.out)
        reset_partials(days_path, rows_path)
        atomic_write_json(self.manifest_path, dict(identity))

    def load_manifest(self) -> dict[str, Any] | None:
        if not self.manifest_path.is_file():
            return None
        try:
            payload = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RunIdentityError("run manifest is not valid JSON") from exc
        return payload if isinstance(payload, dict) else None

    def require_resume(self, identity: Mapping[str, Any]) -> None:
        if self.has_legacy_partials() and self.load_manifest() is None:
            raise RunIdentityError(
                "refusing to resume a legacy checkpoint without a run manifest; start a new run id"
            )
        assert_compatible_identity(self.load_manifest(), identity)
        self.shards_dir.mkdir(parents=True, exist_ok=True)

    def has_legacy_partials(self) -> bool:
        days_path, rows_path = partial_paths(self.out)
        return days_path.is_file() or rows_path.is_file()

    def commit(self, key: str, *, marker: Mapping[str, Any], rows: Iterable[Mapping[str, Any]]) -> None:
        payload = {
            "key": key,
            "marker": dict(marker),
            "rows": [dict(row) for row in rows],
        }
        atomic_write_json(self.shards_dir / f"{_safe_key(key)}.json", payload)

    def _iter_shards(self) -> list[dict[str, Any]]:
        if not self.shards_dir.is_dir():
            return []
        items: list[dict[str, Any]] = []
        for path in sorted(self.shards_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            if isinstance(payload, Mapping) and payload.get("key"):
                items.append(dict(payload))
        return items

    def completed_keys(self) -> set[str]:
        return {str(item["key"]) for item in self._iter_shards()}

    def assemble_markers(self) -> list[dict[str, Any]]:
        markers = []
        for item in self._iter_shards():
            marker = dict(item.get("marker") or {})
            marker.setdefault("key", item["key"])
            markers.append(marker)
        return markers

    def assemble_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in self._iter_shards():
            rows.extend(item.get("rows") or [])
        return rows
