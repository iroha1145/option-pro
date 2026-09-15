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
    TAIL_HURDLE_RANDOM_VARIABLE,
)
from app.services.research.labels import outcome_crosses_split
from app.services.research.metrics import paired_difference_ci, spearman_rank_ic, summarize_daily_ics


def publish_ci(payload: Mapping[str, Any] | None, *, min_n: int = 20) -> dict[str, Any]:
    """Keep the point estimate; hide degenerate or too-short intervals."""

    if not payload:
        return {"status": "unavailable", "reason": "missing", "ci95": None}
    n = int(payload.get("n_paired") or payload.get("n_dates") or payload.get("n") or 0)
    ci = payload.get("ci") if isinstance(payload.get("ci"), Mapping) else payload
    interval = None
    if isinstance(ci, Mapping):
        interval = ci.get("ci95")
        n = int(ci.get("n") or n)
    if n < min_n:
        return {
            "status": "unavailable",
            "reason": "insufficient_dates_for_horizon_blocks",
            "n": n,
            "mean": payload.get("mean") or payload.get("mean_diff") or payload.get("mean_ic"),
            "ci95": None,
        }
    if not interval or interval[0] == interval[1]:
        return {
            "status": "unavailable",
            "reason": "degenerate_or_zero_width_interval",
            "n": n,
            "mean": payload.get("mean") or payload.get("mean_diff") or payload.get("mean_ic"),
            "ci95": None,
        }
    return {"status": "active", "n": n, "ci95": interval, "mean": payload.get("mean") or payload.get("mean_diff")}
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


def row_aligned_pairs(
    rows: Sequence[Mapping[str, Any]],
    score_key: str,
    *,
    horizon: str | int = PRIMARY_SCREENER_HORIZON,
) -> list[tuple[str, float, float]]:
    """Pair score and 20d excess from the same row identity."""

    pairs: list[tuple[str, float, float]] = []
    for row in rows:
        score = _finite(row.get(score_key))
        outcome = close_excess(row, horizon)
        if score is None or outcome is None:
            continue
        pairs.append((str(row.get("ticker") or ""), score, outcome))
    return pairs


def aligned_rank_ic(
    rows: Sequence[Mapping[str, Any]],
    score_key: str,
    *,
    horizon: str | int = PRIMARY_SCREENER_HORIZON,
) -> dict[str, Any]:
    pairs = row_aligned_pairs(rows, score_key, horizon=horizon)
    return spearman_rank_ic(
        [score for _ticker, score, _outcome in pairs],
        [outcome for _ticker, _score, outcome in pairs],
    )


def top10_identity(days: Mapping[str, Sequence[Mapping[str, Any]]], *, k: int = PRIMARY_SCREENER_TOP_K) -> list[tuple[str, tuple[str, ...]]]:
    identity: list[tuple[str, tuple[str, ...]]] = []
    for session in sorted(days):
        names = _selected_by_rank(days[session], k=k)
        identity.append((session, tuple(str(row.get("ticker") or "") for row in names)))
    return identity


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
    tail_n = 0
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
        "available_raw": len(labeled_raw),
        "missing_raw": len(names) - len(labeled_raw),
        "mean_excess": None if not labeled_excess else fmean(labeled_excess),
        "mean_raw": None if not labeled_raw else fmean(labeled_raw),
        "worst_5pct_raw": worst,
        "daily_worst_name": worst,
        "daily_worst_name_n": tail_n,
        "labeled_raws": labeled_raw,
        "tickers": [str(row.get("ticker") or "") for row in names],
        "tail_hurdle_random_variable": TAIL_HURDLE_RANDOM_VARIABLE,
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
        "paired_ci": publish_ci(
            None
            if paired is None
            else {
                "n_paired": len(orig_vals),
                "mean_diff": paired.get("mean_diff"),
                "ci": (paired.get("ci") or {}),
            }
        ),
        "daily_head": daily[:3],
        "daily_tail": daily[-3:],
        "daily": daily,
    }


def _tail_mean(values: Sequence[float], *, fraction: float = 0.05) -> float | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    if not clean:
        return None
    ordered = sorted(clean)
    tail_n = max(1, int(math.ceil(len(ordered) * fraction)))
    return fmean(ordered[:tail_n])


