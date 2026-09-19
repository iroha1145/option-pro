"""Bounded ACTIONS diagnosis, delisted identity, long-history coverage, reconcile.

Does not start a full-market price bulk, does not auto-purchase, does not print
secrets, and does not invent a ticker suffix. Paid rows stay under $HOME.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_actions_history_identity.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.massive_env import MassiveEnvProvider  # noqa: E402
from app.services.research_eod_v1.data.sharadar import (  # noqa: E402
    SharadarClient,
    credential_present,
    redact_text,
    redacted_vendor_error,
)
from app.services.research_eod_v1.data.sharadar_acceptance import (  # noqa: E402
    reconcile_aligned_returns,
)
from app.services.research_eod_v1.data.sharadar_identity import identity_from_ticker_row  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ALLOWED_END,
    ENV_KEY_NAME,
    OFFICIAL_HTTPS_HOST,
    PROBE_SAMPLES,
    RECONCILE_MIN_RETURN_COVERAGE,
    RECONCILE_MIN_SECURITIES,
)
from app.services.research_eod_v1.mathutil import finite  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3"
DEFAULT_STORE = Path.home() / "optix-data" / "authorized_sharadar_samples" / "actions_history_identity"
SCHEMA_URL = "https://api.sharadar.com/v1.0/schema/actions?format=json"
DOCS_ACTIONS = "https://sharadar.com/docs/actions"
DOCS_TICKERS = "https://sharadar.com/docs/tickers"
SAMPLE_WINDOW = {"from": "2024-06-24", "to": str(ALLOWED_END)}

# Pre-registered coverage windows inside the allowed region. firstpricedate on
# the master is not a substitute for actual rows in these windows.
COVERAGE_WINDOWS = (
    {"id": "2010", "from": "2010-01-04", "to": "2010-02-12"},
    {"id": "2016", "from": "2016-01-04", "to": "2016-02-12"},
    {"id": "2020", "from": "2020-03-02", "to": "2020-04-09"},
    {"id": "2024", "from": "2024-05-20", "to": str(ALLOWED_END)},
)
RECONCILE_WINDOW_IDS = ("2016", "2024")
CANDIDATE_TICKERS = (
    "MSFT",
    "AAPL",
    "JNJ",
    "XOM",
    "JPM",
    "WMT",
    "PG",
    "KO",
    "INTC",
    "IBM",
    "SPY",
)
# Name needles used only to scan vendor-returned name/related fields. Not ticker guesses.
NAME_NEEDLES = ("BED BATH", "BED BATH & BEYOND")
LASTPRICE_WINDOWS = (
    {"from": "2023-04-01", "to": "2023-05-31"},
    {"from": "2023-01-01", "to": "2023-12-31"},
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _head() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() or None


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _store_jsonl(path: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_bytes(text.encode("utf-8")),
        "rows": len(rows),
        "bytes": path.stat().st_size,
        "not_committed_to_git": True,
    }


def _page_brief(page, *, include_rows: bool = False) -> dict[str, Any]:
    rows = list(page.rows)
    dates = sorted({str(row.get("date") or "")[:10] for row in rows if row.get("date")})
    payload = {
        "status": page.status,
        "http_status": page.http_status,
        "row_count": page.row_count,
        "body_kind": page.body_kind,
        "complete": page.complete,
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "redacted_url_host_ok": OFFICIAL_HTTPS_HOST in (page.redacted_url or ""),
        "vendor_error": page.vendor_error,
    }
    if include_rows:
        payload["rows"] = rows
    return payload


def _log_slice(client: SharadarClient, start: int) -> dict[str, Any]:
    items = client.request_log[start:]
    retries = sum(1 for item in items if item.get("error") == "retry")
    statuses = [item.get("status") for item in items]
    return {
        "attempts": len(items),
        "retry_count": retries,
        "http_statuses": statuses,
        "official_https_origin": all(item.get("official_https_origin") for item in items) if items else False,
        "hosts": sorted({item.get("host") for item in items}),
    }


def _valid_fields(row: Mapping[str, Any], fields: tuple[str, ...]) -> int:
    return sum(1 for field in fields if row.get(field) not in (None, ""))


def _sessions(rows: list[Mapping[str, Any]]) -> list[date]:
    out: list[date] = []
    for row in rows:
        raw = str(row.get("date") or "")[:10]
        try:
            out.append(date.fromisoformat(raw))
        except ValueError:
            continue
    return sorted(set(out))


def _continuity(sessions: list[date]) -> dict[str, Any]:
    if len(sessions) < 2:
        return {"session_n": len(sessions), "max_gap_days": None, "gap_over_4": False}
    gaps = [(later - earlier).days for earlier, later in zip(sessions, sessions[1:])]
    return {
        "session_n": len(sessions),
        "max_gap_days": max(gaps),
        "gap_over_4": max(gaps) > 4,
    }


def _identity_hits(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    hits = []
    for row in rows:
        ticker = str(row.get("ticker") or "").strip().upper()
        name = str(row.get("name") or "").upper()
        related = str(row.get("relatedtickers") or "").upper()
        vendor_codes = {ticker, *related.replace(",", " ").replace(";", " ").split()}
        vendor_codes.discard("")
        name_hit = any(needle in name for needle in NAME_NEEDLES)
        code_hit = any(code == "BBBY" or code.startswith("BBBY") for code in vendor_codes)
        if not (name_hit or code_hit):
            continue
        hits.append({
            "table": row.get("table"),
            "permaticker": row.get("permaticker"),
            "ticker": row.get("ticker"),
            "name": row.get("name"),
            "isdelisted": row.get("isdelisted"),
            "relatedtickers": row.get("relatedtickers"),
            "firstpricedate": None if row.get("firstpricedate") in (None, "") else str(row.get("firstpricedate"))[:10],
            "lastpricedate": None if row.get("lastpricedate") in (None, "") else str(row.get("lastpricedate"))[:10],
            "exchange": row.get("exchange"),
            "category": row.get("category"),
            "match_via": ["vendor_returned_code" if code_hit else None, "vendor_returned_name" if name_hit else None],
            "suffix_was_not_invented": True,
        })
    return hits


def diagnose_actions(client: SharadarClient) -> dict[str, Any]:
    probes = []
    start = len(client.request_log)
    page = client.fetch_page(
        "actions",
        extra={"ticker": ",".join([PROBE_SAMPLES["non_free_example"], PROBE_SAMPLES["history_example"], PROBE_SAMPLES["fund_example"]]), **SAMPLE_WINDOW},
    )
    probes.append({
        "shape": "paged_ticker_and_window",
        "table": "actions",
        "tickers": [PROBE_SAMPLES["non_free_example"], PROBE_SAMPLES["history_example"], PROBE_SAMPLES["fund_example"]],
        "window": SAMPLE_WINDOW,
        "docs_endpoint": "https://api.sharadar.com/v1.0/data/actions",
        "docs_included_in": ["Fundamentals", "Prices", "Bundle"],
        **_page_brief(page),
        "transport": _log_slice(client, start),
    })

    start = len(client.request_log)
    docs_example = client.fetch_page("actions", extra={"ticker": PROBE_SAMPLES["history_example"]})
    probes.append({
        "shape": "docs_example_ticker_only",
        "table": "actions",
        "tickers": [PROBE_SAMPLES["history_example"]],
        "window": None,
        **_page_brief(docs_example),
        "transport": _log_slice(client, start),
    })

    bulk = []
    for years in ("5", "10", "full"):
        start = len(client.request_log)
        status = client.bulk_status("actions", years=years)
        bulk.append({
            "years": years,
            "status": status.get("status"),
            "http_status": status.get("http_status"),
            "vendor_error": status.get("vendor_error"),
            "body_kind": status.get("body_kind"),
            "files": status.get("files") or [],
            "shape": status.get("shape"),
            "purchase_attempted": False,
            "transport": _log_slice(client, start),
        })

    start = len(client.request_log)
    schema = {"status": "NOT_REQUESTED"}
    try:
        code, body, final_url = client._request(SCHEMA_URL)
        schema = {
            "status": "READ_OK" if 200 <= code < 300 else "HTTP_ERROR",
            "http_status": code,
            "vendor_error": redacted_vendor_error(body, code),
            "redacted_url_host_ok": OFFICIAL_HTTPS_HOST in (urlsplit(final_url).hostname or ""),
            "bytes": len(body),
            "sha256": _sha256_bytes(body),
            "transport": _log_slice(client, start),
        }
    except Exception as exc:
        schema = {
            "status": "NETWORK_UNAVAILABLE",
            "error_type": type(exc).__name__,
            "error": redact_text(str(exc))[:200],
            "transport": _log_slice(client, start),
        }

    http_codes = sorted({
        item.get("http_status")
        for item in [*probes, *bulk, schema]
        if item.get("http_status") is not None
    })
    interpretation = {
        "docs_list_actions_inside_fundamentals_prices_bundle": True,
        "docs_readable_is_not_this_account_entitlement": True,
        "http_401_means_key_rejected_for_this_request": 401 in http_codes,
        "http_403_means_key_reached_server_but_table_forbidden": 403 in http_codes,
        "401_and_403_not_collapsed_to_upgrade_sku": True,
        "same_key_already_reads_stocks_funds_tickers": True,
        "auto_purchase": False,
        "ask_vendor": [
            "whether this API key's product includes the ACTIONS table or only SEP/SFP/TICKERS",
            "the exact HTTP status and vendor error code returned for /v1.0/data/actions",
            "whether ACTIONS requires Fundamentals, Prices, or Bundle on this account",
        ],
    }
    nonempty = any(item.get("status") == "READ_OK" and (item.get("row_count") or 0) > 0 for item in probes)
    return {
        "docs": DOCS_ACTIONS,
        "probes": probes,
        "bulk_status": bulk,
        "schema": schema,
        "distinct_http_statuses": http_codes,
        "interpretation": interpretation,
        "nonempty_actions_rows": nonempty,
        "full_download_started": False,
    }


def resolve_bbby(client: SharadarClient, store: Path) -> dict[str, Any]:
    start = len(client.request_log)
    direct = client.fetch_page("tickers", extra={"ticker": PROBE_SAMPLES["delisted_example"]})
    direct_brief = {
        "query": {"ticker": PROBE_SAMPLES["delisted_example"], "date_params_sent": False},
        "meaning": "lookup_miss_only_if_empty",
        **_page_brief(direct),
        "transport": _log_slice(client, start),
    }
    artifacts = {
        "direct": _store_jsonl(store / "tickers_bbby_direct.jsonl", list(direct.rows)),
    }
    searches = []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for window in LASTPRICE_WINDOWS:
        start = len(client.request_log)
        skip = 0
        pages = 0
        window_rows: list[dict[str, Any]] = []
        while pages < 4:
            page = client.fetch_page("tickers", skip=skip, extra={"table": "stocks", **window})
            pages += 1
            window_rows.extend(page.rows)
            if page.status != "READ_OK" or not page.rows or page.complete:
                break
            if page.next_skip is None:
                break
            skip = page.next_skip
        hits = _identity_hits(window_rows)
        for hit in hits:
            key = (str(hit.get("permaticker")), str(hit.get("ticker")))
            if key not in seen:
                seen.add(key)
                candidates.append(hit)
        searches.append({
            "filter": {"table": "stocks", **window, "filter_field": "lastpricedate_per_official_docs"},
            "pages": pages,
            "rows_scanned": len(window_rows),
            "hits": len(hits),
            "status": page.status if pages else "NOT_REQUESTED",
            "http_status": page.http_status if pages else None,
            "transport": _log_slice(client, start),
            "name_is_not_a_documented_query_filter": True,
        })
        artifacts[f"tickers_lastprice_{window['from']}_{window['to']}"] = _store_jsonl(
            store / f"tickers_lastprice_{window['from']}_{window['to']}.jsonl",
            window_rows,
        )

    history = None
    prices = None
    actions = None
    chosen = None
    if candidates:
        chosen = candidates[0]
        ticker = str(chosen.get("ticker") or "")
        permaticker = str(chosen.get("permaticker") or "")
        start = len(client.request_log)
        by_perm = client.fetch_page("tickers", extra={"permaticker": permaticker}) if permaticker else None
        start_px = len(client.request_log)
        price_page = client.fetch_page(
            "stocks",
            extra={"ticker": ticker, "from": "2010-01-01", "to": str(ALLOWED_END)},
        ) if ticker else None
        start_ax = len(client.request_log)
        action_page = client.fetch_page(
            "actions",
            extra={"ticker": ticker, "from": "2010-01-01", "to": str(ALLOWED_END)},
        ) if ticker else None
        if by_perm is not None:
            history = {**_page_brief(by_perm), "transport": _log_slice(client, start)}
            artifacts["tickers_permaticker"] = _store_jsonl(store / "tickers_bbby_permaticker.jsonl", list(by_perm.rows))
        if price_page is not None:
            sessions = _sessions(list(price_page.rows))
            prices = {
                **_page_brief(price_page),
                "continuity": _continuity(sessions),
                "joined_by": "vendor_returned_ticker_and_permaticker",
                "transport": _log_slice(client, start_px),
            }
            artifacts["stocks_resolved"] = _store_jsonl(store / "stocks_bbby_resolved.jsonl", list(price_page.rows))
        if action_page is not None:
            actions = {**_page_brief(action_page), "transport": _log_slice(client, start_ax)}
            artifacts["actions_resolved"] = _store_jsonl(store / "actions_bbby_resolved.jsonl", list(action_page.rows))

    return {
        "docs": DOCS_TICKERS,
        "direct_ticker_lookup": direct_brief,
        "master_searches": searches,
        "candidates_from_vendor_rows": candidates,
        "chosen": chosen,
        "permaticker_confirm": history,
        "prices_by_vendor_ticker": prices,
        "actions_by_vendor_ticker": actions,
        "one_case_is_smoke_only": True,
        "not_claimed_16_delists_passed": True,
        "relatedtickers_are_hints_only": True,
        "artifacts": artifacts,
    }


def _master_row(client: SharadarClient, ticker: str) -> dict[str, Any]:
    page = client.fetch_page("tickers", extra={"ticker": ticker})
    rows = [row for row in page.rows if str(row.get("ticker") or "").upper() == ticker]
    stocks_rows = [row for row in rows if str(row.get("table") or "").lower() in {"", "stocks", "sep"}]
    funds_rows = [row for row in rows if str(row.get("table") or "").lower() in {"funds", "sfp"}]
    preferred = (funds_rows if ticker == "SPY" else stocks_rows) or rows
    row = preferred[0] if preferred else None
    identity = identity_from_ticker_row(row).to_dict() if row and row.get("permaticker") else None
    return {
        "ticker": ticker,
        "status": page.status,
        "http_status": page.http_status,
        "row_count": page.row_count,
        "tables_seen": sorted({str(item.get("table") or "") for item in rows}),
        "identity": identity,
        "firstpricedate": None if identity is None else identity.get("firstpricedate"),
        "lastpricedate": None if identity is None else identity.get("lastpricedate"),
    }


def _covers(master: Mapping[str, Any], window: Mapping[str, str]) -> bool | None:
    identity = master.get("identity") or {}
    first = str(identity.get("firstpricedate") or "")[:10]
    last = str(identity.get("lastpricedate") or "")[:10]
    if not first or not last:
        return None
    return first <= window["from"] and last >= window["to"]


def expand_coverage(client: SharadarClient, store: Path, masters: list[dict[str, Any]]) -> dict[str, Any]:
    table_for = {item["ticker"]: ("funds" if item["ticker"] == "SPY" else "stocks") for item in masters}
    coverage = []
    artifacts = {}
    for master in masters:
        ticker = master["ticker"]
        table = table_for[ticker]
        for window in COVERAGE_WINDOWS:
            start = len(client.request_log)
            page = client.fetch_page(table, extra={"ticker": ticker, "from": window["from"], "to": window["to"]})
            rows = list(page.rows)
            sessions = _sessions(rows)
            field_ok = [_valid_fields(row, ("ticker", "date", "open", "high", "low", "close", "volume", "closeadj", "closeunadj")) for row in rows]
            coverage.append({
                "table": table,
                "ticker": ticker,
                "permaticker": (master.get("identity") or {}).get("permaticker"),
                "window_id": window["id"],
                "request_from": window["from"],
                "request_to": window["to"],
                "return_from": min((str(row.get("date"))[:10] for row in rows if row.get("date")), default=None),
                "return_to": max((str(row.get("date"))[:10] for row in rows if row.get("date")), default=None),
                "row_count": page.row_count,
                "valid_field_min": min(field_ok) if field_ok else 0,
                "valid_field_max": max(field_ok) if field_ok else 0,
                "continuity": _continuity(sessions),
                "permission_status": page.status,
                "http_status": page.http_status,
                "master_said_covers_window": _covers(master, window),
                "firstpricedate_is_not_row_evidence": True,
                "transport": _log_slice(client, start),
            })
            artifacts[f"{table}_{ticker}_{window['id']}"] = _store_jsonl(
                store / f"{table}_{ticker}_{window['id']}.jsonl",
                rows,
            )
    return {"windows": list(COVERAGE_WINDOWS), "rows": coverage, "artifacts": artifacts}


def _daily_returns(rows: list[Mapping[str, Any]], *, price_field: str, security_id: str) -> list[dict[str, Any]]:
    ordered = []
    for row in rows:
        raw = str(row.get("date") or row.get("session_date") or "")[:10]
        try:
            session = date.fromisoformat(raw)
        except ValueError:
            continue
        price = finite(row.get(price_field))
        if price is None or price <= 0:
            continue
        ordered.append((session, price, finite(row.get("volume"))))
    ordered.sort()
    out = []
    previous = None
    for session, price, volume in ordered:
        ret = None if previous is None else price / previous[1] - 1.0
        out.append({
            "security_id": security_id,
            "session_date": session.isoformat(),
            "return": ret,
            "volume": volume,
            "price": price,
            "previous_price_present": previous is not None,
        })
        previous = (session, price, volume)
    return [row for row in out if row["previous_price_present"] and row["return"] is not None]


def reconcile_sources(
    client: SharadarClient,
    masters: list[dict[str, Any]],
    coverage: Mapping[str, Any],
) -> dict[str, Any]:
    massive_present = bool((os.environ.get("MASSIVE_API_KEY") or "").strip())
    control = MassiveEnvProvider(allow_network=True)
    sharadar_returns: list[dict[str, Any]] = []
    control_returns: list[dict[str, Any]] = []
    per_security = []
    for master in masters:
        identity = master.get("identity") or {}
        sid = identity.get("security_id")
        ticker = master["ticker"]
        if not sid:
            continue
        table = "funds" if ticker == "SPY" else "stocks"
        for window in COVERAGE_WINDOWS:
            if window["id"] not in RECONCILE_WINDOW_IDS:
                continue
            artifact_key = f"{table}_{ticker}_{window['id']}"
            path = Path((coverage["artifacts"].get(artifact_key) or {}).get("path") or "")
            rows = []
            if path.is_file():
                rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            s_ret = _daily_returns(rows, price_field="closeunadj", security_id=sid)
            start = date.fromisoformat(window["from"])
            end = date.fromisoformat(window["to"]) + timedelta(days=1)
            bars = control.fetch_daily_bars(ticker, start, end) if massive_present else "AUTH_REQUIRED"
            m_rows = []
            if isinstance(bars, list):
                m_rows = [{"date": bar.session_date.isoformat(), "closeunadj": bar.raw_close, "volume": bar.volume} for bar in bars]
            m_ret = _daily_returns(m_rows, price_field="closeunadj", security_id=sid)
            sharadar_returns.extend(s_ret)
            control_returns.extend(m_ret)
            per_security.append({
                "ticker": ticker,
                "security_id": sid,
                "window_id": window["id"],
                "sharadar_return_pairs": len(s_ret),
                "control_return_pairs": len(m_ret),
                "control_status": "READ_OK" if isinstance(bars, list) else str(bars),
            })
    result = reconcile_aligned_returns(
        sharadar_returns,
        control_returns,
        source_available=bool(control_returns),
        source={
            "kind": "massive_unadjusted_daily",
            "available": bool(control_returns),
            "auth": "bearer_not_query",
            "not_sent_to_api_sharadar_com": True,
            "yahoo_is_not_truth": True,
            "price_basis": "sharadar_closeunadj_vs_massive_adjusted_false",
            "return_basis": "simple_close_to_close_same_unadjusted_series",
            "volume_session_unknown": True,
        },
        min_return_coverage=RECONCILE_MIN_RETURN_COVERAGE,
        min_securities=RECONCILE_MIN_SECURITIES,
    )
    result.pop("rows", None)
    return {
        "thresholds": {"min_securities": RECONCILE_MIN_SECURITIES, "min_return_pairs": RECONCILE_MIN_RETURN_COVERAGE},
        "massive_present": massive_present,
        "yahoo_cache_used": False,
        "not_silently_spliced": True,
        "volume_experiment_not_claimed": True,
        "economic_ledger_not_claimed": True,
        "per_security": per_security,
        "reconcile": result,
        "sharadar_return_pairs": len(sharadar_returns),
        "control_return_pairs": len(control_returns),
    }


def main() -> int:
    present = bool((os.environ.get(ENV_KEY_NAME) or "").strip())
    assert present is credential_present()
    print({"credential_present": present, "massive_present": bool((os.environ.get("MASSIVE_API_KEY") or "").strip())})
    store = Path(os.environ.get("AUTHORIZED_SHARADAR_SAMPLE_DIR") or DEFAULT_STORE)
    store.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": _now(),
        "code_sha": _head(),
        "credential_present": present,
        "source_channel_required": "api.sharadar.com",
        "yahoo_fallback": False,
        "purchase_attempted": False,
        "full_market_bulk_started": False,
        "factor_or_weight_search": False,
        "authorized_restore_dir": str(store),
        "does_not_overwrite_mock_or_full_store": True,
    }
    if not present:
        report["terminal_status"] = "AUTH_REQUIRED"
        _write(PACK / "actions_history_identity_report.json", report)
        return 2

    client = SharadarClient(allow_network=True)
    report["actions"] = diagnose_actions(client)
    report["identity"] = resolve_bbby(client, store / "identity")
    masters = [_master_row(client, ticker) for ticker in CANDIDATE_TICKERS]
    report["masters"] = masters
    coverage = expand_coverage(client, store / "coverage", masters)
    report["coverage"] = {
        "windows": coverage["windows"],
        "rows": coverage["rows"],
        "artifact_count": len(coverage["artifacts"]),
        "artifacts": {key: {k: v for k, v in item.items() if k != "path" or True} for key, item in coverage["artifacts"].items()},
    }
    report["reconcile"] = reconcile_sources(client, masters, coverage)
    report["real_supplier_requests"] = client.live_request_count
    report["mock_requests"] = 0
    report["cache_reads"] = 0
    report["channel_confirmed"] = client.channel_confirmed()
    report["request_log"] = [
        {k: v for k, v in item.items() if k != "url" or True}
        for item in client.request_log
    ]
    nonempty_prices = sum(1 for item in coverage["rows"] if item["row_count"] > 0 and item["permission_status"] == "READ_OK")
    report["terminal_status"] = "PARTIAL_EVIDENCE"
    if report["actions"]["nonempty_actions_rows"]:
        report["terminal_status"] = "ACTIONS_NONEMPTY"
    elif report["identity"].get("chosen") and nonempty_prices:
        report["terminal_status"] = "IDENTITY_AND_COVERAGE"
    elif nonempty_prices:
        report["terminal_status"] = "COVERAGE_WITHOUT_ACTIONS"
    _write(PACK / "actions_history_identity_report.json", report)
    _write(PACK / "actions_vendor_diagnostic.md".replace(".md", ".json"), {
        "generated_at": report["generated_at"],
        "code_sha": report["code_sha"],
        "actions": report["actions"],
        "ask_vendor": report["actions"]["interpretation"]["ask_vendor"],
        "secrets_omitted": True,
    })
    print(json.dumps({
        "terminal_status": report["terminal_status"],
        "real_supplier_requests": report["real_supplier_requests"],
        "actions_http": report["actions"]["distinct_http_statuses"],
        "identity_candidates": len(report["identity"]["candidates_from_vendor_rows"]),
        "coverage_rows": len(coverage["rows"]),
        "reconcile_status": report["reconcile"]["reconcile"]["status"],
        "return_coverage_n": report["reconcile"]["reconcile"]["return_coverage_n"],
        "securities_n": report["reconcile"]["reconcile"]["securities_n"],
        "out": "sharadar_v3/actions_history_identity_report.json",
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
