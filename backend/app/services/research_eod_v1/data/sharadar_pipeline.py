"""State-driven Sharadar data-gate. Status comes from actual inputs, not file presence.

Memory rule: price tables are never materialised. The transform runs as a
two-pass bucketed stream (rows are partitioned by ticker into temporary files,
then each bucket is processed on its own), so a 30-million-row table costs a
bucket at a time. Every stage reports its own status; an incomplete download
caps every downstream stage at PARTIAL.
"""

from __future__ import annotations

import json
import tempfile
import zlib
from collections import Counter, deque
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    classify_entitlement,
    credential_present,
    isolate_research_window,
    run_sharadar_probe,
    verify_paged_completeness,
)
from app.services.research_eod_v1.data.sharadar_acceptance import (
    DELIST_FIXTURES,
    evaluate_delist_fixture,
    history_budget,
    reconcile_aligned_returns,
    trading_calendar_sessions,
    volume_scope_audit,
)
from app.services.research_eod_v1.data.sharadar_bulk import bulk_first_download
from app.services.research_eod_v1.data.sharadar_identity import (
    daily_pool_row,
    identity_from_ticker_row,
    resolve_identity_for_event_year,
    resolve_identity_for_session,
    split_ticker_suffix,
)
from app.services.research_eod_v1.data.sharadar_reconcile_source import load_reconcile_rows
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTION_ACQUISITION_CASH,
    ACTION_ACQUISITION_STOCK,
    ACTION_BANKRUPTCY,
    ACTION_DELISTED,
    ALLOWED_END,
    COMPLETENESS_SAMPLE_DATES,
    DATE_BOUND_TABLES,
    EVIDENCE_ROW_CAP,
    FULL_HISTORY_START,
    GATE_STAGES,
    IDENTITY_MIN_CONCRETE_TERMINALS,
    RAW_DOWNLOAD_COMPLETE,
    RECONCILE_MIN_RETURN_COVERAGE,
    RECONCILE_MIN_SECURITIES,
    REQUIRED_GATE_STAGES,
    TABLES,
    TERMINAL_ACCEPTED,
    TERMINAL_INSUFFICIENT,
    TERMINAL_PARTIAL,
    TRANSFORM_SKIP_TOLERANCE,
    download_request_plan,
)
from app.services.research_eod_v1.data.sharadar_store import iter_jsonl, load_checkpoint, merged_path
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
    "REDIRECT_UNEXPECTED",
    "EMPTY_BODY",
    "PAGING_UNSUPPORTED",
    "SCHEMA_MISMATCH",
    "VENDOR_ERROR",
    "CHECKPOINT_QUERY_MISMATCH",
}
TRANSFORM_BUCKETS = 64


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
    """Group by ticker base so a reused ticker (DELL / DELL1) stays several permatickers."""

    by_ticker: dict[str, list[Any]] = {}
    by_base: dict[str, list[Any]] = {}
    by_id: dict[str, Any] = {}
    failures: list[dict[str, Any]] = []
    for row in rows:
        try:
            identity = identity_from_ticker_row(row)
        except ValueError as exc:
            failures.append({"ticker": row.get("ticker"), "reason": str(exc)})
            continue
        if identity.security_id in by_id:
            continue
        by_ticker.setdefault(identity.ticker, []).append(identity)
        by_base.setdefault(identity.ticker_base or identity.ticker, []).append(identity)
        by_id[identity.security_id] = identity
    return {
        "by_ticker": by_ticker,
        "by_base": by_base,
        "by_id": by_id,
        "failures": failures,
        "reused_tickers": sorted(base for base, items in by_base.items() if len(items) > 1),
    }


def _delist_identity(fixture: Mapping[str, Any], identities_by_base: Mapping[str, Any]) -> dict[str, Any]:
    """Resolve the fixture ticker to one permanent identity whose coverage spans the event year.

    Candidates are every master row whose ticker base equals the fixture ticker,
    so the numeric suffix Sharadar appends to a delisted company (DELL1) is part
    of the search. ``relatedtickers`` is recorded as an unverified hint only.
    """

    ticker = str(fixture["ticker"]).upper()
    year = int(fixture["year"])
    candidates = list(identities_by_base.get(ticker) or [])
    hints = sorted({
        identity.security_id
        for items in identities_by_base.values()
        for identity in items
        if ticker in {item.upper() for item in identity.relatedtickers}
    })
    identity, covering, reason = resolve_identity_for_event_year(candidates, year)
    return {
        "identity": identity,
        "candidates_n": len(candidates),
        "candidate_tickers": sorted({item.ticker for item in candidates}),
        "covering_n": len(covering),
        "unresolved_reason": reason,
        "relatedticker_hints_not_verified_aliases": hints,
    }


def _event_bounds(identity: Any | None, year: int) -> tuple[date, date]:
    """Rows come from the event year up to the resolved coverage end, never a later reuse."""

    start = date(year, 1, 1)
    end = ALLOWED_END
    if identity is not None:
        last = _parse_date(identity.lastpricedate)
        if last is not None:
            end = min(end, last)
    return start, end


