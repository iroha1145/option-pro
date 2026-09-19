"""Official api.sharadar.com research adapter. Secrets stay in SHARADAR_API_KEY.

Transport rules that the first real run depends on:

* A page is only "complete" when the vendor returns a valid empty page. A short
  page, an empty body, a redirect or an HTTP error never ends a table.
* Every committed page appends its fresh rows to the merged file; nothing here
  holds a table in memory and nothing rewrites the merged file on resume.
* Entitlement comes from the bulk-download metadata (5 / 10 / full), never from
  the earliest row that happened to be observed.
* The API key is attached only to the official https origin and is redacted
  from every URL, error and report, including its URL-encoded form.
"""

from __future__ import annotations

import csv
import email.utils
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
from typing import Any, Callable, Mapping, Sequence
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
    BULK_YEARS_PROBE_ORDER,
    DEFAULT_PAGE_LIMIT,
    ENV_KEY_NAME,
    FULL_HISTORY_START,
    HISTORY_10Y_START,
    HOLDOUT_START,
    OFFICIAL_BASE_URL,
    OFFICIAL_BULK_FILE_FIELDS,
    OFFICIAL_BULK_FILE_LIST_FIELD,
    OFFICIAL_BULK_META_FIELDS,
    OFFICIAL_CHANNEL,
    OFFICIAL_HTTPS_HOST,
    PROBE_SAMPLES,
    SOURCE_VERSION,
    TABLE_FIELDS,
    TABLES,
    TICKERS_OPTIONAL_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import (
    UniqueKeyIndex,
    adopt_orphan_page,
    advance_checkpoint,
    count_rows_by_date,
    empty_checkpoint,
    load_checkpoint,
    merge_committed,
    merged_content_record,
    merged_path,
    query_signature,
    read_jsonl,
    save_checkpoint,
    verify_committed_pages,
    verify_merged_content,
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
_SECRET_RE = re.compile(r"(api_key|apikey|signature|token|credential)=([^&\s\"']+)", re.IGNORECASE)
_HTML_PREFIXES = (b"<!doctype", b"<html", b"<?xml")
_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_RETRY_CODES = frozenset({429, 500, 502, 503, 504})
_RETRY_AFTER_CAP_SECONDS = 60.0
_PAGE_TIMEOUT_SECONDS = 60
_BULK_TIMEOUT_SECONDS = 600


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
    """Remove the key in plain, URL-encoded and plus-encoded forms."""

    blob = _SECRET_RE.sub(lambda match: f"{match.group(1)}=REDACTED", text)
    key = _api_key()
    if key:
        for form in {key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}:
            if form:
                blob = blob.replace(form, "REDACTED")
    return blob


def classify_entitlement(
    *,
    earliest: date | None,
    bulk_years: str | None,
    verified_access: bool | None = None,
    credential: bool | None = None,
) -> dict[str, Any]:
    """Authorized range and observed coverage are separate columns.

    ``bulk_years`` is the only entitlement signal. ``earliest`` is the earliest row
    actually observed, which a late listing, a holiday, or a gap can move without
    saying anything about the subscription. An unknown tier never turns the
    observed earliest row into a research start date.
    """

    observed = {
        "earliest_observed_row": None if earliest is None else earliest.isoformat(),
        "earliest_is_coverage_not_license": True,
        "requested_from": FULL_HISTORY_START.isoformat(),
        "requested_to": ALLOWED_END.isoformat(),
    }
    access = bool(earliest is not None) if verified_access is None else bool(verified_access)
    has_key = (access or earliest is not None) if credential is None else bool(credential)
    if not has_key:
        return {
            "status": "AUTH_REQUIRED",
            "verified_access": False,
            "observed_coverage": observed,
            "authorized_range_unknown": True,
            "bulk_years": None,
            "earliest": None,
            "research_start": None,
            "continue": False,
            "needs_review": False,
        }
    if bulk_years == "5":
        return {
            "status": "ENTITLEMENT_SHORT_5Y",
            "verified_access": access,
            "observed_coverage": observed,
            "authorized_range_unknown": False,
            "bulk_years": bulk_years,
            "earliest": observed["earliest_observed_row"],
            "research_start": None,
            "continue": False,
            "needs_review": True,
        }
    if bulk_years == "10":
        return {
            "status": "HISTORY_10Y",
            "verified_access": access,
            "observed_coverage": observed,
            "authorized_range_unknown": False,
            "bulk_years": bulk_years,
            "earliest": observed["earliest_observed_row"],
            "research_start": HISTORY_10Y_START.isoformat(),
            "continue": True,
            "needs_review": False,
        }
    if bulk_years in {"full", "all"}:
        return {
            "status": "READ_OK",
            "verified_access": access,
            "observed_coverage": observed,
            "authorized_range_unknown": False,
            "bulk_years": bulk_years,
            "earliest": observed["earliest_observed_row"],
            "research_start": FULL_HISTORY_START.isoformat(),
            "continue": True,
            "needs_review": False,
        }
    if earliest is None:
        return {
            "status": "COVERAGE_UNOBSERVED",
            "verified_access": access,
            "observed_coverage": observed,
            "authorized_range_unknown": True,
            "bulk_years": None,
            "earliest": None,
            "research_start": None,
            "continue": False,
            "needs_review": True,
        }
    return {
        "status": "ACCESS_VERIFIED_RANGE_UNKNOWN",
        "verified_access": access,
        "observed_coverage": observed,
        "authorized_range_unknown": True,
        "bulk_years": None,
        "earliest": observed["earliest_observed_row"],
        # Unknown tier: the requested window is the only defensible start; the
        # observed earliest row stays in observed_coverage.
        "research_start": None,
        "continue": True,
        "needs_review": True,
    }


def isolate_research_window(session: date) -> str:
    if session >= HOLDOUT_START:
        return "HOLDOUT_SEALED"
    if session > ALLOWED_END:
        return "BETWEEN_ALLOWED_AND_HOLDOUT"
    if session < FULL_HISTORY_START:
        return "BEFORE_RESEARCH_START"
    return "ALLOWED"


def _retry_after_seconds(headers: Any, *, attempt: int) -> float:
    """Honour Retry-After (seconds or HTTP-date, capped); otherwise exponential backoff."""

    raw = None
    try:
        raw = headers.get("Retry-After") if headers is not None else None
    except Exception:  # pragma: no cover - defensive against odd header objects
        raw = None
    if raw:
        text = str(raw).strip()
        if text.isdigit():
            return min(float(text), _RETRY_AFTER_CAP_SECONDS)
        try:
            when = email.utils.parsedate_to_datetime(text)
            delta = (when - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, min(delta, _RETRY_AFTER_CAP_SECONDS))
        except (TypeError, ValueError):
            pass
    return min(0.5 * (2 ** attempt), 8.0)


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
    short_page: bool = False
    pk_missing: int = 0
    http_status: int | None = None
    vendor_error: dict[str, Any] | None = None


class SharadarClient:
    """Official Sharadar.com REST client. One channel only. No secret echo."""

    def __init__(
        self,
        *,
        allow_network: bool = True,
        base_url: str = OFFICIAL_BASE_URL,
        opener: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
        persistence_hooks: Mapping[str, Callable[..., Any]] | None = None,
    ) -> None:
        self.allow_network = allow_network
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.sleep = sleep
        self.max_retries = max(1, int(max_retries))
        self.persistence_hooks = dict(persistence_hooks or {})
        self.failures: list[dict[str, Any]] = []
        self.request_log: list[dict[str, Any]] = []
        self.live_request_count = 0

    # ------------------------------------------------------------------ urls
    def table_url(self, table: str, params: Mapping[str, Any] | None = None, *, with_format: bool = True) -> str:
        if table not in TABLES:
            raise ValueError(f"unknown_sharadar_table:{table}")
        query = {k: v for k, v in dict(params or {}).items() if v is not None}
        if with_format:
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
        official = official_https_origin(url)
        self.request_log.append({
            "url": redact_url(url),
            "host": host,
            "official_https_origin": official,
            "status": status,
            "error": error,
            "api_key_attached": bool(official and _api_key()),
        })

    def channel_confirmed(self) -> bool:
        """True only when every request that carried the key went to the official https origin."""

        signed = [item for item in self.request_log if item.get("api_key_attached")]
        return bool(signed) and all(item.get("official_https_origin") for item in signed)

    # ------------------------------------------------------------- transport
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
                    if int(code) in _RETRY_CODES and attempt + 1 < self.max_retries:
                        self.sleep(_retry_after_seconds(None, attempt=attempt))
                        continue
                    return int(code), body, str(final_url)
                opener = urllib.request.build_opener(
                    urllib.request.HTTPRedirectHandler() if follow_redirects else _NoRedirect()
                )
                with opener.open(request, timeout=_PAGE_TIMEOUT_SECONDS) as response:
                    body = response.read()
                    final = str(response.geturl())
                    self._record_request(signed, int(response.status))
                    return int(response.status), body, final
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                location = exc.headers.get("Location") or "" if exc.headers is not None else ""
                if exc.code in _REDIRECT_CODES and follow_redirects and location:
                    if official_https_origin(location):
                        return self._request(location, follow_redirects=True)
                    self._record_request(location, None, error="cross_origin_redirect")
                    return self._request_unsigned(location)
                if exc.code in _RETRY_CODES and attempt + 1 < self.max_retries:
                    self._record_request(signed, exc.code, error="retry")
                    self.sleep(_retry_after_seconds(exc.headers, attempt=attempt))
                    last_error = exc
                    continue
                self.failures.append({
                    "status": exc.code,
                    "url": redact_url(url),
                    "error": "http_error",
                })
                self._record_request(signed, exc.code, error="http_error")
                final = redact_url(str(exc.geturl()) if getattr(exc, "geturl", None) else url)
                return exc.code, body, final
            except PermissionError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    self.sleep(_retry_after_seconds(None, attempt=attempt))
                    continue
                self.failures.append({"error": type(exc).__name__, "url": redact_url(url)})
                self._record_request(signed, None, error=type(exc).__name__)
                raise RuntimeError(redact_text(f"{type(exc).__name__}: {exc}")) from None
        raise RuntimeError(redact_text(str(last_error) if last_error else "request_failed"))

    def _request_unsigned(self, url: str) -> tuple[int, bytes, str]:
        request = urllib.request.Request(url, headers={"Accept": "application/zip,application/octet-stream,*/*"})
        try:
            if self.opener is not None:
                code, body, final_url = self.opener(url, follow_redirects=True)
                self._record_request(url, int(code))
                return int(code), body, str(final_url)
            opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
            with opener.open(request, timeout=_BULK_TIMEOUT_SECONDS) as response:
                body = response.read()
                self._record_request(url, int(response.status))
                return int(response.status), body, str(response.geturl())
        except urllib.error.HTTPError as exc:
            body = exc.read() if hasattr(exc, "read") else b""
            self._record_request(url, exc.code, error="http_error")
            return exc.code, body, redact_url(url)

    # ------------------------------------------------------------------ pages
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
        vendor = redacted_vendor_error(body, status)
        if 300 <= status < 400:
            return QueryPage(table, [], skip, limit, "REDIRECT_UNEXPECTED", 0, digest, redacted, None, "redirect", False, http_status=status, vendor_error=vendor)
        if status in {401, 403}:
            return QueryPage(table, [], skip, limit, "AUTH_FAILED", 0, digest, redacted, None, "auth", False, http_status=status, vendor_error=vendor)
        if status >= 400 or status < 200:
            return QueryPage(table, [], skip, limit, "NETWORK_UNAVAILABLE", 0, digest, redacted, None, "http_error", False, http_status=status, vendor_error=vendor)
        inspected = inspect_table_body(body)
        kind = inspected["kind"]
        if kind == "empty_body":
            return QueryPage(table, [], skip, limit, "EMPTY_BODY", 0, digest, redacted, None, "empty_body", False, http_status=status)
        if kind == "error":
            return QueryPage(
                table, [], skip, limit, status_from_error_payload(inspected.get("error"), status),
                0, digest, redacted, None, "error", False, http_status=status, vendor_error=vendor,
            )
        if kind in {"html", "unknown", "metadata"}:
            return QueryPage(table, [], skip, limit, "SCHEMA_MISMATCH", 0, digest, redacted, None, kind, False, http_status=status, vendor_error=vendor)
        rows = inspected["rows"]
        missing = required_field_gap(table, rows)
        if missing:
            return QueryPage(table, [], skip, limit, "SCHEMA_MISMATCH", 0, digest, redacted, None, "rows", False, http_status=status)
        if len(rows) > limit:
            return QueryPage(table, [], skip, limit, "SCHEMA_MISMATCH", len(rows), digest, redacted, None, "rows", False, http_status=status)
        pk_missing = sum(1 for row in rows if _pk_missing(table, row))
        if not rows:
            # A valid empty page is the only end-of-table signal.
            return QueryPage(table, [], skip, limit, "READ_OK", 0, digest, redacted, None, "empty", True, http_status=status)
        return QueryPage(
            table, rows, skip, limit, "READ_OK", len(rows), digest, redacted, skip + len(rows), "rows", False,
            short_page=len(rows) < limit, pk_missing=pk_missing, http_status=status,
        )

    def fetch_all(
        self,
        table: str,
        *,
        extra: Mapping[str, Any] | None = None,
        resume: Mapping[str, Any] | bool | None = None,
        max_pages: int | None = None,
        store: Path | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
        include_rows: bool | None = None,
    ) -> dict[str, Any]:
        extra = dict(extra or {})
        if include_rows is None:
            include_rows = store is None
        signature = query_signature(table, extra, limit)
        memory_rows: list[dict[str, Any]] = []
        hashes: list[str] = []
        pages = 0
        session_new = 0
        status = "AUTH_REQUIRED"
        complete = False
        checkpoint = empty_checkpoint(table, extra=extra, limit=limit)
        skip = 0
        key_index: UniqueKeyIndex | None = None
        short_page_observed: int | None = None
        try:
            if store is not None:
                existing = load_checkpoint(store, table)
                if existing is not None:
                    if existing.get("query_signature") != signature:
                        return self._fetch_result(
                            table, "CHECKPOINT_QUERY_MISMATCH", existing, pages=0, hashes=[], session_new=0,
                            complete=False, rows=[], resume_skip=existing.get("next_skip"), store=store,
                        )
                    errors = verify_committed_pages(store, table, existing)
                    if errors:
                        payload = self._fetch_result(
                            table, "SCHEMA_MISMATCH", existing, pages=0, hashes=[], session_new=0,
                            complete=False, rows=[], resume_skip=existing.get("next_skip"), store=store,
                        )
                        payload["checkpoint_errors"] = errors
                        return payload
                    checkpoint = adopt_orphan_page(store, table, dict(existing))
                    skip = int(checkpoint.get("next_skip") or 0)
                    key_index = UniqueKeyIndex(store, table)
                    if key_index.count != int(checkpoint.get("committed_row_count") or 0):
                        checkpoint["committed_row_count"] = key_index.rebuild(checkpoint.get("committed_pages") or [])
                        checkpoint["merged_row_count"] = -1
                        checkpoint["merged_content"] = None
                    if checkpoint.get("complete"):
                        checkpoint = self._ensure_merged(store, table, checkpoint)
                        rows = read_jsonl(merged_path(store, table)) if include_rows else []
                        payload = self._fetch_result(
                            table, "READ_OK", checkpoint, pages=0, hashes=[], session_new=0,
                            complete=True, rows=rows, resume_skip=checkpoint.get("next_skip"), store=store,
                        )
                        return payload
                elif isinstance(resume, Mapping):
                    skip = int(resume.get("next_skip") or resume.get("skip") or 0)
            elif isinstance(resume, Mapping):
                skip = int(resume.get("next_skip") or resume.get("skip") or 0)
            if store is not None and key_index is None:
                key_index = UniqueKeyIndex(store, table)
                if key_index.count != int(checkpoint.get("committed_row_count") or 0):
                    checkpoint["committed_row_count"] = key_index.rebuild(checkpoint.get("committed_pages") or [])
                    checkpoint["merged_row_count"] = -1
                    checkpoint["merged_content"] = None

            previous_hash: str | None = None
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
                if previous_hash is not None and page.content_sha256 == previous_hash:
                    # The vendor returned the identical page for a new skip: paging is not honoured.
                    status = "PAGING_UNSUPPORTED"
                    complete = False
                    break
                previous_hash = page.content_sha256
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
                        key_index=key_index,
                    )
                    after_ck = self.persistence_hooks.get("after_checkpoint_durable")
                    if after_ck is not None:
                        after_ck(checkpoint)
                else:
                    memory_rows.extend(page.rows)
                session_new += len(page.rows)
                if page.short_page:
                    short_page_observed = len(page.rows)
                skip = int(page.next_skip or skip + len(page.rows))

            if store is not None:
                checkpoint["status"] = "READ_OK" if complete else status
                checkpoint["complete"] = complete
                checkpoint["next_skip"] = None if complete else skip
                checkpoint["session_row_count"] = session_new
                if short_page_observed is not None:
                    checkpoint["short_page_observed"] = short_page_observed
                save_checkpoint(store, checkpoint)
                if checkpoint.get("committed_pages"):
                    checkpoint = self._ensure_merged(store, table, checkpoint)
                rows = read_jsonl(merged_path(store, table)) if include_rows and checkpoint.get("committed_pages") else []
                return self._fetch_result(
                    table, status, checkpoint, pages=pages, hashes=hashes, session_new=session_new,
                    complete=complete, rows=rows, resume_skip=None if complete else skip, store=store,
                )
        finally:
            if key_index is not None:
                key_index.close()
        return {
            "table": table,
            "status": status,
            "row_count": session_new,
            "session_row_count": session_new,
            "committed_row_count": session_new,
            "pages": pages,
            "page_hashes": hashes,
            "used_default_first_page_as_universe": False,
            "resume_skip": None if complete else skip,
            "complete": complete,
            "rows": memory_rows if include_rows else [],
            "checkpoint": {
                "skip": skip,
                "next_skip": None if complete else skip,
                "query_signature": signature,
            },
            "first_saved_row": memory_rows[0] if memory_rows else None,
            "merged": None,
            "download_mode": "paged",
            "short_page_observed": short_page_observed,
            "pk_missing_rows": 0,
        }

    @staticmethod
    def _ensure_merged(store: Path, table: str, checkpoint: dict[str, Any]) -> dict[str, Any]:
        """The merged file must hold exactly the committed unique rows, byte for byte.

        The row count says how much is there; the content digest says whether it
        is still what the pages produced. Either one failing rebuilds the file
        from the committed pages and rebinds the digest.
        """

        merged_n = int(checkpoint.get("merged_row_count") if checkpoint.get("merged_row_count") is not None else -1)
        committed_n = int(checkpoint.get("committed_row_count") or 0)
        path = merged_path(store, table)
        content = verify_merged_content(store, table, checkpoint)
        if merged_n != committed_n or not path.is_file() or content["status"] != "MATCH":
            merged = merge_committed(store, table, checkpoint)
            checkpoint["merged_row_count"] = int(merged["row_count"])
            checkpoint["committed_row_count"] = int(merged["row_count"])
            checkpoint["merged_content"] = merged_content_record(int(merged["row_count"]), str(merged["chain_sha256"]))
            checkpoint["merged_content_rebuilt_because"] = content["status"] if content["status"] != "MATCH" else "row_count"
            save_checkpoint(store, checkpoint)
        return checkpoint

    @staticmethod
    def _fetch_result(
        table: str,
        status: str,
        checkpoint: Mapping[str, Any],
        *,
        pages: int,
        hashes: list[str],
        session_new: int,
        complete: bool,
        rows: list[dict[str, Any]],
        resume_skip: Any,
        store: Path,
    ) -> dict[str, Any]:
        committed = int(checkpoint.get("committed_row_count") or 0)
        merged = merged_path(store, table)
        return {
            "table": table,
            "status": status,
            "row_count": committed,
            "session_row_count": session_new,
            "committed_row_count": committed,
            "pages": pages,
            "page_hashes": hashes,
            "used_default_first_page_as_universe": False,
            "resume_skip": resume_skip,
            "complete": complete,
            "rows": rows,
            "checkpoint": dict(checkpoint),
            "first_saved_row": _first_jsonl_row(merged) if merged.is_file() else None,
            "merged": {"path": str(merged), "row_count": committed} if merged.is_file() else None,
            "download_mode": str(checkpoint.get("download_mode") or "paged"),
            "short_page_observed": checkpoint.get("short_page_observed"),
            "pk_missing_rows": int(checkpoint.get("pk_missing_rows") or 0),
        }

    # ------------------------------------------------------------------- bulk
    def bulk_status(self, table: str, *, years: str = "full") -> dict[str, Any]:
        if years not in {"5", "10", "full"}:
            raise ValueError("bulk_years_must_be_5_10_or_full")
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "years": years, "metadata": {}}
        url = self.table_url(table, {"years": years, "status": "True"}, with_format=False)
        try:
            code, body, final_url = self._request(url)
        except PermissionError:
            return {"table": table, "status": "AUTH_REQUIRED", "years": years, "metadata": {}}
        except Exception:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "years": years, "metadata": {}}
        inspected = inspect_table_body(body)
        vendor = redacted_vendor_error(body, code)
        if code in {401, 403}:
            return {"table": table, "status": "AUTH_FAILED", "years": years, "url": redact_url(final_url), "metadata": {}, "http_status": code, "vendor_error": vendor}
        if code >= 400 or code < 200:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "years": years, "url": redact_url(final_url), "metadata": {}, "http_status": code, "vendor_error": vendor}
        if inspected["kind"] in {"error", "html", "unknown", "empty_body"}:
            status = status_from_error_payload(inspected.get("error"), code) if inspected["kind"] == "error" else "SCHEMA_MISMATCH"
            return {"table": table, "status": status, "years": years, "url": redact_url(final_url), "metadata": {}, "body_kind": inspected["kind"], "http_status": code, "vendor_error": vendor}
        payload = _decode_json(body)
        metadata = parse_bulk_metadata(payload)
        return {
            "table": table,
            "status": metadata["status"],
            "years": years,
            "url": redact_url(final_url),
            "metadata": metadata["metadata"],
            "files": metadata["files"],
            "shape": metadata["shape"],
            "history_years_from_filename": None,
            "filename_does_not_prove_entitlement": True,
        }

    def bulk_download(self, table: str, *, years: str, dest: Path) -> dict[str, Any]:
        if years not in {"5", "10", "full"}:
            raise ValueError("bulk_years_must_be_5_10_or_full")
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "years": years, "final_file_exists": dest.exists()}
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = self.table_url(table, {"years": years}, with_format=False)
        tmp = dest.with_name(dest.name + ".partial")
        code = 0
        final_url = url
        digest = ""
        nbytes = 0
        for attempt in range(self.max_retries):
            try:
                code, final_url, digest, nbytes = self._stream_download(url, tmp, follow_redirects=True)
            except PermissionError:
                tmp.unlink(missing_ok=True)
                return {"table": table, "status": "AUTH_REQUIRED", "years": years, "final_file_exists": dest.exists()}
            except Exception:
                tmp.unlink(missing_ok=True)
                if attempt + 1 < self.max_retries:
                    self.sleep(_retry_after_seconds(None, attempt=attempt))
                    continue
                return {
                    "table": table,
                    "status": "NETWORK_UNAVAILABLE",
                    "years": years,
                    "url": redact_url(url),
                    "final_file_exists": dest.exists(),
                }
            if code in _RETRY_CODES and attempt + 1 < self.max_retries:
                tmp.unlink(missing_ok=True)
                self.sleep(_retry_after_seconds(None, attempt=attempt))
                continue
            break
        if code in {401, 403}:
            tmp.unlink(missing_ok=True)
            return {"table": table, "status": "AUTH_FAILED", "years": years, "url": redact_url(final_url), "final_file_exists": dest.exists()}
        if code < 200 or code >= 300:
            tmp.unlink(missing_ok=True)
            return {
                "table": table,
                "status": "NETWORK_UNAVAILABLE",
                "years": years,
                "http_status": code,
                "url": redact_url(final_url),
                "final_file_exists": dest.exists(),
            }
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
            "years_requested": years,
            "years": years,
            "observed_range_verified_at_ingest": True,
            "bytes": nbytes,
            "sha256": digest or _sha256_stream(dest),
            "path": str(dest),
            "url": redact_url(final_url),
            "final_file_exists": dest.exists(),
            "streamed": True,
        }

    def _stream_download(self, url: str, dest: Path, *, follow_redirects: bool) -> tuple[int, str, str, int]:
        if not self.allow_network:
            raise RuntimeError("NETWORK_DISABLED")
        if not _api_key():
            raise PermissionError("AUTH_REQUIRED")
        signed = self._attach_key_if_official(url)
        if self.opener is not None:
            code, body, final_url = self.opener(signed, follow_redirects=follow_redirects)
            self._record_request(signed, int(code))
            if int(code) < 200 or int(code) >= 300:
                return int(code), str(final_url), "", 0
            digest = hashlib.sha256()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("wb") as handle:
                view = memoryview(body)
                for start in range(0, len(body), 1024 * 1024):
                    chunk = view[start:start + 1024 * 1024]
                    handle.write(chunk)
                    digest.update(chunk)
            return int(code), str(final_url), digest.hexdigest(), len(body)
        request = urllib.request.Request(signed, headers={"Accept": "application/zip,application/octet-stream,*/*"})
        opener = urllib.request.build_opener(
            urllib.request.HTTPRedirectHandler() if follow_redirects else _NoRedirect()
        )
        try:
            with opener.open(request, timeout=_BULK_TIMEOUT_SECONDS) as response:
                code = int(response.status)
                final_url = str(response.geturl())
                self._record_request(signed, code)
                if code < 200 or code >= 300:
                    return code, final_url, "", 0
                return code, final_url, *_write_stream(response, dest)
        except urllib.error.HTTPError as exc:
            location = exc.headers.get("Location") or "" if exc.headers is not None else ""
            if exc.code in _REDIRECT_CODES and follow_redirects and location:
                if official_https_origin(location):
                    return self._stream_download(location, dest, follow_redirects=True)
                return self._stream_download_unsigned(location, dest)
            self._record_request(signed, exc.code, error="http_error")
            return exc.code, redact_url(url), "", 0

    def _stream_download_unsigned(self, url: str, dest: Path) -> tuple[int, str, str, int]:
        request = urllib.request.Request(url, headers={"Accept": "application/zip,application/octet-stream,*/*"})
        if self.opener is not None:
            code, body, final_url = self.opener(url, follow_redirects=True)
            self._record_request(url, int(code))
            if int(code) < 200 or int(code) >= 300:
                return int(code), str(final_url), "", 0
            dest.write_bytes(body)
            return int(code), str(final_url), hashlib.sha256(body).hexdigest(), len(body)
        opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler())
        with opener.open(request, timeout=_BULK_TIMEOUT_SECONDS) as response:
            code = int(response.status)
            self._record_request(url, code)
            if code < 200 or code >= 300:
                return code, str(response.geturl()), "", 0
            digest, nbytes = _write_stream(response, dest)
            return code, str(response.geturl()), digest, nbytes


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def probe_entitlement(client: SharadarClient, *, table: str = "stocks") -> dict[str, Any]:
    """Ask the bulk endpoint which history tier the account may download.

    The first tier whose metadata is served is the entitlement. A tier that is not
    subscribed answers with an error payload, not with a smaller file.
    """

    attempts: list[dict[str, Any]] = []
    if not credential_present():
        return {"bulk_years": None, "status": "AUTH_REQUIRED", "attempts": attempts}
    for years in BULK_YEARS_PROBE_ORDER:
        meta = client.bulk_status(table, years=years)
        attempts.append({"years": years, "status": meta.get("status"), "shape": meta.get("shape")})
        if meta.get("status") == "READ_OK":
            return {"bulk_years": years, "status": "READ_OK", "metadata": meta.get("metadata"), "attempts": attempts}
        if meta.get("status") in {"AUTH_REQUIRED", "AUTH_FAILED", "NETWORK_UNAVAILABLE"}:
            return {"bulk_years": None, "status": str(meta.get("status")), "attempts": attempts}
    return {"bulk_years": None, "status": "ENTITLEMENT_UNKNOWN", "attempts": attempts}


