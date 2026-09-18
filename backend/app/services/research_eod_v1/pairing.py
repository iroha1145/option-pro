"""Common-member paired IC. Own-set IC subtraction is not a pair."""

from __future__ import annotations

import math
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.source_bind import member_set_hash
from app.services.research_eod_v1.stats import IC_MIN_CROSS_SECTION, spearman

PAIR_ID_FIELDS = (
    "session",
    "theme",
    "family",
    "track",
    "profile",
    "score_horizon",
    "label_horizon",
    "security_id",
)
NONFINITE_SCORE = "NONFINITE_SCORE"
NONFINITE_LABEL = "NONFINITE_LABEL"


class DuplicatePairIdError(ValueError):
    """The same pair identity appeared twice in one side of a comparison."""


def pair_identity(
    *,
    session: str,
    theme: str,
    family: str,
    track: str,
    profile: str,
    score_horizon: str,
    label_horizon: Any,
    security_id: str,
) -> tuple[str, ...]:
    return (
        str(session),
        str(theme),
        str(family),
        str(track),
        str(profile),
        str(score_horizon),
        str(label_horizon),
        str(security_id),
    )


def _coerce_finite(value: Any) -> tuple[float | None, bool]:
    if value is None:
        return None, False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None, False
    if not math.isfinite(number):
        return None, False
    return number, True


def row_finite_reason(row: Mapping[str, Any]) -> str | None:
    score, score_ok = _coerce_finite(row.get("score"))
    if not score_ok:
        return NONFINITE_SCORE
    _label, label_ok = _coerce_finite(row.get("label"))
    if not label_ok:
        return NONFINITE_LABEL
    return None


def _index_side(
    rows: Iterable[Mapping[str, Any]],
    *,
    require_finite_score_and_label: bool = True,
) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Index one side. Seen-set includes invalid rows so duplicates cannot hide."""

    finite: dict[str, dict[str, Any]] = {}
    invalid: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        sid = str(row.get("security_id"))
        if sid in seen:
            raise DuplicatePairIdError(f"duplicate security_id {sid}")
        seen.add(sid)
        if not require_finite_score_and_label:
            finite[sid] = dict(row)
            continue
        score, score_ok = _coerce_finite(row.get("score"))
        label, label_ok = _coerce_finite(row.get("label"))
        if not score_ok:
            invalid[sid] = NONFINITE_SCORE
            continue
        if not label_ok:
            invalid[sid] = NONFINITE_LABEL
            continue
        finite[sid] = {**dict(row), "score": score, "label": label}
    return finite, invalid


def common_member_pair(
    baseline_rows: Sequence[Mapping[str, Any]],
    variant_rows: Sequence[Mapping[str, Any]],
    *,
    min_n: int = IC_MIN_CROSS_SECTION,
) -> dict[str, Any]:
    """Rerank both sides on the common finite-score, matured-label names.

    Subtracting own-set ICs is not a pair. Cross-theme names are not pooled.
    Non-finite score or label is rejected before the common set is formed.
    """

    baseline, baseline_invalid = _index_side(baseline_rows)
    variant, variant_invalid = _index_side(variant_rows)
    baseline_ids = set(baseline)
    variant_ids = set(variant)
    common = sorted(baseline_ids & variant_ids)
    only_baseline = sorted(baseline_ids - variant_ids)
    only_variant = sorted(variant_ids - baseline_ids)
    missing_reasons = {}
    for sid in only_baseline:
        if sid in variant_invalid:
            missing_reasons[sid] = variant_invalid[sid]
        else:
            missing_reasons[sid] = str(variant_lookup_reason(sid, variant_rows))
    own_base = spearman([row["score"] for row in baseline.values()], [row["label"] for row in baseline.values()])
    own_var = spearman([row["score"] for row in variant.values()], [row["label"] for row in variant.values()])
    member_hash = member_set_hash(common)
    payload: dict[str, Any] = {
        "baseline_n": len(baseline),
        "variant_n": len(variant),
        "common_n": len(common),
        "baseline_only": only_baseline,
        "variant_only": only_variant,
        "baseline_only_n": len(only_baseline),
        "variant_only_n": len(only_variant),
        "missing_reasons": missing_reasons,
        "invalid_reasons": {"baseline": dict(baseline_invalid), "variant": dict(variant_invalid)},
        "common_security_ids": common,
        "member_hash": member_hash,
        "baseline_ic_own_set": own_base.value,
        "variant_ic_own_set": own_var.value,
        "own_set_ic_diff_is_not_a_pair": True,
        "paired_diff": None,
        "baseline_ic_common": None,
        "variant_ic_common": None,
        "undefined_reason": None,
    }
    if payload["common_n"] != len(payload["common_security_ids"]):
        raise RuntimeError("common_n disagrees with common_security_ids")
    if not common:
        payload["undefined_reason"] = "NO_COMMON_MEMBERS"
        return payload
    if len(common) < min_n:
        payload["undefined_reason"] = "CROSS_SECTION_BELOW_N"
        return payload
    base_scores = [baseline[sid]["score"] for sid in common]
    var_scores = [variant[sid]["score"] for sid in common]
    labels = [baseline[sid]["label"] for sid in common]
    for sid in common:
        if abs(float(baseline[sid]["label"]) - float(variant[sid]["label"])) > 1e-12:
            payload["undefined_reason"] = "LABEL_MISMATCH"
            return payload
    if len(base_scores) != payload["common_n"]:
        raise RuntimeError("actual Spearman N disagrees with common_n")
    base_common = spearman(base_scores, labels)
    var_common = spearman(var_scores, labels)
    payload["baseline_ic_common"] = base_common.value
    payload["variant_ic_common"] = var_common.value
    if base_common.value is None or var_common.value is None:
        payload["undefined_reason"] = base_common.reason or var_common.reason or "UNDEFINED_CORRELATION"
        return payload
    if base_common.n != payload["common_n"] or var_common.n != payload["common_n"]:
        raise RuntimeError("finite Spearman n disagrees with common_n / member hash")
    payload["paired_diff"] = float(var_common.value) - float(base_common.value)
    return payload


def variant_lookup_reason(security_id: str, variant_rows: Sequence[Mapping[str, Any]]) -> str:
    for row in variant_rows:
        if str(row.get("security_id")) != security_id:
            continue
        finite_reason = row_finite_reason(row)
        if finite_reason:
            return finite_reason
        reasons = row.get("rejection_reasons") or ()
        if reasons:
            return str(reasons[0])
        if row.get("score") is None:
            return str(row.get("status") or "MISSING_SCORE")
        if row.get("label") is None:
            return "LABEL_NOT_MATURE"
        return "ABSENT_FROM_VARIANT_UNIVERSE"
    return "ABSENT_FROM_VARIANT_UNIVERSE"
