"""Common-member paired IC. Own-set IC subtraction is not a pair."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

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


def _index_side(
    rows: Iterable[Mapping[str, Any]],
    *,
    require_finite_score_and_label: bool = True,
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        sid = str(row.get("security_id"))
        if sid in out:
            raise DuplicatePairIdError(f"duplicate security_id {sid}")
        score = row.get("score")
        label = row.get("label")
        if require_finite_score_and_label:
            try:
                score_f = float(score)
                label_f = float(label)
            except (TypeError, ValueError):
                continue
            if score is None or label is None:
                continue
            out[sid] = {**dict(row), "score": score_f, "label": label_f}
        else:
            out[sid] = dict(row)
    return out


def common_member_pair(
    baseline_rows: Sequence[Mapping[str, Any]],
    variant_rows: Sequence[Mapping[str, Any]],
    *,
    min_n: int = IC_MIN_CROSS_SECTION,
) -> dict[str, Any]:
    """Rerank both sides on the common finite-score, matured-label names.

    Subtracting own-set ICs is not a pair. Cross-theme names are not pooled.
    """

    baseline = _index_side(baseline_rows)
    variant = _index_side(variant_rows)
    baseline_ids = set(baseline)
    variant_ids = set(variant)
    common = sorted(baseline_ids & variant_ids)
    only_baseline = sorted(baseline_ids - variant_ids)
    only_variant = sorted(variant_ids - baseline_ids)
    missing_reasons = {
        sid: str(variant_lookup_reason(sid, variant_rows))
        for sid in only_baseline
    }
    own_base = spearman([row["score"] for row in baseline.values()], [row["label"] for row in baseline.values()])
    own_var = spearman([row["score"] for row in variant.values()], [row["label"] for row in variant.values()])
    payload: dict[str, Any] = {
        "baseline_n": len(baseline),
        "variant_n": len(variant),
        "common_n": len(common),
        "baseline_only": only_baseline,
        "variant_only": only_variant,
        "baseline_only_n": len(only_baseline),
        "variant_only_n": len(only_variant),
        "missing_reasons": missing_reasons,
        "common_security_ids": common,
        "baseline_ic_own_set": own_base.value,
        "variant_ic_own_set": own_var.value,
        "own_set_ic_diff_is_not_a_pair": True,
        "paired_diff": None,
        "baseline_ic_common": None,
        "variant_ic_common": None,
        "undefined_reason": None,
    }
    if not common:
        payload["undefined_reason"] = "NO_COMMON_MEMBERS"
        return payload
    if len(common) < min_n:
        payload["undefined_reason"] = "CROSS_SECTION_BELOW_N"
        return payload
    base_scores = [baseline[sid]["score"] for sid in common]
    var_scores = [variant[sid]["score"] for sid in common]
    labels = [baseline[sid]["label"] for sid in common]
    # labels on the variant side must match; a mismatch is not a pair
    for sid in common:
        if abs(float(baseline[sid]["label"]) - float(variant[sid]["label"])) > 1e-12:
            payload["undefined_reason"] = "LABEL_MISMATCH"
            return payload
    base_common = spearman(base_scores, labels)
    var_common = spearman(var_scores, labels)
    payload["baseline_ic_common"] = base_common.value
    payload["variant_ic_common"] = var_common.value
    if base_common.value is None or var_common.value is None:
        payload["undefined_reason"] = base_common.reason or var_common.reason or "UNDEFINED_CORRELATION"
        return payload
    payload["paired_diff"] = float(var_common.value) - float(base_common.value)
    return payload


def variant_lookup_reason(security_id: str, variant_rows: Sequence[Mapping[str, Any]]) -> str:
    for row in variant_rows:
        if str(row.get("security_id")) != security_id:
            continue
        reasons = row.get("rejection_reasons") or ()
        if reasons:
            return str(reasons[0])
        if row.get("score") is None:
            return str(row.get("status") or "MISSING_SCORE")
        if row.get("label") is None:
            return "LABEL_NOT_MATURE"
        return "ABSENT_FROM_VARIANT_UNIVERSE"
    return "ABSENT_FROM_VARIANT_UNIVERSE"
