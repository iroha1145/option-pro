"""Turn ranking candidates into cash-conserving portfolio signals."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from app.services.research.compare import group_by_date
from app.services.strength.ranking_variants import apply_a0_mid_long, apply_c0_sector_quota


def _signal(row: Mapping[str, Any], score: Any) -> dict[str, Any]:
    rank = row.get("selected_view_rank")
    return {
        "ticker": row.get("ticker"),
        "signal_date": row.get("signal_date"),
        "selected_view_rank": rank,
        "rank": rank,
        "score": score,
    }


def original_top_signals(rows: Iterable[Mapping[str, Any]], *, max_rank: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session, items in group_by_date(rows).items():
        ordered = sorted(
            items,
            key=lambda item: (
                item.get("ranking_score") is not None,
                item.get("ranking_score") if item.get("ranking_score") is not None else -1,
                str(item.get("ticker") or ""),
            ),
            reverse=True,
        )
        for rank, row in enumerate(ordered, start=1):
            if rank > max_rank:
                break
            item = dict(row)
            item["signal_date"] = session
            item["selected_view_rank"] = rank
            out.append(_signal(item, item.get("ranking_score")))
    return out


def a0_top_signals(rows: Iterable[Mapping[str, Any]], *, max_rank: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session, items in group_by_date(rows).items():
        ranked = apply_a0_mid_long(items)
        for row in ranked:
            if int(row["selected_view_rank"]) > max_rank:
                continue
            if row.get("candidate_score") is None:
                continue
            item = dict(row)
            item["signal_date"] = session
            out.append(_signal(item, item.get("candidate_score")))
    return out


def c0_top_signals(rows: Iterable[Mapping[str, Any]], *, max_rank: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session, items in group_by_date(rows).items():
        result = apply_c0_sector_quota(items, top_k=max_rank)
        for row in result["selected"]:
            item = dict(row)
            item["signal_date"] = session
            out.append(_signal(item, item.get("ranking_score")))
    return out


def momentum_top_signals(rows: Iterable[Mapping[str, Any]], *, max_rank: int = 10) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for session, items in group_by_date(rows).items():
        ordered = sorted(
            items,
            key=lambda item: (
                item.get("return_63d") is not None,
                item.get("return_63d") if item.get("return_63d") is not None else -999,
                str(item.get("ticker") or ""),
            ),
            reverse=True,
        )
        for rank, row in enumerate(ordered, start=1):
            if rank > max_rank or row.get("return_63d") is None:
                if rank > max_rank:
                    break
                continue
            item = dict(row)
            item["signal_date"] = session
            item["selected_view_rank"] = rank
            out.append(_signal(item, item.get("return_63d")))
    return out
