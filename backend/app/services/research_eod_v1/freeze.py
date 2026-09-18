"""Freeze the unique Round 2 candidate and validate it. No new weight grid."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.bootstrap import paired_diff_intervals
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.event_groups import EventLedger, count_groups
from app.services.research_eod_v1.pairing import common_member_pair
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.round1b import MAIN_LABEL_HORIZON, PRICE_BASELINE_ID
from app.services.research_eod_v1.round2 import (
    TargetedCandidate,
    historical_halve_at_boundary,
    planned_candidate_weights,
    weight_vector_hash,
)
from app.services.research_eod_v1.source_bind import member_set_hash

ensure_reference_on_path()
from registry import resolve_weights  # type: ignore

FROZEN_CANDIDATE_ID = "R2_automotive_D_residual_momentum_V_DOWNWEIGHT_5PP"
FROZEN_THEME = "automotive"
FROZEN_FAMILY = "D_residual_momentum"
FREEZE_PROTOCOL = "us-eod-research-freeze-v1"
CHECKLIST = (
    "valid_date_start_end",
    "yearly_counts",
    "quarterly_counts",
    "rejection_reasons_n0_label_unmature_common_n_below_10",
    "daily_member_hash_N_baseline_ic_variant_ic_delta",
    "own_ic_with_N_below_10_is_not_a_pair",
    "persistent_members_churn_repeat_events_concentration",
    "eligible_n_vs_matured_labels_vs_overlap_groups",
    "frozen_H_2H_seed174_2000_not_cherry_picked",
    "leave_one_member_N10_becomes_insufficient",
    "absolute_common_set_ICs",
    "descriptive_forward_only_if_cache_supports",
    "stop_rule_one_of_three",
)


def checklist_manifest(*, head: str | None = None, daily_effects_read: bool = False) -> dict[str, Any]:
    """Write the full validation list before any daily IC/effect is read."""

    return {
        "protocol": FREEZE_PROTOCOL,
        "candidate_id": FROZEN_CANDIDATE_ID,
        "theme": FROZEN_THEME,
        "family": FROZEN_FAMILY,
        "label_horizon": MAIN_LABEL_HORIZON,
        "control": PRICE_ONLY_DIAGNOSTIC,
        "control_alias": D_MARKET_RESIDUAL_DIAGNOSTIC,
        "not_a_second_experiment": True,
        "daily_effects_read": daily_effects_read,
        "holdout_start": "2024-07-01",
        "holdout_unsealed": False,
        "allowed_end": "2024-06-28",
        "executed_backtests": 0,
        "do_not_rerun_1152": True,
        "do_not_retune_to_1_2_3pp": True,
        "do_not_overwrite_b0_r1_r1b_r2": True,
        "do_not_unseal_or_merge": True,
        "bootstrap": {"seed": 174, "repeats": 2000, "block_choice": "frozen_H_and_2H", "not_cherry_picked": True},
        "checklist": [{"item": item, "required": True, "status": "PENDING"} for item in CHECKLIST],
        "head": head,
        "note": (
            "This manifest is the pre-read contract. Yearly/quarter/leave-one rows are kept "
            "even when negative. N=10 leave-one to N=9 is insufficient. Missing daily detail "
            "is NOT_VERIFIABLE, not an invented summary."
        ),
    }


def project_gaps() -> list[dict[str, str]]:
    return [
        {
            "gap": "三档三周期",
            "status": "已实现未验证",
            "note": (
                "Registry has conservative/balanced/aggressive and short/mid/long. "
                "Only balanced/mid with main label 20 was validated on B0."
            ),
        },
        {
            "gap": "十年以上数据",
            "status": "未做",
            "note": "Frozen B0 window is 2018-01-02 through 2024-06-28. No 10-year-plus tape.",
        },
        {
            "gap": "历史成员/退市/行业",
            "status": "未做",
            "note": (
                "known_theme_members only. No as-of membership, delist, rename, "
                "or historical industry reconstitution tape."
            ),
        },
        {
            "gap": "独立综合层",
            "status": "未做",
            "note": "No independent combiner or portfolio layer. Theme-family cards stay separate.",
        },
        {
            "gap": "经济执行与生产EOD接入",
            "status": "未做",
            "note": (
                "No raw trade prices or corporate-action evidence for the automotive pool. "
                "NAV, win-rate, slippage, and capacity are not invented. Production EOD is not wired."
            ),
        },
    ]


def next_stage_data_requirements() -> list[dict[str, str]]:
    """Field/coverage/access checklist only. Do not assume API rights or auto-buy."""

    return [
        {
            "field": "historical_constituent_membership",
            "coverage": "add/drop dates for all 24 themes through allowed_end",
            "access": "licensed or already-held membership tape; do not assume API rights",
        },
        {
            "field": "delisted_and_renamed_securities",
            "coverage": "survivorship-complete names in each theme window",
            "access": "licensed corporate action / delist feed; do not auto-buy",
        },
        {
            "field": "as_of_industry_classification",
            "coverage": "industry and theme tags as-of each session",
            "access": "point-in-time classification vendor; do not assume current tags are historical",
        },
        {
            "field": "split_and_unadjusted_eod_with_corporate_actions",
            "coverage": "OHLCV plus split/dividend events for candidate names",
            "access": "existing legal cache or licensed EOD; do not assume API rights; current local cache is SPY/NVDA only",
        },
        {
            "field": "next_day_confirm_trade_prices",
            "coverage": "T+1 open/close after signal_available_at for NEXT_DAY_CONFIRM",
            "access": "same legal cache; do not auto-buy; do not invent NAV from T-close to T+H-close labels",
        },
    ]


def load_frozen_candidate(path: Path) -> dict[str, Any]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    match = next(item for item in rows if item["candidate_id"] == FROZEN_CANDIDATE_ID)
    return dict(match)


def freeze_snapshot(row: Mapping[str, Any]) -> dict[str, Any]:
    before = 0.05497251372678729
    after = float(row["weights"]["V"])
    return {
        "candidate_id": FROZEN_CANDIDATE_ID,
        "theme": FROZEN_THEME,
        "family": FROZEN_FAMILY,
        "track": PRICE_ONLY_DIAGNOSTIC,
        "track_alias": D_MARKET_RESIDUAL_DIAGNOSTIC,
        "not_a_second_experiment": True,
        "profile": row.get("profile") or "balanced",
        "score_horizon": row.get("score_horizon") or "mid",
        "label_horizon": MAIN_LABEL_HORIZON,
        "factor": "V",
        "requested_delta": -0.05,
        "effective_delta": after - before,
        "before_V": before,
        "after_V": after,
        "relative_reduction": 1.0 - (after / before),
        "not_a_small_relative_cut": True,
        "weights": dict(row["weights"]),
        "vector_hash": weight_vector_hash(row["weights"]),
        "source_r2_pair_days": row.get("pair_days"),
        "source_r2_mean_common_n": row.get("mean_common_n"),
        "source_r2_eligible_signal_n": row.get("eligible_signal_n"),
        "source_r2_intervals": row.get("paired_diff_block_bootstrap"),
        "do_not_retune_to_1_2_3pp": True,
        "exploratory_seen_window": True,
        "not_a_sealed_winner": True,
        "protocol": FREEZE_PROTOCOL,
    }


def etfs_parameter_correction(registry: Mapping[str, Any], executed: Mapping[str, Any]) -> dict[str, Any]:
    """Append metadata to the immutable ETF D/P run. Do not rewrite it as a true -5pp."""

    candidate = TargetedCandidate(
        candidate_id=str(executed["candidate_id"]),
        theme="etfs",
        family="D_residual_momentum",
        track=PRICE_ONLY_DIAGNOSTIC,
        profile="balanced",
        score_horizon="mid",
        label_horizon=20,
        kind="reduce_weight",
        factor="P",
        delta=-0.05,
        source_variant=str(executed.get("source_variant") or "PRICE_DROP_P"),
        status="registered",
        reason="historical correction only",
        aliases=(D_MARKET_RESIDUAL_DIAGNOSTIC,),
    )
    base = resolve_weights(registry, "etfs", "D_residual_momentum", "balanced", "mid")
    before = diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family="D_residual_momentum")
    current = float(before["P"])
    requested = -0.05
    effective = historical_halve_at_boundary(current, requested)
    after = dict(executed["weights"])
    return {
        "candidate_id": executed["candidate_id"],
        "immutable_original_delta_field": executed.get("delta"),
        "requested_delta": requested,
        "effective_delta": float(after["P"]) - current,
        "boundary_policy": "HALVE_AT_BOUNDARY",
        "superseded_fallback": effective,
        "before": before,
        "after": after,
        "before_P": current,
        "after_P": float(after["P"]),
        "vector_hash": weight_vector_hash(after),
        "new_policy": "REJECT_INFEASIBLE",
        "do_not_rewrite_old_run_as_true_minus_5pp": True,
        "keep_historical_result": True,
        "recompute_full_history": False,
        "status": "keep_baseline",
        "note": (
            f"Registered -5pp from P={current} was infeasible. The executed vector halved P to "
            f"{after['P']}. Future runs raise instead of silent fallback."
        ),
    }


def theme_family_status(cards: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Inherit/keep/candidate/awaiting-data. Do not mint a new version per theme."""

    out = []
    for card in cards:
        theme = card["theme"]
        for family in card["families"]:
            name = family["family"]
            if theme == FROZEN_THEME and name == FROZEN_FAMILY:
                status = "FROZEN_EXPLORATORY_CANDIDATE"
            elif theme == "etfs" and name == FROZEN_FAMILY:
                status = "KEEP_BASELINE"
            elif theme == "fintech" and name == "B_confirmed_base_breakout":
                status = "KEEP_BASELINE"
            elif family.get("decision") == "CROSS_SECTION_THIN":
                status = "THIN_INHERIT"
            elif family.get("decision") in {"KEEP_FACTOR_EVIDENCE", "NO_ROBUST_INCREMENT"}:
                status = "KEEP_BASELINE"
            elif family.get("decision") == "CONTINUE_CANDIDATE":
                status = "KEEP_BASELINE_LIMITED_CONTINUE"
            elif family.get("price_track_status") in {None, "NO_DEFINED_PAIR", "NO_PRICE_BASELINE"}:
                status = "AWAITING_DATA"
            else:
                status = "KEEP_BASELINE"
            out.append(
                {
                    "theme": theme,
                    "family": name,
                    "eight_factor_status": family.get("eight_factor_status"),
                    "price_track_status": family.get("price_track_status"),
                    "r2_decision": family.get("decision"),
                    "final_status": status,
                    "new_version_minted": False,
                }
            )
    return out


