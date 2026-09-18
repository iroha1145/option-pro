"""Field/coverage acceptance fixtures. Live Sharadar rows stay AUTH_REQUIRED without a secret."""

from __future__ import annotations

import csv
import io
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.constants import RESIDUAL_HISTORY_MIN
from app.services.research_eod_v1.data.sharadar_identity import classify_terminal
from app.services.research_eod_v1.data.sharadar_schema import (
    ALLOWED_END,
    FULL_HISTORY_START,
    HISTORY_10Y_START,
    HOLDOUT_START,
)

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


def evaluate_delist_fixture(fixture: Mapping[str, Any], actions: Sequence[Mapping[str, Any]], last_trade: float | None) -> dict[str, Any]:
    terminal = classify_terminal(actions, last_trade=last_trade)
    expected = fixture["expected_terminal"]
    if expected == "acquisition_or_unknown":
        matched = terminal["label"] in {"acquisition_cash", "TERMINAL_UNKNOWN"}
    elif expected == "acquisition_cash":
        matched = terminal["label"] == "acquisition_cash"
    else:
        matched = terminal["label"] == expected
    return {
        **dict(fixture),
        "live_status": "AUTH_REQUIRED" if not actions else ("PASS" if matched else "FAIL"),
        "observed_terminal": terminal,
    }


def reconcile_aligned_returns(
    sharadar: Sequence[Mapping[str, Any]],
    yahoo: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    yahoo_map = {(row["security_id"], row["session_date"]): row for row in yahoo}
    rows: list[ReconcileRow] = []
    for row in sharadar:
        key = (row["security_id"], row["session_date"])
        other = yahoo_map.get(key)
        if other is None:
            continue
        ret_s = row.get("return")
        ret_y = other.get("return")
        vol_s = row.get("volume")
        vol_y = other.get("volume")
        diff_bp = None if ret_s is None or ret_y is None else abs(float(ret_s) - float(ret_y)) * 10_000
        ratio = None if not vol_s or not vol_y else float(vol_s) / float(vol_y)
        return_marked = diff_bp is not None and diff_bp > RECONCILE_RETURN_MARK_BP
        volume_marked = ratio is not None and not (RECONCILE_VOLUME_LOW <= ratio <= RECONCILE_VOLUME_HIGH)
        reason = "ok"
        if return_marked and volume_marked:
            reason = "return_and_volume"
        elif return_marked:
            reason = "return_gt_50bp"
        elif volume_marked:
            reason = "volume_outside_0.8_1.25"
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
    marked = [row for row in rows if row.return_marked or row.volume_marked]
    share = 0.0 if n == 0 else len(marked) / n
    return {
        "aligned_n": n,
        "marked_n": len(marked),
        "marked_share": share,
        "status": "FAIL" if n and share > RECONCILE_FAIL_SHARE else ("PASS" if n else "AUTH_REQUIRED"),
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


def history_budget(*, earliest: date | None, entitlement_status: str, calendar_sessions: int | None) -> dict[str, Any]:
    warmup = dict(FAMILY_WARMUP_SESSIONS)
    start = None
    if entitlement_status == "HISTORY_10Y":
        start = HISTORY_10Y_START
    elif entitlement_status in {"READ_OK", "full"}:
        start = FULL_HISTORY_START
    if earliest is not None and start is not None:
        start = max(start, earliest)
    mature = None if start is None else "calendar_minus_family_warmup"
    return {
        "status": "AUTH_REQUIRED" if earliest is None else "COMPUTED",
        "entitlement_status": entitlement_status,
        "raw_start": None if start is None else start.isoformat(),
        "allowed_end": ALLOWED_END.isoformat(),
        "holdout_start": HOLDOUT_START.isoformat(),
        "warmup_sessions": warmup,
        "residual_history_min": RESIDUAL_HISTORY_MIN,
        "first_score_day": mature,
        "mature_labels": "holding_horizon after first_score_day",
        "evaluable_years_not_claimed_from_2010_alone": True,
        "calendar_sessions_in_window": calendar_sessions,
        "young_names_have_independent_insufficient": True,
    }
