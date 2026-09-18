"""Round 2 targeted candidates. At most three signed label-20 slots."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    diagnostic_weights,
    family_required,
    realloc_plus_pp,
    rescore_row,
)
from app.services.research_eod_v1.pairing import common_member_pair
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.round1b import (
    MAIN_LABEL_HORIZON,
    PRICE_BASELINE_ID,
    SCORE_FLOOR_ID,
    ci_location,
    signed_ablation_direction,
    transform_kind,
)
from app.services.research_eod_v1.runs import canonical_json
from app.services.research_eod_v1.bootstrap import paired_diff_intervals

ensure_reference_on_path()
from registry import resolve_weights  # type: ignore

MAX_CANDIDATES = 3
DOWNWEIGHT_PP = -0.05
SKIP_ALREADY_RUN_NEIGHBORS = {
    ("semiconductors", "A_trend_quality", "N_M_PLUS_5PP_REALLOC_V1"),
}


@dataclass(frozen=True)
class TargetedCandidate:
    candidate_id: str
    theme: str
    family: str
    track: str
    profile: str
    score_horizon: str
    label_horizon: int
    kind: str
    factor: str | None
    delta: float | None
    source_variant: str
    status: str
    reason: str
    aliases: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "theme": self.theme,
            "family": self.family,
            "track": self.track,
            "profile": self.profile,
            "score_horizon": self.score_horizon,
            "label_horizon": self.label_horizon,
            "kind": self.kind,
            "factor": self.factor,
            "delta": self.delta,
            "source_variant": self.source_variant,
            "status": self.status,
            "reason": self.reason,
            "aliases": list(self.aliases),
            "hard_gates_unchanged": True,
            "reference_pool_unchanged": True,
            "exploratory_seen_window": True,
            "not_a_sealed_winner": True,
        }


def _yearly_same_side(item: Mapping[str, Any], *, want: str) -> bool:
    years = item.get("yearly_direction") or {}
    directions = [
        row.get("direction")
        for row in years.values()
        if isinstance(row, Mapping) and row.get("direction") in {"positive", "negative"}
    ]
    if len(directions) < 2:
        return True
    return all(direction == want for direction in directions)


def _both_bands(item: Mapping[str, Any], *, side: str) -> bool:
    bands = item.get("paired_diff_block_bootstrap") or {}
    h_side = ci_location((bands.get("H") or {}).get("ci95"))
    h2_side = ci_location((bands.get("2H") or {}).get("ci95"))
    return h_side == side and h2_side == side


def _dropped_factor(variant_id: str) -> str | None:
    prefix = "PRICE_DROP_"
    if variant_id.startswith(prefix) and len(variant_id) == len(prefix) + 1:
        return variant_id[len(prefix) :]
    return None


def select_targeted_candidates(
    pairing: Iterable[Mapping[str, Any]],
    *,
    profile: str = "balanced",
    score_horizon: str = "mid",
    max_candidates: int = MAX_CANDIDATES,
) -> list[TargetedCandidate]:
    """Register at most three reduce-weight slots from the revised ablation table.

    Unified M+/S+ reallocs are not reapplied to every theme. H-only or year-flipped
    cells stay keep-baseline / limited continue. 63-day diagnostics are not slots.
    """

    chosen: list[TargetedCandidate] = []
    used_slots: set[tuple[str, str]] = set()
    for item in pairing:
        if len(chosen) >= max_candidates:
            break
        if int(item.get("label_horizon") or 0) != MAIN_LABEL_HORIZON:
            continue
        if item.get("track") != PRICE_ONLY_DIAGNOSTIC:
            continue
        if item.get("variant_id") == SCORE_FLOOR_ID:
            continue
        key = (str(item.get("theme")), str(item.get("family")), str(item.get("variant_id")))
        if key in SKIP_ALREADY_RUN_NEIGHBORS:
            continue
        if signed_ablation_direction(item) != "REDUCE_WEIGHT_CANDIDATE":
            continue
        if not _both_bands(item, side="above_zero"):
            continue
        if not _yearly_same_side(item, want="positive"):
            continue
        factor = item.get("dropped") or _dropped_factor(str(item.get("variant_id") or ""))
        if not factor:
            continue
        slot = (str(item["theme"]), str(item["family"]))
        if slot in used_slots:
            continue
        used_slots.add(slot)
        aliases = (D_MARKET_RESIDUAL_DIAGNOSTIC,) if item["family"] == "D_residual_momentum" else ()
        chosen.append(
            TargetedCandidate(
                candidate_id=f"R2_{item['theme']}_{item['family']}_{factor}_DOWNWEIGHT_5PP",
                theme=str(item["theme"]),
                family=str(item["family"]),
                track=PRICE_ONLY_DIAGNOSTIC,
                profile=profile,
                score_horizon=score_horizon,
                label_horizon=MAIN_LABEL_HORIZON,
                kind="reduce_weight",
                factor=str(factor),
                delta=DOWNWEIGHT_PP,
                source_variant=str(item["variant_id"]),
                status="registered",
                reason=(
                    "label-20 drop/realloc H and 2H both above 0 with yearly same-side; "
                    "register a small downweight, not an automatic delete"
                ),
                aliases=aliases,
            )
        )
    while len(chosen) < max_candidates:
        idx = len(chosen) + 1
        chosen.append(
            TargetedCandidate(
                candidate_id=f"R2_UNFILLED_{idx}",
                theme="",
                family="",
                track=PRICE_ONLY_DIAGNOSTIC,
                profile=profile,
                score_horizon=score_horizon,
                label_horizon=MAIN_LABEL_HORIZON,
                kind="keep_baseline",
                factor=None,
                delta=None,
                source_variant="",
                status="keep_baseline",
                reason="no additional signed label-20 reduce-weight evidence; keep price baseline",
            )
        )
    return chosen[:max_candidates]


def weight_vector_hash(weights: Mapping[str, float]) -> str:
    return hashlib.sha256(canonical_json(dict(weights)).encode()).hexdigest()


def planned_candidate_weights(
    registry: Mapping[str, Any],
    candidate: TargetedCandidate,
) -> dict[str, Any]:
    """Refuse infeasible deltas. Do not silently halve at the boundary."""

    if candidate.status != "registered" or not candidate.factor or candidate.delta is None:
        raise ValueError(f"{candidate.candidate_id} is not an executable weight change")
    base = resolve_weights(registry, candidate.theme, candidate.family, candidate.profile, candidate.score_horizon)
    price = diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family=candidate.family)
    current = float(price.get(candidate.factor) or 0.0)
    requested = float(candidate.delta)
    target = current + requested
    if target <= 0.0 or target >= 1.0:
        raise ValueError(
            f"{candidate.candidate_id}: requested_delta={requested} is infeasible from "
            f"{candidate.factor}={current}; refuse silent HALVE_AT_BOUNDARY; "
            "register a replacement candidate before execution"
        )
    after = realloc_plus_pp(price, candidate.factor, requested)
    effective = float(after[candidate.factor]) - current
    return {
        "requested_delta": requested,
        "effective_delta": effective,
        "boundary_policy": "REJECT_INFEASIBLE",
        "before": dict(price),
        "after": after,
        "before_factor": current,
        "after_factor": float(after[candidate.factor]),
        "vector_hash": weight_vector_hash(after),
        "transform_kind": transform_kind(
            {"kind": candidate.kind, "variant_id": candidate.candidate_id, "delta": requested}
        ),
    }


def candidate_weights(
    registry: Mapping[str, Any],
    candidate: TargetedCandidate,
) -> dict[str, float]:
    return planned_candidate_weights(registry, candidate)["after"]


def historical_halve_at_boundary(current: float, requested_delta: float) -> float:
    """Document the superseded R2 fallback. Not used for new execution."""

    if current + requested_delta <= 0:
        return -0.5 * current
    return requested_delta


def analyze_targeted_candidates(
    rows: Iterable[Mapping[str, Any]],
    registry: Mapping[str, Any],
    candidates: Sequence[TargetedCandidate],
    *,
    sessions: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Rescore only the registered theme/family slots. Capability set stays unchanged."""

    executable = [item for item in candidates if item.status == "registered"]
    if not executable:
        return [item.as_dict() | {"executed": False, "result": "keep_baseline"} for item in candidates]
    wanted = {(item.theme, item.family, item.label_horizon) for item in executable}
    weight_map = {item.candidate_id: candidate_weights(registry, item) for item in executable}
    required_map = {item.family: family_required(item.family) for item in executable}
    floor = float(registry["profiles"]["balanced"]["score_floor"])
    coverage_min = float(registry["profiles"]["balanced"]["coverage_min"])
    session_order: list[str] = []
    current = None
    last_snap = None
    last_scored: dict[str, dict[str, Any]] = {}
    pair_by: dict[str, dict[str, float | None]] = {item.candidate_id: {} for item in executable}
    common_n_sum = {item.candidate_id: 0 for item in executable}
    pair_days = {item.candidate_id: 0 for item in executable}
    own_ic_days = {item.candidate_id: 0 for item in executable}
    undefined = {item.candidate_id: 0 for item in executable}
    eligible_n = {item.candidate_id: 0 for item in executable}
    rescore_calls = 0
    session_members: dict[tuple[str, str, int], dict[str, list[dict[str, Any]]]] = {}

    def flush_session(session: str) -> None:
        for (theme, family, label_h), groups in session_members.items():
            baseline = groups.get(PRICE_BASELINE_ID) or []
            for candidate in executable:
                if candidate.theme != theme or candidate.family != family or candidate.label_horizon != label_h:
                    continue
                members = groups.get(candidate.candidate_id) or []
                own = common_member_pair(members, members)
                if own["baseline_ic_own_set"] is not None:
                    own_ic_days[candidate.candidate_id] += 1
                pair = common_member_pair(baseline, members)
                if pair["paired_diff"] is None:
                    undefined[candidate.candidate_id] += 1
                    pair_by[candidate.candidate_id][session] = None
                else:
                    pair_days[candidate.candidate_id] += 1
                    common_n_sum[candidate.candidate_id] += pair["common_n"]
                    pair_by[candidate.candidate_id][session] = pair["paired_diff"]
        session_members.clear()

    for row in rows:
        theme = str(row.get("theme_id"))
        family = str(row.get("algorithm"))
        label_h = int(row.get("label_horizon") or 5)
        if (theme, family, label_h) not in wanted:
            continue
        session = str(row.get("signal_session"))
        if session != current:
            if current is not None:
                flush_session(current)
            current = session
            session_order.append(session)
        sid = str(row.get("security_id"))
        snap = str(row.get("snapshot_key") or f"{session}|{theme}|{family}|balanced|mid|{sid}")
        matching = [
            item
            for item in executable
            if item.theme == theme and item.family == family and item.label_horizon == label_h
        ]
        if snap != last_snap:
            last_snap = snap
            last_scored = {}
            base = resolve_weights(registry, theme, family, "balanced", "mid")
            price = diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family=family)
            last_scored[PRICE_BASELINE_ID] = rescore_row(
                row, price, coverage_min=coverage_min, required=required_map[family], score_floor=floor
            )
            rescore_calls += 1
            for candidate in matching:
                last_scored[candidate.candidate_id] = rescore_row(
                    row,
                    weight_map[candidate.candidate_id],
                    coverage_min=coverage_min,
                    required=required_map[family],
                    score_floor=floor,
                )
                rescore_calls += 1
        key = (theme, family, label_h)
        slot = session_members.setdefault(key, {PRICE_BASELINE_ID: []})
        scored = last_scored[PRICE_BASELINE_ID]
        if scored["score"] is not None and row.get("label") is not None:
            slot[PRICE_BASELINE_ID].append(
                {"security_id": sid, "score": float(scored["score"]), "label": float(row["label"])}
            )
        for candidate in matching:
            scored_c = last_scored[candidate.candidate_id]
            slot.setdefault(candidate.candidate_id, [])
            if scored_c["score"] is not None and row.get("label") is not None:
                slot[candidate.candidate_id].append(
                    {"security_id": sid, "score": float(scored_c["score"]), "label": float(row["label"])}
                )
            if scored_c["final_eligible"]:
                eligible_n[candidate.candidate_id] += 1
    if current is not None:
        flush_session(current)
    timeline = sessions or session_order
    out = []
    for candidate in candidates:
        payload = candidate.as_dict()
        if candidate.status != "registered":
            payload.update({"executed": False, "result": "keep_baseline"})
            out.append(payload)
            continue
        aligned = [pair_by[candidate.candidate_id].get(session) for session in timeline]
        intervals = paired_diff_intervals(aligned, label_horizon=candidate.label_horizon)
        payload.update(
            {
                "executed": True,
                "result": "executed",
                "actual_rescore_calls": rescore_calls,
                "pair_days": pair_days[candidate.candidate_id],
                "own_ic_days": own_ic_days[candidate.candidate_id],
                "insufficient_date_n": undefined[candidate.candidate_id],
                "mean_common_n": None
                if not pair_days[candidate.candidate_id]
                else common_n_sum[candidate.candidate_id] / pair_days[candidate.candidate_id],
                "eligible_signal_n": eligible_n[candidate.candidate_id],
                "paired_diff_block_bootstrap": intervals,
                "weights": weight_map[candidate.candidate_id],
            }
        )
        out.append(payload)
    return out


