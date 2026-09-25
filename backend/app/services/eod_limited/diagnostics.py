"""Small, explicit summaries of the complete EOD scoring decisions."""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.market_calendar import prior_trading_sessions
from app.services.research_eod_v1.composite import _collapse_same_family, _dedup_security
from app.services.research_eod_v1.constants import COMPOSITE_FLOORS
from app.services.sectors import SECTORS

from .full_market_tuning import finite_number

REFERENCE_PROFILE = "balanced"
REFERENCE_HORIZON = "mid"
REFERENCE_FAMILY = "A_trend_quality"

# These are data/coverage failures, even when another feature produced a score.
DATA_REASONS = frozenset({
    "DATA_INSUFFICIENT", "MISSING_SCORE", "LOW_COVERAGE", "MISSING_T_BAR",
    "MISSING_SESSION_BAR", "SOURCE_UNAVAILABLE", "LATE_SOURCE", "SHORT_HISTORY",
    "INCOMPLETE_DAILY_DATA", "INCOMPLETE_COMMON_INPUTS", "NO_HISTORY",
    "INVALID_OHLC", "MISSING_TARGET_SESSION",
})


def _stage(row: Mapping[str, Any]) -> str:
    reasons = {str(reason) for reason in row.get("rejection_reasons") or ()}
    if finite_number(row.get("score")) is None or reasons & DATA_REASONS:
        return "data_insufficient"
    if row.get("status") == "eligible":
        return "strict_eligible"
    if row.get("status") == "watch":
        return "qualification_watch"
    return "technical_rejected"


def _m1_funnel(rows: Sequence[Mapping[str, Any]], profile: str, published_n: int) -> dict[str, int]:
    """Count M1's existing gates before its final top-20 truncation."""
    eligible = [row for row in rows if row.get("status") == "eligible" and finite_number(row.get("score")) is not None]
    grouped = _dedup_security(eligible)
    by_security: dict[str, list[Mapping[str, Any]]] = {}
    for row in eligible:
        by_security.setdefault(str(row["security_id"]), []).append(row)
    two_family = 0
    floor_pass = 0
    spread_pass = 0
    for row in grouped:
        if len(set(row.get("family_votes") or ())) < 2:
            continue
        two_family += 1
        if finite_number(row.get("consensus_z")) is None or float(row["consensus_z"]) < COMPOSITE_FLOORS[profile]:
            continue
        floor_pass += 1
        family = _collapse_same_family(by_security[str(row["security_id"])])
        scores = [float(item["score"]) for item in family if finite_number(item.get("score")) is not None]
        if scores and max(scores) - min(scores) <= 25:
            spread_pass += 1
    if published_n != min(20, spread_pass):
        raise ValueError("m1_funnel_does_not_match_published_consensus")
    return {
        "strict_security_count": len(grouped),
        "two_family_count": two_family,
        "consensus_floor_count": floor_pass,
        "family_spread_count": spread_pass,
        "top20_count": published_n,
        "top20_truncated_count": spread_pass - published_n,
    }