def verify_paged_completeness(
    client: SharadarClient,
    table: str,
    store: Path,
    dates: Sequence[date | str],
    *,
    extra: Mapping[str, Any] | None = None,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, Any]:
    """Re-query sampled single days and compare row counts with the store.

    Paging by skip can silently drop rows when the vendor's tie order shifts
    between pages; a per-day count is a direct check on that.
    """

    wanted = [str(item)[:10] for item in dates]
    store_counts = count_rows_by_date(merged_path(store, table), wanted)
    base = {k: v for k, v in dict(extra or {}).items() if k not in {"from", "to"}}
    results: list[dict[str, Any]] = []
    all_match = True
    for day in wanted:
        total = 0
        skip = 0
        pages = 0
        status = "READ_OK"
        while True:
            page = client.fetch_page(table, skip=skip, limit=limit, extra={**base, "from": day, "to": day})
            pages += 1
            if page.status != "READ_OK":
                status = page.status
                break
            total += len(page.rows)
            if not page.rows or len(page.rows) < limit or pages >= 50:
                break
            skip += len(page.rows)
        match = status == "READ_OK" and total == int(store_counts.get(day, 0))
        all_match = all_match and match
        results.append({
            "date": day,
            "vendor_rows": total,
            "store_rows": int(store_counts.get(day, 0)),
            "status": status,
            "match": match,
        })
    return {"status": "PASS" if all_match else "FAIL", "dates": results, "sampled_n": len(wanted)}


