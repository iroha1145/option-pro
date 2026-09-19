"""Field/coverage acceptance fixtures. Live Sharadar rows stay AUTH_REQUIRED without a secret."""

from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.market_calendar import trading_sessions
from app.services.research_eod_v1.constants import RESIDUAL_HISTORY_MIN
from app.services.research_eod_v1.data.sharadar_identity import classify_terminal
from app.services.research_eod_v1.data.sharadar_schema import (
    ALLOWED_END,
    FULL_HISTORY_START,
    HISTORY_10Y_START,
    HOLDOUT_START,
    LABEL_HORIZONS,
    RECONCILE_MIN_RETURN_COVERAGE,
    RECONCILE_MIN_SECURITIES,
    TERMINAL_ACQUISITION_CASH,
    TERMINAL_BANKRUPTCY,
)
from app.services.research_eod_v1.mathutil import finite

FAMILY_WARMUP_SESSIONS = {
    "A_trend_quality": 252,
    "B_confirmed_base_breakout": 252,
    "C_trend_pullback": 252,
    "D_residual_momentum": max(330, RESIDUAL_HISTORY_MIN),
}

DELIST_FIXTURES: tuple[dict[str, Any], ...] = (
    {"ticker": "DELL", "year": 2013, "kind": "ticker_reuse_going_private", "expected_terminal": "acquisition_or_unknown", "note": "2013 take-private; later ticker reuse must use permaticker"},
    {"ticker": "DTV", "year": 2015, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "AT&T acquisition"},
    {"ticker": "YHOO", "year": 2017, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "Verizon / Altaba"},
    {"ticker": "WFM", "year": 2017, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "Amazon acquisition"},
    {"ticker": "MON", "year": 2018, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "Bayer acquisition"},
    {"ticker": "SHLD", "year": 2018, "kind": "bankruptcy_otc", "expected_terminal": "bankruptcy_last_trade", "note": "Sears bankruptcy / OTC"},
    {"ticker": "CELG", "year": 2019, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "BMS acquisition"},
    {"ticker": "ETFC", "year": 2020, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "Morgan Stanley acquisition"},
    {"ticker": "JCP", "year": 2020, "kind": "bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "J.C. Penney bankruptcy"},
    {"ticker": "XLNX", "year": 2022, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "AMD acquisition; cash may be mixed with stock"},
    {"ticker": "TWTR", "year": 2022, "kind": "take_private", "expected_terminal": "acquisition_cash", "note": "take-private cash"},
    {"ticker": "ATVI", "year": 2023, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "Microsoft acquisition"},
    {"ticker": "SIVB", "year": 2023, "kind": "bank_failure", "expected_terminal": "bankruptcy_last_trade", "note": "FDIC failure; confirm on actions before treating as last trade"},
    {"ticker": "BBBY", "year": 2023, "kind": "bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "Bed Bath & Beyond bankruptcy"},
    {"ticker": "PIR", "year": 2020, "kind": "small_cap_bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "Pier 1 bankruptcy"},
    {"ticker": "ASNA", "year": 2020, "kind": "small_cap_bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "Ascena bankruptcy"},
)

RECONCILE_RETURN_MARK_BP = 50.0
RECONCILE_VOLUME_LOW = 0.8
RECONCILE_VOLUME_HIGH = 1.25
RECONCILE_FAIL_SHARE = 0.02

THEME_PARENT_SCHEMA = {
    "themes_planned": 24,
    "parent_groups_planned": 30,
    "sectors_planned": 11,
    "historical_membership_rebuilt": False,
    "classification_flag": "CLASSIFICATION_CURRENT",
    "members_stage": "not_this_round",
}

CONCRETE_TERMINALS = frozenset({TERMINAL_ACQUISITION_CASH, TERMINAL_BANKRUPTCY})
# Only a settlement carried on a corporate action releases the economic gate. A
# bankruptcy label priced off the last observed quote stays an observation.
SETTLEMENT_EVIDENCE_REASONS = frozenset({"actions.cash_consideration"})
# v1 counted a bankruptcy priced off the last quote as a settled terminal, which
# let an observation stand in for an exit price. v2 keeps that row as a
# determinate label for the identity layer and requires action-borne evidence
# before the economic layer opens. The identity threshold itself is unchanged.
DELIST_RULE_VERSION = "delist-terminal-rule-v2"


@dataclass(frozen=True)
class ReconcileRow:
    security_id: str
    session_date: str
    return_diff_bp: float | None
    volume_ratio: float | None
    return_marked: bool
    volume_marked: bool
    reason_class: str


def evaluate_delist_fixture(
    fixture: Mapping[str, Any],
    actions: Sequence[Mapping[str, Any]],
    last_trade: float | None,
    *,
    identity: Mapping[str, Any] | None = None,
    event_window: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    resolution = {
        "security_id": None if identity is None else identity.get("security_id"),
        "permaticker": None if identity is None else identity.get("permaticker"),
        "ticker_as_stored": None if identity is None else identity.get("ticker"),
        "resolved_by": "permaticker_and_event_year" if identity else "unresolved",
        "event_window": dict(event_window or {}),
        "fixture_year_used_to_resolve_identity": True,
        "not_last_row_of_same_named_security": True,
    }
    terminal = classify_terminal(actions, last_trade=last_trade)
    settled = str(terminal.get("reason")) in SETTLEMENT_EVIDENCE_REASONS
    determinate = terminal["label"] in CONCRETE_TERMINALS and terminal.get("value") is not None
    concrete = determinate and settled
    expected = fixture["expected_terminal"]
    if not actions and last_trade is None:
        return {
            **dict(fixture),
            "live_status": "AUTH_REQUIRED" if identity is None else "INSUFFICIENT",
            "verification": "fixture_list_only" if identity is None else "identity_only_no_rows",
            "observed_terminal": terminal,
            "identity_resolution": resolution,
            "identity_resolved": identity is not None,
            "determinate_terminal": False,
            "concrete_terminal": False,
            "settlement_evidence": None,
            "economic_settlement_blocked_only": True,
            "rule_version": DELIST_RULE_VERSION,
        }
    if expected == "acquisition_or_unknown":
        matched = terminal["label"] in {TERMINAL_ACQUISITION_CASH, "TERMINAL_UNKNOWN"}
        status = "PASS" if matched and concrete else ("UNSUPPORTED" if matched else "FAIL")
    elif expected == "acquisition_cash":
        if terminal["label"] == TERMINAL_ACQUISITION_CASH:
            status = "PASS"
        elif terminal["label"] == "TERMINAL_UNKNOWN":
            status = "UNSUPPORTED"
        else:
            status = "FAIL"
    elif expected == "bankruptcy_last_trade":
        if terminal["label"] == TERMINAL_BANKRUPTCY:
            status = "PASS"
        elif terminal["label"] == "TERMINAL_UNKNOWN":
            status = "INSUFFICIENT"
        else:
            status = "FAIL"
    else:
        status = "PASS" if terminal["label"] == expected else "FAIL"
    return {
        **dict(fixture),
        "live_status": status,
        "verification": "store_or_live_matched",
        "observed_terminal": terminal,
        "last_trade_is_not_liquidation_value": True,
        "observed_last_quote": last_trade,
        "settlement_evidence": str(terminal.get("reason")) if settled else None,
        "identity_resolution": resolution,
        "identity_resolved": identity is not None,
        # Determinate = the label is resolved. Concrete = a settlement carries it.
        "determinate_terminal": determinate,
        "concrete_terminal": concrete,
        # An observed last quote is a fact about the tape, not a settled exit price.
        "economic_settlement_blocked_only": not settled,
        "rule_version": DELIST_RULE_VERSION,
    }


def reconcile_aligned_returns(
    sharadar: Sequence[Mapping[str, Any]],
    yahoo: Sequence[Mapping[str, Any]],
    *,
    source_available: bool | None = None,
    source: Mapping[str, Any] | None = None,
    min_return_coverage: int = RECONCILE_MIN_RETURN_COVERAGE,
    min_securities: int = RECONCILE_MIN_SECURITIES,
    sharadar_available: bool | None = None,
) -> dict[str, Any]:
    """Each field carries its own valid denominator. A missing comparison source is not a pass.

    A PASS needs a real sample: at least ``min_return_coverage`` compared returns
    across at least ``min_securities`` securities. Fewer comparisons is
    INSUFFICIENT, never a pass by absence of evidence.
    """

    yahoo_map = {(row["security_id"], row["session_date"]): row for row in yahoo}
    rows: list[ReconcileRow] = []
    return_coverage = 0
    volume_coverage = 0
    return_marked_n = 0
    volume_marked_n = 0
    securities: set[str] = set()
    for row in sharadar:
        key = (row["security_id"], row["session_date"])
        other = yahoo_map.get(key)
        if other is None:
            continue
        ret_s = finite(row.get("return"))
        ret_y = finite(other.get("return"))
        vol_s = finite(row.get("volume"))
        vol_y = finite(other.get("volume"))
        return_ok = ret_s is not None and ret_y is not None
        volume_ok = vol_s is not None and vol_y is not None and vol_s > 0 and vol_y > 0
        diff_bp = None if not return_ok else abs(ret_s - ret_y) * 10_000  # type: ignore[operator]
        ratio = None if not volume_ok else float(vol_s) / float(vol_y)  # type: ignore[arg-type]
        return_marked = diff_bp is not None and diff_bp > RECONCILE_RETURN_MARK_BP
        volume_marked = ratio is not None and not (RECONCILE_VOLUME_LOW <= ratio <= RECONCILE_VOLUME_HIGH)
        reasons: list[str] = []
        if return_ok:
            return_coverage += 1
            securities.add(str(row["security_id"]))
            if return_marked:
                return_marked_n += 1
                reasons.append("return_gt_50bp")
        else:
            reasons.append("missing_return")
        if volume_ok:
            volume_coverage += 1
            if volume_marked:
                volume_marked_n += 1
                reasons.append("volume_outside_0.8_1.25")
        elif vol_s == 0 or vol_y == 0:
            reasons.append("zero_volume")
        else:
            reasons.append("missing_volume")
        if not return_ok and not volume_ok:
            reason = "missing_return_and_volume"
        elif not reasons:
            reason = "ok"
        else:
            reason = "+".join(reasons)
        rows.append(
            ReconcileRow(
                security_id=str(row["security_id"]),
                session_date=str(row["session_date"]),
                return_diff_bp=diff_bp,
                volume_ratio=ratio,
                return_marked=return_marked,
                volume_marked=volume_marked,
                reason_class=reason,
            )
        )
    n = len(rows)
    return_share = 0.0 if return_coverage == 0 else return_marked_n / return_coverage
    volume_share = 0.0 if volume_coverage == 0 else volume_marked_n / volume_coverage
    available = bool(yahoo) if source_available is None else bool(source_available)
    thin_reason = None
    have_sharadar = bool(sharadar) if sharadar_available is None else bool(sharadar_available)
    if not available:
        status = "RECONCILIATION_MISSING" if have_sharadar else "AUTH_REQUIRED"
    elif n == 0 or (return_coverage == 0 and volume_coverage == 0):
        status = "INSUFFICIENT"
        thin_reason = "no_comparable_rows"
    elif return_coverage < int(min_return_coverage) or len(securities) < int(min_securities):
        status = "INSUFFICIENT"
        thin_reason = f"return_coverage_{return_coverage}_lt_{int(min_return_coverage)}_or_securities_{len(securities)}_lt_{int(min_securities)}"
    elif return_share > RECONCILE_FAIL_SHARE or volume_share > RECONCILE_FAIL_SHARE:
        status = "FAIL"
    else:
        status = "PASS"
    return {
        "aligned_n": n,
        "comparison_source": dict(source or {"kind": "caller_supplied_rows", "available": available}),
        "sharadar_n": len(sharadar),
        "yahoo_n": len(yahoo),
        "return_coverage_n": return_coverage,
        "volume_coverage_n": volume_coverage,
        "securities_n": len(securities),
        "marked_n": return_marked_n + volume_marked_n,
        "marked_share": None if return_coverage + volume_coverage == 0 else (return_marked_n + volume_marked_n) / max(return_coverage + volume_coverage, 1),
        "return_marked_share": return_share,
        "volume_marked_share": volume_share,
        "status": status,
        "insufficient_reason": thin_reason,
        "rows": [asdict(row) for row in rows],
        "yahoo_is_not_truth": True,
        "thresholds": {
            "return_abs_bp": RECONCILE_RETURN_MARK_BP,
            "volume_ratio": [RECONCILE_VOLUME_LOW, RECONCILE_VOLUME_HIGH],
            "fail_share": RECONCILE_FAIL_SHARE,
            "min_return_coverage": int(min_return_coverage),
            "min_securities": int(min_securities),
        },
    }


def reconciliation_csv(result: Mapping[str, Any], *, max_rows: int | None = None) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["security_id", "session_date", "return_diff_bp", "volume_ratio", "return_marked", "volume_marked", "reason_class"],
        lineterminator="\n",
    )
    writer.writeheader()
    rows = list(result.get("rows") or [])
    if max_rows is not None:
        rows = rows[: int(max_rows)]
    for row in rows:
        writer.writerow(row)
    if not rows:
        writer.writerow({
            "security_id": "NONE",
            "session_date": "NONE",
            "return_diff_bp": "NA",
            "volume_ratio": "NA",
            "return_marked": "false",
            "volume_marked": "false",
            "reason_class": result.get("status") or "AUTH_REQUIRED",
        })
    return buffer.getvalue()


def volume_scope_audit(*, minute_entitlement: bool) -> dict[str, Any]:
    return {
        "official_daily_volume_definition": "stocks.volume is split-adjusted; tape volume = volume * close / closeunadj",
        "session_scope": "UNKNOWN",
        "minute_entitlement": minute_entitlement,
        "status": "UNSUPPORTED" if not minute_entitlement else "PENDING_SAMPLE",
        "rvol_thresholds_not_migrated": True,
        "note": "No minute rights in this runtime; do not claim regular-session volume verification.",
    }


def trading_calendar_sessions(start: date, end: date) -> list[date]:
    """Independent exchange calendar. A missing vendor bar cannot shorten an H-day span."""

    if start > end:
        return []
    return list(trading_sessions(start, min(end, ALLOWED_END)))


def history_budget(
    *,
    earliest: date | None,
    entitlement_status: str,
    calendar: Sequence[date] | None = None,
    security_sessions: Mapping[str, Any] | None = None,
    warmup: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    """Numeric first-score and label-maturity dates, or NOT_COMPUTED. No expression strings.

    The family table is the earliest-any-security view; ``per_security`` carries
    the distribution of first-score days across securities, which is what a
    listing-date-aware evaluation actually uses. ``warmup`` exists so a test can
    state a short dependency; the production thresholds are unchanged.
    """

    warmup = dict(warmup or FAMILY_WARMUP_SESSIONS)
    start = None
    if entitlement_status == "HISTORY_10Y":
        start = HISTORY_10Y_START
    elif entitlement_status in {"READ_OK", "full"}:
        start = FULL_HISTORY_START
    if earliest is not None:
        start = earliest if start is None else max(start, earliest)
    sessions = [item for item in (calendar or []) if item <= ALLOWED_END]
    if start is not None:
        sessions = [item for item in sessions if item >= start]
    sessions.sort()
    per_security = _per_security_budget(security_sessions or {}, warmup, sessions)
    if earliest is None and not sessions:
        status = "AUTH_REQUIRED" if entitlement_status in {"AUTH_REQUIRED", ""} else "NOT_COMPUTED"
    elif not sessions:
        status = "NOT_COMPUTED"
    else:
        status = "COMPUTED"
    families = _family_budget(sessions, warmup) if status == "COMPUTED" else {
        family: {
            "warmup_sessions": need,
            "first_score_day": None,
            "last_valid_signal_day": None,
            "label_maturity": {str(h): {"mature_label_day": None, "evaluable_sessions": None} for h in LABEL_HORIZONS},
            "evaluable_sessions_after_warmup": None,
        }
        for family, need in warmup.items()
    }
    return {
        "status": status,
        "entitlement_status": entitlement_status,
        "raw_start": None if start is None else start.isoformat(),
        "allowed_end": ALLOWED_END.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "warmup_sessions": warmup,
        "residual_history_min": RESIDUAL_HISTORY_MIN,
        "label_horizons": list(LABEL_HORIZONS),
        "families": families,
        "first_score_day": {family: item["first_score_day"] for family, item in families.items()},
        "first_score_day_is_earliest_any_security": True,
        "evaluable_sessions_after_warmup": (
            {family: item["evaluable_sessions_after_warmup"] for family, item in families.items()}
            if status == "COMPUTED"
            else None
        ),
        "calendar_sessions_in_window": len(sessions) if sessions else None,
        "calendar_is_independent_of_price_rows": True,
        "calendar_first": sessions[0].isoformat() if sessions else None,
        "calendar_last": sessions[-1].isoformat() if sessions else None,
        "per_security": per_security,
        "evaluable_years_not_claimed_from_2010_alone": True,
        "ten_year_raw_length_is_not_ten_year_evaluable": True,
        "young_names_have_independent_insufficient": True,
    }


def _family_budget(sessions: Sequence[date], warmup: Mapping[str, int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    total = len(sessions)
    for family, need in warmup.items():
        need = int(need)
        first = sessions[need - 1] if total >= need else None
        labels: dict[str, Any] = {}
        for horizon in LABEL_HORIZONS:
            index = need - 1 + horizon
            mature = sessions[index] if total > index else None
            labels[str(horizon)] = {
                "mature_label_day": None if mature is None else mature.isoformat(),
                "evaluable_sessions": max(0, total - need - horizon + 1),
            }
        out[family] = {
            "warmup_sessions": need,
            "first_score_day": None if first is None else first.isoformat(),
            "last_valid_signal_day": sessions[-1].isoformat() if first is not None else None,
            "label_maturity": labels,
            "evaluable_sessions_after_warmup": max(0, total - need + 1) if first is not None else 0,
        }
    return out


def _security_summary(rows: Any) -> tuple[date | None, date | None, int]:
    if isinstance(rows, Mapping):
        first = rows.get("first")
        last = rows.get("last")
        n = int(rows.get("n") or 0)
        first = first if isinstance(first, date) or first is None else date.fromisoformat(str(first)[:10])
        last = last if isinstance(last, date) or last is None else date.fromisoformat(str(last)[:10])
        if last is not None and last > ALLOWED_END:
            last = ALLOWED_END
        return first, last, n
    usable = sorted({item for item in rows if item <= ALLOWED_END})
    if not usable:
        return None, None, 0
    return usable[0], usable[-1], len(usable)


def warmup_hit_date(rows: Any, need: int) -> tuple[date | None, bool]:
    """Date of the need-th valid observation for one security.

    Sparse bars are never compressed onto a contiguous calendar: the answer comes
    from the observations themselves. A summary that only kept first/last/n cannot
    answer it, and says so instead of guessing.
    """

    need = int(need)
    if need <= 0:
        return None, True
    if isinstance(rows, Mapping):
        hits = rows.get("warmup_hits") or {}
        token = hits.get(str(need)) if isinstance(hits, Mapping) else None
        if token:
            return date.fromisoformat(str(token)[:10]), True
        return None, False
    usable = sorted({item for item in rows if item <= ALLOWED_END})
    if len(usable) < need:
        return None, True
    return usable[need - 1], True


def _per_security_budget(
    security_sessions: Mapping[str, Any],
    warmup: Mapping[str, int],
    calendar: Sequence[date] | None = None,
) -> dict[str, Any]:
    longest = max(int(value) for value in warmup.values()) if warmup else 0
    evaluable: list[str] = []
    insufficient: list[str] = []
    sessions = list(calendar or [])
    first_scores: dict[str, list[date]] = {family: [] for family in warmup}
    exact_by_family: dict[str, bool] = {family: True for family in warmup}
    unknown_by_family: Counter[str] = Counter()
    gapped_securities = 0
    for security_id, rows in security_sessions.items():
        first, _last, n = _security_summary(rows)
        if n >= longest:
            evaluable.append(security_id)
        else:
            insufficient.append(security_id)
        if isinstance(rows, Mapping) and int(rows.get("max_gap_sessions") or 0) > 1:
            gapped_securities += 1
        if first is None:
            continue
        for family, need in warmup.items():
            need = int(need)
            if n < need:
                continue
            hit, known = warmup_hit_date(rows, need)
            if not known:
                exact_by_family[family] = False
                unknown_by_family[family] += 1
                continue
            if hit is not None:
                first_scores[family].append(hit)
    distribution: dict[str, Any] = {}
    for family, dates in first_scores.items():
        dates.sort()
        exact = exact_by_family[family]
        distribution[family] = {
            "status": "COMPUTED" if exact else "NOT_COMPUTED",
            "computed_from": "nth_valid_observation" if exact else "summary_without_valid_observation_dates",
            "securities_with_first_score": len(dates),
            "securities_without_observation_dates": int(unknown_by_family[family]),
            "earliest": dates[0].isoformat() if dates else None,
            "median": dates[len(dates) // 2].isoformat() if dates else None,
            "latest": dates[-1].isoformat() if dates else None,
        }
    return {
        "n": len(security_sessions),
        "longest_feature_dependency_sessions": longest,
        "evaluable_n": len(evaluable),
        "insufficient_n": len(insufficient),
        "insufficient_sample": sorted(insufficient)[:10],
        "first_score_day_distribution": distribution,
        "first_score_day_is_per_security": True,
        "first_score_day_counts_valid_observations_not_calendar_offset": True,
        "securities_with_observation_gaps": gapped_securities,
        "pool_earliest_is_not_every_security_ready": True,
        "calendar_sessions_supplied": len(sessions),
    }
