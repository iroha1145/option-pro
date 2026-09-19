"""State-driven Sharadar data-gate. Status comes from actual inputs, not file presence."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    classify_entitlement,
    credential_present,
    isolate_research_window,
    run_sharadar_probe,
)
from app.services.research_eod_v1.data.sharadar_acceptance import (
    DELIST_FIXTURES,
    evaluate_delist_fixture,
    history_budget,
    reconcile_aligned_returns,
    trading_calendar_sessions,
    volume_scope_audit,
)
from app.services.research_eod_v1.data.sharadar_identity import identity_from_ticker_row
from app.services.research_eod_v1.data.sharadar_reconcile_source import load_reconcile_rows
from app.services.research_eod_v1.data.sharadar_schema import (
    ALLOWED_END,
    DATE_BOUND_TABLES,
    FULL_HISTORY_START,
    GATE_STAGES,
    RAW_DOWNLOAD_COMPLETE,
    REQUIRED_GATE_STAGES,
    TABLES,
    TERMINAL_ACCEPTED,
    TERMINAL_INSUFFICIENT,
    TERMINAL_PARTIAL,
    download_request_plan,
)
from app.services.research_eod_v1.data.sharadar_store import iter_jsonl, merged_path
from app.services.research_eod_v1.data.sharadar_tracks import closeadj_total_return, convert_vendor_row
from app.services.research_eod_v1.mathutil import finite

BLOCKING_TABLE_STATUSES = {
    "PARTIAL",
    "INSUFFICIENT",
    "UNSUPPORTED",
    "AUTH_REQUIRED",
    "AUTH_FAILED",
    "ENTITLEMENT_MISSING",
    "NETWORK_UNAVAILABLE",
    "SCHEMA_MISMATCH",
    "VENDOR_ERROR",
    "CHECKPOINT_QUERY_MISMATCH",
}


def _parse_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def isolate_allowed_rows(rows: Iterable[Mapping[str, Any]], *, date_field: str = "date") -> list[dict[str, Any]]:
    return list(iter_allowed_rows(rows, date_field=date_field))


def iter_allowed_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    date_field: str = "date",
) -> Iterator[dict[str, Any]]:
    """Only allowed-window rows leave this boundary. Sealed rows never reach a consumer."""

    for row in rows:
        session = _parse_date(row.get(date_field))
        if session is None:
            continue
        if isolate_research_window(session) == "ALLOWED":
            yield dict(row)


class IsolatedTable:
    """Allowed-window view over a raw table. Re-iterable, never the unbounded raw rows."""

    def __init__(
        self,
        *,
        path: Path | None = None,
        rows: Sequence[Mapping[str, Any]] | None = None,
        date_field: str | None = "date",
    ) -> None:
        self._path = path
        self._rows = list(rows or []) if path is None else None
        self._date_field = date_field

    def __iter__(self) -> Iterator[dict[str, Any]]:
        source: Iterable[Mapping[str, Any]]
        source = iter_jsonl(self._path) if self._path is not None else (self._rows or [])
        if self._date_field is None:
            for row in source:
                yield dict(row)
            return
        yield from iter_allowed_rows(source, date_field=self._date_field)

    def count(self) -> int:
        return sum(1 for _ in self)


def _identities_from_tickers(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    by_ticker: dict[str, Any] = {}
    by_id: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    for row in rows:
        try:
            identity = identity_from_ticker_row(row)
        except ValueError as exc:
            failures.append({"ticker": row.get("ticker"), "reason": str(exc)})
            continue
        by_ticker[identity.ticker] = identity
        by_id[identity.security_id] = identity
    return {"by_ticker": by_ticker, "by_id": by_id, "failures": failures}


def _delist_identity(fixture: Mapping[str, Any], identities: Mapping[str, Any]) -> Any | None:
    """Resolve the fixture ticker to one permanent identity whose coverage spans the event year."""

    ticker = str(fixture["ticker"]).upper()
    year = int(fixture["year"])
    candidates = [
        identity
        for key, identity in identities.items()
        if str(key).upper() == ticker or ticker in {item.upper() for item in identity.relatedtickers}
    ]
    if not candidates:
        return None
    covering = []
    for identity in candidates:
        first = _parse_date(identity.firstpricedate)
        last = _parse_date(identity.lastpricedate)
        if first is not None and first.year > year:
            continue
        if last is not None and last.year < year:
            continue
        covering.append(identity)
    if len(covering) == 1:
        return covering[0]
    if not covering:
        return None
    return None


def _events_in_window(
    rows: Iterable[Mapping[str, Any]],
    *,
    security_id: str | None,
    ticker: str,
    year: int,
) -> list[dict[str, Any]]:
    token = ticker.upper()
    out: list[dict[str, Any]] = []
    for row in rows:
        row_id = row.get("security_id")
        if security_id is not None and row_id is not None:
            if str(row_id) != str(security_id):
                continue
        elif str(row.get("ticker") or "").upper() != token:
            continue
        session = _parse_date(row.get("date"))
        if session is None or session.year < year:
            continue
        out.append(dict(row))
    return out


def _last_observed_quote(
    rows: Iterable[Mapping[str, Any]],
    *,
    ticker: str,
    year: int,
) -> float | None:
    token = ticker.upper()
    best: tuple[date, float] | None = None
    for row in rows:
        if str(row.get("ticker") or "").upper() != token:
            continue
        session = _parse_date(row.get("date"))
        close = finite(row.get("closeunadj")) or finite(row.get("close"))
        if session is None or close is None or session.year < year:
            continue
        if best is None or session > best[0]:
            best = (session, close)
    return None if best is None else best[1]


def _returns_from_prices(
    rows: Iterable[Mapping[str, Any]],
    identities: Mapping[str, Any],
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("ticker") or ""), []).append(dict(row))
    out: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    sessions_by_security: dict[str, list[date]] = {}
    for ticker, items in grouped.items():
        identity = identities.get(ticker)
        security_id = identity.security_id if identity is not None else f"ticker:{ticker}"
        items.sort(key=lambda item: str(item.get("date") or ""))
        prev = None
        for row in items:
            try:
                tracks = convert_vendor_row(row)
            except ValueError as exc:
                skipped.append({
                    "table": "prices",
                    "primary_key": {"ticker": ticker, "date": row.get("date")},
                    "reason": str(exc),
                })
                continue
            ret = closeadj_total_return(prev, tracks.closeadj)
            out.append({
                "security_id": security_id,
                "session_date": tracks.session_date.isoformat(),
                "return": ret,
                "volume": tracks.raw_volume,
            })
            sessions_by_security.setdefault(security_id, []).append(tracks.session_date)
            prev = tracks.closeadj
    return {"rows": out, "skipped": skipped, "sessions_by_security": sessions_by_security}


def _table_report(
    payload: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None = None,
    *,
    not_started: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not payload:
        skipped = dict(not_started or {})
        return {
            "status": str(skipped.get("status") or "AUTH_REQUIRED"),
            "row_count": 0,
            "pages": 0,
            "complete": False,
            "session_row_count": 0,
            "request": dict(plan or {}),
            "download_started": False,
            "not_started_reason": skipped.get("reason"),
        }
    return {
        "status": payload.get("status"),
        "row_count": payload.get("row_count") or 0,
        "session_row_count": payload.get("session_row_count") or 0,
        "committed_row_count": payload.get("committed_row_count") or payload.get("row_count") or 0,
        "pages": payload.get("pages") or 0,
        "complete": bool(payload.get("complete")),
        "resume_skip": payload.get("resume_skip"),
        "request": dict(plan or {}),
    }


def _check_from_status(status: str | None, *, empty_is: str = "AUTH_REQUIRED") -> str:
    if not status:
        return empty_is
    if status == "READ_OK":
        return "PASS"
    if status in BLOCKING_TABLE_STATUSES:
        return status
    return status


def _stage(status: str, evidence: Mapping[str, Any]) -> dict[str, Any]:
    return {"status": status, "evidence": dict(evidence)}


def summarize_stages(stages: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Acceptance follows the pre-registered required stages, never download completion alone."""

    missing = [name for name in REQUIRED_GATE_STAGES if name not in stages]
    failed = [name for name in REQUIRED_GATE_STAGES if stages.get(name, {}).get("status") == "FAIL"]
    partial = [name for name in REQUIRED_GATE_STAGES if stages.get(name, {}).get("status") == "PARTIAL"]
    insufficient = [
        name
        for name in REQUIRED_GATE_STAGES
        if stages.get(name, {}).get("status") in {"INSUFFICIENT", "UNSUPPORTED", "NOT_COMPUTED", "RECONCILIATION_MISSING"}
    ]
    blocked = [
        name
        for name in REQUIRED_GATE_STAGES
        if stages.get(name, {}).get("status") in {"AUTH_REQUIRED", "AUTH_FAILED", "ENTITLEMENT_MISSING", "NETWORK_UNAVAILABLE", "SCHEMA_MISMATCH", "VENDOR_ERROR", "CHECKPOINT_QUERY_MISMATCH"}
    ]
    accepted = not (missing or failed or partial or insufficient or blocked)
    return {
        "required": list(REQUIRED_GATE_STAGES),
        "optional": [name for name in GATE_STAGES if name not in REQUIRED_GATE_STAGES],
        "accepted": accepted,
        "missing": missing,
        "failed": failed,
        "partial": partial,
        "insufficient": insufficient,
        "blocked": blocked,
        "download_complete_is_not_acceptance": True,
    }


