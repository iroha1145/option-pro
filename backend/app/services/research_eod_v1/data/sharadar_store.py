"""Durable Sharadar page store. Commit a page before advancing the cursor.

Memory rule: nothing in this module holds a whole table in RAM. The unique
primary-key index lives in SQLite on disk, and the merged table file is
append-only (fresh rows are appended when their page is committed), so a
resumed download never rewrites the whole file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.services.research_eod_v1.data.contract import hash_payload
from app.services.research_eod_v1.data.sharadar_schema import SOURCE_VERSION, TABLE_FIELDS

PRIMARY_KEYS = {
    "stocks": ("ticker", "date"),
    "funds": ("ticker", "date"),
    "tickers": ("table", "permaticker"),
    "actions": ("date", "action", "ticker", "value", "contraticker"),
}
# Fields that must be non-empty for a row to have an identity at all. A row that
# lacks one is skipped and counted, never collapsed onto the empty key.
REQUIRED_PK_FIELDS = {
    "stocks": ("ticker", "date"),
    "funds": ("ticker", "date"),
    "tickers": ("permaticker",),
    "actions": ("date", "action", "ticker"),
}
KEY_SEPARATOR = "\x1f"

# Content chain over the merged file every consumer reads. The chain is folded
# one line at a time, so an append costs one hash and a verification costs one
# streaming pass -- the same pass a row count already needed. Row counts and
# primary keys cannot see a value edited in place; this can.
MERGED_DIGEST_VERSION = "merged-content-chain-v1"


def query_signature(table: str, extra: Mapping[str, Any] | None, limit: int) -> str:
    payload = {
        "table": table,
        "extra": dict(sorted((extra or {}).items())),
        "limit": int(limit),
        "schema_fields": list(TABLE_FIELDS[table]),
        "source_version": SOURCE_VERSION,
    }
    return hash_payload(payload)


def row_primary_key(table: str, row: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Primary key tuple, or None when a required key field is empty."""

    for field in REQUIRED_PK_FIELDS[table]:
        if row.get(field) in (None, ""):
            return None
    keys = PRIMARY_KEYS[table]
    return tuple("" if row.get(key) is None else str(row.get(key)) for key in keys)


def row_key_token(table: str, row: Mapping[str, Any]) -> str | None:
    key = row_primary_key(table, row)
    return None if key is None else KEY_SEPARATOR.join(key)


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


def merged_chain_seed(table: str) -> str:
    return hashlib.sha256(f"{MERGED_DIGEST_VERSION}:{table}".encode("utf-8")).hexdigest()


def merged_chain_step(state: str, line: str) -> str:
    return hashlib.sha256(f"{state}:{line}".encode("utf-8")).hexdigest()


def merged_content_digest(store: Path, table: str) -> dict[str, Any]:
    """Fold the merged file as it sits on disk. Nothing is held beyond one line."""

    path = merged_path(store, table)
    if not path.is_file():
        return {"version": MERGED_DIGEST_VERSION, "present": False, "rows": None, "chain_sha256": None}
    state = merged_chain_seed(table)
    rows = 0
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.rstrip("\n")
            if not text.strip():
                continue
            state = merged_chain_step(state, text)
            rows += 1
    return {"version": MERGED_DIGEST_VERSION, "present": True, "rows": rows, "chain_sha256": state}


