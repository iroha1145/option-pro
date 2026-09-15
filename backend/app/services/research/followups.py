"""At most two diagnosis-driven follow-ups. Not a parameter search."""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.services.research.compare import close_excess, group_by_date, paired_top_series
from app.services.strength.scoring import _factor_result, absolute_return_score, score_value
from app.services.strength.ranking_variants import (
    apply_a0_mid_long,
    production_tiebreak_sort_key,
)


F1_ID = "F1_rs_spy_63d_gt_0"
F2_ID = "F2_return_factor_percentiles_then_a0"
F1_THRESHOLD = 0.0
F2_MIN_CROSS_SECTION = 8


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def xs_percentile(values: Sequence[float], value: float) -> float | None:
    if len(values) < F2_MIN_CROSS_SECTION:
        return None
    if len(set(values)) < 2:
        return None
    ordered = sorted(values)
    lo = ordered.index(value)
    hi = len(ordered) - list(reversed(ordered)).index(value) - 1
    mid = (lo + hi) / 2.0
    return 100.0 * mid / (len(ordered) - 1)


def apply_f1_rs_filter(rows: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep original order among names with T-visible rs_spy_63d > 0."""

    eligible = [dict(row) for row in rows]
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    missing = 0
    for row in eligible:
        rs = _finite(row.get("rs_spy_63d"))
        if rs is None:
            missing += 1
            dropped.append(row)
            continue
        if rs > F1_THRESHOLD:
            item = dict(row)
            item["research_ranking"] = F1_ID
            kept.append(item)
        else:
            dropped.append(row)
    kept.sort(
        key=lambda item: production_tiebreak_sort_key(
            _finite(item.get("ranking_score")),
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    for rank, item in enumerate(kept, start=1):
        item["selected_view_rank"] = rank
    return {
        "rows": kept,
        "dropped": dropped,
        "missing_rs": missing,
        "kept": len(kept),
        "eligible": len(eligible),
        "coverage": None if not eligible else len(kept) / len(eligible),
    }


def _rebuild_mid_long(row: Mapping[str, Any], pct: Mapping[str, float | None]) -> tuple[float | None, float | None]:
    mid = _factor_result(
        {
            "return_63d": pct.get("return_63d"),
            "relative_strength_spy_63d": pct.get("rs_spy_63d"),
            "ma_alignment": score_value(row.get("ma_alignment")),
            "distance_sma50": absolute_return_score(row.get("dist_sma50"), 320.0),
        },
        {
            "return_63d": 0.28,
            "relative_strength_spy_63d": 0.27,
            "ma_alignment": 0.20,
            "distance_sma50": 0.15,
        },
        min_active_weight=0.45,
    )
    long = _factor_result(
        {
            "return_126d": pct.get("return_126d"),
            "return_252d": pct.get("return_252d"),
            "distance_sma200": absolute_return_score(row.get("dist_sma200"), 260.0),
            "ath_proximity": score_value(row.get("ath_proximity")),
            "ma_alignment": score_value(row.get("ma_alignment")),
        },
        {
            "return_126d": 0.26,
            "return_252d": 0.22,
            "distance_sma200": 0.24,
            "ath_proximity": 0.18,
            "ma_alignment": 0.10,
        },
        min_active_weight=0.45,
    )
    return mid.get("score"), long.get("score")


def apply_f2_percentile_a0(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Replace return/RS saturation scales with same-day percentiles, then A0.

    ``macd_direction`` is not in the compact dump, so mid is rebuilt without it.
    This is a disclosed family-rebuild proxy, not a claim that production
    score_mid was reproduced bit-for-bit.
    """

    columns = {
        "return_63d": [ _finite(row.get("return_63d")) for row in rows ],
        "rs_spy_63d": [ _finite(row.get("rs_spy_63d")) for row in rows ],
        "return_126d": [ _finite(row.get("return_126d")) for row in rows ],
        "return_252d": [ _finite(row.get("return_252d")) for row in rows ],
    }
    pools = {
        name: [value for value in values if value is not None]
        for name, values in columns.items()
    }
    copies: list[dict[str, Any]] = []
    for row in rows:
        pct = {
            key: (
                None
                if _finite(row.get(key if key != "rs_spy_63d" else "rs_spy_63d")) is None
                else xs_percentile(pools[key], float(row.get(key)))
            )
            for key in pools
        }
        item = dict(row)
        mid, long = _rebuild_mid_long(item, pct)
        item["score_mid"] = mid
        item["score_long"] = long
        item["research_ranking"] = F2_ID
        copies.append(item)
    ranked = apply_a0_mid_long(copies)
    for item in ranked:
        item["research_ranking"] = F2_ID
    return ranked


def compare_followups(
    original_days: Mapping[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    f1_days: dict[str, list[dict[str, Any]]] = {}
    f2_days: dict[str, list[dict[str, Any]]] = {}
    a0_days: dict[str, list[dict[str, Any]]] = {}
    f1_coverage: list[float] = []
    missed_winners = 0
    missed_winner_excess: list[float] = []
    for session, items in original_days.items():
        ordered = sorted(
            items,
            key=lambda item: production_tiebreak_sort_key(
                _finite(item.get("ranking_score")),
                str(item.get("ticker") or ""),
            ),
            reverse=True,
        )
        for rank, item in enumerate(ordered, start=1):
            item["selected_view_rank"] = rank
        f1 = apply_f1_rs_filter(items)
        f1_days[session] = f1["rows"]
        if f1["coverage"] is not None:
            f1_coverage.append(f1["coverage"])
        original_top = {str(row.get("ticker") or "") for row in ordered[:10]}
        kept_top = {str(row.get("ticker") or "") for row in f1["rows"][:10]}
        for row in ordered[:10]:
            ticker = str(row.get("ticker") or "")
            if ticker in kept_top:
                continue
            excess = close_excess(row)
            if excess is not None and excess > 0:
                missed_winners += 1
                missed_winner_excess.append(excess)
        f2_days[session] = apply_f2_percentile_a0(items)
        a0_days[session] = apply_a0_mid_long(items)

    return {
        "F1": {
            "id": F1_ID,
            "rule": "original ranking after rs_spy_63d > 0; missing RS dropped",
            "mean_coverage": None if not f1_coverage else sum(f1_coverage) / len(f1_coverage),
            "missed_original_top10_winners": missed_winners,
            "missed_winner_mean_excess": None if not missed_winner_excess else sum(missed_winner_excess) / len(missed_winner_excess),
            "vs_original_top10": paired_top_series(original_days, f1_days, k=10),
        },
        "F2": {
            "id": F2_ID,
            "rule": "same-day percentiles of return_63d/rs/return_126d/return_252d, rebuild mid/long, then A0",
            "proxy_note": "macd_direction absent from compact dump; mid rebuild omits that 10% component",
            "min_cross_section": F2_MIN_CROSS_SECTION,
            "vs_original_top10": paired_top_series(original_days, f2_days, k=10),
            "vs_a0_top10": paired_top_series(a0_days, f2_days, k=10),
        },
    }