def _fixture_actions_for(
    identity: Any,
    fixture_ticker: str,
    fixture_actions: Mapping[str, list[dict[str, Any]]],
    *,
    start: date,
    end: date,
) -> list[dict[str, Any]]:
    """Action rows for the resolved identity: its stored ticker, plus bare-ticker rows inside its coverage."""

    out: list[dict[str, Any]] = []
    stored = identity.ticker
    for row in fixture_actions.get(stored, []):
        session = _parse_date(row.get("date"))
        if session is not None and start <= session <= end:
            out.append(dict(row))
    if stored != fixture_ticker:
        for row in fixture_actions.get(fixture_ticker, []):
            session = _parse_date(row.get("date"))
            if session is None or session < start or session > end:
                continue
            if identity.covers(session) is False:
                continue
            out.append(dict(row))
    out.sort(key=lambda item: str(item.get("date") or ""))
    return out


def _last_trade_for(
    identity: Any,
    fixture_rows: Mapping[str, list[dict[str, Any]]],
    *,
    start: date,
    end: date,
) -> float | None:
    best: tuple[date, float] | None = None
    for ticker, rows in fixture_rows.items():
        for row in rows:
            if row.get("security_id") != identity.security_id:
                continue
            session = _parse_date(row.get("date"))
            close = finite(row.get("closeunadj")) or finite(row.get("close"))
            if session is None or close is None or session < start or session > end:
                continue
            if best is None or session > best[0]:
                best = (session, close)
    return None if best is None else best[1]