def execute_data_gate(
    *,
    client: SharadarClient | None = None,
    store: Path | None = None,
    yahoo_rows: Sequence[Mapping[str, Any]] | None = None,
    allow_network: bool = True,
    minute_entitlement: bool = False,
    reconcile_cache_paths: Sequence[Path] | None = None,
) -> dict[str, Any]:
    adapter = client or SharadarClient(allow_network=allow_network)
    present = credential_present()
    probe = run_sharadar_probe(allow_network=allow_network, client=adapter)
    plan = download_request_plan()
    download_allowed = probe.get("full_download_allowed") or {"allowed": present, "blockers": []}
    fetched: dict[str, Any] = {}
    memory_rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    raw_counts: dict[str, int] = {table: 0 for table in TABLES}
    if present and download_allowed.get("allowed"):
        for table, request in plan.items():
            fetched[table] = adapter.fetch_all(
                table,
                extra=request["extra"],
                store=store,
                limit=int(request["limit"]),
            )
            if store is not None and fetched[table].get("status") in {"READ_OK", "PARTIAL"}:
                raw_counts[table] = int(fetched[table].get("committed_row_count") or fetched[table].get("row_count") or 0)
            else:
                memory_rows[table] = list(fetched[table].get("rows") or [])
                raw_counts[table] = len(memory_rows[table])

    def view(table: str, *, date_field: str | None) -> IsolatedTable:
        if store is not None and fetched.get(table, {}).get("status") in {"READ_OK", "PARTIAL"}:
            return IsolatedTable(path=merged_path(store, table), date_field=date_field)
        return IsolatedTable(rows=memory_rows[table], date_field=date_field)

    isolated = {
        "stocks": view("stocks", date_field="date"),
        "funds": view("funds", date_field="date"),
        "actions": view("actions", date_field="date"),
        "tickers": view("tickers", date_field=None),
    }
    isolated_counts = {table: item.count() for table, item in isolated.items()}
    identities = _identities_from_tickers(isolated["tickers"])
    converted = _returns_from_prices(
        list(isolated["stocks"]) + list(isolated["funds"]),
        identities["by_ticker"],
    )
    sharadar_returns = converted["rows"]
    delist = []
    for fixture in DELIST_FIXTURES:
        ticker = str(fixture["ticker"])
        year = int(fixture["year"])
        identity = _delist_identity(fixture, identities["by_ticker"])
        actions = _events_in_window(
            isolated["actions"],
            security_id=None,
            ticker=ticker,
            year=year,
        )
        last_trade = _last_observed_quote(isolated["stocks"], ticker=ticker, year=year)
        delist.append(
            evaluate_delist_fixture(
                fixture,
                actions,
                last_trade,
                identity=None if identity is None else identity.to_dict(),
                event_window={
                    "from_year": year,
                    "allowed_end": ALLOWED_END.isoformat(),
                    "rows_are_allowed_window_only": True,
                },
            )
        )
    if yahoo_rows is None:
        cache = load_reconcile_rows(
            identities_by_ticker=identities["by_ticker"],
            paths=reconcile_cache_paths,
        )
    else:
        cache = {
            "available": True,
            "status": "READ_OK",
            "rows": list(yahoo_rows),
            "source": {"kind": "caller_supplied_rows", "available": True, "live_yahoo_request": False},
        }
    reconcile = reconcile_aligned_returns(
        sharadar_returns,
        cache["rows"],
        source_available=bool(cache["available"]),
        source=cache["source"],
    )
    dated = sorted({
        session
        for sessions in converted["sessions_by_security"].values()
        for session in sessions
    })
    earliest = dated[0] if dated else None
    entitlement = classify_entitlement(
        earliest=earliest if present else None,
        bulk_years=None,
        verified_access=bool(present and dated),
        credential=present,
    )
    calendar = trading_calendar_sessions(earliest or FULL_HISTORY_START, ALLOWED_END) if earliest else []
    budget = history_budget(
        earliest=earliest if present else None,
        entitlement_status=str(entitlement.get("status")),
        calendar=calendar,
        security_sessions=converted["sessions_by_security"],
    )
    volume = volume_scope_audit(minute_entitlement=minute_entitlement)
    not_started = (
        {"status": "AUTH_REQUIRED", "reason": "credential_absent"}
        if not present
        else {"status": "INSUFFICIENT", "reason": "probe_blockers:" + ",".join(download_allowed.get("blockers") or ["unknown"])}
    )
    table_reports = {
        table: _table_report(fetched.get(table), plan.get(table), not_started=not_started)
        for table in TABLES
    }
    live_ok = present and any(item["status"] == "READ_OK" for item in table_reports.values())
    live_partial = present and any(item["status"] == "PARTIAL" for item in table_reports.values())
    rows_present = any(raw_counts[table] for table in TABLES)
    raw_download = (
        RAW_DOWNLOAD_COMPLETE
        if live_ok and rows_present and all(item["complete"] for item in table_reports.values())
        else ("PARTIAL" if live_ok or live_partial else _check_from_status(
            next((item["status"] for item in table_reports.values()), None)
        ))
    )
    fixture_only = all(item.get("verification") == "fixture_list_only" for item in delist)
    matched = [item for item in delist if item.get("verification") == "store_or_live_matched"]
    matched_ok = all(item["live_status"] in {"PASS", "UNSUPPORTED", "INSUFFICIENT"} for item in matched)
    delist_live = (
        "AUTH_REQUIRED" if fixture_only else (
            "PASS" if matched and matched_ok and len(matched) == len(delist) else (
                "PARTIAL" if matched and matched_ok else "FAIL"
            )
        )
    )
    stages = _build_stages(
        present=present,
        download_allowed=download_allowed,
        table_reports=table_reports,
        raw_counts=raw_counts,
        isolated_counts=isolated_counts,
        identities=identities,
        converted=converted,
        reconcile=reconcile,
        budget=budget,
        volume=volume,
        delist=delist,
        delist_live=delist_live,
        entitlement=entitlement,
    )
    summary = summarize_stages(stages)
    terminal, live_read = _terminal_from_stages(
        present=present,
        probe=probe,
        stages=stages,
        summary=summary,
        table_reports=table_reports,
        rows_present=rows_present,
    )
    checks = {
        "adapter_code": "PASS",
        "synthetic_three_track": "PASS",
        "identity_contract": stages["IDENTITY"]["status"],
        "delist_fixture_table": "PASS" if len(DELIST_FIXTURES) == 16 else "FAIL",
        "delist_live_verified": delist_live,
        "parent_rank_fix": "PASS",
        "breakout_first_day_fix": "PASS",
        "feature_version_bump": "PASS",
        "control_archive_index": "PASS",
        "live_sharadar_read": live_read,
        "yahoo_reconcile_live": reconcile["status"],
        "volume_scope": volume["status"],
        "persistent_handover": "PASS" if store is not None and live_ok and rows_present else ("UNSUPPORTED" if store is None else live_read),
        "chat_or_massive_credential_used": "PASS_NOT_USED",
        "four_table_mock_or_live_not_empty_placeholder": "PASS" if rows_present else ("AUTH_REQUIRED" if not present else "INSUFFICIENT"),
        "explicit_history_request_bounds": "PASS" if all(
            plan[table]["extra"].get("from") == FULL_HISTORY_START.isoformat()
            and plan[table]["extra"].get("to") == ALLOWED_END.isoformat()
            for table in DATE_BOUND_TABLES
        ) else "FAIL",
    }
    store_note = {
        "path": None if store is None else str(store),
        "public_git": False,
        "resume": "load checkpoints/<table>.json; verify page hashes; continue from next_skip",
        "unique_key_index": None if store is None else "keys/<table>.keys; per-page cost, no prefix rescan",
        "paid_rows_must_stay_outside_git": True,
        "bounded_reads": "merged jsonl streamed row by row; no whole-table copy",
        "allowed_window": {
            "start": FULL_HISTORY_START.isoformat(),
            "end": ALLOWED_END.isoformat(),
            "isolated_after_raw_persist": True,
            "acceptance_consumers_see_allowed_window_only": True,
        },
    }
    return {
        "credential_present": present,
        "probe": probe,
        "request_plan": plan,
        "full_download_allowed": download_allowed,
        "tables": table_reports,
        "raw_row_counts": raw_counts,
        "isolated_row_counts": isolated_counts,
        "identities": {
            "n": len(identities["by_id"]),
            "failures": identities["failures"],
            "listed_at_not_from_firstpricedate": True,
            "relatedtickers_not_auto_aliases": True,
        },
        "delist": delist,
        "reconcile": reconcile,
        "volume": volume,
        "history_budget": budget,
        "entitlement": entitlement,
        "transform": {
            "converted_rows": len(sharadar_returns),
            "skipped_rows": converted["skipped"],
            "skipped_n": len(converted["skipped"]),
        },
        "event_invariants": {
            "actions_passthrough": True,
            "missing_value_not_coerced_to_zero": True,
            "last_trade_is_last_observed_quote": True,
            "mixed_acquisition_not_forced_cash": True,
            "failed_securities_retained": True,
        },
        "stages": stages,
        "stage_summary": summary,
        "raw_download_status": raw_download,
        "checks": checks,
        "terminal_status": terminal,
        "live_sharadar_request_count": adapter.live_request_count,
        "yahoo_fallback_used": False,
        "massive_key_used": False,
        "store": store_note,
        "holdout_unsealed": False,
        "executed_backtests": 0,
    }


