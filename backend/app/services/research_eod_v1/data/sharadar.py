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
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.services.research_eod_v1.data.contract import (
    UNSUPPORTED,
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
    OFFICIAL_CHANNEL,
    PROBE_SAMPLES,
    TABLE_FIELDS,
    TABLES,
)
from app.services.research_eod_v1.data.sharadar_tracks import convert_vendor_row

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


def credential_present() -> bool:
    return bool((os.environ.get(ENV_KEY_NAME) or "").strip())


def _api_key() -> str:
    return (os.environ.get(ENV_KEY_NAME) or "").strip()


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
    ) -> None:
        self.allow_network = allow_network
        self.base_url = base_url.rstrip("/")
        self.opener = opener
        self.sleep = sleep
        self.max_retries = max_retries
        self.failures: list[dict[str, Any]] = []

    def table_url(self, table: str, params: Mapping[str, Any] | None = None) -> str:
        if table not in TABLES:
            raise ValueError(f"unknown_sharadar_table:{table}")
        query = dict(params or {})
        query.setdefault("format", "json")
        return f"{self.base_url}/{table}?{urlencode(query, doseq=True)}"

    def _request(self, url: str, *, follow_redirects: bool = False) -> tuple[int, bytes, str]:
        if not self.allow_network:
            raise RuntimeError("NETWORK_DISABLED")
        key = _api_key()
        if not key:
            raise PermissionError("AUTH_REQUIRED")
        parts = urlsplit(url)
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
        query[AUTH_QUERY_PARAM] = key
        signed = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        request = urllib.request.Request(signed, headers={"Accept": "application/json,text/csv"})
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                if self.opener is not None:
                    return self.opener(signed, follow_redirects=follow_redirects)
                opener = urllib.request.build_opener(
                    urllib.request.HTTPRedirectHandler() if follow_redirects else _NoRedirect()
                )
                with opener.open(request, timeout=30) as response:
                    return int(response.status), response.read(), str(response.geturl())
            except urllib.error.HTTPError as exc:
                body = exc.read() if hasattr(exc, "read") else b""
                if exc.code in {301, 302, 303, 307, 308} and follow_redirects:
                    location = exc.headers.get("Location") or ""
                    if location:
                        return self._request(location, follow_redirects=True)
                if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < self.max_retries:
                    self.sleep(0.25 * (2 ** attempt))
                    last_error = exc
                    continue
                self.failures.append({
                    "status": exc.code,
                    "url": redact_url(url),
                    "error": "http_error",
                })
                return exc.code, body, redact_url(str(exc.geturl()) if getattr(exc, "geturl", None) else url)
            except PermissionError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 < self.max_retries:
                    self.sleep(0.25 * (2 ** attempt))
                    continue
                self.failures.append({"error": type(exc).__name__, "url": redact_url(url)})
                raise
        raise RuntimeError(redact_text(str(last_error) if last_error else "request_failed"))

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
            return QueryPage(table, [], skip, limit, "AUTH_REQUIRED", 0, "", self.table_url(table), None)
        params = {"limit": limit, "skip": skip, "format": "json"}
        if extra:
            params.update({k: v for k, v in extra.items() if v is not None})
        url = self.table_url(table, {k: v for k, v in params.items() if k != AUTH_QUERY_PARAM})
        try:
            status, body, final_url = self._request(url)
        except PermissionError:
            return QueryPage(table, [], skip, limit, "AUTH_REQUIRED", 0, "", redact_url(url), None)
        except Exception:
            return QueryPage(table, [], skip, limit, "NETWORK_UNAVAILABLE", 0, "", redact_url(url), None)
        redacted = redact_url(final_url)
        if status in {401, 403}:
            return QueryPage(table, [], skip, limit, "AUTH_FAILED", 0, hashlib.sha256(body).hexdigest(), redacted, None)
        if status >= 400:
            return QueryPage(table, [], skip, limit, "NETWORK_UNAVAILABLE", 0, hashlib.sha256(body).hexdigest(), redacted, None)
        rows = parse_table_body(body)
        missing = required_field_gap(table, rows)
        page_status = "SCHEMA_MISMATCH" if missing else "READ_OK"
        digest = hashlib.sha256(body).hexdigest()
        next_skip = skip + len(rows) if len(rows) >= limit else None
        return QueryPage(table, rows, skip, limit, page_status, len(rows), digest, redacted, next_skip)

    def fetch_all(
        self,
        table: str,
        *,
        extra: Mapping[str, Any] | None = None,
        resume: Mapping[str, Any] | None = None,
        max_pages: int | None = None,
        store: Path | None = None,
    ) -> dict[str, Any]:
        skip = int((resume or {}).get("skip") or 0)
        pages = 0
        rows: list[dict[str, Any]] = []
        hashes: list[str] = []
        status = "AUTH_REQUIRED"
        while True:
            if max_pages is not None and pages >= max_pages:
                break
            page = self.fetch_page(table, skip=skip, extra=extra)
            status = page.status
            pages += 1
            hashes.append(page.content_sha256)
            if page.status != "READ_OK":
                break
            if not page.rows:
                break
            if len(page.rows) > DEFAULT_PAGE_LIMIT:
                status = "SCHEMA_MISMATCH"
                break
            rows.extend(page.rows)
            if store is not None:
                _write_checkpoint(store, table, skip=page.skip, rows_so_far=len(rows), page=page)
            if page.next_skip is None:
                break
            skip = page.next_skip
        if store is not None and rows:
            _write_rows(store, table, rows)
        return {
            "table": table,
            "status": status,
            "row_count": len(rows),
            "pages": pages,
            "page_hashes": hashes,
            "used_default_first_page_as_universe": False,
            "resume_skip": skip,
            "rows": rows,
        }

    def bulk_status(self, table: str) -> dict[str, Any]:
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "files": []}
        url = self.table_url(table, {"status": "True", "format": "json"})
        try:
            code, body, final_url = self._request(url)
        except PermissionError:
            return {"table": table, "status": "AUTH_REQUIRED", "files": []}
        except Exception:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "files": []}
        if code in {401, 403}:
            return {"table": table, "status": "AUTH_FAILED", "files": [], "url": redact_url(final_url)}
        payload = _decode_json(body)
        return {
            "table": table,
            "status": "READ_OK",
            "url": redact_url(final_url),
            "payload": payload if isinstance(payload, dict) else {"raw_sha256": hashlib.sha256(body).hexdigest()},
        }

    def bulk_download(self, table: str, *, years: str, dest: Path) -> dict[str, Any]:
        if years not in {"5", "10", "full"}:
            raise ValueError("bulk_years_must_be_5_10_or_full")
        if not credential_present():
            return {"table": table, "status": "AUTH_REQUIRED", "years": years}
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = self.table_url(table, {"years": years})
        try:
            code, body, final_url = self._request(url, follow_redirects=True)
        except PermissionError:
            return {"table": table, "status": "AUTH_REQUIRED", "years": years}
        except Exception:
            return {"table": table, "status": "NETWORK_UNAVAILABLE", "years": years, "url": redact_url(url)}
        if code in {401, 403}:
            return {"table": table, "status": "AUTH_FAILED", "years": years, "url": redact_url(final_url)}
        dest.write_bytes(body)
        return {
            "table": table,
            "status": "READ_OK",
            "years": years,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "path": str(dest),
            "url": redact_url(final_url),
        }


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def parse_table_body(body: bytes) -> list[dict[str, Any]]:
    if not body:
        return []
    text = body.decode("utf-8", errors="replace").lstrip("\ufeff")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        reader = csv.DictReader(io.StringIO(text))
        return [dict(row) for row in reader]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("data"), list):
        data = payload["data"]
        columns = payload.get("columns") or payload.get("fields")
        if columns and data and data and not isinstance(data[0], dict):
            names = [str(col) for col in columns]
            return [dict(zip(names, row)) for row in data]
        return [item for item in data if isinstance(item, dict)]
    if isinstance(payload.get("rows"), list):
        return [item for item in payload["rows"] if isinstance(item, dict)]
    return []


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