def inspect_table_body(body: bytes) -> dict[str, Any]:
    if not body or not body.strip():
        return {"kind": "empty_body", "rows": []}
    raw = body.lstrip()
    if raw[:20].lower().startswith(_HTML_PREFIXES):
        return {"kind": "html", "rows": []}
    text = body.decode("utf-8", errors="replace").lstrip("﻿")
    stripped = text.lstrip()
    if stripped.lower().startswith("<!doctype") or stripped.lower().startswith("<html"):
        return {"kind": "html", "rows": []}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        first_line = stripped.splitlines()[0] if stripped.splitlines() else ""
        if "," in first_line:
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
        if columns and data and not isinstance(data[0], dict):
            names = [str(col) for col in columns]
            rows = [dict(zip(names, row)) for row in data]
            return {"kind": "rows" if rows else "empty", "rows": rows}
        rows = [item for item in data if isinstance(item, dict)]
        return {"kind": "rows" if rows else "empty", "rows": rows}
    if isinstance(payload.get("rows"), list):
        rows = [item for item in payload["rows"] if isinstance(item, dict)]
        return {"kind": "rows" if rows else "empty", "rows": rows}
    if isinstance(payload.get(OFFICIAL_BULK_FILE_LIST_FIELD), list):
        return {"kind": "metadata", "rows": [], "metadata": payload}
    if any(key in payload for key in OFFICIAL_BULK_META_FIELDS):
        return {"kind": "metadata", "rows": [], "metadata": payload}
    return {"kind": "unknown", "rows": []}


