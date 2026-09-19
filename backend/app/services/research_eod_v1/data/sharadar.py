"""Official api.sharadar.com research adapter. Secrets stay in SHARADAR_API_KEY."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import platform
import re
import time
import zipfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.research_eod_v1.data.contract import (
    CorporateAction,
    DatasetMeta,
    ProviderCapabilities,
    ResearchBar,
    SecurityIdentity,
    hash_payload,
)
from app.services.research_eod_v1.data.sharadar_identity import (
    identity_from_ticker_row,
)
from app.services.research_eod_v1.data.sharadar_schema import (
    ALLOWED_END,
    AUTH_QUERY_PARAM,
    DEFAULT_PAGE_LIMIT,
    ENV_KEY_NAME,
    FULL_HISTORY_START,
    HISTORY_10Y_START,
    HOLDOUT_START,
    OFFICIAL_BASE_URL,
    OFFICIAL_BULK_META_FIELDS,
    OFFICIAL_CHANNEL,
    OFFICIAL_HTTPS_HOST,
    PROBE_SAMPLES,
    SOURCE_VERSION,
    TABLE_FIELDS,
    TABLES,
)
from app.services.research_eod_v1.data.sharadar_store import (
    adopt_orphan_page,
    advance_checkpoint,
    empty_checkpoint,
    load_checkpoint,
    merge_committed,
    query_signature,
    read_jsonl,
    save_checkpoint,
    verify_committed_pages,
    write_page_file,
)
from app.services.research_eod_v1.data.sharadar_tracks import convert_vendor_row
from app.services.research_eod_v1.mathutil import finite

SENSITIVE_QUERY_KEYS = frozenset({
    "api_key",
    "apikey",
    "access_key",
    "accesskey",
    "signature",
    "x-amz-signature",
    "x-amz-credential",
    "x-amz-security-token",
    "token",
    "expires",
    "x-amz-expires",
})
_SECRET_RE = re.compile(r"(api_key|apikey|signature|token|credential)=([^&]+)", re.IGNORECASE)
_HTML_PREFIXES = (b"<!doctype", b"<html", b"<?xml")


def credential_present() -> bool:
    return bool((os.environ.get(ENV_KEY_NAME) or "").strip())


def _api_key() -> str:
    return (os.environ.get(ENV_KEY_NAME) or "").strip()


def official_https_origin(url: str) -> bool:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and host == OFFICIAL_HTTPS_HOST


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in SENSITIVE_QUERY_KEYS:
            query.append((key, "REDACTED"))
        else:
            query.append((key, value))
    redacted = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    return _SECRET_RE.sub(lambda match: f"{match.group(1)}=REDACTED", redacted)


def redact_text(text: str) -> str:
    blob = _SECRET_RE.sub(lambda match: f"{match.group(1)}=REDACTED", text)
    key = _api_key()
    if key:
        blob = blob.replace(key, "REDACTED")
    return blob


def classify_entitlement(*, earliest: date | None, bulk_years: str | None) -> dict[str, Any]:
    if bulk_years == "5" or (earliest is not None and earliest > HISTORY_10Y_START):
        return {
            "status": "ENTITLEMENT_SHORT_5Y",
            "bulk_years": bulk_years,
            "earliest": None if earliest is None else earliest.isoformat(),
            "research_start": None,
            "continue": False,
        }
    if bulk_years == "10" or (earliest is not None and earliest > FULL_HISTORY_START):
        return {
            "status": "HISTORY_10Y",
            "bulk_years": bulk_years or "10",
            "earliest": None if earliest is None else earliest.isoformat(),
            "research_start": HISTORY_10Y_START.isoformat(),
            "continue": True,
        }
    if earliest is None and bulk_years is None:
        return {
            "status": "AUTH_REQUIRED",
            "bulk_years": None,
            "earliest": None,
            "research_start": None,
            "continue": False,
        }
    return {
        "status": "READ_OK",
        "bulk_years": bulk_years or "full",
        "earliest": None if earliest is None else earliest.isoformat(),
        "research_start": FULL_HISTORY_START.isoformat(),
        "continue": True,
    }


def isolate_research_window(session: date) -> str:
    if session >= HOLDOUT_START:
        return "HOLDOUT_SEALED"
    if session > ALLOWED_END:
        return "BETWEEN_ALLOWED_AND_HOLDOUT"
    if session < FULL_HISTORY_START:
        return "BEFORE_RESEARCH_START"
    return "ALLOWED"


@dataclass
class QueryPage:
    table: str
    rows: list[dict[str, Any]]
    skip: int
    limit: int
    status: str
    row_count: int
    content_sha256: str
    redacted_url: str
    next_skip: int | None
    body_kind: str = "rows"
    complete: bool | None = None


class SharadarClient:
    """Official Sharadar.com REST client. One channel only. No secret echo."""

    def __init__(
        self,
        *,
        allow_network: bool = True,
        base_url: str = OFFICIAL_BASE_URL,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
        persistence_hooks: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.allow_network = allow_network
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.sleep = sleep
        self.max_retries = max_retries
        self.persistence_hooks = dict(persistence_hooks or {})
        self.failures: list[dict[str, Any]] = []
        self.request_log: list[dict[str, Any]] = []
        self.live_request_count = 0

    def table_url(self, table: str, params: Mapping[str, Any] | None = None) -> str:
        if table not in TABLES:
            raise ValueError(f"unknown_sharadar_table:{table}")
        query = dict(params or {})
        query.setdefault("format", "json")
        return f"{self.base_url}/{table}?{urlencode(query, doseq=True)}"

    def _attach_key_if_official(self, url: str) -> str:
        if not official_https_origin(url):
            return url
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[AUTH_QUERY_PARAM] = _api_key()
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))

    def _record_request(self, url: str, status: int | None, *, error: str | None = None) -> None:
        self.live_request_count += 1
        host = (urlsplit(url).hostname or "").lower()
        self.request_log.append({
            "url": redact_url(url),
            "host": host,
            "official_https_origin": official_https_origin(url),
            "status": status,
            "error": error,
            "api_key_attached": official_https_origin(url),
        })

    def _request(self, url: str, *, follow_redirects: bool = False) -> tuple[int, bytes, str]:
        if not self.allow_network:
            raise RuntimeError("NETWORK_DISABLED")
        key = _api_key()
        if not key:
            raise PermissionError("AUTH_REQUIRED")
        signed = self._attach_key_if_official(url)
        request = urllib.request.Request(signed, headers={"Accept": "application/json,text/csv,application/zip"})
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if self.opener is not None:
                    code, body, final_url = self.opener(signed, follow_redirects=follow_redirects)
                    self._record_request(signed, int(code))
                    return int(code), body, str(final_url)
                opener = urllib.request.build_opener(
                    urllib.request.HTTPRedirectHandler() if follow_redirects else _NoRedirect()
                )
                with opener.open(request, timeout=30) as response:
                    body = response.read()
                    final = str(response.geturl())
                    self._record_request(signed, int(response.status))
                    return int(response.status), body, final
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                location = exc.headers.get("Location") or ""
                if exc.code in {301, 302, 303, 307, 308} and follow_redirects and location:
                    if official_https_origin(location):
                        return self._request(location, follow_redirects=True)
                    self._record_request(location, None, error="cross_origin_redirect")
                    return self._request_unsigned(location)
                if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_retries:
                    self.sleep(0.25 * (2 ** attempt))
                    last_error = exc
                    continue
                self.failures.append({
                    "status": exc.code,
                    "url": redact_url(url),
                    "error": "http_error",
                })
                self._record_request(signed, exc.code, error="http_error")
                return exc.code, body, redact_url(str(exc.geturl()) if getattr(exc, "geturl", None) else url)
            except PermissionError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    self.sleep(0.25 * (2 ** attempt))
                    continue
                self.failures.append({"error": type(exc).__name__, "url": redact_url(url)})
                self._record_request(signed, None, error=type(exc).__name__)
                raise
        raise RuntimeError(redact_text(str(last_error) if last_error else "request_failed"))

    def _request_unsigned(self, url: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(url, headers={"Accept": "application/zip,application/octet-stream,*/*"})
        try:
            if self.opener is not None:
                code, body, final_url = self.opener(url, follow_redirects=True)
                self._record_request(url, int(code))
                return int(code), body, str(final_url)
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            with opener.open(request, timeout=60) as response:
                body = response.read()
                self._record_request(url, int(response.status))
                return int(response.status), body, str(response.geturl())
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            self._record_request(url, exc.code, error="http_error")
            return exc.code, body, redact_url(url)

    def fetch_page(
        self,
        table: str,
        *,
        skip: int = 0,
        limit: int = DEFAULT_PAGE_LIMIT,
        extra: Mapping[str, Any] | None = None,
    ) -> QueryPage:
        if table not in TABLES:
            raise ValueError(f"unknown_sharadar_table:{table}")
        if not credential_present():
            return QueryPage(table, [], skip, limit, "AUTH_REQUIRED", 0, "", self.table_url(table), None, "auth", False)
        params = {"limit": limit, "skip": skip, "format": "json"}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        url = self.table_url(table, {k: v for k, v in params.items() if k != AUTH_QUERY_PARAM})
        try:
            status, body, final_url = self._request(url)
        except PermissionError:
            return QueryPage(table, [], skip, limit, "AUTH_REQUIRED", 0, "", redact_url(url), None, "auth", False)
        except Exception:
            return QueryPage(table, [], skip, limit, "NETWORK_UNAVAILABLE", 0, "", redact_url(url), None, "network", False)
        redacted = redact_url(final_url)
        digest = hashlib.sha256(body).hexdigest()
        if status in {401, 403}:
            return QueryPage(table, [], skip, limit, "AUTH_FAILED", 0, digest, redacted, None, "auth", False)
        if status >= 400:
            return QueryPage(table, [], skip, limit, "NETWORK_UNAVAILABLE", 0, digest, redacted, None, "http_error", False)
        inspected = inspect_table_body(body)
        if inspected["kind"] == "error":
            return QueryPage(
                table, [], skip, limit, status_from_error_payload(inspected.get("error"), status),
                0, digest, redacted, None, "error", False,
            )
        if inspected["kind"] == "html":
            return QueryPage(table, [], skip, limit, "SCHEMA_MISMATCH", 0, digest, redacted, None, "html", False)
        if inspected["kind"] == "unknown":
            return QueryPage(table, [], skip, limit, "SCHEMA_MISMATCH", 0, digest, redacted, None, "unknown", False)
        rows = inspected["rows"]
        missing = required_field_gap(table, rows)
        page_status = "SCHEMA_MISMATCH" if missing else "READ_OK"
        next_skip = skip + len(rows) if len(rows) >= limit else None
        return QueryPage(table, rows, skip, limit, page_status, len(rows), digest, redacted, next_skip, inspected["kind"], next_skip is None)

    def fetch_all(
        self,
        table: str,
        *,
        extra: Mapping[str, Any] | None = None,
        resume: Mapping[str, Any] | bool | None = None,
        max_pages: int | None = None,
        store: Path | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        include_rows: bool = True,
    ) -> dict[str, Any]:
        extra = dict(extra or {})
        signature = query_signature(table, extra, limit)
        memory_rows: list[dict[str, Any]] = []
        hashes: list[str] = []
        pages = 0
        session_new = 0
        status = "AUTH_REQUIRED"
        complete = False
        checkpoint = empty_checkpoint(table, extra=extra, limit=limit)
        skip = 0
        if store is not None:
            existing = load_checkpoint(store, table)
            if existing is not None:
                if existing.get("query_signature") != signature:
                    return {
                        "table": table,
                        "status": "CHECKPOINT_QUERY_MISMATCH",
                        "row_count": int(existing.get("committed_row_count") or 0),
                        "session_row_count": 0,
                        "committed_row_count": int(existing.get("committed_row_count") or 0),
                        "pages": 0,
                        "page_hashes": [],
                        "used_default_first_page_as_universe": False,
                        "resume_skip": existing.get("next_skip"),
                        "complete": False,
                        "rows": [],
                        "checkpoint": existing,
                    }
                errors = verify_committed_pages(store, table, existing)
                if errors:
                    return {
                        "table": table,
                        "status": "SCHEMA_MISMATCH",
                        "row_count": int(existing.get("committed_row_count") or 0),
                        "session_row_count": 0,
                        "committed_row_count": int(existing.get("committed_row_count") or 0),
                        "pages": 0,
                        "page_hashes": [],
                        "used_default_first_page_as_universe": False,
                        "resume_skip": existing.get("next_skip"),
                        "complete": False,
                        "rows": [],
                        "checkpoint_errors": errors,
                    }
                checkpoint = adopt_orphan_page(store, table, dict(existing))
                skip = int(checkpoint.get("next_skip") or 0)
                if checkpoint.get("complete"):
                    merged = merge_committed(store, table, checkpoint)
                    rows = read_jsonl(Path(merged["path"])) if include_rows and merged.get("path") else []
                    return {
                        "table": table,
                        "status": "READ_OK",
                        "row_count": merged["row_count"],
                        "session_row_count": 0,
                        "committed_row_count": merged["row_count"],
                        "pages": 0,
                        "page_hashes": [],
                        "used_default_first_page_as_universe": False,
                        "resume_skip": checkpoint.get("next_skip"),
                        "complete": True,
                        "rows": rows,
                        "checkpoint": checkpoint,
                        "first_saved_row": merged.get("first_row"),
                    }
            elif isinstance(resume, Mapping):
                skip = int(resume.get("next_skip") or resume.get("skip") or 0)
        elif isinstance(resume, Mapping):
            skip = int(resume.get("next_skip") or resume.get("skip") or 0)

        while True:
            if max_pages is not None and pages >= max_pages:
                status = "PARTIAL"
                complete = False
                break
            page = self.fetch_page(table, skip=skip, limit=limit, extra=extra)
            pages += 1
            hashes.append(page.content_sha256)
            status = page.status
            if page.status != "READ_OK":
                complete = False
                break
            if not page.rows:
                status = "READ_OK"
                complete = True
                break
            if len(page.rows) > limit:
                status = "SCHEMA_MISMATCH"
                complete = False
                break
            hook = self.persistence_hooks.get("before_commit_page")
            if hook is not None:
                hook(page)
            if store is not None:
                write_page_file(store, table, page.skip, page.rows)
                after_page = self.persistence_hooks.get("after_page_durable")
                if after_page is not None:
                    after_page(page)
                checkpoint = advance_checkpoint(
                    store,
                    table,
                    skip=page.skip,
                    rows=page.rows,
                    checkpoint=checkpoint,
                    page_sha256=page.content_sha256,
                )
                after_ck = self.persistence_hooks.get("after_checkpoint_durable")
                if after_ck is not None:
                    after_ck(checkpoint)
            else:
                memory_rows.extend(page.rows)
            session_new += len(page.rows)
            if page.next_skip is None:
                status = "READ_OK"
                complete = True
                break
            skip = page.next_skip

        committed_total = session_new
        first_row = memory_rows[0] if memory_rows else None
        merged_info: dict[str, Any] | None = None
        if store is not None:
            checkpoint["status"] = status if not complete else "READ_OK"
            checkpoint["complete"] = complete
            if complete and status == "READ_OK":
                checkpoint["status"] = "READ_OK"
            save_checkpoint(store, checkpoint)
            if checkpoint.get("committed_pages"):
                merged_info = merge_committed(store, table, checkpoint)
                committed_total = int(merged_info["row_count"])
                first_row = merged_info.get("first_row")
                if include_rows:
                    memory_rows = read_jsonl(Path(merged_info["path"]))
            elif include_rows:
                memory_rows = []
        return {
            "table": table,
            "status": status,
            "row_count": committed_total if store is not None else session_new,
            "session_row_count": session_new,
            "committed_row_count": committed_total if store is not None else session_new,
            "pages": pages,
            "page_hashes": hashes,
            "used_default_first_page_as_universe": False,
            "resume_skip": skip,
            "complete": complete,
            "rows": memory_rows if include_rows else [],
            "checkpoint": checkpoint if store is not None else {
                "skip": skip,
                "next_skip": None if complete else skip,
                "query_signature": signature,
            },
            "first_saved_row": first_row,
            "merged": merged_info,
        }

    def bulk_status(self, table: str) -> dict[str, Any]:
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "metadata": {}}
        url = self.table_url(table, {"status": "True", "format": "json"})
        try:
            code, body, final_url = self._request(url)
        except PermissionError:
            return {"table": table, "status": "AUTH_REQUIRED", "metadata": {}}
        except Exception:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "metadata": {}}
        inspected = inspect_table_body(body)
        if code in {401, 403}:
            return {"table": table, "status": "AUTH_FAILED", "url": redact_url(final_url), "metadata": {}}
        if code >= 400:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "url": redact_url(final_url), "metadata": {}}
        if inspected["kind"] in {"error", "html", "unknown"}:
            status = status_from_error_payload(inspected.get("error"), code) if inspected["kind"] == "error" else "SCHEMA_MISMATCH"
            return {"table": table, "status": status, "url": redact_url(final_url), "metadata": {}, "body_kind": inspected["kind"]}
        payload = _decode_json(body)
        metadata = parse_bulk_metadata(payload)
        return {
            "table": table,
            "status": metadata["status"],
            "url": redact_url(final_url),
            "metadata": metadata["metadata"],
            "history_years_from_filename": None,
            "filename_does_not_prove_entitlement": True,
        }

    def bulk_download(self, table: str, *, years: str, dest: Path) -> dict[str, Any]:
        if years not in {"5", "10", "full"}:
            raise ValueError("bulk_years_must_be_5_10_or_full")
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "years": years, "final_file_exists": dest.exists()}
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = self.table_url(table, {"years": years})
        try:
            code, body, final_url = self._request(url, follow_redirects=True)
        except PermissionError:
            return {"table": table, "status": "AUTH_REQUIRED", "years": years, "final_file_exists": dest.exists()}
        except Exception:
            return {
                "table": table,
                "status": "NETWORK_UNAVAILABLE",
                "years": years,
                "url": redact_url(url),
                "final_file_exists": dest.exists(),
            }
        if code in {401, 403}:
            return {"table": table, "status": "AUTH_FAILED", "years": years, "url": redact_url(final_url), "final_file_exists": dest.exists()}
        if code < 200 or code >= 300:
            return {
                "table": table,
                "status": "NETWORK_UNAVAILABLE",
                "years": years,
                "http_status": code,
                "url": redact_url(final_url),
                "final_file_exists": dest.exists(),
            }
        tmp = dest.with_name(dest.name + ".partial")
        tmp.write_bytes(body)
        check = validate_bulk_archive(tmp)
        if not check["ok"]:
            tmp.unlink(missing_ok=True)
            return {
                "table": table,
                "status": "SCHEMA_MISMATCH",
                "years": years,
                "reason": check["reason"],
                "url": redact_url(final_url),
                "final_file_exists": dest.exists(),
            }
        tmp.replace(dest)
        return {
            "table": table,
            "status": "READ_OK",
            "years": years,
            "bytes": dest.stat().st_size,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest() if dest.stat().st_size < 8_000_000 else _sha256_stream(dest),
            "path": str(dest),
            "url": redact_url(final_url),
            "final_file_exists": dest.exists(),
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def inspect_table_body(body: bytes) -> dict[str, Any]:
    if not body:
        return {"kind": "empty", "rows": []}
    raw = body.lstrip()
    if raw[:20].lower().startswith(_HTML_PREFIXES):
        return {"kind": "html", "rows": []}
    text = body.decode("utf-8", errors="replace").lstrip("\ufeff")
    stripped = text.lstrip()
    if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
        return {"kind": "html", "rows": []}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        if "," in stripped.splitlines()[0] if stripped.splitlines() else "":
            reader = csv.DictReader(io.StringIO(text))
            rows = [dict(row) for row in reader]
            return {"kind": "rows" if rows else "empty", "rows": rows}
        return {"kind": "unknown", "rows": []}
    if isinstance(payload, list):
        rows = [item for item in payload if isinstance(item, dict)]
        return {"kind": "rows" if rows else "empty", "rows": rows}
    if not isinstance(payload, dict):
        return {"kind": "unknown", "rows": []}
    if "error" in payload or "errors" in payload:
        return {"kind": "error", "rows": [], "error": payload}
    if isinstance(payload.get("data"), list):
        data = payload["data"]
        columns = payload.get("columns") or payload.get("fields")
        if columns and data and data and not isinstance(data[0], dict):
            names = [str(col) for col in columns]
            rows = [dict(zip(names, row)) for row in data]
            return {"kind": "rows" if rows else "empty", "rows": rows}
        rows = [item for item in data if isinstance(item, dict)]
        return {"kind": "rows" if rows else "empty", "rows": rows}
    if isinstance(payload.get("rows"), list):
        rows = [item for item in payload["rows"] if isinstance(item, dict)]
        return {"kind": "rows" if rows else "empty", "rows": rows}
    if any(key in payload for key in OFFICIAL_BULK_META_FIELDS):
        return {"kind": "metadata", "rows": [], "metadata": payload}
    return {"kind": "unknown", "rows": []}


def parse_table_body(body: bytes) -> list[dict[str, Any]]:
    return list(inspect_table_body(body)["rows"])


def status_from_error_payload(payload: Any, http_status: int) -> str:
    blob = json.dumps(payload or {}, default=str).lower()
    if http_status in {401, 403} or "invalid api key" in blob or "unauthorized" in blob:
        return "AUTH_FAILED"
    if "not subscribed" in blob or "entitlement" in blob or "permission" in blob:
        return "ENTITLEMENT_MISSING"
    return "VENDOR_ERROR"


def parse_bulk_metadata(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {"status": "SCHEMA_MISMATCH", "metadata": {}}
    if "error" in payload or "errors" in payload:
        return {"status": status_from_error_payload(payload, 200), "metadata": {}}
    metadata = {field: payload.get(field) for field in OFFICIAL_BULK_META_FIELDS}
    if all(metadata[field] is None for field in OFFICIAL_BULK_META_FIELDS):
        return {"status": "SCHEMA_MISMATCH", "metadata": metadata}
    return {"status": "READ_OK", "metadata": metadata}


def validate_bulk_archive(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "reason": "empty"}
    with path.open("rb") as handle:
        head = handle.read(32)
    if head.lstrip().lower().startswith(_HTML_PREFIXES):
        return {"ok": False, "reason": "html"}
    if not zipfile.is_zipfile(path):
        return {"ok": False, "reason": "not_zip"}
    try:
        with zipfile.ZipFile(path) as archive:
            broken = archive.testzip()
            if broken:
                return {"ok": False, "reason": "truncated_zip", "member": broken}
            if not archive.namelist():
                return {"ok": False, "reason": "empty_zip"}
    except zipfile.BadZipFile:
        return {"ok": False, "reason": "truncated_zip"}
    return {"ok": True, "reason": "zip"}


def required_field_gap(table: str, rows: list[dict[str, Any]]) -> tuple[str, ...]:
    if not rows:
        return ()
    required = TABLE_FIELDS[table]
    present = set(rows[0].keys())
    return tuple(field for field in required if field not in present)


def _decode_json(body: bytes) -> Any:
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def _sha256_stream(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


class SharadarProvider:
    def __init__(self, *, allow_network: bool = True, client: SharadarClient | None = None) -> None:
        self.allow_network = allow_network
        self.client = client or SharadarClient(allow_network=allow_network)
        self.failures = self.client.failures
        self._access_tested = False
        self._last_access_status: str | None = None

    def probe_capabilities(self) -> ProviderCapabilities:
        present = credential_present()
        notes = [
            "channel=api.sharadar.com",
            "credential_from_env_SHARADAR_API_KEY_only",
            "nasdaq_data_link_not_attempted",
            "chat_credentials_not_read",
            "yahoo_not_used_as_fallback",
            "default_first_10000_is_not_universe",
            "credential_present_is_not_ACCESS_TESTED",
        ]
        if not present:
            notes.append("AUTH_REQUIRED")
            return ProviderCapabilities(
                provider="sharadar",
                dataset_id="sharadar-official-v1",
                dataset_version=SOURCE_VERSION,
                probe_status="AUTH_REQUIRED",
                daily_bars="AUTH_REQUIRED",
                corporate_actions="AUTH_REQUIRED",
                classification_history="AUTH_REQUIRED",
                security_master="AUTH_REQUIRED",
                delisted_coverage="AUTH_REQUIRED",
                volume_session_scope="UNKNOWN",
                raw_price_verified=False,
                notes=tuple(notes),
                capability_level="DOCUMENTED_ONLY",
            )
        if not self.allow_network:
            return ProviderCapabilities(
                provider="sharadar",
                dataset_id="sharadar-official-v1",
                dataset_version=SOURCE_VERSION,
                probe_status="DOCUMENTED_ONLY",
                daily_bars="DOCUMENTED_ONLY",
                corporate_actions="DOCUMENTED_ONLY",
                classification_history="DOCUMENTED_ONLY",
                security_master="DOCUMENTED_ONLY",
                delisted_coverage="documented_survivorship_free",
                volume_session_scope="UNKNOWN",
                raw_price_verified=False,
                notes=tuple(notes + ["network_disabled"]),
                capability_level="DOCUMENTED_ONLY",
            )
        page = self.client.fetch_page("tickers", extra={"ticker": PROBE_SAMPLES["non_free_example"]})
        self._last_access_status = page.status
        self._access_tested = page.status == "READ_OK"
        level = "ACCESS_TESTED" if self._access_tested else "DOCUMENTED_ONLY"
        notes.append(f"access_request_status={page.status}")
        return ProviderCapabilities(
            provider="sharadar",
            dataset_id="sharadar-official-v1",
            dataset_version=SOURCE_VERSION,
            probe_status=page.status if page.status != "READ_OK" else "ACCESS_TESTED",
            daily_bars="available" if self._access_tested else page.status,
            corporate_actions="available" if self._access_tested else page.status,
            classification_history="partial_actions_sic_only" if self._access_tested else page.status,
            security_master="available" if self._access_tested else page.status,
            delisted_coverage="documented_survivorship_free" if self._access_tested else page.status,
            volume_session_scope="UNKNOWN",
            raw_price_verified=False,
            notes=tuple(notes),
            capability_level=level,
        )

    def load_security_master(self) -> list[SecurityIdentity] | str:
        payload = self.client.fetch_all("tickers", extra={"table": "stocks"})
        if payload["status"] != "READ_OK":
            return payload["status"]
        out: list[SecurityIdentity] = []
        for row in payload["rows"]:
            identity = identity_from_ticker_row(row)
            out.append(
                SecurityIdentity(
                    security_id=identity.security_id,
                    provider_symbol=identity.ticker,
                    share_class=identity.share_class,
                    security_type=identity.security_type,
                    primary_mic=None,
                    listing_country="US",
                    asset_track=identity.asset_track,
                    listed_at=None,
                    delisted_at=None,
                    aliases=(),
                    identity_confidence="permaticker",
                )
            )
        return out

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
        table: str = "stocks",
    ) -> list[ResearchBar] | str:
        payload = self.client.fetch_all(
            table,
            extra={"ticker": symbol, "from": start.isoformat(), "to": end.isoformat()},
        )
        if payload["status"] != "READ_OK":
            return payload["status"]
        retrieved = datetime.now(timezone.utc)
        out: list[ResearchBar] = []
        security_id = identity.security_id if identity else f"ticker:{symbol}"
        for row in payload["rows"]:
            tracks = convert_vendor_row(row)
            window = isolate_research_window(tracks.session_date)
            out.append(
                ResearchBar(
                    security_id=security_id,
                    session_date=tracks.session_date,
                    open=tracks.open,
                    high=tracks.high,
                    low=tracks.low,
                    close=tracks.close,
                    raw_open=tracks.raw_open,
                    raw_close=tracks.raw_close,
                    volume=tracks.volume,
                    dollar_volume=tracks.split_dollar_volume,
                    tri=tracks.closeadj,
                    volume_scope="UNKNOWN",
                    price_adjustment="sharadar_split_adjusted",
                    volume_adjustment="sharadar_split_adjusted",
                    source_published_at=None,
                    retrieved_at=retrieved,
                    vintage_status="download_time_not_pit_source_published_unknown" if window == "ALLOWED" else window,
                )
            )
        return out

    def fetch_corporate_actions(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
    ) -> list[CorporateAction] | str:
        payload = self.client.fetch_all(
            "actions",
            extra={"ticker": symbol, "from": start.isoformat(), "to": end.isoformat()},
        )
        if payload["status"] != "READ_OK":
            return payload["status"]
        security_id = identity.security_id if identity else f"ticker:{symbol}"
        out: list[CorporateAction] = []
        for row in payload["rows"]:
            session = _parse_date(row.get("date"))
            if session is None:
                continue
            raw_value = row.get("value")
            number = finite(raw_value) if raw_value not in (None, "") else None
            if raw_value not in (None, "") and number is None:
                economic = "UNSUPPORTED"
                reason = "non_numeric_value"
            elif number is None:
                economic = "UNSUPPORTED"
                reason = "missing_value"
            else:
                economic = "ok"
                reason = None
            out.append(
                CorporateAction(
                    security_id=security_id,
                    session_date=session,
                    kind=str(row.get("action") or "unknown"),
                    value=number,
                    effective_at=session,
                    raw_value=None if raw_value is None else str(raw_value),
                    contraticker=None if row.get("contraticker") in (None, "") else str(row.get("contraticker")),
                    contraname=None if row.get("contraname") in (None, "") else str(row.get("contraname")),
                    economic_status=economic,
                    reason=reason,
                )
            )
        return out

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str:
        payload = self.client.fetch_all("actions", extra={"ticker": symbol})
        if payload["status"] != "READ_OK":
            return payload["status"]
        changes = [row for row in payload["rows"] if str(row.get("action") or "").startswith("sicchange")]
        return {
            "symbol": symbol,
            "flag": "CLASSIFICATION_CURRENT",
            "current_table_is_not_pit": True,
            "sic_change_actions": changes,
        }

    def export_snapshot(self, path: str) -> DatasetMeta | str:
        return DatasetMeta(
            provider="sharadar",
            dataset_id="sharadar-official-v1",
            dataset_version=SOURCE_VERSION,
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            request_params_redacted={"auth": ENV_KEY_NAME, "channel": OFFICIAL_CHANNEL},
            content_sha256=hash_payload(self.failures),
        )


def runtime_probe_identity() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "hostname_hash": hashlib.sha256(platform.node().encode("utf-8")).hexdigest()[:16],
        "env_key_name": ENV_KEY_NAME,
    }


def run_sharadar_probe(*, allow_network: bool = True, client: SharadarClient | None = None) -> dict[str, Any]:
    present = credential_present()
    adapter = client or SharadarClient(allow_network=allow_network)
    tables: dict[str, Any] = {}
    for table in TABLES:
        extra = {"ticker": PROBE_SAMPLES["history_example"], "from": "2010-01-01", "to": "2010-01-08"}
        if table == "tickers":
            extra = {"ticker": PROBE_SAMPLES["history_example"]}
        if table == "actions":
            extra = {"ticker": PROBE_SAMPLES["history_example"], "from": "2010-01-01", "to": "2010-12-31"}
        if table == "funds":
            extra = {"ticker": PROBE_SAMPLES["fund_example"], "from": "2010-01-01", "to": "2010-01-08"}
        page = adapter.fetch_page(table, extra=extra)
        tables[table] = {
            "alias": TABLES[table]["alias"],
            "status": page.status,
            "row_count": page.row_count,
            "redacted_url": page.redacted_url,
            "schema_gap": required_field_gap(table, page.rows),
            "complete": page.complete,
            "body_kind": page.body_kind,
        }
    entitlement = classify_entitlement(earliest=None, bulk_years=None)
    samples = {
        "non_free_example": "AUTH_REQUIRED",
        "delisted": "AUTH_REQUIRED",
        "history_2010": "AUTH_REQUIRED",
        "funds_scope": "AUTH_REQUIRED",
        "aapl_earliest": None,
        "non_free_example_ticker": PROBE_SAMPLES["non_free_example"],
        "history_example_ticker": PROBE_SAMPLES["history_example"],
        "aapl_not_used_as_non_free_proof": True,
        "delisted_not_concluded_missing_history": True,
    }
    if present:
        non_free = adapter.fetch_page(
            "stocks",
            extra={"ticker": PROBE_SAMPLES["non_free_example"], "from": "2010-01-01", "to": "2010-01-08"},
        )
        history = adapter.fetch_page(
            "stocks",
            extra={"ticker": PROBE_SAMPLES["history_example"], "from": "1998-01-01", "to": "2016-12-31"},
        )
        delisted_id = adapter.fetch_page("tickers", extra={"ticker": PROBE_SAMPLES["delisted_example"]})
        delisted_px = adapter.fetch_page(
            "stocks",
            extra={"ticker": PROBE_SAMPLES["delisted_example"], "from": "2010-01-01", "to": "2024-06-28"},
        )
        delisted_actions = adapter.fetch_page("actions", extra={"ticker": PROBE_SAMPLES["delisted_example"]})
        funds = adapter.fetch_page(
            "funds",
            extra={"ticker": PROBE_SAMPLES["fund_example"], "from": "2010-01-01", "to": "2010-01-08"},
        )
        dates = sorted(str(row.get("date") or "")[:10] for row in history.rows if row.get("date"))
        earliest = date.fromisoformat(dates[0]) if dates else None
        entitlement = classify_entitlement(earliest=earliest, bulk_years=None)
        tables["stocks"]["earliest_aapl"] = dates[0] if dates else None
        samples["non_free_example"] = non_free.status
        samples["history_2010"] = history.status
        samples["funds_scope"] = funds.status
        samples["aapl_earliest"] = dates[0] if dates else None
        if delisted_id.rows or delisted_px.rows or delisted_actions.rows:
            samples["delisted"] = "READ_OK" if delisted_id.status == "READ_OK" or delisted_px.status == "READ_OK" else delisted_id.status
            samples["delisted_not_concluded_missing_history"] = False
        else:
            samples["delisted"] = "IDENTITY_UNRESOLVED" if delisted_id.status == "READ_OK" else delisted_id.status
            samples["delisted_not_concluded_missing_history"] = True
        tables["stocks"]["non_free_status"] = non_free.status
        tables["tickers"]["delisted_status"] = delisted_id.status
    report = {
        "credential_present": present,
        "runtime": runtime_probe_identity(),
        "provider": "sharadar",
        "channel": OFFICIAL_CHANNEL,
        "channel_confirmed": (
            "official_https_origin_api.sharadar.com"
            if present and adapter.live_request_count and all(item.get("official_https_origin") or not item.get("api_key_attached") for item in adapter.request_log)
            else ("official_docs_mapped; live_channel_unconfirmed_without_secret" if not present else "official_https_origin_attempted")
        ),
        "nasdaq_data_link_attempted": False,
        "chat_credentials_read": False,
        "massive_key_used": False,
        "yahoo_fallback_used": False,
        "live_sharadar_request_count": adapter.live_request_count,
        "tables": tables,
        "samples": samples,
        "entitlement": entitlement,
        "volume_session_scope": "UNKNOWN",
        "secret_present_in_report": False,
        "terminal_status": _terminal_from_probe(present, tables, entitlement),
    }
    blob = json.dumps(report, default=str)
    if _api_key() and _api_key() in blob:
        raise RuntimeError("secret_leaked_into_probe_report")
    return report


def _terminal_from_probe(present: bool, tables: Mapping[str, Any], entitlement: Mapping[str, Any]) -> str:
    if not present:
        return "AUTH_REQUIRED"
    if entitlement.get("status") == "ENTITLEMENT_SHORT_5Y":
        return "ENTITLEMENT_MISSING"
    statuses = {str(item.get("status")) for item in tables.values()}
    if "NETWORK_UNAVAILABLE" in statuses and not statuses - {"NETWORK_UNAVAILABLE", "AUTH_REQUIRED"}:
        return "NETWORK_UNAVAILABLE"
    if "AUTH_FAILED" in statuses:
        return "AUTH_FAILED"
    if "READ_OK" in statuses:
        return "DATA_GATE_PARTIAL_REVIEW_REQUIRED"
    return "AUTH_REQUIRED"


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