def scan_tape_nonfinite(path: Path, *, limit: int | None = None) -> dict[str, Any]:
    """Audit whether the frozen B0 tape actually has non-finite score/label rows."""

    n_rows = 0
    n_score = 0
    n_label = 0
    examples: list[dict[str, Any]] = []
    with path.open("rb") as handle:
        for raw in handle:
            n_rows += 1
            row = json.loads(raw)
            score = row.get("score")
            label = row.get("label")
            score_bad = _is_nonfinite(score)
            label_bad = _is_nonfinite(label)
            if score_bad:
                n_score += 1
            if label_bad:
                n_label += 1
            if (score_bad or label_bad) and len(examples) < 5:
                examples.append(
                    {
                        "signal_session": row.get("signal_session"),
                        "theme_id": row.get("theme_id"),
                        "security_id": row.get("security_id"),
                        "score": score,
                        "label": label,
                    }
                )
            if limit is not None and n_rows >= limit:
                break
    present = n_score > 0 or n_label > 0
    return {
        "rows_scanned": n_rows,
        "nonfinite_score_rows": n_score,
        "nonfinite_label_rows": n_label,
        "examples": examples,
        "present_on_tape": present,
        "implication": (
            "B0 tape contains non-finite score/label rows; pairing now rejects them before the common set."
            if present
            else (
                "B0 tape scan found no non-finite score/label. This round only adds a guard. "
                "It does not imply that prior pairing results are invalid."
            )
        ),
    }


def _is_nonfinite(value: Any) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return True
    return not math.isfinite(number)