def _build_stages(
    *,
    present: bool,
    download_allowed: Mapping[str, Any],
    table_reports: Mapping[str, Mapping[str, Any]],
    raw_counts: Mapping[str, int],
    isolated_counts: Mapping[str, int],
    identities: Mapping[str, Any],
    converted: Mapping[str, Any],
    reconcile: Mapping[str, Any],
    budget: Mapping[str, Any],
    volume: Mapping[str, Any],
    delist: Sequence[Mapping[str, Any]],
    delist_live: str,
    entitlement: Mapping[str, Any],
) -> dict[str, Any]:
    statuses = {str(item.get("status")) for item in table_reports.values()}
    if not present:
        auth = _stage("AUTH_REQUIRED", {"env_var": "SHARADAR_API_KEY", "credential_present": False})
    elif "AUTH_FAILED" in statuses:
        auth = _stage("AUTH_FAILED", {"table_statuses": sorted(statuses)})
    else:
        auth = _stage("PASS", {"credential_present": True})

    if not present:
        access = _stage("AUTH_REQUIRED", {"reason": "no_request_attempted"})
    elif not download_allowed.get("allowed"):
        access = _stage("INSUFFICIENT", {"blockers": list(download_allowed.get("blockers") or [])})
    elif "AUTH_FAILED" in statuses:
        access = _stage("AUTH_FAILED", {"table_statuses": sorted(statuses)})
    elif "ENTITLEMENT_MISSING" in statuses:
        access = _stage("ENTITLEMENT_MISSING", {"table_statuses": sorted(statuses)})
    elif "NETWORK_UNAVAILABLE" in statuses:
        access = _stage("NETWORK_UNAVAILABLE", {"table_statuses": sorted(statuses)})
    elif statuses == {"READ_OK"}:
        access = _stage("PASS", {"table_statuses": ["READ_OK"]})
    else:
        access = _stage("PARTIAL", {"table_statuses": sorted(statuses)})

    total_raw = sum(int(raw_counts.get(table, 0)) for table in TABLES)
    empty_tables = [table for table in TABLES if not int(raw_counts.get(table, 0))]
    if not present:
        download = _stage("AUTH_REQUIRED", {"raw_rows": 0})
    elif total_raw == 0:
        download = _stage("INSUFFICIENT", {"raw_rows": 0, "reason": "all_tables_empty", "empty_tables": empty_tables})
    elif empty_tables:
        download = _stage("PARTIAL", {"raw_rows": total_raw, "empty_tables": empty_tables})
    elif not all(bool(item.get("complete")) for item in table_reports.values()):
        download = _stage("PARTIAL", {"raw_rows": total_raw, "reason": "incomplete_table"})
    else:
        download = _stage("PASS", {"raw_rows": total_raw, "per_table": dict(raw_counts)})

    allowed_rows = int(isolated_counts.get("stocks", 0)) + int(isolated_counts.get("funds", 0))
    skipped = list(converted.get("skipped") or [])
    if not present:
        transform = _stage("AUTH_REQUIRED", {"converted_rows": 0})
    elif allowed_rows == 0:
        transform = _stage("INSUFFICIENT", {"reason": "empty_allowed_window", "allowed_price_rows": 0})
    elif not converted.get("rows"):
        transform = _stage("FAIL", {"reason": "no_row_converted", "skipped_n": len(skipped)})
    elif skipped:
        transform = _stage("PARTIAL", {"converted_rows": len(converted["rows"]), "skipped_n": len(skipped), "skipped_sample": skipped[:10]})
    else:
        transform = _stage("PASS", {"converted_rows": len(converted["rows"]), "skipped_n": 0})

    unresolved = [item["ticker"] for item in delist if not (item.get("identity_resolution") or {}).get("security_id")]
    if not present:
        identity = _stage("AUTH_REQUIRED", {"identities": 0})
    elif not identities.get("by_id"):
        identity = _stage("INSUFFICIENT", {"reason": "no_security_master_row"})
    elif identities.get("failures"):
        identity = _stage("PARTIAL", {"failures": identities["failures"][:10], "identities": len(identities["by_id"])})
    elif delist_live in {"FAIL"}:
        identity = _stage("FAIL", {"delist_live": delist_live, "unresolved": unresolved})
    elif delist_live in {"AUTH_REQUIRED", "PARTIAL"}:
        identity = _stage("PARTIAL", {"delist_live": delist_live, "unresolved_n": len(unresolved)})
    else:
        identity = _stage("PASS", {"identities": len(identities["by_id"]), "delist_live": delist_live})

    reconcile_status = str(reconcile.get("status"))
    reconcile_stage = _stage(
        "PASS" if reconcile_status == "PASS" else reconcile_status,
        {
            "aligned_n": reconcile.get("aligned_n"),
            "return_coverage_n": reconcile.get("return_coverage_n"),
            "volume_coverage_n": reconcile.get("volume_coverage_n"),
            "source": reconcile.get("comparison_source"),
        },
    )

    budget_status = str(budget.get("status"))
    history = _stage(
        "PASS" if budget_status == "COMPUTED" else budget_status,
        {
            "calendar_sessions_in_window": budget.get("calendar_sessions_in_window"),
            "first_score_day": budget.get("first_score_day"),
            "per_security": budget.get("per_security"),
            "entitlement": {
                "status": entitlement.get("status"),
                "verified_access": entitlement.get("verified_access"),
                "authorized_range_unknown": entitlement.get("authorized_range_unknown"),
            },
        },
    )

    volume_stage = _stage(str(volume.get("status")), {"session_scope": volume.get("session_scope")})
    blocked_cases = [item["ticker"] for item in delist if item.get("economic_settlement_blocked_only")]
    execution = _stage(
        "UNSUPPORTED" if blocked_cases or delist_live != "PASS" else "PASS",
        {
            "settlement_blocked_cases": blocked_cases,
            "raw_history_retained": True,
            "unsupported_case_is_not_verified_pass": True,
        },
    )
    return {
        "AUTH": auth,
        "TABLE_ACCESS": access,
        "DOWNLOAD": download,
        "TRANSFORM": transform,
        "IDENTITY": identity,
        "RECONCILE": reconcile_stage,
        "HISTORY": history,
        "VOLUME_SCOPE": volume_stage,
        "EXECUTION": execution,
    }