def parse_table_body(body: bytes) -> list[dict[str, Any]]:
    return list(inspect_table_body(body)["rows"])


def status_from_error_payload(payload: Any, http_status: int) -> str:
    blob = json.dumps(payload or {}, default=str).lower()
    if http_status in {401, 403} or "invalid api key" in blob or "unauthorized" in blob:
        return "AUTH_FAILED"
    if "not subscribed" in blob or "entitlement" in blob or "permission" in blob or "subscription" in blob:
        return "ENTITLEMENT_MISSING"
    return "VENDOR_ERROR"


def redacted_vendor_error(body: bytes, http_status: int) -> dict[str, Any]:
    """Keep 401 and 403 distinct. Do not rewrite either as 'must upgrade SKU'."""

    inspected = inspect_table_body(body)
    payload = inspected.get("error") if inspected.get("kind") == "error" else None
    vendor_code = None
    vendor_message = None
    if isinstance(payload, dict):
        inner = payload.get("error") if "error" in payload else payload.get("errors")
        if isinstance(inner, dict):
            vendor_code = inner.get("code") or inner.get("type") or inner.get("errorcode")
            vendor_message = inner.get("message") or inner.get("msg") or inner.get("error")
        elif isinstance(inner, str):
            vendor_message = inner
        elif isinstance(inner, list) and inner:
            first = inner[0]
            if isinstance(first, dict):
                vendor_code = first.get("code") or first.get("type")
                vendor_message = first.get("message") or first.get("msg")
            else:
                vendor_message = str(first)
        if vendor_message is None:
            vendor_message = payload.get("message") or payload.get("msg")
        if vendor_code is None:
            vendor_code = payload.get("code") or payload.get("type")
    excerpt = None
    if vendor_message is None and body:
        excerpt = redact_text(body.decode("utf-8", errors="replace"))[:300]
    return {
        "http_status": http_status,
        "http_401_distinct_from_403": True,
        "body_kind": inspected.get("kind"),
        "vendor_code": None if vendor_code is None else redact_text(str(vendor_code))[:80],
        "vendor_message": None if vendor_message is None else redact_text(str(vendor_message))[:300],
        "excerpt": excerpt,
        "not_collapsed_to_upgrade_sku": True,
    }