def _quarter(session: str) -> str:
    month = int(session[5:7])
    return f"{session[:4]}Q{(month - 1) // 3 + 1}"


def validate_frozen_candidate(
    rows: Iterable[Mapping[str, Any]],
    registry: Mapping[str, Any],
    *,
    frozen: Mapping[str, Any],
    sessions: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Rescore only the frozen automotive D / V vector vs PRICE_ONLY baseline."""

    weights = dict(frozen["weights"])
    base = resolve_weights(registry, FROZEN_THEME, FROZEN_FAMILY, "balanced", "mid")
    price = diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family=FROZEN_FAMILY)
    required = family_required(FROZEN_FAMILY)
    floor = float(registry["profiles"]["balanced"]["score_floor"])
    coverage_min = float(registry["profiles"]["balanced"]["coverage_min"])
    session_order: list[str] = []
    current = None
    buckets: dict[str, list[dict[str, Any]]] = {"baseline": [], "candidate": []}
    daily: list[dict[str, Any]] = []
    eligible_days: dict[str, list[date]] = defaultdict(list)
    eligible_n = 0
    matured_eligible_n = 0
    rescore_calls = 0
    last_snap = None
    last_scored: dict[str, dict[str, Any]] = {}
    session_seen = 0
    session_missing_label = 0
    session_scored = 0

    def flush(session: str) -> None:
        nonlocal session_seen, session_missing_label, session_scored
        baseline = buckets["baseline"]
        variant = buckets["candidate"]
        pair = common_member_pair(baseline, variant)
        own_base = common_member_pair(baseline, baseline)
        reason = pair.get("undefined_reason")
        if not baseline and not variant:
            if session_seen == 0:
                reason = "N0_NO_SCORED_ROWS"
            elif session_missing_label == session_seen:
                reason = "LABEL_NOT_MATURE"
            elif session_scored == 0:
                reason = "N0_NO_SCORED_ROWS"
            else:
                reason = reason or "N0_NO_SCORED_ROWS"
        elif session_missing_label and pair["common_n"] == 0:
            reason = reason or "LABEL_NOT_MATURE"
        record = {
            "session": session,
            "year": session[:4],
            "quarter": _quarter(session),
            "baseline_n": pair["baseline_n"],
            "variant_n": pair["variant_n"],
            "common_n": pair["common_n"],
            "member_hash": pair.get("member_hash") or member_set_hash(pair.get("common_security_ids") or []),
            "common_security_ids": list(pair.get("common_security_ids") or []),
            "baseline_ic_own_set": own_base.get("baseline_ic_own_set"),
            "own_ic_is_not_a_pair": True,
            "baseline_ic_common": pair.get("baseline_ic_common"),
            "variant_ic_common": pair.get("variant_ic_common"),
            "paired_diff": pair.get("paired_diff"),
            "undefined_reason": reason,
            "defined_pair": pair.get("paired_diff") is not None,
        }
        if record["defined_pair"] is False and (own_base.get("baseline_n") or 0) >= 2:
            if (own_base.get("baseline_n") or 0) < 10:
                record["own_ic_below_pair_threshold"] = True
        daily.append(record)
        buckets["baseline"] = []
        buckets["candidate"] = []
        session_seen = 0
        session_missing_label = 0
        session_scored = 0

    for row in rows:
        theme = str(row.get("theme_id"))
        family = str(row.get("algorithm"))
        label_h = int(row.get("label_horizon") or 5)
        if theme != FROZEN_THEME or family != FROZEN_FAMILY or label_h != MAIN_LABEL_HORIZON:
            continue
        session = str(row.get("signal_session"))
        if session != current:
            if current is not None:
                flush(current)
            current = session
            session_order.append(session)
        sid = str(row.get("security_id"))
        snap = str(row.get("snapshot_key") or f"{session}|{theme}|{family}|balanced|mid|{sid}")
        if snap != last_snap:
            last_snap = snap
            last_scored = {
                "baseline": rescore_row(row, price, coverage_min=coverage_min, required=required, score_floor=floor),
                "candidate": rescore_row(row, weights, coverage_min=coverage_min, required=required, score_floor=floor),
            }
            rescore_calls += 2
        label = row.get("label")
        session_seen += 1
        if label is None:
            session_missing_label += 1
        if last_scored["baseline"]["score"] is not None or last_scored["candidate"]["score"] is not None:
            session_scored += 1
        for key in ("baseline", "candidate"):
            scored = last_scored[key]
            if scored["score"] is not None and label is not None:
                buckets[key].append({"security_id": sid, "score": float(scored["score"]), "label": float(label)})
        if last_scored["candidate"]["final_eligible"]:
            eligible_n += 1
            day = date.fromisoformat(session[:10])
            eligible_days[sid].append(day)
            if label is not None:
                matured_eligible_n += 1
    if current is not None:
        flush(current)

    timeline = list(sessions) if sessions else session_order
    by_session = {row["session"]: row for row in daily}
    aligned = []
    classified = Counter()
    for session in timeline:
        row = by_session.get(session)
        if row is None:
            classified["N0_NO_THEME_ROWS"] += 1
            aligned.append(None)
            continue
        if row["defined_pair"]:
            classified["DEFINED_PAIR"] += 1
            aligned.append(row["paired_diff"])
        else:
            reason = row["undefined_reason"] or "UNDEFINED"
            if reason == "CROSS_SECTION_BELOW_N":
                classified["COMMON_N_BELOW_10"] += 1
            elif reason in {"N0_NO_SCORED_ROWS", "NO_COMMON_MEMBERS"}:
                classified["N0_OR_NO_COMMON"] += 1
            elif reason == "LABEL_NOT_MATURE":
                classified["LABEL_NOT_MATURE"] += 1
            else:
                classified[reason] += 1
            aligned.append(None)
    valid = [row for row in daily if row["defined_pair"]]
    intervals = paired_diff_intervals(aligned, label_horizon=MAIN_LABEL_HORIZON)
    yearly: dict[str, dict[str, Any]] = {}
    quarterly: dict[str, dict[str, Any]] = {}
    for row in valid:
        yearly.setdefault(row["year"], []).append(row)
        quarterly.setdefault(row["quarter"], []).append(row)

    def _bucket_stats(groups: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
        out = {}
        for key, items in sorted(groups.items()):
            diffs = [float(item["paired_diff"]) for item in items]
            base_ics = [float(item["baseline_ic_common"]) for item in items if item["baseline_ic_common"] is not None]
            var_ics = [float(item["variant_ic_common"]) for item in items if item["variant_ic_common"] is not None]
            mean = sum(diffs) / len(diffs)
            out[key] = {
                "n": len(items),
                "mean_delta": mean,
                "mean_baseline_ic": None if not base_ics else sum(base_ics) / len(base_ics),
                "mean_variant_ic": None if not var_ics else sum(var_ics) / len(var_ics),
                "direction": "positive" if mean > 0 else "negative" if mean < 0 else "zero",
            }
        return out

    members = Counter()
    for row in valid:
        members.update(row["common_security_ids"])
    leave_one = []
    for sid, hits in members.most_common():
        remain = []
        lost = 0
        for row in valid:
            if sid not in row["common_security_ids"]:
                remain.append(row)
                continue
            if row["common_n"] - 1 < 10:
                lost += 1
                continue
            remain.append(row)
        remain_diffs = [float(item["paired_diff"]) for item in remain]
        leave_one.append(
            {
                "security_id": sid,
                "days_present": hits,
                "days_lost_below_n": lost,
                "remaining_defined_days": len(remain),
                "remaining_statistically_thin": len(remain) < 10,
                "remaining_mean_delta": None if not remain_diffs else sum(remain_diffs) / len(remain_diffs),
            }
        )
    theme_events = EventLedger()
    for sid, days in eligible_days.items():
        for day in days:
            theme_events.add((FROZEN_CANDIDATE_ID, FROZEN_THEME, FROZEN_FAMILY, "balanced", "mid", MAIN_LABEL_HORIZON, sid), day)
    fragments = 0
    overlaps = 0
    for key, days in theme_events.items():
        counted = count_groups(days, label_horizon=MAIN_LABEL_HORIZON)
        fragments += counted["consecutive_trigger_fragments"]
        overlaps += counted["label_horizon_overlap_groups"]
    abs_base = [float(row["baseline_ic_common"]) for row in valid if row["baseline_ic_common"] is not None]
    abs_var = [float(row["variant_ic_common"]) for row in valid if row["variant_ic_common"] is not None]
    mean_delta = None if not valid else sum(float(row["paired_diff"]) for row in valid) / len(valid)
    mean_base = None if not abs_base else sum(abs_base) / len(abs_base)
    mean_var = None if not abs_var else sum(abs_var) / len(abs_var)
    absolute_still_negative = mean_var is not None and mean_var < 0
    stop = "FROZEN_EXPLORATORY_CANDIDATE"
    if not valid:
        stop = "NOT_VERIFIABLE"
    elif len(valid) < 10:
        stop = "INSUFFICIENT"
    return {
        "protocol": FREEZE_PROTOCOL,
        "candidate_id": FROZEN_CANDIDATE_ID,
        "rescore_calls": rescore_calls,
        "timeline_n": len(timeline),
        "valid_pair_days": len(valid),
        "valid_start": None if not valid else valid[0]["session"],
        "valid_end": None if not valid else valid[-1]["session"],
        "valid_fraction": None if not timeline else len(valid) / len(timeline),
        "rejection_counts": dict(classified),
        "mean_common_n": None if not valid else sum(row["common_n"] for row in valid) / len(valid),
        "mean_delta": mean_delta,
        "mean_baseline_ic_common": mean_base,
        "mean_variant_ic_common": mean_var,
        "absolute_ics_may_be_negative": True,
        "absolute_variant_ic_negative": absolute_still_negative,
        "delta_positive_is_not_absolute_skill": True,
        "paired_diff_block_bootstrap": intervals,
        "yearly": _bucket_stats(yearly),
        "quarterly": _bucket_stats(quarterly),
        "member_counts": dict(members),
        "leave_one_member": leave_one,
        "eligible_signal_records": eligible_n,
        "eligible_matured_label_records": matured_eligible_n,
        "eligible_not_independent_trades": True,
        "event_fragments": fragments,
        "event_overlap_groups": overlaps,
        "independent_events": None,
        "execution_prices": "MISSING",
        "nav_winrate_capacity": "NOT_INVENTED",
        "signal_label_only": "T_close_to_T_plus_H_close",
        "earliest_trade_if_executed": "NEXT_DAY_CONFIRM",
        "stop_rule": stop,
        "daily": daily,
        "sessions": timeline,
        "checklist": list(CHECKLIST),
    }


def public_validation_table(result: Mapping[str, Any], *, limit: int = 500) -> list[dict[str, Any]]:
    rows = []
    for item in result.get("daily") or []:
        if not item.get("defined_pair"):
            continue
        rows.append(
            {
                "session": item["session"],
                "common_n": item["common_n"],
                "member_hash": item["member_hash"],
                "baseline_ic_common": item["baseline_ic_common"],
                "variant_ic_common": item["variant_ic_common"],
                "paired_diff": item["paired_diff"],
                "undefined_reason": item["undefined_reason"],
            }
        )
        if len(rows) >= limit:
            break
    return rows


def decide_stop(result: Mapping[str, Any]) -> dict[str, Any]:
    """One of three outcomes. Do not convert NOT_VERIFIABLE into more weight search."""

    valid = int(result.get("valid_pair_days") or 0)
    intervals = result.get("paired_diff_block_bootstrap") or {}
    h = (intervals.get("H") or {}).get("ci95")
    h2 = (intervals.get("2H") or {}).get("ci95")
    h_pos = bool(h and h[0] > 0)
    h2_pos = bool(h2 and h2[0] > 0)
    yearly = result.get("yearly") or {}
    year_dirs = {row.get("direction") for row in yearly.values()}
    leave = result.get("leave_one_member") or []
    all_leave_thin = bool(leave) and all(item.get("remaining_statistically_thin") for item in leave)
    if valid == 0:
        outcome = "INSUFFICIENT"
        reason = "no defined pair days"
    elif all_leave_thin and valid:
        outcome = "FROZEN_EXPLORATORY_CANDIDATE"
        reason = (
            "H/2H incremental intervals stay positive on the seen window, but every leave-one "
            "member drops N below 10. Keep as a frozen exploratory candidate, not a winner."
        )
        if not (h_pos and h2_pos):
            outcome = "REVERT_BASELINE"
            reason = "leave-one is thin and H/2H are not both above 0; revert to PRICE_ONLY baseline"
    elif h_pos and h2_pos and valid >= 10:
        outcome = "FROZEN_EXPLORATORY_CANDIDATE"
        reason = (
            "consistent incremental H/2H on the already-seen development window with N=10 and "
            f"{valid} pair days. Restricted evidence. Not a production champion."
        )
        if "negative" in year_dirs and "positive" in year_dirs:
            reason += " Year signs flip; do not treat as stable."
    elif not h_pos or not h2_pos:
        outcome = "REVERT_BASELINE"
        reason = "H or 2H crosses zero after the frozen-vector replay"
    else:
        outcome = "INSUFFICIENT"
        reason = "defined pairs exist but do not support a keep-or-revert call"
    return {
        "outcome": outcome,
        "reason": reason,
        "not_a_production_champion": True,
        "holdout_unsealed": False,
        "do_not_retune_weights": True,
        "two_cis_not_a_winner": True,
    }
