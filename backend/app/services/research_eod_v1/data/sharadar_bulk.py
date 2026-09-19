"""Bulk-first backfill: one zipped CSV per table, streamed into the page store.

The vendor's bulk archive is the only download path that does not depend on
skip/limit paging staying stable across tens of millions of rows. Rows are
streamed straight from the zip into page files; nothing holds the table in
memory. Rows outside the pinned research window are dropped at ingest so the
raw store matches what the paged plan would have requested.
"""

from __future__ import annotations

import csv
import io
import shutil
import zipfile
from datetime import date
from pathlib import Path
from typing import Any, Mapping

from app.services.research_eod_v1.data.sharadar import SharadarClient, credential_present, validate_bulk_archive
from app.services.research_eod_v1.data.sharadar_schema import (
    ALLOWED_END,
    DATE_BOUND_TABLES,
    DEFAULT_PAGE_LIMIT,
    FULL_HISTORY_START,
    TABLE_FIELDS,
    TICKERS_OPTIONAL_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import (
    UniqueKeyIndex,
    advance_checkpoint,
    checkpoint_path,
    empty_checkpoint,
    key_index_db_path,
    key_index_path,
    load_checkpoint,
    merged_path,
    page_dir,
    query_signature,
    save_checkpoint,
    write_page_file,
)

BULK_MODE = "bulk"


def bulk_extra(table: str, years: str, *, start: date = FULL_HISTORY_START, end: date = ALLOWED_END) -> dict[str, Any]:
    extra: dict[str, Any] = {"mode": BULK_MODE, "years": years}
    if table in DATE_BOUND_TABLES:
        extra["from"] = start.isoformat()
        extra["to"] = end.isoformat()
    return extra


def _reset_table(store: Path, table: str) -> None:
    """A fresh bulk ingest replaces whatever partial paged state exists for the table."""

    shutil.rmtree(page_dir(store, table), ignore_errors=True)
    for path in (
        merged_path(store, table),
        checkpoint_path(store, table),
        key_index_path(store, table),
        key_index_db_path(store, table),
    ):
        path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(key_index_db_path(store, table)) + suffix).unlink(missing_ok=True)


def ingest_bulk_archive(
    store: Path,
    table: str,
    archive_path: Path,
    *,
    years: str,
    limit: int = DEFAULT_PAGE_LIMIT,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
) -> dict[str, Any]:
    extra = bulk_extra(table, years, start=start, end=end)
    signature = query_signature(table, extra, limit)
    existing = load_checkpoint(store, table)
    if existing is not None and existing.get("query_signature") == signature and existing.get("complete"):
        return {
            "table": table,
            "status": "READ_OK",
            "row_count": int(existing.get("committed_row_count") or 0),
            "committed_row_count": int(existing.get("committed_row_count") or 0),
            "session_row_count": 0,
            "pages": 0,
            "complete": True,
            "download_mode": BULK_MODE,
            "years": years,
            "already_ingested": True,
            "observed_min_date": existing.get("observed_min_date"),
            "observed_max_date": existing.get("observed_max_date"),
            "rows_in_archive": existing.get("rows_in_archive"),
            "rows_dropped_outside_window": existing.get("rows_dropped_outside_window"),
            "rows_dropped_bad_date": existing.get("rows_dropped_bad_date"),
            "pk_missing_rows": int(existing.get("pk_missing_rows") or 0),
            "checkpoint": dict(existing),
        }
    if existing is not None and existing.get("query_signature") != signature and existing.get("complete"):
        return {
            "table": table,
            "status": "CHECKPOINT_QUERY_MISMATCH",
            "row_count": int(existing.get("committed_row_count") or 0),
            "complete": False,
            "download_mode": BULK_MODE,
            "years": years,
            "reason": "store_holds_a_complete_table_from_a_different_query; clear it explicitly",
        }
    check = validate_bulk_archive(archive_path)
    if not check["ok"]:
        return {"table": table, "status": "SCHEMA_MISMATCH", "reason": check["reason"], "complete": False, "download_mode": BULK_MODE, "years": years, "row_count": 0}
    _reset_table(store, table)
    checkpoint = empty_checkpoint(table, extra=extra, limit=limit)
    checkpoint["download_mode"] = BULK_MODE
    save_checkpoint(store, checkpoint)
    key_index = UniqueKeyIndex(store, table)
    required = [field for field in TABLE_FIELDS[table] if field not in TICKERS_OPTIONAL_FIELDS]
    chunk: list[dict[str, Any]] = []
    skip = 0
    pages = 0
    rows_in = 0
    dropped_outside = 0
    dropped_bad_date = 0
    min_date: date | None = None
    max_date: date | None = None
    members: list[str] = []

    def flush() -> None:
        nonlocal chunk, skip, pages, checkpoint
        if not chunk:
            return
        write_page_file(store, table, skip, chunk)
        checkpoint = advance_checkpoint(
            store,
            table,
            skip=skip,
            rows=chunk,
            checkpoint=checkpoint,
            page_sha256="",
            key_index=key_index,
        )
        skip += len(chunk)
        pages += 1
        chunk = []

    try:
        with zipfile.ZipFile(archive_path) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if not names:
                return {"table": table, "status": "SCHEMA_MISMATCH", "reason": "zip_without_csv", "complete": False, "download_mode": BULK_MODE, "years": years, "row_count": 0}
            for name in names:
                members.append(name)
                with archive.open(name) as raw:
                    text = io.TextIOWrapper(raw, encoding="utf-8", newline="")
                    reader = csv.DictReader(text)
                    fields = [str(item).strip().lstrip("﻿") for item in (reader.fieldnames or [])]
                    missing = [field for field in required if field not in fields]
                    if missing:
                        return {
                            "table": table,
                            "status": "SCHEMA_MISMATCH",
                            "reason": "csv_missing_fields",
                            "missing": missing,
                            "member": name,
                            "complete": False,
                            "download_mode": BULK_MODE,
                            "years": years,
                            "row_count": int(checkpoint.get("committed_row_count") or 0),
                        }
                    for raw_row in reader:
                        rows_in += 1
                        row = {str(key).strip().lstrip("﻿"): value for key, value in raw_row.items() if key is not None}
                        if table in DATE_BOUND_TABLES:
                            token = str(row.get("date") or "")[:10]
                            try:
                                session = date.fromisoformat(token)
                            except ValueError:
                                dropped_bad_date += 1
                                continue
                            if session < start or session > end:
                                dropped_outside += 1
                                continue
                            if min_date is None or session < min_date:
                                min_date = session
                            if max_date is None or session > max_date:
                                max_date = session
                        chunk.append(row)
                        if len(chunk) >= limit:
                            flush()
            flush()
    finally:
        key_index.close()
    checkpoint["complete"] = True
    checkpoint["status"] = "READ_OK"
    checkpoint["next_skip"] = None
    checkpoint["download_mode"] = BULK_MODE
    checkpoint["archive_members"] = members
    checkpoint["rows_in_archive"] = rows_in
    checkpoint["rows_dropped_outside_window"] = dropped_outside
    checkpoint["rows_dropped_bad_date"] = dropped_bad_date
    checkpoint["observed_min_date"] = None if min_date is None else min_date.isoformat()
    checkpoint["observed_max_date"] = None if max_date is None else max_date.isoformat()
    save_checkpoint(store, checkpoint)
    committed = int(checkpoint.get("committed_row_count") or 0)
    return {
        "table": table,
        "status": "READ_OK",
        "row_count": committed,
        "committed_row_count": committed,
        "session_row_count": committed,
        "pages": pages,
        "complete": True,
        "download_mode": BULK_MODE,
        "years": years,
        "already_ingested": False,
        "observed_min_date": checkpoint["observed_min_date"],
        "observed_max_date": checkpoint["observed_max_date"],
        "rows_in_archive": rows_in,
        "rows_dropped_outside_window": dropped_outside,
        "rows_dropped_bad_date": dropped_bad_date,
        "pk_missing_rows": int(checkpoint.get("pk_missing_rows") or 0),
        "archive_members": members,
        "checkpoint": dict(checkpoint),
    }


