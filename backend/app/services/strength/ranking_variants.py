"""Production ranking variants for the strength scanner.

A0 is an optional sort over already-computed family scores. It does not
recompute RSI, eligibility, or family weights. C0 and later research
variants are intentionally absent.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.algorithm_modes import A0_ALGORITHM, A0_SCORE_BASIS, A0_VERSION
from app.services.strength.features import _safe_float


def production_tiebreak_sort_key(score: float | None, ticker: str) -> tuple[bool, float, str]:
    """Match `_sort_scored` for timeframe=all: usable scores first, then score desc, ticker desc."""

    return (score is not None, score if score is not None else -1.0, ticker)


def a0_family_score(row: Mapping[str, Any]) -> float | None:
    """Equal-weight mid/long family scores. Missing or non-finite stays missing.

    This is not the mid/long page view key (94% family + 6% ranking_score).
    Zero is a valid score. NaN/Inf/None are not coerced to zero.
    """

    mid = _safe_float(row.get("score_mid"), 4)
    long = _safe_float(row.get("score_long"), 4)
    if mid is None or long is None:
        return None
    return round(0.5 * mid + 0.5 * long, 6)


def annotate_a0_row(row: Mapping[str, Any]) -> dict[str, Any]:
    item = dict(row)
    score = a0_family_score(item)
    item["a0_score"] = score
    item["sort_score"] = score
    item["sort_basis"] = A0_SCORE_BASIS
    item["sort_algorithm"] = A0_ALGORITHM
    item["sort_algorithm_version"] = A0_VERSION
    item["a0_available"] = score is not None
    return item


def apply_a0_mid_long(rows: list[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Sort the full eligible pool by A0, then assign contiguous ranks.

    Rows that cannot compute A0 stay visible after every scored row. They are
    not filled with ranking_score and are not dropped.
    """

    copies = [annotate_a0_row(row) for row in rows]
    copies.sort(
        key=lambda item: production_tiebreak_sort_key(
            _safe_float(item.get("sort_score"), 6),
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    for rank, item in enumerate(copies, start=1):
        item["selected_view_rank"] = rank
    return copies


def a0_request_can_score(rows: list[Mapping[str, Any]]) -> bool:
    return any(a0_family_score(row) is not None for row in rows)