def verify_merged_content(store: Path, table: str, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the merged file against the digest bound when it was written.

    A store whose checkpoint carries no digest is not treated as correct: the
    merged file cannot vouch for itself, so it has to be rebuilt from artifacts
    that still verify before a digest is bound to it.
    """

    observed = merged_content_digest(store, table)
    recorded = checkpoint.get("merged_content")
    recorded_chain = None
    recorded_version = None
    if isinstance(recorded, Mapping):
        recorded_chain = recorded.get("chain_sha256")
        recorded_version = recorded.get("version")
    if not observed["present"]:
        status = "MISSING"
    elif not recorded_chain:
        status = "UNRECORDED"
    elif recorded_version != MERGED_DIGEST_VERSION:
        status = "UNRECORDED"
    elif str(recorded_chain) != str(observed["chain_sha256"]):
        status = "MISMATCH"
    else:
        status = "MATCH"
    return {
        "status": status,
        "version": MERGED_DIGEST_VERSION,
        "recorded_version": recorded_version,
        "rows": observed["rows"],
        "observed_chain_sha256": observed["chain_sha256"],
        "recorded_chain_sha256": recorded_chain,
        "row_count_alone_is_not_content": True,
    }


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
        "merged_row_count": 0,
        "merged_content": None,
        "pk_missing_rows": 0,
        "session_row_count": 0,
        "next_skip": 0,
        "complete": False,
        "status": "PARTIAL",
        "download_mode": "paged",
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
    """A page file written before its checkpoint was saved is real data; adopt it once."""

    if checkpoint.get("complete"):
        return checkpoint
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
    # The merged file and key index no longer match the page list; force a rebuild.
    checkpoint["merged_row_count"] = -1
    checkpoint["merged_content"] = None
    save_checkpoint(store, checkpoint)
    return checkpoint


def write_page_file(store: Path, table: str, skip: int, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    path = page_path(store, table, skip)
    write_jsonl_atomic(path, rows)
    return {"skip": int(skip), "row_count": len(rows), "sha256": sha256_file(path), "path": str(path)}


def key_index_path(store: Path, table: str) -> Path:
    return store / "keys" / f"{table}.keys"


def key_index_db_path(store: Path, table: str) -> Path:
    return store / "keys" / f"{table}.sqlite"


class UniqueKeyIndex:
    """Durable primary-key index in SQLite. Each page costs its own rows, never the prefix.

    ``keys/<table>.keys`` is an append-only audit listing of the same tokens; the
    lookup structure is the SQLite file, so the index never sits in Python memory.
    """

    def __init__(self, store: Path, table: str) -> None:
        self.store = store
        self.table = table
        self.path = key_index_path(store, table)
        self.db_path = key_index_db_path(store, table)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS keys (token TEXT PRIMARY KEY)")
        self.conn.commit()
        self._count = int(self.conn.execute("SELECT COUNT(*) FROM keys").fetchone()[0])
        if not self.path.is_file() and self._count:
            # Audit listing missing (older store); regenerate it from the database.
            self._rewrite_listing()

    @property
    def count(self) -> int:
        return self._count

    def add_rows(self, rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        """Insert the page's keys. Returns (rows that were new, rows without a key)."""

        fresh: list[dict[str, Any]] = []
        tokens: list[str] = []
        pk_missing = 0
        cursor = self.conn.cursor()
        for row in rows:
            token = row_key_token(self.table, row)
            if token is None:
                pk_missing += 1
                continue
            cursor.execute("INSERT OR IGNORE INTO keys(token) VALUES (?)", (token,))
            if cursor.rowcount == 1:
                fresh.append(dict(row))
                tokens.append(token)
        self.conn.commit()
        self._count += len(tokens)
        if tokens:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write("".join(f"{token}\n" for token in tokens))
        return fresh, pk_missing

    def contains(self, token: str) -> bool:
        return self.conn.execute("SELECT 1 FROM keys WHERE token = ? LIMIT 1", (token,)).fetchone() is not None

    def rebuild(self, pages: Sequence[Mapping[str, Any]]) -> int:
        self.conn.execute("DELETE FROM keys")
        self.conn.commit()
        self._count = 0
        cursor = self.conn.cursor()
        for page in sorted(pages, key=lambda item: int(item["skip"])):
            for row in iter_jsonl(page_path(self.store, self.table, int(page["skip"]))):
                token = row_key_token(self.table, row)
                if token is None:
                    continue
                cursor.execute("INSERT OR IGNORE INTO keys(token) VALUES (?)", (token,))
                if cursor.rowcount == 1:
                    self._count += 1
        self.conn.commit()
        self._rewrite_listing()
        return self._count

    def _rewrite_listing(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".partial")
        with tmp.open("w", encoding="utf-8") as handle:
            for (token,) in self.conn.execute("SELECT token FROM keys ORDER BY token"):
                handle.write(f"{token}\n")
        tmp.replace(self.path)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


def append_merged(store: Path, table: str, rows: Sequence[Mapping[str, Any]], *, chain_state: str) -> dict[str, Any]:
    """Append already-deduplicated rows and carry the content chain forward."""

    state = chain_state
    if not rows:
        return {"appended": 0, "chain_sha256": state}
    dest = merged_path(store, table)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, default=str)
            handle.write(line + "\n")
            state = merged_chain_step(state, line)
        handle.flush()
    return {"appended": len(rows), "chain_sha256": state}


def merged_content_record(rows: int, chain: str) -> dict[str, Any]:
    return {"version": MERGED_DIGEST_VERSION, "rows": int(rows), "chain_sha256": chain}