def observed_shorter_than_requested(years: str, observed_min: str | None) -> bool | None:
    """A 'full' archive whose earliest row is recent did not deliver the requested tier."""

    if observed_min is None:
        return None
    try:
        earliest = date.fromisoformat(str(observed_min)[:10])
    except ValueError:
        return None
    if years == "full":
        return earliest.year >= 2015
    if years == "10":
        return earliest.year >= 2020
    return False


def bulk_first_download(
    client: SharadarClient,
    table: str,
    store: Path,
    *,
    years: str,
    dest_dir: Path,
    limit: int = DEFAULT_PAGE_LIMIT,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
) -> dict[str, Any]:
    """status=True -> zip download -> validate -> stream into the store. Any failure is reported, never masked."""

    if not credential_present():
        return {"table": table, "status": "AUTH_REQUIRED", "download_mode": BULK_MODE, "stage": "credential", "years": years, "row_count": 0, "complete": False}
    existing = load_checkpoint(store, table)
    if existing is not None and existing.get("complete") and existing.get("download_mode") == BULK_MODE:
        expected = query_signature(table, bulk_extra(table, years, start=start, end=end), limit)
        if existing.get("query_signature") == expected:
            ingest = ingest_bulk_archive(store, table, dest_dir / f"{table}_{years}.zip", years=years, limit=limit, start=start, end=end)
            ingest["stage"] = "already_ingested"
            return ingest
    meta = client.bulk_status(table, years=years)
    if meta.get("status") != "READ_OK":
        return {
            "table": table,
            "status": str(meta.get("status")),
            "download_mode": BULK_MODE,
            "stage": "status",
            "years": years,
            "metadata": meta.get("metadata") or {},
            "row_count": 0,
            "complete": False,
        }
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{table}_{years}.zip"
    download: dict[str, Any]
    if dest.is_file() and validate_bulk_archive(dest)["ok"]:
        download = {"status": "READ_OK", "path": str(dest), "reused_existing_archive": True}
    else:
        download = client.bulk_download(table, years=years, dest=dest)
    if download.get("status") != "READ_OK":
        return {
            "table": table,
            "status": str(download.get("status")),
            "download_mode": BULK_MODE,
            "stage": "download",
            "years": years,
            "metadata": meta.get("metadata") or {},
            "download": {k: v for k, v in download.items() if k != "rows"},
            "row_count": 0,
            "complete": False,
        }
    ingest = ingest_bulk_archive(store, table, dest, years=years, limit=limit, start=start, end=end)
    ingest["stage"] = "ingest"
    ingest["metadata"] = meta.get("metadata") or {}
    ingest["archive"] = {
        "path": str(dest),
        "sha256": download.get("sha256"),
        "bytes": download.get("bytes"),
        "reused_existing_archive": bool(download.get("reused_existing_archive")),
    }
    ingest["observed_shorter_than_requested"] = observed_shorter_than_requested(years, ingest.get("observed_min_date"))
    return ingest