class VariantDiagnostics:
    """Accumulate bounded counters while each full family block is available."""

    def __init__(self, *, profile: str, horizon: str) -> None:
        self.profile = profile
        self.horizon = horizon
        self.branches: list[dict[str, Any]] = []
        self.theme_scores: dict[str, dict[str, float]] = {}
        self.theme_missing: dict[str, dict[str, str]] = {}
        self.evaluated_reference_themes: set[str] = set()

    def add_block(self, theme_id: str, family: str, rows: Sequence[Mapping[str, Any]]) -> None:
        counts = Counter(_stage(row) for row in rows)
        total = len(rows)
        data_usable = total - counts["data_insufficient"]
        technical_pass = counts["qualification_watch"] + counts["strict_eligible"]
        if data_usable != counts["technical_rejected"] + technical_pass:
            raise ValueError("family_funnel_not_conserved")
        reasons = Counter(str(reason) for row in rows for reason in row.get("rejection_reasons") or ())
        self.branches.append({
            "theme_id": theme_id, "algorithm_id": family,
            "input_count": total,
            "data_insufficient_count": counts["data_insufficient"],
            "data_usable_count": data_usable,
            "technical_rejected_count": counts["technical_rejected"],
            "technical_pass_count": technical_pass,
            "qualification_watch_count": counts["qualification_watch"],
            "strict_eligible_count": counts["strict_eligible"],
            "reason_counts_nonexclusive": dict(sorted(reasons.items())),
        })
        if self.profile == REFERENCE_PROFILE and self.horizon == REFERENCE_HORIZON and family == REFERENCE_FAMILY and theme_id in SECTORS:
            self.evaluated_reference_themes.add(theme_id)
            scores = self.theme_scores.setdefault(theme_id, {})
            missing = self.theme_missing.setdefault(theme_id, {})
            for row in rows:
                ticker = str(row["security_id"])
                score = finite_number(row.get("score"))
                if score is not None:
                    scores[ticker] = score
                else:
                    reasons = [str(reason) for reason in row.get("rejection_reasons") or ()]
                    missing[ticker] = next((reason for reason in reasons if reason in DATA_REASONS), reasons[0] if reasons else "SCORE_UNAVAILABLE")

    def funnel(self, scored: Mapping[str, Any]) -> dict[str, Any]:
        rows = [row for block in scored.get("family_results") or () for row in block.get("rows") or ()]
        # Market compaction removes rejected rows from family_results. M1 only
        # consumes eligible rows, which remain there in both representations.
        stock = [row for row in rows if row.get("stock_or_etf_track") != "etf"]
        etf = [row for row in rows if row.get("stock_or_etf_track") == "etf"]
        return {
            "stage_order": ["input", "data_usable", "technical_pass", "qualification_watch_or_strict_eligible"],
            "count_unit": "security_theme_family_path",
            "branches": self.branches,
            "composite": {
                "stock": _m1_funnel(stock, self.profile, len(scored.get("composite_stock") or ())),
                "etf": _m1_funnel(etf, self.profile, len(scored.get("composite_etf") or ())),
            },
        }


def _return_pct(series: Any, days: int, served_session: str) -> float | None:
    close = getattr(series, "close", None)
    dates = getattr(series, "dates", None)
    if close is None or dates is None or len(close) != len(dates) or not dates:
        return None
    try:
        end_session = date.fromisoformat(served_session)
        available_sessions = [value if isinstance(value, date) else date.fromisoformat(str(value)) for value in dates]
    except (TypeError, ValueError):
        return None
    if available_sessions[-1] != end_session or len(set(available_sessions)) != len(available_sessions):
        return None
    start_session = prior_trading_sessions(end_session, days)[0]
    try:
        start_index = available_sessions.index(start_session)
    except ValueError:
        return None
    start = finite_number(close[start_index])
    end = finite_number(close[-1])
    return None if start is None or end is None or start <= 0 else 100.0 * (end / start - 1.0)