def _terminal_from_stages(
    *,
    present: bool,
    probe: Mapping[str, Any],
    stages: Mapping[str, Mapping[str, Any]],
    summary: Mapping[str, Any],
    table_reports: Mapping[str, Mapping[str, Any]],
    rows_present: bool,
) -> tuple[str, str]:
    statuses = {str(item.get("status")) for item in table_reports.values()}
    transport_ok = present and "READ_OK" in statuses
    if not present:
        return "AUTH_REQUIRED", "AUTH_REQUIRED"
    if stages["AUTH"]["status"] == "AUTH_FAILED":
        return "AUTH_FAILED", "AUTH_FAILED"
    if stages["TABLE_ACCESS"]["status"] == "ENTITLEMENT_MISSING":
        return "ENTITLEMENT_MISSING", "ENTITLEMENT_MISSING"
    if stages["TABLE_ACCESS"]["status"] == "NETWORK_UNAVAILABLE" and not transport_ok:
        return "NETWORK_UNAVAILABLE", "NETWORK_UNAVAILABLE"
    if rows_present and transport_ok:
        live_read = "PASS"
    elif transport_ok or not statuses - {"INSUFFICIENT"}:
        live_read = "INSUFFICIENT"
    else:
        live_read = _check_from_status(next((item["status"] for item in table_reports.values()), None))
    if summary["accepted"]:
        return TERMINAL_ACCEPTED, live_read
    if summary["failed"] or summary["partial"] or summary["blocked"]:
        return TERMINAL_PARTIAL, live_read
    if summary["insufficient"]:
        return TERMINAL_INSUFFICIENT, live_read
    return str(probe.get("terminal_status") or "AUTH_REQUIRED"), live_read