def parse_bulk_metadata(payload: Any) -> dict[str, Any]:
    """Read the shape each table actually returns: flat for stocks/funds, `files` for actions."""

    if not isinstance(payload, dict):
        return {"status": "SCHEMA_MISMATCH", "metadata": {}, "files": [], "shape": "unknown"}
    if "error" in payload or "errors" in payload:
        return {"status": status_from_error_payload(payload, 200), "metadata": {}, "files": [], "shape": "error"}
    metadata = {field: payload.get(field) for field in OFFICIAL_BULK_META_FIELDS}
    listed = payload.get(OFFICIAL_BULK_FILE_LIST_FIELD)
    files: list[dict[str, Any]] = []
    if isinstance(listed, list):
        for item in listed:
            if isinstance(item, dict):
                files.append({field: item.get(field) for field in OFFICIAL_BULK_FILE_FIELDS})
    flat_present = any(metadata[field] is not None for field in OFFICIAL_BULK_META_FIELDS)
    if not flat_present and not files:
        return {"status": "SCHEMA_MISMATCH", "metadata": metadata, "files": [], "shape": "unknown"}
    if files and not flat_present:
        shape = "file_list"
    elif files:
        shape = "flat_with_file_list"
    else:
        shape = "flat"
    return {"status": "READ_OK", "metadata": metadata, "files": files, "shape": shape}


