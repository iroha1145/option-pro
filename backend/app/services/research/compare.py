"""Same-day / same-opportunity comparisons for this algorithm round."""

from __future__ import annotations

import math
from collections import defaultdict
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from app.services.research.algorithm_protocol import (
    AUX_SCREENER_TOP_K,
    MINIMUM_MEANINGFUL,
    PRIMARY_SCREENER_HORIZON,
    PRIMARY_SCREENER_TOP_K,
)
from app.services.research.labels import outcome_crosses_split
from app.services.research.metrics import paired_difference_ci, spearman_rank_ic, summarize_daily_ics
from app.services.research.protocol import split_for_date
from app.services.strength.ranking_variants import (
    A0_VARIANT,
    apply_a0_mid_long,
    apply_c0_sector_quota,
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def group_by_date(rows: Iterable[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        session = str(row.get("signal_date") or "")
        if not session:
            continue
        grouped[session].append(dict(row))
    return dict(grouped)


def close_excess(row: Mapping[str, Any], horizon: str | int = PRIMARY_SCREENER_HORIZON) -> float | None:
    if outcome_crosses_split(row, horizon=horizon):
        return None
    return _finite(((row.get("excess") or {}).get(str(horizon)) or {}).get("excess_vs_universe"))


def close_raw(row: Mapping[str, Any], horizon: str | int = PRIMARY_SCREENER_HORIZON) -> float | None:
    if outcome_crosses_split(row, horizon=horizon):
        return None
    return _finite(((row.get("labels") or {}).get(str(horizon)) or {}).get("forward_return"))


def filter_split(rows: Iterable[Mapping[str, Any]], split: str) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for row in rows:
        session = str(row.get("signal_date") or row.get("trading_date") or "")
        if split_for_date(session) != split:
            continue
        kept.append(dict(row))
    return kept


def _selected_by_rank(
    rows: Sequence[Mapping[str, Any]],
    *,
    k: int,
    rank_key: str = "selected_view_rank",
) -> list[dict[str, Any]]:
    selected = [
        dict(row)
        for row in rows
        if row.get(rank_key) is not None and int(row[rank_key]) <= k
    ]
    selected.sort(key=lambda item: int(item[rank_key]))
    return selected


def top_set_stats(
    rows: Sequence[Mapping[str, Any]],
    *,
    k: int,
    selected: Sequence[Mapping[str, Any]] | None = None,
    vacancies: int = 0,
) -> dict[str, Any]:
    names = list(selected) if selected is not None else _selected_by_rank(rows, k=k)
    excesses = [close_excess(row) for row in names]
    raws = [close_raw(row) for row in names]
    labeled_excess = [value for value in excesses if value is not None]
    labeled_raw = [value for value in raws if value is not None]
    worst = None
    if labeled_raw:
        ordered = sorted(labeled_raw)
        tail_n = max(1, int(math.ceil(len(ordered) * 0.05)))
        worst = fmean(ordered[:tail_n])
    return {
        "k": k,
        "selected": len(names),
        "vacancies": vacancies,
        "available_excess": len(labeled_excess),
        "missing_excess": len(names) - len(labeled_excess),
        "mean_excess": None if not labeled_excess else fmean(labeled_excess),
        "mean_raw": None if not labeled_raw else fmean(labeled_raw),
        "worst_5pct_raw": worst,
        "tickers": [str(row.get("ticker") or "") for row in names],
    }


def paired_top_series(
    original_days: Mapping[str, Sequence[Mapping[str, Any]]],
    candidate_days: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    k: int,
    candidate_selected: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    candidate_vacancies: Mapping[str, int] | None = None,
) -> dict[str, Any]:
    dates = sorted(set(original_days) & set(candidate_days))
    orig_vals: list[float] = []
    cand_vals: list[float] = []
    daily: list[dict[str, Any]] = []
    for session in dates:
        orig = top_set_stats(original_days[session], k=k)
        selected = None if candidate_selected is None else candidate_selected.get(session)
        vacant = 0 if candidate_vacancies is None else int(candidate_vacancies.get(session, 0))
        cand = top_set_stats(
            candidate_days[session],
            k=k,
            selected=selected,
            vacancies=vacant,
        )
        daily.append(
            {
                "signal_date": session,
                "original": orig,
                "candidate": cand,
                "overlap": sorted(set(orig["tickers"]) & set(cand["tickers"])),
            }
        )
        if orig["mean_excess"] is not None and cand["mean_excess"] is not None:
            orig_vals.append(float(orig["mean_excess"]))
            cand_vals.append(float(cand["mean_excess"]))
    paired = paired_difference_ci(
        cand_vals,
        orig_vals,
        horizon_days=PRIMARY_SCREENER_HORIZON,
    )
    return {
        "k": k,
        "n_dates": len(dates),
        "n_paired": len(orig_vals),
        "original_mean": None if not orig_vals else fmean(orig_vals),
        "candidate_mean": None if not cand_vals else fmean(cand_vals),
        "paired": paired,
        "daily_head": daily[:3],
        "daily_tail": daily[-3:],
        "daily": daily,
    }


def worst_5pct_pooled(daily: Sequence[Mapping[str, Any]], side: str) -> float | None:
    values: list[float] = []
    for row in daily:
        block = row.get(side) or {}
        raw = block.get("mean_raw")
        # Keep the per-name tail from the stored tickers if present later.
        value = _finite(block.get("worst_5pct_raw"))
        if value is not None:
            values.append(value)
    return None if not values else fmean(values)


def compare_screener_candidates(rows: Sequence[Mapping[str, Any]], *, split: str) -> dict[str, Any]:
    design = filter_split(rows, split)
    days = group_by_date(design)
    original_days: dict[str, list[dict[str, Any]]] = {}
    a0_days: dict[str, list[dict[str, Any]]] = {}
    momentum_days: dict[str, list[dict[str, Any]]] = {}
    c0_selected: dict[str, list[dict[str, Any]]] = {}
    c0_vacancies: dict[str, int] = {}
    c0_audit: list[dict[str, Any]] = []
    ics = {"original": [], "a0": [], "momentum63": []}

    for session, items in sorted(days.items()):
        original = [dict(item) for item in items]
        original.sort(
            key=lambda item: (
                item.get("ranking_score") is not None,
                item.get("ranking_score") if item.get("ranking_score") is not None else -1,
                str(item.get("ticker") or ""),
            ),
            reverse=True,
        )
        for rank, item in enumerate(original, start=1):
            item["selected_view_rank"] = rank
        original_days[session] = original

        a0 = apply_a0_mid_long(items)
        a0_days[session] = a0

        momentum = [dict(item) for item in items]
        momentum.sort(
            key=lambda item: (
                item.get("return_63d") is not None,
                item.get("return_63d") if item.get("return_63d") is not None else -999,
                str(item.get("ticker") or ""),
            ),
            reverse=True,
        )
        for rank, item in enumerate(momentum, start=1):
            item["selected_view_rank"] = rank
            item["candidate_score"] = item.get("return_63d")
        momentum_days[session] = momentum

        c0 = apply_c0_sector_quota(original)
        c0_selected[session] = c0["selected"]
        c0_vacancies[session] = int(c0["vacancies"])
        c0_audit.append(
            {
                "signal_date": session,
                "vacancies": c0["vacancies"],
                "displaced": c0["displaced"],
                "fill_ins": c0["fill_ins"],
                "sector_counts": c0["sector_counts"],
            }
        )

        outcomes = [close_excess(item) for item in items]
        ics["original"].append(
            {"signal_date": session, **spearman_rank_ic([item.get("ranking_score") for item in items], outcomes)}
        )
        ics["a0"].append(
            {"signal_date": session, **spearman_rank_ic([item.get("candidate_score") for item in a0], outcomes)}
        )
        ics["momentum63"].append(
            {"signal_date": session, **spearman_rank_ic([item.get("return_63d") for item in items], outcomes)}
        )

    a0_pairs = {
        str(k): paired_top_series(original_days, a0_days, k=k)
        for k in AUX_SCREENER_TOP_K
    }
    mom_pairs = {
        str(k): paired_top_series(original_days, momentum_days, k=k)
        for k in AUX_SCREENER_TOP_K
    }
    c0_pairs = {}
    for k in AUX_SCREENER_TOP_K:
        selected_k = {session: names[:k] for session, names in c0_selected.items()}
        vacancies = c0_vacancies if k == PRIMARY_SCREENER_TOP_K else {
            session: max(0, k - len(names[:k])) for session, names in c0_selected.items()
        }
        c0_pairs[str(k)] = paired_top_series(
            original_days,
            original_days,
            k=k,
            candidate_selected=selected_k,
            candidate_vacancies=vacancies,
        )
    vacancy_days = sum(1 for value in c0_vacancies.values() if value)
    return {
        "split": split,
        "day_count": len(days),
        "row_count": len(design),
        "ics": {
            name: summarize_daily_ics(points, horizon_days=PRIMARY_SCREENER_HORIZON)
            for name, points in ics.items()
        },
        "a0_vs_original": a0_pairs,
        "momentum63_vs_original": mom_pairs,
        "c0_vs_original": c0_pairs,
        "c0_vacancy_day_share": None if not c0_vacancies else vacancy_days / len(c0_vacancies),
        "c0_audit_head": c0_audit[:5],
        "c0_sector_source": apply_c0_sector_quota(next(iter(original_days.values()), []))["sector_source"],
        "minimum_meaningful": MINIMUM_MEANINGFUL,
        "notes": [
            "A0 uses family scores only; mid/long page views are not this candidate.",
            "C0 Top10 vacancies stay vacant; opportunity means use the selected set only.",
            "63d momentum is a baseline, not a production score.",
        ],
    }


def yearly_top10_means(paired: Mapping[str, Any]) -> dict[str, dict[str, float | None]]:
    by_year: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for row in paired.get("daily") or []:
        session = str(row.get("signal_date") or "")
        year = session[:4]
        orig = _finite((row.get("original") or {}).get("mean_excess"))
        cand = _finite((row.get("candidate") or {}).get("mean_excess"))
        if orig is None or cand is None:
            continue
        by_year[year].append((orig, cand))
    out: dict[str, dict[str, float | None]] = {}
    for year, pairs in sorted(by_year.items()):
        out[year] = {
            "n": len(pairs),
            "original": fmean(item[0] for item in pairs),
            "candidate": fmean(item[1] for item in pairs),
            "diff": fmean(item[1] - item[0] for item in pairs),
        }
    return out
