"""Append-only replay artifacts. Used so a crashed 988-day run can resume."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping


def partial_paths(out: Path) -> tuple[Path, Path]:
    return (
        out.with_name(out.stem + ".partial-days.jsonl"),
        out.with_name(out.stem + ".partial-rows.jsonl"),
    )


def append_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


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