def build_theme_statistics(
    reference: VariantDiagnostics | None,
    *,
    panel: Mapping[str, Any],
    coverage_records: Sequence[Mapping[str, Any]],
    served_session: str,
    compute_version: str,
    feature_version: str,
    source_hash: str | None,
) -> dict[str, Any]:
    """Score all 24 authored themes from one fixed profile/family, never Top-K."""
    records = {str(row.get("ticker") or ""): row for row in coverage_records}
    benchmark = panel.get("SPY")
    spy_returns = {days: _return_pct(benchmark, days, served_session) for days in (20, 63, 126)}
    sectors: list[dict[str, Any]] = []
    evaluated = set() if reference is None else reference.evaluated_reference_themes
    for theme_id, spec in SECTORS.items():
        members = sorted({str(ticker) for ticker in spec["tickers"]})
        scores = {} if reference is None else reference.theme_scores.get(theme_id, {})
        missing = Counter()
        directory_missing = 0
        eligible_universe = 0
        complete = 0
        for ticker in members:
            coverage = records.get(ticker)
            if coverage is None:
                status = "ok" if ticker in panel else "NOT_IN_DIRECTORY"
            else:
                status = str(coverage.get("status") or "unknown")
            if status == "NOT_IN_DIRECTORY":
                directory_missing += 1
            elif status == "ok":
                eligible_universe += 1
                if ticker in panel:
                    complete += 1
            elif not status.startswith("excluded:"):
                eligible_universe += 1
            if theme_id in evaluated and ticker not in scores:
                reason = (
                    str((coverage or {}).get("reason") or status)
                    if status != "ok" else
                    (reference.theme_missing.get(theme_id, {}).get(ticker, "SCORE_UNAVAILABLE") if reference else "SCORE_UNAVAILABLE")
                )
                missing[reason] += 1
        valid_scores = [scores[ticker] for ticker in members if ticker in scores]
        row: dict[str, Any] = {
            "sector_id": theme_id, "reference_profile": REFERENCE_PROFILE,
            "reference_horizon": REFERENCE_HORIZON, "reference_family": REFERENCE_FAMILY,
            "member_count": len(members), "eligible_universe_count": eligible_universe,
            "directory_missing_count": directory_missing,
            "complete_bar_count": complete,
            "scored_count": len(valid_scores) if theme_id in evaluated else 0,
            "avg_strength": round(sum(valid_scores) / len(valid_scores), 2) if valid_scores and theme_id in evaluated else None,
            "leaders": [
                {"ticker": ticker, "score": round(score, 2)}
                for ticker, score in sorted(
                    ((ticker, scores[ticker]) for ticker in members if ticker in scores),
                    key=lambda item: (-item[1], item[0]),
                )[:4]
            ] if theme_id in evaluated else [],
            "missing_reasons": dict(sorted(missing.items())) if theme_id in evaluated else {"NOT_EVALUATED_IN_BATCH": len(members)},
            "score_source_status": (
                "unavailable" if theme_id not in evaluated or not valid_scores
                else "active" if len(valid_scores) == len(members) else "degraded"
            ),
        }
        for days, suffix in ((20, "1mo"), (63, "3mo"), (126, "6mo")):
            returns = [_return_pct(panel[ticker], days, served_session) for ticker in members if ticker in panel]
            valid_returns = [value for value in returns if value is not None]
            avg = sum(valid_returns) / len(valid_returns) if valid_returns else None
            spy = spy_returns[days]
            row[f"avg_return_{suffix}"] = round(avg, 2) if avg is not None else None
            row[f"return_coverage_{suffix}"] = len(valid_returns)
            row[f"spy_return_{suffix}"] = round(spy, 2) if spy is not None else None
            row[f"excess_vs_spy_{suffix}"] = round(avg - spy, 2) if avg is not None and spy is not None else None
        sectors.append(row)
    status = (
        "unavailable" if reference is None else
        "partial" if len(evaluated) != len(SECTORS) else
        "degraded" if any(row["score_source_status"] != "active" for row in sectors) else "active"
    )
    return {
        "status": status,
        "served_session": served_session,
        "compute_version": compute_version,
        "feature_version": feature_version,
        "source_hash": source_hash,
        "reference_profile": REFERENCE_PROFILE,
        "reference_horizon": REFERENCE_HORIZON,
        "reference_family": REFERENCE_FAMILY,
        "sector_count": len(sectors),
        "evaluated_theme_count": len(evaluated),
        "scored_theme_count": sum(row["scored_count"] > 0 for row in sectors),
        "sectors": sectors,
    }


def build_universe_funnel(coverage_records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Account for directory and bar availability before scoring begins."""
    if not coverage_records:
        return {"status": "unavailable", "count_unit": "directory_security"}
    statuses = Counter(str(row.get("status") or "unknown") for row in coverage_records)
    excluded = sum(count for status, count in statuses.items() if status.startswith("excluded:"))
    complete = statuses.get("ok", 0)
    in_scope = len(coverage_records) - excluded
    missing = in_scope - complete
    if missing < 0:
        raise ValueError("universe_funnel_not_conserved")
    return {
        "status": "complete", "count_unit": "directory_security",
        "directory_count": len(coverage_records),
        "excluded_count": excluded,
        "in_scope_count": in_scope,
        "complete_bar_count": complete,
        "data_unavailable_count": missing,
        "short_history_with_bar_count": sum(bool(row.get("short_history")) for row in coverage_records if row.get("status") == "ok"),
        "residual_short_history_with_bar_count": sum(bool(row.get("residual_short_history")) for row in coverage_records if row.get("status") == "ok"),
        "status_counts": dict(sorted(statuses.items())),
    }
