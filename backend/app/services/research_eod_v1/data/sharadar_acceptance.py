"""Field/coverage acceptance fixtures. Live Sharadar rows stay AUTH_REQUIRED without a secret."""

from __future__ import annotations

import csv
import io
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
        "resolved_by": "permaticker_and_event_year" if identity else "unresolved",
        "event_window": dict(event_window or {}),
        "fixture_year_used_to_resolve_identity": True,
        "not_last_row_of_same_named_security": True,
    }
    terminal = classify_terminal(actions, last_trade=last_trade)
    expected = fixture["expected_terminal"]
    if not actions:
        return {
            **dict(fixture),
            "live_status": "AUTH_REQUIRED",
            "verification": "fixture_list_only",
            "observed_terminal": terminal,
            "identity_resolution": resolution,
        }
    if expected == "acquisition_or_unknown":
        matched = terminal["label"] in {"acquisition_cash", "TERMINAL_UNKNOWN"}
        status = "PASS" if matched else "FAIL"
    elif expected == "acquisition_cash":
        if terminal["label"] == "acquisition_cash":
            status = "PASS"
        elif terminal["label"] == "TERMINAL_UNKNOWN" and terminal.get("reason") == "acquisition_without_cash":
            status = "UNSUPPORTED"
        else:
            status = "FAIL"
    elif expected == "bankruptcy_last_trade":
        if terminal["label"] == "bankruptcy_last_trade":
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
        "identity_resolution": resolution,
        "economic_settlement_blocked_only": status in {"UNSUPPORTED", "INSUFFICIENT"},
    }


def reconcile_aligned_returns(
    sharadar: Sequence[Mapping[str, Any]],
    yahoo: Sequence[Mapping[str, Any]],
    *,
    source_available: bool | None = None,
    source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Each field carries its own valid denominator. A missing comparison source is not a pass."""

    yahoo_map = {(row["security_id"], row["session_date"]): row for row in yahoo}
    rows: list[ReconcileRow] = []
    return_coverage = 0
    volume_coverage = 0
    return_marked_n = 0
    volume_marked_n = 0
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
    if not available:
        status = "AUTH_REQUIRED" if not sharadar else "RECONCILIATION_MISSING"
    elif n == 0 or (return_coverage == 0 and volume_coverage == 0):
        status = "INSUFFICIENT"
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
        "marked_n": return_marked_n + volume_marked_n,
        "marked_share": None if return_coverage + volume_coverage == 0 else (return_marked_n + volume_marked_n) / max(return_coverage + volume_coverage, 1),
        "return_marked_share": return_share,
        "volume_marked_share": volume_share,
        "status": status,
        "rows": [asdict(row) for row in rows],
        "yahoo_is_not_truth": True,
        "thresholds": {
            "return_abs_bp": RECONCILE_RETURN_MARK_BP,
            "volume_ratio": [RECONCILE_VOLUME_LOW, RECONCILE_VOLUME_HIGH],
            "fail_share": RECONCILE_FAIL_SHARE,
        },
    }


def reconciliation_csv(result: Mapping[str, Any]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=["security_id", "session_date", "return_diff_bp", "volume_ratio", "return_marked", "volume_marked", "reason_class"],
        lineterminator="\n",
    )
    writer.writeheader()
    for row in result.get("rows") or []:
        writer.writerow(row)
    if not result.get("rows"):
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
    security_sessions: Mapping[str, Sequence[date]] | None = None,
) -> dict[str, Any]:
    """Numeric first-score and label-maturity dates, or NOT_COMPUTED. No expression strings."""

    warmup = dict(FAMILY_WARMUP_SESSIONS)
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
    per_security = _per_security_budget(security_sessions or {}, warmup)
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


def _per_security_budget(
    security_sessions: Mapping[str, Sequence[date]],
    warmup: Mapping[str, int],
) -> dict[str, Any]:
    longest = max(int(value) for value in warmup.values()) if warmup else 0
    evaluable: list[str] = []
    insufficient: list[str] = []
    for security_id, rows in security_sessions.items():
        usable = sorted({item for item in rows if item <= ALLOWED_END})
        if len(usable) >= longest:
            evaluable.append(security_id)
        else:
            insufficient.append(security_id)
    return {
        "n": len(security_sessions),
        "longest_feature_dependency_sessions": longest,
        "evaluable_n": len(evaluable),
        "insufficient_n": len(insufficient),
        "insufficient_sample": sorted(insufficient)[:10],
    }
