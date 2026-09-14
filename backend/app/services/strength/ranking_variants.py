"""Optional research ranking variants. Production default does not call these.

A0 and C0 are isolated ranking-layer experiments. They reuse the production
eligibility set and do not change RSI, market timing, or exits.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.strength.features import _safe_float


PRODUCTION_VARIANT = "production"
A0_VARIANT = "a0_mid_long"
C0_VARIANT = "c0_sector_quota"
SUPPORTED_VARIANTS = (PRODUCTION_VARIANT, A0_VARIANT, C0_VARIANT)

UNCLASSIFIED_SECTOR = "__unclassified__"
C0_TOP_K = 10
C0_MAX_PER_SECTOR = 2

SECTOR_MAP_SOURCE = {
    "module": "app.services.sectors.primary_sector_id / row.primary_sector_id",
    "temporal": "static_current_theme_map",
    "as_of_code": "2026-09-14",
    "note": (
        "Theme map first-listing wins. Overlapping themes are not unique GICS "
        "industries. Missing sector shares one unclassified bucket and is not "
        "treated as an independent category."
    ),
}


def _finite_score(value: Any) -> float | None:
    return _safe_float(value, 4)


def production_tiebreak_sort_key(score: float | None, ticker: str) -> tuple[bool, float, str]:
    """Match `_sort_scored`: usable scores first, then score desc, ticker desc."""

    return (score is not None, score if score is not None else -1.0, ticker)


def a0_family_score(row: Mapping[str, Any]) -> float | None:
    """Equal-weight family scores. Missing mid or long stays missing.

    This is not a mid/long page view key. Those views mix 94% family score
    with 6% ranking_score.
    """

    mid = _finite_score(row.get("score_mid"))
    long = _finite_score(row.get("score_long"))
    if mid is None or long is None:
        return None
    return round(0.5 * mid + 0.5 * long, 6)


def _assign_contiguous_ranks(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for rank, item in enumerate(rows, start=1):
        item["selected_view_rank"] = rank
    return rows


def apply_a0_mid_long(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    copies: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["candidate_score"] = a0_family_score(item)
        item["research_ranking"] = A0_VARIANT
        copies.append(item)
    copies.sort(
        key=lambda item: production_tiebreak_sort_key(
            _finite_score(item.get("candidate_score")),
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    return _assign_contiguous_ranks(copies)


def _sector_bucket(row: Mapping[str, Any]) -> str:
    sector = str(row.get("primary_sector_id") or "").strip()
    if not sector:
        return UNCLASSIFIED_SECTOR
    return sector


def apply_c0_sector_quota(
    rows: list[Mapping[str, Any]],
    *,
    top_k: int = C0_TOP_K,
    max_per_sector: int = C0_MAX_PER_SECTOR,
) -> dict[str, Any]:
    """Keep original order, cap one primary sector at ``max_per_sector`` in Top-K.

    Vacancies stay vacant. Remainder never occupy the empty Top-K slots.
    """

    ordered = [dict(row) for row in rows]
    ordered.sort(
        key=lambda item: production_tiebreak_sort_key(
            _finite_score(item.get("ranking_score")),
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    original_top = ordered[:top_k]
    original_tickers = {str(item.get("ticker") or "") for item in original_top}

    selected: list[dict[str, Any]] = []
    displaced: list[dict[str, Any]] = []
    fill_ins: list[dict[str, Any]] = []
    counts: dict[str, int] = {}

    for row in ordered:
        if len(selected) >= top_k:
            break
        ticker = str(row.get("ticker") or "")
        bucket = _sector_bucket(row)
        if counts.get(bucket, 0) >= max_per_sector:
            if ticker in original_tickers:
                displaced.append(
                    {
                        "ticker": ticker,
                        "ranking_score": row.get("ranking_score"),
                        "original_rank": original_top.index(row) + 1
                        if row in original_top
                        else None,
                        "sector_bucket": bucket,
                        "reason": "sector_quota",
                    }
                )
            continue
        item = dict(row)
        item["research_ranking"] = C0_VARIANT
        item["c0_sector_bucket"] = bucket
        item["c0_in_quota_top"] = True
        selected.append(item)
        counts[bucket] = counts.get(bucket, 0) + 1
        if ticker not in original_tickers:
            fill_ins.append(
                {
                    "ticker": ticker,
                    "ranking_score": row.get("ranking_score"),
                    "sector_bucket": bucket,
                }
            )

    selected_tickers = {str(item.get("ticker") or "") for item in selected}
    remainder: list[dict[str, Any]] = []
    for row in ordered:
        ticker = str(row.get("ticker") or "")
        if ticker in selected_tickers:
            continue
        item = dict(row)
        item["research_ranking"] = C0_VARIANT
        item["c0_sector_bucket"] = _sector_bucket(item)
        item["c0_in_quota_top"] = False
        remainder.append(item)

    for rank, item in enumerate(selected, start=1):
        item["selected_view_rank"] = rank
    for offset, item in enumerate(remainder, start=1):
        item["selected_view_rank"] = top_k + offset

    return {
        "rows": selected + remainder,
        "selected": selected,
        "remainder": remainder,
        "vacancies": max(0, top_k - len(selected)),
        "displaced": displaced,
        "fill_ins": fill_ins,
        "sector_counts": dict(counts),
        "top_k": top_k,
        "max_per_sector": max_per_sector,
        "sector_source": SECTOR_MAP_SOURCE,
    }


def apply_research_ranking(
    rows: list[Mapping[str, Any]],
    variant: str | None,
    *,
    top_k: int = C0_TOP_K,
) -> list[dict[str, Any]]:
    name = str(variant or PRODUCTION_VARIANT).strip() or PRODUCTION_VARIANT
    if name == PRODUCTION_VARIANT:
        copies = [dict(row) for row in rows]
        return _assign_contiguous_ranks(copies)
    if name == A0_VARIANT:
        return apply_a0_mid_long(rows)
    if name == C0_VARIANT:
        return apply_c0_sector_quota(rows, top_k=top_k)["rows"]
    raise ValueError(f"unsupported research_ranking: {variant}")
