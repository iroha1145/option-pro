"""Durable Sharadar page store. Commit a page before advancing the cursor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.services.research_eod_v1.data.contract import hash_payload
from app.services.research_eod_v1.data.sharadar_schema import SOURCE_VERSION, TABLE_FIELDS

PRIMARY_KEYS = {
    "stocks": ("ticker", "date"),
    "funds": ("ticker", "date"),
    "tickers": ("permaticker",),
    "actions": ("date", "action", "ticker", "value", "contraticker"),
}


def query_signature(table: str, extra: Mapping[str, Any] | None, limit: int) -> str:
    payload = {
        "table": table,
        "extra": dict(sorted((extra or {}).items())),
        "limit": int(limit),
        "schema_fields": list(TABLE_FIELDS[table]),
        "source_version": SOURCE_VERSION,
    }
    return hash_payload(payload)


def row_primary_key(table: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    keys = PRIMARY_KEYS[table]
    return tuple("" if row.get(key) is None else str(row.get(key)) for key in keys)


def page_dir(store: Path, table: str) -> Path:
    return store / "pages" / table


def page_path(store: Path, table: str, skip: int) -> Path:
    return page_dir(store, table) / f"skip_{int(skip):08d}.jsonl"


def checkpoint_path(store: Path, table: str) -> Path:
    return store / "checkpoints" / f"{table}.json"


def merged_path(store: Path, table: str) -> Path:
    return store / f"{table}.jsonl"


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    tmp.write_bytes(payload)
    tmp.replace(path)


def write_jsonl_atomic(path: Path, rows: Sequence[Mapping[str, Any]]) -> str:
    digest = hashlib.sha256()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".partial")
    with tmp.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, default=str) + "\n"
            handle.write(line)
            digest.update(line.encode("utf-8"))
    tmp.replace(path)
    return digest.hexdigest()


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream rows so a caller never has to hold a whole table in memory."""

    if not path.is_file():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            item = json.loads(text)
            if isinstance(item, dict):
                yield item


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return list(iter_jsonl(path))


def count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    total = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                total += 1
    return total


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def empty_checkpoint(table: str, *, extra: Mapping[str, Any] | None, limit: int) -> dict[str, Any]:
    return {
        "table": table,
        "query_signature": query_signature(table, extra, limit),
        "schema_fields": list(TABLE_FIELDS[table]),
        "source_version": SOURCE_VERSION,
        "limit": int(limit),
        "extra": dict(extra or {}),
        "committed_pages": [],
        "committed_row_count": 0,
        "session_row_count": 0,
        "next_skip": 0,
        "complete": False,
        "status": "PARTIAL",
    }


def load_checkpoint(store: Path, table: str) -> dict[str, Any] | None:
    path = checkpoint_path(store, table)
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else None


def save_checkpoint(store: Path, payload: Mapping[str, Any]) -> None:
    atomic_write_text(checkpoint_path(store, str(payload["table"])), json.dumps(payload, indent=2, default=str) + "\n")