def named_tail_stats(daily: Sequence[Mapping[str, Any]], side: str) -> dict[str, Any]:
    """Three named tail series. None of these is portfolio MDD."""

    daily_worst_name: list[float] = []
    pooled: list[float] = []
    daily_ew: list[float] = []
    missing_raw = 0
    selected = 0
    days_used = 0
    for row in daily:
        block = row.get(side) or {}
        selected += int(block.get("selected") or 0)
        missing_raw += int(block.get("missing_raw") or 0)
        labeled = [
            float(value)
            for value in (block.get("labeled_raws") or [])
            if _finite(value) is not None
        ]
        if not labeled:
            legacy = _finite(block.get("daily_worst_name") or block.get("worst_5pct_raw"))
            if legacy is not None:
                daily_worst_name.append(legacy)
                days_used += 1
            continue
        days_used += 1
        pooled.extend(labeled)
        daily_ew.append(fmean(labeled))
        tail_n = max(1, int(math.ceil(len(labeled) * 0.05)))
        daily_worst_name.append(fmean(sorted(labeled)[:tail_n]))
    return {
        "daily_worst_name_mean": {
            "name": "daily_worst_name_mean",
            "mean": None if not daily_worst_name else fmean(daily_worst_name),
            "n_days": len(daily_worst_name),
            "weight": "equal_day",
            "missing_raw": missing_raw,
            "selected": selected,
            "hurdle_random_variable": True,
            "note": TAIL_HURDLE_RANDOM_VARIABLE,
        },
        "pooled_stock_date_worst5pct": {
            "name": "pooled_stock_date_worst5pct",
            "mean": _tail_mean(pooled),
            "n_stock_dates": len(pooled),
            "tail_n": None if not pooled else max(1, int(math.ceil(len(pooled) * 0.05))),
            "weight": "equal_stock_date",
            "missing_raw": missing_raw,
            "hurdle_random_variable": False,
        },
        "daily_ew_portfolio_worst5pct": {
            "name": "daily_ew_portfolio_worst5pct",
            "mean": _tail_mean(daily_ew),
            "n_days": len(daily_ew),
            "tail_n": None if not daily_ew else max(1, int(math.ceil(len(daily_ew) * 0.05))),
            "weight": "equal_day_portfolio",
            "missing_raw": missing_raw,
            "hurdle_random_variable": False,
        },
        "days_used": days_used,
        "not_max_drawdown": True,
    }


def worst_5pct_pooled(daily: Sequence[Mapping[str, Any]], side: str) -> float | None:
    """Legacy alias for daily_worst_name_mean. Not pooled stock-date 5%."""

    return named_tail_stats(daily, side)["daily_worst_name_mean"]["mean"]


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

        ics["original"].append({"signal_date": session, **aligned_rank_ic(items, "ranking_score")})
        ics["a0"].append({"signal_date": session, **aligned_rank_ic(a0, "candidate_score")})
        ics["momentum63"].append({"signal_date": session, **aligned_rank_ic(items, "return_63d")})

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
    a0_identity = top10_identity(a0_days)
    original_identity = top10_identity(original_days)
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
        "named_tails": {
            "original": named_tail_stats(a0_pairs[str(PRIMARY_SCREENER_TOP_K)]["daily"], "original"),
            "a0": named_tail_stats(a0_pairs[str(PRIMARY_SCREENER_TOP_K)]["daily"], "candidate"),
            "c0": named_tail_stats(c0_pairs[str(PRIMARY_SCREENER_TOP_K)]["daily"], "candidate"),
            "momentum63": named_tail_stats(mom_pairs[str(PRIMARY_SCREENER_TOP_K)]["daily"], "candidate"),
        },
        "top10_identity": {
            "original_n": len(original_identity),
            "a0_n": len(a0_identity),
            "a0_head": a0_identity[:2],
            "a0_tail": a0_identity[-2:],
        },
        "c0_vacancy_day_share": None if not c0_vacancies else vacancy_days / len(c0_vacancies),
        "c0_audit_head": c0_audit[:5],
        "c0_sector_source": apply_c0_sector_quota(next(iter(original_days.values()), []))["sector_source"],
        "minimum_meaningful": MINIMUM_MEANINGFUL,
        "tail_hurdle_random_variable": TAIL_HURDLE_RANDOM_VARIABLE,
        "notes": [
            "A0 uses family scores only; mid/long page views are not this candidate.",
            "A0 IC pairs candidate_score with close_excess on the same a0 row.",
            "C0 Top10 vacancies stay vacant; opportunity means use the selected set only.",
            "63d momentum is a baseline, not a production score.",
            "worst_5pct_raw is daily_worst_name_mean, not pooled stock-date 5% and not portfolio 5%.",
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