def _write_checkpoint(store: Path, table: str, *, skip: int, rows_so_far: int, page: QueryPage) -> None:
    path = store / "checkpoints" / f"{table}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "table": table,
                "skip": skip,
                "rows_so_far": rows_so_far,
                "page_status": page.status,
                "page_sha256": page.content_sha256,
                "next_skip": page.next_skip,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def _write_rows(store: Path, table: str, rows: list[dict[str, Any]]) -> None:
    path = store / f"{table}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")


class SharadarProvider:
    def __init__(self, *, allow_network: bool = True, client: SharadarClient | None = None) -> None:
        self.allow_network = allow_network
        self.client = client or SharadarClient(allow_network=allow_network)
        self.failures = self.client.failures

    def probe_capabilities(self) -> ProviderCapabilities:
        present = credential_present()
        notes = [
            "channel=api.sharadar.com",
            "credential_from_env_SHARADAR_API_KEY_only",
            "nasdaq_data_link_not_attempted",
            "chat_credentials_not_read",
            "yahoo_not_used_as_fallback",
            "default_first_10000_is_not_universe",
        ]
        status = "AUTH_REQUIRED" if not present else ("ACCESS_TESTED" if self.allow_network else "DOCUMENTED_ONLY")
        if not present:
            notes.append("AUTH_REQUIRED")
        return ProviderCapabilities(
            provider="sharadar",
            dataset_id="sharadar-official-v1",
            dataset_version="api.sharadar.com/v1.0",
            probe_status=status,
            daily_bars="available" if present else "AUTH_REQUIRED",
            corporate_actions="available" if present else "AUTH_REQUIRED",
            classification_history="partial_actions_sic_only" if present else "AUTH_REQUIRED",
            security_master="available" if present else "AUTH_REQUIRED",
            delisted_coverage="documented_survivorship_free" if present else "AUTH_REQUIRED",
            volume_session_scope="UNKNOWN",
            raw_price_verified=False,
            notes=tuple(notes),
            capability_level="ACCESS_TESTED" if present else "DOCUMENTED_ONLY",
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
                    listed_at=_parse_date(identity.firstpricedate),
                    delisted_at=_parse_date(identity.lastpricedate) if identity.isdelisted else None,
                    aliases=identity.relatedtickers,
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
            extra={"ticker": symbol, "from": start.isoformat(), "to": end.isoformat(), "sort": "date.asc"},
        )
        if payload["status"] != "READ_OK":
            return payload["status"]
        retrieved = datetime.now(timezone.utc)
        out: list[ResearchBar] = []
        for row in payload["rows"]:
            tracks = convert_vendor_row(row)
            window = isolate_research_window(tracks.session_date)
            out.append(
                ResearchBar(
                    security_id=identity.security_id if identity else f"ticker:{symbol}",
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
                    source_published_at=retrieved,
                    retrieved_at=retrieved,
                    vintage_status="download_time_not_pit" if window == "ALLOWED" else window,
                )
            )
        return out

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str:
        payload = self.client.fetch_all(
            "actions",
            extra={"ticker": symbol, "from": start.isoformat(), "to": end.isoformat(), "sort": "date.asc"},
        )
        if payload["status"] != "READ_OK":
            return payload["status"]
        out: list[CorporateAction] = []
        for row in payload["rows"]:
            session = _parse_date(row.get("date"))
            if session is None:
                continue
            value = row.get("value")
            try:
                number = float(value) if value not in (None, "") else 0.0
            except (TypeError, ValueError):
                number = 0.0
            out.append(
                CorporateAction(
                    security_id=symbol,
                    session_date=session,
                    kind=str(row.get("action") or "unknown"),
                    value=number,
                    effective_at=session,
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
            dataset_version="api.sharadar.com/v1.0",
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
        }
    entitlement = classify_entitlement(earliest=None, bulk_years=None)
    if present:
        history = adapter.fetch_page(
            "stocks",
            extra={"ticker": PROBE_SAMPLES["history_example"], "from": "1998-01-01", "to": "2016-12-31", "sort": "date.asc"},
        )
        dates = sorted(str(row.get("date") or "")[:10] for row in history.rows if row.get("date"))
        earliest = date.fromisoformat(dates[0]) if dates else None
        bulk = adapter.bulk_status("stocks")
        years = _bulk_years_from_status(bulk)
        entitlement = classify_entitlement(earliest=earliest, bulk_years=years)
        tables["stocks"]["earliest_aapl"] = dates[0] if dates else None
        tables["stocks"]["bulk_status"] = bulk.get("status")
    report = {
        "credential_present": present,
        "runtime": runtime_probe_identity(),
        "provider": "sharadar",
        "channel": OFFICIAL_CHANNEL,
        "channel_confirmed": "official_docs_mapped; live_channel_unconfirmed_without_secret",
        "nasdaq_data_link_attempted": False,
        "chat_credentials_read": False,
        "massive_key_used": False,
        "yahoo_fallback_used": False,
        "tables": tables,
        "samples": {
            "non_free_example": "AUTH_REQUIRED" if not present else tables["stocks"]["status"],
            "delisted": "AUTH_REQUIRED" if not present else "PENDING_LIVE",
            "history_2010": "AUTH_REQUIRED" if not present else tables["stocks"]["status"],
            "funds_scope": "AUTH_REQUIRED" if not present else tables["funds"]["status"],
            "aapl_earliest": tables["stocks"].get("earliest_aapl"),
        },
        "entitlement": entitlement,
        "volume_session_scope": "UNKNOWN",
        "secret_present_in_report": False,
        "terminal_status": _terminal_from_probe(present, tables, entitlement),
    }
    blob = json.dumps(report, default=str)
    if _api_key() and _api_key() in blob:
        raise RuntimeError("secret_leaked_into_probe_report")
    return report


def _bulk_years_from_status(payload: Mapping[str, Any]) -> str | None:
    body = payload.get("payload") if isinstance(payload.get("payload"), dict) else payload
    files = body.get("files") if isinstance(body, dict) else None
    if isinstance(files, list):
        available = [str(item.get("history")) for item in files if item.get("available")]
        if "full" in available:
            return "full"
        if "10y" in available or "10" in available:
            return "10"
        if "5y" in available or "5" in available:
            return "5"
    return None


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