def verify_committed_pages(store: Path, table: str, checkpoint: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for page in checkpoint.get("committed_pages") or []:
        path = page_path(store, table, int(page["skip"]))
        if not path.is_file():
            errors.append(f"missing_page:{page['skip']}")
            continue
        digest = sha256_file(path)
        if digest != page.get("sha256"):
            errors.append(f"hash_mismatch:{page['skip']}")
    return errors


def adopt_orphan_page(store: Path, table: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
    skip = int(checkpoint.get("next_skip") or 0)
    path = page_path(store, table, skip)
    if not path.is_file():
        return checkpoint
    rows = read_jsonl(path)
    if not rows:
        return checkpoint
    already = {int(item["skip"]) for item in checkpoint.get("committed_pages") or []}
    if skip in already:
        return checkpoint
    page_meta = {
        "skip": skip,
        "row_count": len(rows),
        "sha256": sha256_file(path),
    }
    committed = list(checkpoint.get("committed_pages") or [])
    committed.append(page_meta)
    checkpoint = dict(checkpoint)
    checkpoint["committed_pages"] = committed
    checkpoint["committed_row_count"] = int(checkpoint.get("committed_row_count") or 0) + len(rows)
    checkpoint["next_skip"] = skip + len(rows)
    save_checkpoint(store, checkpoint)
    return checkpoint


def write_page_file(store: Path, table: str, skip: int, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    path = page_path(store, table, skip)
    write_jsonl_atomic(path, rows)
    return {"skip": int(skip), "row_count": len(rows), "sha256": sha256_file(path), "path": str(path)}


KEY_SEPARATOR = "\x1f"


def key_index_path(store: Path, table: str) -> Path:
    return store / "keys" / f"{table}.keys"


class UniqueKeyIndex:
    """Durable primary-key index so each page costs its own rows, not the whole prefix."""

    def __init__(self, store: Path, table: str) -> None:
        self.store = store
        self.table = table
        self.path = key_index_path(store, table)
        self._keys: set[str] = set()
        if self.path.is_file():
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    text = line.rstrip("\n")
                    if text:
                        self._keys.add(text)

    @property
    def count(self) -> int:
        return len(self._keys)

    def add_rows(self, rows: Sequence[Mapping[str, Any]]) -> int:
        fresh: list[str] = []
        for row in rows:
            token = KEY_SEPARATOR.join(row_primary_key(self.table, row))
            if token in self._keys:
                continue
            self._keys.add(token)
            fresh.append(token)
        if fresh:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("".join(f"{token}\n" for token in fresh))
        return len(fresh)

    def rebuild(self, pages: Sequence[Mapping[str, Any]]) -> int:
        self._keys = set()
        for page in sorted(pages, key=lambda item: int(item["skip"])):
            for row in iter_jsonl(page_path(self.store, self.table, int(page["skip"]))):
                self._keys.add(KEY_SEPARATOR.join(row_primary_key(self.table, row)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(self.path, "".join(f"{token}\n" for token in sorted(self._keys)))
        return len(self._keys)


def advance_checkpoint(
    store: Path,
    table: str,
    *,
    skip: int,
    rows: Sequence[Mapping[str, Any]],
    checkpoint: dict[str, Any],
    page_sha256: str,
    key_index: UniqueKeyIndex | None = None,
) -> dict[str, Any]:
    path = page_path(store, table, skip)
    file_sha = sha256_file(path) if path.is_file() else ""
    page_meta = {
        "skip": int(skip),
        "row_count": len(rows),
        "sha256": file_sha,
        "request_sha256": page_sha256,
    }
    previous = list(checkpoint.get("committed_pages") or [])
    replacing = any(int(item["skip"]) == int(skip) for item in previous)
    committed = [item for item in previous if int(item["skip"]) != int(skip)]
    committed.append(page_meta)
    committed.sort(key=lambda item: int(item["skip"]))
    next_skip = int(skip) + len(rows)
    if key_index is None:
        unique_n = count_unique_rows(store, table, committed)
    elif replacing:
        unique_n = key_index.rebuild(committed)
    else:
        key_index.add_rows(rows)
        unique_n = key_index.count
    updated = dict(checkpoint)
    updated["committed_pages"] = committed
    updated["committed_row_count"] = unique_n
    updated["next_skip"] = next_skip
    updated["complete"] = False
    updated["status"] = "PARTIAL"
    save_checkpoint(store, updated)
    return updated


def commit_page(
    store: Path,
    table: str,
    *,
    skip: int,
    rows: Sequence[Mapping[str, Any]],
    checkpoint: dict[str, Any],
    page_sha256: str,
) -> dict[str, Any]:
    write_page_file(store, table, skip, rows)
    return advance_checkpoint(
        store,
        table,
        skip=skip,
        rows=rows,
        checkpoint=checkpoint,
        page_sha256=page_sha256,
    )


def count_unique_rows(store: Path, table: str, pages: Sequence[Mapping[str, Any]]) -> int:
    seen: set[tuple[str, ...]] = set()
    for page in sorted(pages, key=lambda item: int(item["skip"])):
        for row in iter_jsonl(page_path(store, table, int(page["skip"]))):
            seen.add(row_primary_key(table, row))
    return len(seen)


def merge_committed(store: Path, table: str, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    seen: set[tuple[str, ...]] = set()
    dest = merged_path(store, table)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    first_key = None
    count = 0
    with tmp.open("w", encoding="utf-8") as handle:
        for page in sorted(checkpoint.get("committed_pages") or [], key=lambda item: int(item["skip"])):
            for row in iter_jsonl(page_path(store, table, int(page["skip"]))):
                key = row_primary_key(table, row)
                if key in seen:
                    continue
                seen.add(key)
                if first_key is None:
                    first_key = row
                handle.write(json.dumps(row, default=str) + "\n")
                count += 1
    tmp.replace(dest)
    return {
        "path": str(dest),
        "row_count": count,
        "first_row": first_key,
        "sha256": sha256_file(dest) if dest.is_file() else "",
    }