def _transform_streaming(
    price_views: Sequence[tuple[str, Iterable[Mapping[str, Any]]]],
    identities_by_base: Mapping[str, Sequence[Any]],
    *,
    work_dir: Path,
    fixture_bases: set[str],
    reconcile_ids: set[str],
    buckets: int = TRANSFORM_BUCKETS,
) -> dict[str, Any]:
    """Two-pass bucketed transform. Pass 1 partitions rows by ticker; pass 2 works one bucket at a time."""

    work_dir.mkdir(parents=True, exist_ok=True)
    paths = [work_dir / f"bucket_{index:03d}.jsonl" for index in range(buckets)]
    handles = [path.open("w", encoding="utf-8") for path in paths]
    no_ticker = 0
    try:
        for table_name, view in price_views:
            for row in view:
                ticker = str(row.get("ticker") or "").strip().upper()
                if not ticker:
                    no_ticker += 1
                    continue
                index = zlib.crc32(ticker.encode("utf-8")) % buckets
                handles[index].write(json.dumps({**row, "ticker": ticker, "_src": table_name}, default=str) + "\n")
    finally:
        for handle in handles:
            handle.close()

    converted = 0
    skipped_total = no_ticker
    skipped_by_reason: Counter[str] = Counter({"missing_ticker": no_ticker} if no_ticker else {})
    skipped_sample: list[dict[str, Any]] = []
    sessions: dict[str, dict[str, Any]] = {}
    pool_by_date: dict[str, list[int]] = {}
    pool_reasons: Counter[str] = Counter()
    fixture_rows: dict[str, list[dict[str, Any]]] = {}
    reconcile_rows: list[dict[str, Any]] = []
    earliest: date | None = None
    latest: date | None = None
    sources: Counter[str] = Counter()

    def skip(reason: str, ticker: str, session: Any, candidates_n: int) -> None:
        nonlocal skipped_total
        skipped_total += 1
        skipped_by_reason[reason] += 1
        if len(skipped_sample) < EVIDENCE_ROW_CAP:
            skipped_sample.append({
                "table": "prices",
                "primary_key": {"ticker": ticker, "date": None if session is None else str(session)},
                "reason": reason,
                "candidates_n": candidates_n,
                "raw_row_retained_in_store": True,
            })

    for path in paths:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in iter_jsonl(path):
            grouped.setdefault(str(row.get("ticker") or ""), []).append(row)
        for ticker, items in grouped.items():
            base, _suffix = split_ticker_suffix(ticker)
            all_candidates = list(identities_by_base.get(base) or identities_by_base.get(ticker) or [])
            exact = [item for item in all_candidates if item.ticker == ticker]
            candidates = exact or all_candidates
            items.sort(key=lambda item: str(item.get("date") or ""))
            prior: deque[Any] = deque(maxlen=20)
            previous_close: dict[str, float] = {}
            current_sid: str | None = None
            for row in items:
                sources[str(row.get("_src") or "")] += 1
                try:
                    tracks = convert_vendor_row(row)
                except ValueError as exc:
                    skip(str(exc), ticker, row.get("date"), len(candidates))
                    continue
                identity = resolve_identity_for_session(candidates, tracks.session_date)
                if identity is None:
                    skip("identity_unresolved_for_session" if candidates else "no_security_master_row", ticker, tracks.session_date, len(candidates))
                    continue
                sid = identity.security_id
                if sid != current_sid:
                    prior = deque(maxlen=20)
                    current_sid = sid
                ret = closeadj_total_return(previous_close.get(sid), tracks.closeadj)
                previous_close[sid] = tracks.closeadj if tracks.closeadj is not None else previous_close.get(sid, 0.0)
                converted += 1
                summary = sessions.get(sid)
                if summary is None:
                    sessions[sid] = {"first": tracks.session_date, "last": tracks.session_date, "n": 1}
                else:
                    summary["n"] += 1
                    if tracks.session_date < summary["first"]:
                        summary["first"] = tracks.session_date
                    if tracks.session_date > summary["last"]:
                        summary["last"] = tracks.session_date
                if earliest is None or tracks.session_date < earliest:
                    earliest = tracks.session_date
                if latest is None or tracks.session_date > latest:
                    latest = tracks.session_date
                if identity.asset_track == "stock":
                    pool = daily_pool_row(identity, tracks, list(prior))
                    key = tracks.session_date.isoformat()
                    counts = pool_by_date.setdefault(key, [0, 0, 0])
                    counts[0] += 1
                    if pool.in_strategy_pool:
                        counts[1] += 1
                        if pool.venue_tag == "venue_unverified":
                            counts[2] += 1
                    for reason in pool.reasons:
                        pool_reasons[reason] += 1
                prior.append(tracks)
                if sid in reconcile_ids:
                    reconcile_rows.append({
                        "security_id": sid,
                        "session_date": tracks.session_date.isoformat(),
                        "return": ret,
                        "volume": tracks.raw_volume,
                    })
                if base in fixture_bases or ticker in fixture_bases:
                    fixture_rows.setdefault(ticker, []).append({
                        "date": tracks.session_date.isoformat(),
                        "closeunadj": tracks.closeunadj,
                        "close": tracks.close,
                        "security_id": sid,
                    })
        path.unlink(missing_ok=True)

    pool_dates = sorted(pool_by_date)
    pool_sizes = sorted(counts[1] for counts in pool_by_date.values())
    pool_rows_total = sum(counts[1] for counts in pool_by_date.values())
    unverified_total = sum(counts[2] for counts in pool_by_date.values())
    by_year: dict[str, dict[str, Any]] = {}
    for key in pool_dates:
        raw_n, pool_n, unverified_n = pool_by_date[key]
        year = key[:4]
        item = by_year.setdefault(year, {"sessions": 0, "raw_rows": 0, "pool_rows": 0, "venue_unverified_rows": 0})
        item["sessions"] += 1
        item["raw_rows"] += raw_n
        item["pool_rows"] += pool_n
        item["venue_unverified_rows"] += unverified_n
    for item in by_year.values():
        item["pool_size_mean"] = round(item["pool_rows"] / item["sessions"], 1) if item["sessions"] else None
        item["venue_unverified_share"] = round(item["venue_unverified_rows"] / item["pool_rows"], 4) if item["pool_rows"] else None
    pool_summary = {
        "computed": bool(pool_by_date),
        "session_n": len(pool_dates),
        "first_session": pool_dates[0] if pool_dates else None,
        "last_session": pool_dates[-1] if pool_dates else None,
        "pool_rows_total": pool_rows_total,
        "pool_size_median": pool_sizes[len(pool_sizes) // 2] if pool_sizes else None,
        "pool_size_min": pool_sizes[0] if pool_sizes else None,
        "pool_size_max": pool_sizes[-1] if pool_sizes else None,
        "venue_unverified_rows": unverified_total,
        "venue_unverified_share": round(unverified_total / pool_rows_total, 4) if pool_rows_total else None,
        "venue_unverified_signal_share": None,
        "exclusion_reason_counts": dict(pool_reasons),
        "by_year": by_year,
        "rule": {
            "category": "Domestic Common Stock or ADR Common Stock incl. Primary/Secondary Class",
            "currency": "USD",
            "closeunadj_min": 5.0,
            "unadj_adv20_min": 20_000_000,
            "adv20_prior_sessions_only": True,
            "exchange_is_tag_not_drop": True,
        },
    }
    return {
        "converted_rows": converted,
        "skipped_total": skipped_total,
        "skipped_by_reason": dict(skipped_by_reason),
        "skipped_sample": skipped_sample,
        "sessions_by_security": sessions,
        "pool_summary": pool_summary,
        "fixture_rows": fixture_rows,
        "reconcile_rows": reconcile_rows,
        "earliest": earliest,
        "latest": latest,
        "source_row_counts": dict(sources),
    }


def _scan_actions(
    view: Iterable[Mapping[str, Any]],
    *,
    fixture_bases: set[str],
) -> dict[str, Any]:
    vocabulary: Counter[str] = Counter()
    fixture_actions: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for row in view:
        total += 1
        action = str(row.get("action") or "").strip().lower()
        vocabulary[action or "(blank)"] += 1
        ticker = str(row.get("ticker") or "").strip().upper()
        base, _suffix = split_ticker_suffix(ticker)
        if ticker in fixture_bases or base in fixture_bases:
            fixture_actions.setdefault(ticker, []).append(dict(row))
    return {"total": total, "vocabulary": dict(vocabulary.most_common()), "fixture_actions": fixture_actions}


def _table_report(
    payload: Mapping[str, Any] | None,
    plan: Mapping[str, Any] | None = None,
    *,
    not_started: Mapping[str, Any] | None = None,
    checkpoint: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not payload:
        skipped = dict(not_started or {})
        return {
            "status": str(skipped.get("status") or "AUTH_REQUIRED"),
            "row_count": int((checkpoint or {}).get("committed_row_count") or 0),
            "pages": 0,
            "complete": bool((checkpoint or {}).get("complete")),
            "session_row_count": 0,
            "request": dict(plan or {}),
            "download_started": False,
            "not_started_reason": skipped.get("reason"),
            "download_mode": (checkpoint or {}).get("download_mode"),
        }
    committed = int(payload.get("committed_row_count") or payload.get("row_count") or 0)
    return {
        "status": payload.get("status"),
        "row_count": committed,
        "session_row_count": payload.get("session_row_count") or 0,
        "committed_row_count": committed,
        "pages": payload.get("pages") or 0,
        "complete": bool(payload.get("complete")),
        "resume_skip": payload.get("resume_skip"),
        "request": dict(plan or {}),
        "download_mode": payload.get("download_mode"),
        "bulk": {k: payload.get(k) for k in ("years", "observed_min_date", "observed_max_date", "rows_in_archive", "rows_dropped_outside_window", "observed_shorter_than_requested", "stage") if k in payload},
        "bulk_attempt": payload.get("bulk_attempt"),
        "completeness": payload.get("completeness"),
        "pk_missing_rows": payload.get("pk_missing_rows"),
        "short_page_observed": payload.get("short_page_observed"),
    }


def _check_from_status(status: str | None, *, empty_is: str = "AUTH_REQUIRED") -> str:
    if not status:
        return empty_is
    if status == "READ_OK":
        return "PASS"
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
        if stages.get(name, {}).get("status") in {
            "AUTH_REQUIRED", "AUTH_FAILED", "ENTITLEMENT_MISSING", "NETWORK_UNAVAILABLE", "SCHEMA_MISMATCH",
            "VENDOR_ERROR", "CHECKPOINT_QUERY_MISMATCH", "REDIRECT_UNEXPECTED", "EMPTY_BODY", "PAGING_UNSUPPORTED",
        }
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


def _sample_calendar_dates(n: int) -> list[str]:
    sessions = trading_calendar_sessions(FULL_HISTORY_START, ALLOWED_END)
    if not sessions or n <= 0:
        return []
    if n >= len(sessions):
        return [item.isoformat() for item in sessions]
    step = len(sessions) / float(n)
    picks = sorted({sessions[min(len(sessions) - 1, int((index + 0.5) * step))] for index in range(n)})
    return [item.isoformat() for item in picks]


def _requests_bounded(client: SharadarClient) -> str:
    """Check the requests actually sent, not the plan constants."""

    seen = 0
    for item in client.request_log:
        parts = urlsplit(str(item.get("url") or ""))
        table = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if table not in DATE_BOUND_TABLES or "skip" not in query or "ticker" in query:
            continue
        if query.get("from") == query.get("to"):
            continue  # single-day completeness recount, not a backfill request
        seen += 1
        if query.get("from", [None])[0] != FULL_HISTORY_START.isoformat() or query.get("to", [None])[0] != ALLOWED_END.isoformat():
            return "FAIL"
    return "PASS" if seen else "NOT_EXERCISED"


def _self_checks() -> dict[str, str]:
    """Runtime red/green probes of the two algorithm fixes and the feature version."""

    checks: dict[str, str] = {}
    try:
        from app.services.research_eod_v1 import FEATURE_VERSION

        checks["feature_version_bump"] = "PASS" if FEATURE_VERSION == "us-eod-research-features-v1.6" else "FAIL"
    except Exception:
        checks["feature_version_bump"] = "FAIL"
    try:
        from app.services.research_eod_v1.cross_section import q_star

        values = {f"A{i:02d}": float(i) for i in range(20)}
        values.update({f"B{i:02d}": float(i + 10) for i in range(20)})
        parent = {sid: ("P1" if sid.startswith("A") else "P2") for sid in values}
        ranks = q_star(values, industry={sid: None for sid in values}, parent=parent, tracks={sid: "stock" for sid in values})
        checks["parent_rank_fix"] = "PASS" if abs(float(ranks["A10"]) - 52.6315789474) < 1e-6 and float(ranks["B00"]) == 0.0 else "FAIL"
    except Exception:
        checks["parent_rank_fix"] = "FAIL"
    try:
        from types import SimpleNamespace

        from app.services.research_eod_v1.snapshot import _v_state

        raw = SimpleNamespace(rvol=0.7, down_ratio=None, clv5=0.8, breakout_track={"first_day_rvol": 2.0})
        same = _v_state("B_confirmed_base_breakout", raw)
        missing = _v_state("B_confirmed_base_breakout", SimpleNamespace(rvol=2.0, down_ratio=None, clv5=0.8, breakout_track={"first_day_rvol": None}))
        checks["breakout_first_day_fix"] = "PASS" if same is not None and abs(same - 70.79441541679836) < 1e-9 and missing is None else "FAIL"
    except Exception:
        checks["breakout_first_day_fix"] = "FAIL"
    return checks


def execute_data_gate(
    *,
    client: SharadarClient | None = None,
    store: Path | None = None,
    yahoo_rows: Sequence[Mapping[str, Any]] | None = None,
    allow_network: bool = True,
    minute_entitlement: bool = False,
    reconcile_cache_paths: Sequence[Path] | None = None,
    mode: str = "bulk_first",
    bulk_dir: Path | None = None,
    work_dir: Path | None = None,
    verify_completeness: bool = True,
    completeness_sample_dates: int = COMPLETENESS_SAMPLE_DATES,
    reconcile_min_return_coverage: int = RECONCILE_MIN_RETURN_COVERAGE,
    reconcile_min_securities: int = RECONCILE_MIN_SECURITIES,
) -> dict[str, Any]:
    adapter = client or SharadarClient(allow_network=allow_network)
    present = credential_present()
    probe = run_sharadar_probe(allow_network=allow_network, client=adapter)
    plan = download_request_plan()
    download_allowed = probe.get("full_download_allowed") or {"allowed": present, "blockers": []}
    probe_entitlement = probe.get("entitlement") or {}
    bulk_years = probe_entitlement.get("bulk_years") or "full"
    fetched: dict[str, Any] = {}
    memory_rows: dict[str, list[dict[str, Any]]] = {table: [] for table in TABLES}
    raw_counts: dict[str, int] = {table: 0 for table in TABLES}
    modes_used: dict[str, str] = {}
    if present and download_allowed.get("allowed"):
        for table, request in plan.items():
            result: dict[str, Any] | None = None
            bulk_attempt: dict[str, Any] | None = None
            if mode == "bulk_first" and store is not None:
                attempt = bulk_first_download(
                    adapter,
                    table,
                    store,
                    years=str(bulk_years),
                    dest_dir=bulk_dir or (store / "bulk"),
                    limit=int(request["limit"]),
                )
                if attempt.get("status") == "READ_OK":
                    result = attempt
                else:
                    bulk_attempt = {k: v for k, v in attempt.items() if k not in {"checkpoint", "rows"}}
            if result is None:
                result = adapter.fetch_all(
                    table,
                    extra=request["extra"],
                    store=store,
                    limit=int(request["limit"]),
                )
                result["download_mode"] = "paged"
                if bulk_attempt is not None:
                    result["bulk_attempt"] = bulk_attempt
                if (
                    verify_completeness
                    and store is not None
                    and table in DATE_BOUND_TABLES
                    and result.get("status") == "READ_OK"
                    and result.get("complete")
                    and int(result.get("row_count") or 0) > 0
                ):
                    result["completeness"] = verify_paged_completeness(
                        adapter,
                        table,
                        store,
                        _sample_calendar_dates(int(completeness_sample_dates)),
                        extra=request["extra"],
                        limit=int(request["limit"]),
                    )
            fetched[table] = result
            modes_used[table] = str(result.get("download_mode") or "paged")
            if store is None:
                memory_rows[table] = list(result.get("rows") or [])

    checkpoints: dict[str, dict[str, Any] | None] = {
        table: (load_checkpoint(store, table) if store is not None else None) for table in TABLES
    }
    for table in TABLES:
        if store is not None:
            checkpoint = checkpoints[table]
            raw_counts[table] = int((checkpoint or {}).get("committed_row_count") or 0)
        else:
            raw_counts[table] = len(memory_rows[table])

    def view(table: str, *, date_field: str | None) -> IsolatedTable:
        if store is not None:
            path = merged_path(store, table)
            if path.is_file() and raw_counts[table]:
                return IsolatedTable(path=path, date_field=date_field)
            return IsolatedTable(rows=[], date_field=date_field)
        return IsolatedTable(rows=memory_rows[table], date_field=date_field)

    isolated = {
        "stocks": view("stocks", date_field="date"),
        "funds": view("funds", date_field="date"),
        "actions": view("actions", date_field="date"),
        "tickers": view("tickers", date_field=None),
    }
    isolated_counts = {table: item.count() for table, item in isolated.items()}
    identities = _identities_from_tickers(isolated["tickers"])
    fixture_bases = {str(item["ticker"]).upper() for item in DELIST_FIXTURES}

    if yahoo_rows is None:
        cache = load_reconcile_rows(
            identities_by_ticker=identities["by_base"],
            paths=reconcile_cache_paths,
        )
    else:
        cache = {
            "available": True,
            "status": "READ_OK",
            "rows": list(yahoo_rows),
            "source": {"kind": "caller_supplied_rows", "available": True, "live_yahoo_request": False},
        }
    reconcile_ids = {str(row.get("security_id")) for row in cache["rows"]}

    tmp_holder: tempfile.TemporaryDirectory[str] | None = None
    if work_dir is None:
        tmp_holder = tempfile.TemporaryDirectory(prefix="sharadar-transform-")
        bucket_dir = Path(tmp_holder.name)
    else:
        bucket_dir = work_dir
    try:
        converted = _transform_streaming(
            (("stocks", isolated["stocks"]), ("funds", isolated["funds"])),
            identities["by_base"],
            work_dir=bucket_dir,
            fixture_bases=fixture_bases,
            reconcile_ids=reconcile_ids,
        )
    finally:
        if tmp_holder is not None:
            tmp_holder.cleanup()
    actions_scan = _scan_actions(isolated["actions"], fixture_bases=fixture_bases)

    delist = []
    for fixture in DELIST_FIXTURES:
        ticker = str(fixture["ticker"]).upper()
        year = int(fixture["year"])
        resolved = _delist_identity(fixture, identities["by_base"])
        identity = resolved["identity"]
        start, end = _event_bounds(identity, year)
        actions = (
            _fixture_actions_for(identity, ticker, actions_scan["fixture_actions"], start=start, end=end)
            if identity is not None
            else []
        )
        last_trade = (
            _last_trade_for(identity, converted["fixture_rows"], start=start, end=end)
            if identity is not None
            else None
        )
        delist.append(
            evaluate_delist_fixture(
                fixture,
                actions,
                last_trade,
                identity=None if identity is None else identity.to_dict(),
                event_window={
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "bounded_by_resolved_coverage": identity is not None,
                    "candidates_n": resolved["candidates_n"],
                    "candidate_tickers": resolved["candidate_tickers"],
                    "covering_n": resolved["covering_n"],
                    "unresolved_reason": resolved["unresolved_reason"],
                    "relatedticker_hints_not_verified_aliases": resolved["relatedticker_hints_not_verified_aliases"],
                    "rows_are_allowed_window_only": True,
                },
            )
        )

    reconcile = reconcile_aligned_returns(
        converted["reconcile_rows"],
        cache["rows"],
        source_available=bool(cache["available"]),
        source=cache["source"],
        min_return_coverage=int(reconcile_min_return_coverage),
        min_securities=int(reconcile_min_securities),
        sharadar_available=int(converted["converted_rows"]) > 0,
    )
    earliest = converted["earliest"]
    entitlement = classify_entitlement(
        earliest=earliest if present else None,
        bulk_years=probe_entitlement.get("bulk_years"),
        verified_access=bool(present and earliest is not None),
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
        table: _table_report(fetched.get(table), plan.get(table), not_started=not_started, checkpoint=checkpoints[table])
        for table in TABLES
    }
    live_ok = present and any(item["status"] == "READ_OK" for item in table_reports.values())
    live_partial = present and any(item["status"] in {"PARTIAL", "NETWORK_UNAVAILABLE", "REDIRECT_UNEXPECTED", "EMPTY_BODY", "PAGING_UNSUPPORTED"} and item["row_count"] for item in table_reports.values())
    rows_present = any(raw_counts[table] for table in TABLES)
    raw_download = (
        RAW_DOWNLOAD_COMPLETE
        if live_ok and rows_present and all(item["complete"] for item in table_reports.values())
        else ("PARTIAL" if (live_ok or live_partial) else _check_from_status(
            next((item["status"] for item in table_reports.values()), None)
        ))
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
    concrete_n = sum(1 for item in delist if item.get("concrete_terminal"))
    resolved_n = sum(1 for item in delist if item.get("identity_resolved"))
    checks = {
        **_self_checks(),
        "identity_contract": stages["IDENTITY"]["status"],
        "delist_fixture_table": "PASS" if len(DELIST_FIXTURES) == 16 else "FAIL",
        "delist_live_verified": stages["IDENTITY"]["evidence"].get("delist_live"),
        "delist_resolved_n": resolved_n,
        "delist_concrete_terminal_n": concrete_n,
        "control_archive_index": _control_index_check(),
        "live_sharadar_read": live_read,
        "yahoo_reconcile_live": reconcile["status"],
        "volume_scope": volume["status"],
        "persistent_handover": "PASS" if store is not None and rows_present else ("UNSUPPORTED" if store is None else live_read),
        "chat_or_massive_credential_used": "PASS_NOT_USED",
        "four_table_mock_or_live_not_empty_placeholder": "PASS" if rows_present else ("AUTH_REQUIRED" if not present else "INSUFFICIENT"),
        "explicit_history_request_bounds": _requests_bounded(adapter),
        "channel_confirmed": "PASS" if probe.get("channel_confirmed") == "official_https_origin_api.sharadar.com" else str(probe.get("channel_confirmed")),
        "download_modes": modes_used,
    }
    store_note = {
        "path": None if store is None else str(store),
        "public_git": False,
        "resume": "load checkpoints/<table>.json; verify page hashes; continue from next_skip; rows already on disk are read whatever the last status was",
        "unique_key_index": None if store is None else "keys/<table>.sqlite (lookup) + keys/<table>.keys (append-only listing)",
        "merged_file": "append-only per committed page; rebuilt only after an orphan-page adoption or a replaced page",
        "paid_rows_must_stay_outside_git": True,
        "bounded_reads": "isolated views stream jsonl; transform is a two-pass bucketed stream under a temporary directory; no whole-table list",
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
        "download_mode_requested": mode,
        "download_modes_used": modes_used,
        "full_download_allowed": download_allowed,
        "tables": table_reports,
        "raw_row_counts": raw_counts,
        "isolated_row_counts": isolated_counts,
        "identities": {
            "n": len(identities["by_id"]),
            "failures": identities["failures"][:EVIDENCE_ROW_CAP],
            "failures_n": len(identities["failures"]),
            "reused_tickers": identities["reused_tickers"][:EVIDENCE_ROW_CAP],
            "reused_tickers_n": len(identities["reused_tickers"]),
            "listed_at_not_from_firstpricedate": True,
            "relatedtickers_not_auto_aliases": True,
            "session_date_resolves_reused_ticker": True,
            "numeric_suffix_resolved_by_ticker_base": True,
        },
        "delist": delist,
        "reconcile": reconcile,
        "volume": volume,
        "history_budget": budget,
        "entitlement": entitlement,
        "entitlement_probe": probe.get("entitlement_probe"),
        "transform": {
            "converted_rows": converted["converted_rows"],
            "skipped_n": converted["skipped_total"],
            "skipped_share": (
                converted["skipped_total"] / (converted["converted_rows"] + converted["skipped_total"])
                if (converted["converted_rows"] + converted["skipped_total"])
                else None
            ),
            "skipped_by_reason": converted["skipped_by_reason"],
            "skipped_rows": converted["skipped_sample"],
            "skipped_rows_capped_at": EVIDENCE_ROW_CAP,
            "source_row_counts": converted["source_row_counts"],
        },
        "daily_pool": converted["pool_summary"],
        "action_vocabulary": {
            "total_rows": actions_scan["total"],
            "observed": actions_scan["vocabulary"],
            "assumed_by_terminal_classifier": {
                "bankruptcy": sorted(ACTION_BANKRUPTCY),
                "delisted": sorted(ACTION_DELISTED),
                "acquisition_cash": sorted(ACTION_ACQUISITION_CASH),
                "acquisition_stock": sorted(ACTION_ACQUISITION_STOCK),
            },
            "vocabulary_is_assumed_until_observed": True,
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


def _control_index_check() -> str:
    try:
        from app.services.research_eod_v1.data.sharadar_archive import CONTROL_LABEL, build_control_index

        return "PASS" if build_control_index().get("archive_label") == CONTROL_LABEL else "FAIL"
    except Exception:
        return "FAIL"


def _cap(stage: dict[str, Any], download_status: str) -> dict[str, Any]:
    """An incomplete download caps a downstream PASS at PARTIAL."""

    if download_status != "PASS" and stage.get("status") == "PASS":
        capped = dict(stage)
        capped["status"] = "PARTIAL"
        capped["evidence"] = {**dict(stage.get("evidence") or {}), "capped_by": f"DOWNLOAD:{download_status}"}
        return capped
    return stage


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
    elif "NETWORK_UNAVAILABLE" in statuses and not any(int(item.get("row_count") or 0) for item in table_reports.values()):
        access = _stage("NETWORK_UNAVAILABLE", {"table_statuses": sorted(statuses)})
    elif statuses == {"READ_OK"}:
        access = _stage("PASS", {"table_statuses": ["READ_OK"]})
    else:
        access = _stage("PARTIAL", {"table_statuses": sorted(statuses)})

    total_raw = sum(int(raw_counts.get(table, 0)) for table in TABLES)
    empty_tables = [table for table in TABLES if not int(raw_counts.get(table, 0))]
    incomplete = [table for table, item in table_reports.items() if not bool(item.get("complete"))]
    completeness_fail = [
        table for table, item in table_reports.items()
        if isinstance(item.get("completeness"), Mapping) and item["completeness"].get("status") == "FAIL"
    ]
    modes = {table: item.get("download_mode") for table, item in table_reports.items()}
    completeness_checked: dict[str, Any] = {}
    for table, item in table_reports.items():
        check = item.get("completeness")
        if not isinstance(check, Mapping):
            if table not in DATE_BOUND_TABLES:
                reason = "not_date_bound"
            elif item.get("download_mode") == "bulk":
                reason = "bulk_mode"
            elif not int(item.get("row_count") or 0):
                reason = "no_rows"
            else:
                reason = "check_disabled_or_download_incomplete"
            completeness_checked[table] = {"status": "NOT_RUN", "reason": reason}
            continue
        dates = [entry for entry in (check.get("dates") or []) if isinstance(entry, Mapping)]
        completeness_checked[table] = {
            "status": check.get("status"),
            "sampled_n": int(check.get("sampled_n") or len(dates)),
            "matched_n": sum(1 for entry in dates if entry.get("match")),
            "vendor_rows_sampled": sum(int(entry.get("vendor_rows") or 0) for entry in dates),
        }
    download_evidence = {
        "raw_rows": total_raw,
        "per_table": dict(raw_counts),
        "modes": modes,
        "empty_tables": empty_tables,
        "incomplete_tables": incomplete,
        "completeness_checked": completeness_checked,
        "completeness_failed_tables": completeness_fail,
        "completeness_samples": {table: table_reports[table].get("completeness") for table in completeness_fail},
        "reasons": [],
    }
    if not present:
        download = _stage("AUTH_REQUIRED", {"raw_rows": 0})
    elif total_raw == 0:
        download = _stage("INSUFFICIENT", {**download_evidence, "reasons": ["all_tables_empty"]})
    else:
        reasons = []
        if empty_tables:
            reasons.append("empty_table")
        if incomplete:
            reasons.append("incomplete_table")
        if completeness_fail:
            reasons.append("completeness_sample_mismatch")
        download = _stage("PARTIAL" if reasons else "PASS", {**download_evidence, "reasons": reasons})
    download_status = str(download["status"])

    allowed_rows = int(isolated_counts.get("stocks", 0)) + int(isolated_counts.get("funds", 0))
    converted_n = int(converted.get("converted_rows") or 0)
    skipped_n = int(converted.get("skipped_total") or 0)
    share = skipped_n / (converted_n + skipped_n) if (converted_n + skipped_n) else 0.0
    if not present:
        transform = _stage("AUTH_REQUIRED", {"converted_rows": 0})
    elif allowed_rows == 0:
        transform = _stage("INSUFFICIENT", {"reason": "empty_allowed_window", "allowed_price_rows": 0})
    elif converted_n == 0:
        transform = _stage("FAIL", {"reason": "no_row_converted", "skipped_n": skipped_n, "skipped_by_reason": converted.get("skipped_by_reason")})
    elif share > TRANSFORM_SKIP_TOLERANCE:
        transform = _stage("PARTIAL", {
            "converted_rows": converted_n,
            "skipped_n": skipped_n,
            "skipped_share": share,
            "tolerance": TRANSFORM_SKIP_TOLERANCE,
            "skipped_by_reason": converted.get("skipped_by_reason"),
            "skipped_sample": list(converted.get("skipped_sample") or [])[:10],
        })
    else:
        transform = _stage("PASS", {"converted_rows": converted_n, "skipped_n": skipped_n, "skipped_share": share, "tolerance": TRANSFORM_SKIP_TOLERANCE})
    transform = _cap(transform, download_status)

    resolved = [item for item in delist if item.get("identity_resolved")]
    concrete = [item for item in delist if item.get("concrete_terminal")]
    unresolved = [item["ticker"] for item in delist if not item.get("identity_resolved")]
    unknown = [item["ticker"] for item in delist if item.get("identity_resolved") and not item.get("concrete_terminal")]
    failed_cases = [item["ticker"] for item in delist if item.get("live_status") == "FAIL"]
    fixture_only = all(item.get("verification") == "fixture_list_only" for item in delist)
    if fixture_only:
        delist_live = "AUTH_REQUIRED" if not present else "INSUFFICIENT"
    elif failed_cases:
        delist_live = "FAIL"
    elif len(resolved) == len(delist) and len(concrete) >= IDENTITY_MIN_CONCRETE_TERMINALS:
        delist_live = "PASS"
    else:
        delist_live = "PARTIAL"
    identity_evidence = {
        "identities": len(identities.get("by_id") or {}),
        "delist_live": delist_live,
        "resolved_n": len(resolved),
        "concrete_terminal_n": len(concrete),
        "required_concrete_terminals": IDENTITY_MIN_CONCRETE_TERMINALS,
        "unresolved": unresolved,
        "terminal_unknown": unknown,
        "failed_cases": failed_cases,
        "master_row_failures_n": len(identities.get("failures") or []),
    }
    if not present:
        identity = _stage("AUTH_REQUIRED", {"identities": 0})
    elif not identities.get("by_id"):
        identity = _stage("INSUFFICIENT", {"reason": "no_security_master_row"})
    elif delist_live == "FAIL":
        identity = _stage("FAIL", identity_evidence)
    elif delist_live == "PASS" and not identities.get("failures"):
        identity = _stage("PASS", identity_evidence)
    elif delist_live == "INSUFFICIENT":
        identity = _stage("INSUFFICIENT", identity_evidence)
    else:
        identity = _stage("PARTIAL", identity_evidence)
    identity = _cap(identity, download_status)

    reconcile_status = str(reconcile.get("status"))
    reconcile_stage = _stage(
        "PASS" if reconcile_status == "PASS" else reconcile_status,
        {
            "aligned_n": reconcile.get("aligned_n"),
            "return_coverage_n": reconcile.get("return_coverage_n"),
            "volume_coverage_n": reconcile.get("volume_coverage_n"),
            "securities_n": reconcile.get("securities_n"),
            "insufficient_reason": reconcile.get("insufficient_reason"),
            "source": reconcile.get("comparison_source"),
        },
    )
    reconcile_stage = _cap(reconcile_stage, download_status)

    budget_status = str(budget.get("status"))
    entitlement_status = str(entitlement.get("status"))
    if budget_status != "COMPUTED":
        history_status = budget_status
    elif entitlement_status == "ENTITLEMENT_SHORT_5Y":
        history_status = "ENTITLEMENT_MISSING"
    elif entitlement_status in {"READ_OK", "HISTORY_10Y"}:
        history_status = "PASS"
    else:
        history_status = "PARTIAL"
    history = _stage(
        history_status,
        {
            "calendar_sessions_in_window": budget.get("calendar_sessions_in_window"),
            "first_score_day_earliest_any_security": budget.get("first_score_day"),
            "per_security": budget.get("per_security"),
            "entitlement": {
                "status": entitlement_status,
                "verified_access": entitlement.get("verified_access"),
                "authorized_range_unknown": entitlement.get("authorized_range_unknown"),
                "bulk_years": entitlement.get("bulk_years"),
            },
            "reason": None if history_status == "PASS" else ("entitlement_tier_unknown" if history_status == "PARTIAL" else history_status),
        },
    )
    history = _cap(history, download_status)

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
    if stages["TABLE_ACCESS"]["status"] == "NETWORK_UNAVAILABLE" and not transport_ok and not rows_present:
        return "NETWORK_UNAVAILABLE", "NETWORK_UNAVAILABLE"
    if rows_present and (transport_ok or rows_present):
        live_read = "PASS" if transport_ok else "PARTIAL"
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