def _resume_chain_state(store: Path, table: str, checkpoint: Mapping[str, Any]) -> str | None:
    """Chain state to append onto, or None when the merged file has to be rebuilt."""

    path = merged_path(store, table)
    if not path.is_file():
        return merged_chain_seed(table)
    recorded = checkpoint.get("merged_content")
    if isinstance(recorded, Mapping) and recorded.get("version") == MERGED_DIGEST_VERSION and recorded.get("chain_sha256"):
        return str(recorded["chain_sha256"])
    # A store written before the digest existed: fold the file once so later
    # appends extend a chain that was checked against the bytes on disk.
    observed = merged_content_digest(store, table)
    if observed["present"] and int(observed["rows"] or 0) == int(checkpoint.get("merged_row_count") or 0):
        return str(observed["chain_sha256"])
    return None


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
    updated = dict(checkpoint)
    pk_missing_total = int(checkpoint.get("pk_missing_rows") or 0)
    merged_content: dict[str, Any] | None
    resume_state = None if key_index is None else _resume_chain_state(store, table, checkpoint)
    if key_index is None:
        unique_n = count_unique_rows(store, table, committed)
        merged_n = -1
        merged_content = None
    elif replacing or int(checkpoint.get("merged_row_count") or 0) < 0 or resume_state is None:
        unique_n = key_index.rebuild(committed)
        merged = merge_committed(store, table, {"committed_pages": committed})
        merged_n = int(merged["row_count"])
        merged_content = merged_content_record(merged_n, str(merged["chain_sha256"]))
    else:
        fresh, pk_missing = key_index.add_rows(rows)
        pk_missing_total += pk_missing
        appended = append_merged(store, table, fresh, chain_state=resume_state)
        unique_n = key_index.count
        merged_n = int(checkpoint.get("merged_row_count") or 0) + len(fresh)
        merged_content = merged_content_record(merged_n, str(appended["chain_sha256"]))
    updated["committed_pages"] = committed
    updated["committed_row_count"] = unique_n
    updated["merged_row_count"] = merged_n
    updated["merged_content"] = merged_content
    updated["pk_missing_rows"] = pk_missing_total
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
    """Count distinct primary keys across pages with a temporary SQLite set (no RAM set)."""

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE keys (token TEXT PRIMARY KEY)")
    cursor = conn.cursor()
    total = 0
    for page in sorted(pages, key=lambda item: int(item["skip"])):
        for row in iter_jsonl(page_path(store, table, int(page["skip"]))):
            token = row_key_token(table, row)
            if token is None:
                continue
            cursor.execute("INSERT OR IGNORE INTO keys(token) VALUES (?)", (token,))
            if cursor.rowcount == 1:
                total += 1
    conn.close()
    return total


def merge_committed(store: Path, table: str, checkpoint: Mapping[str, Any]) -> dict[str, Any]:
    """Rebuild the merged file from committed pages, deduplicating through SQLite."""

    dest = merged_path(store, table)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".partial")
    seen_db = dest.with_name(dest.name + ".merge.sqlite")
    if seen_db.exists():
        seen_db.unlink()
    conn = sqlite3.connect(str(seen_db))
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("CREATE TABLE keys (token TEXT PRIMARY KEY)")
    cursor = conn.cursor()
    first_row = None
    count = 0
    state = merged_chain_seed(table)
    with tmp.open("w", encoding="utf-8") as handle:
        for page in sorted(checkpoint.get("committed_pages") or [], key=lambda item: int(item["skip"])):
            for row in iter_jsonl(page_path(store, table, int(page["skip"]))):
                token = row_key_token(table, row)
                if token is None:
                    continue
                cursor.execute("INSERT OR IGNORE INTO keys(token) VALUES (?)", (token,))
                if cursor.rowcount != 1:
                    continue
                if first_row is None:
                    first_row = row
                line = json.dumps(row, default=str)
                handle.write(line + "\n")
                state = merged_chain_step(state, line)
                count += 1
    conn.close()
    seen_db.unlink(missing_ok=True)
    tmp.replace(dest)
    return {
        "path": str(dest),
        "row_count": count,
        "first_row": first_row,
        "sha256": sha256_file(dest) if dest.is_file() else "",
        "chain_sha256": state,
    }


def count_rows_by_date(path: Path, dates: Sequence[str], *, date_field: str = "date") -> dict[str, int]:
    """One streaming pass counting rows for the sampled dates (completeness check input)."""

    wanted = {str(item)[:10] for item in dates}
    counts = {item: 0 for item in wanted}
    for row in iter_jsonl(path):
        session = str(row.get(date_field) or "")[:10]
        if session in counts:
            counts[session] += 1
    return counts