def validate_bulk_archive(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.stat().st_size == 0:
        return {"ok": False, "reason": "empty"}
    with path.open("rb") as handle:
        head = handle.read(32)
    if head.lstrip().lower().startswith(_HTML_PREFIXES):
        return {"ok": False, "reason": "html"}
    if head.lstrip()[:1] in (b"{", b"["):
        return {"ok": False, "reason": "json_not_zip"}
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
    """Fields absent from every row of the page. Optional master fields are not required."""

    if not rows:
        return ()
    required = [field for field in TABLE_FIELDS[table] if field not in TICKERS_OPTIONAL_FIELDS]
    present: set[str] = set()
    for row in rows:
        present.update(row.keys())
    return tuple(field for field in required if field not in present)


def _pk_missing(table: str, row: Mapping[str, Any]) -> bool:
    from app.services.research_eod_v1.data.sharadar_store import row_primary_key

    return row_primary_key(table, row) is None


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


def _write_stream(response: Any, dest: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    nbytes = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("wb") as handle:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            handle.write(chunk)
            digest.update(chunk)
            nbytes += len(chunk)
    return digest.hexdigest(), nbytes


def _first_jsonl_row(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text:
                item = json.loads(text)
                return item if isinstance(item, dict) else None
    return None


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
        self._access_tested = page.status == "READ_OK" and bool(page.rows)
        level = "ACCESS_TESTED" if self._access_tested else "DOCUMENTED_ONLY"
        notes.append(f"access_request_status={page.status}")
        return ProviderCapabilities(
            provider="sharadar",
            dataset_id="sharadar-official-v1",
            dataset_version=SOURCE_VERSION,
            probe_status=page.status if not self._access_tested else "ACCESS_TESTED",
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
    entitlement = classify_entitlement(earliest=None, bulk_years=None, verified_access=False, credential=present)
    entitlement_probe: dict[str, Any] = {"bulk_years": None, "status": "AUTH_REQUIRED", "attempts": []}
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
        "row_counts": {},
        "empty_read_is_not_sample_pass": True,
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
        verified = any(page.status == "READ_OK" and page.rows for page in (non_free, history, funds, delisted_id, delisted_px))
        entitlement_probe = probe_entitlement(adapter)
        entitlement = classify_entitlement(
            earliest=earliest,
            bulk_years=entitlement_probe.get("bulk_years"),
            verified_access=verified,
            credential=True,
        )
        tables["stocks"]["earliest_aapl"] = dates[0] if dates else None
        samples["non_free_example"] = _sample_status(non_free)
        samples["history_2010"] = _sample_status(history)
        samples["funds_scope"] = _sample_status(funds)
        samples["aapl_earliest"] = dates[0] if dates else None
        samples["row_counts"] = {
            "non_free_example": non_free.row_count,
            "history_2010": history.row_count,
            "funds_scope": funds.row_count,
            "delisted_identity": delisted_id.row_count,
            "delisted_prices": delisted_px.row_count,
            "delisted_actions": delisted_actions.row_count,
        }
        if delisted_id.rows or delisted_px.rows or delisted_actions.rows:
            samples["delisted"] = "READ_OK" if delisted_id.status == "READ_OK" or delisted_px.status == "READ_OK" else delisted_id.status
            samples["delisted_not_concluded_missing_history"] = False
        else:
            samples["delisted"] = "IDENTITY_UNRESOLVED" if delisted_id.status == "READ_OK" else delisted_id.status
            samples["delisted_not_concluded_missing_history"] = True
        tables["stocks"]["non_free_status"] = _sample_status(non_free)
        tables["tickers"]["delisted_status"] = _sample_status(delisted_id)
    if not present:
        channel = "official_docs_mapped; live_channel_unconfirmed_without_secret"
    elif adapter.live_request_count == 0:
        channel = "no_live_request"
    elif adapter.channel_confirmed():
        channel = "official_https_origin_api.sharadar.com"
    else:
        channel = "NON_OFFICIAL_ORIGIN_USED"
    report = {
        "credential_present": present,
        "runtime": runtime_probe_identity(),
        "provider": "sharadar",
        "channel": OFFICIAL_CHANNEL,
        "channel_confirmed": channel,
        "nasdaq_data_link_attempted": False,
        "chat_credentials_read": False,
        "massive_key_used": False,
        "yahoo_fallback_used": False,
        "live_sharadar_request_count": adapter.live_request_count,
        "tables": tables,
        "samples": samples,
        "entitlement": entitlement,
        "entitlement_probe": entitlement_probe,
        "volume_session_scope": "UNKNOWN",
        "secret_present_in_report": False,
        "terminal_status": _terminal_from_probe(present, tables, entitlement),
        "full_download_allowed": full_download_allowed(present, tables, entitlement, samples),
    }
    blob = json.dumps(report, default=str)
    if _api_key() and _api_key() in blob:
        raise RuntimeError("secret_leaked_into_probe_report")
    return report


def _sample_status(page: QueryPage) -> str:
    """A READ_OK page with no rows is transport proof, never sample proof."""

    if page.status == "READ_OK" and not page.rows:
        return "EMPTY_NO_SAMPLE"
    return page.status


def full_download_allowed(
    present: bool,
    tables: Mapping[str, Any],
    entitlement: Mapping[str, Any],
    samples: Mapping[str, Any],
) -> dict[str, Any]:
    """A full download only starts after the probe shows real access, not just transport."""

    blockers: list[str] = []
    if not present:
        blockers.append("credential_absent")
    statuses = {str(item.get("status")) for item in tables.values()}
    for blocking in (
        "AUTH_FAILED",
        "ENTITLEMENT_MISSING",
        "SCHEMA_MISMATCH",
        "VENDOR_ERROR",
        "NETWORK_UNAVAILABLE",
        "REDIRECT_UNEXPECTED",
        "EMPTY_BODY",
    ):
        if blocking in statuses:
            blockers.append(f"table_status:{blocking}")
    if entitlement.get("status") == "ENTITLEMENT_SHORT_5Y":
        blockers.append("entitlement_short_5y")
    if present and samples.get("non_free_example") != "READ_OK":
        blockers.append(f"non_free_sample:{samples.get('non_free_example')}")
    if present and samples.get("history_2010") != "READ_OK":
        blockers.append(f"history_sample:{samples.get('history_2010')}")
    return {
        "allowed": not blockers,
        "blockers": blockers,
        "empty_page_is_not_sample_proof": True,
    }


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
