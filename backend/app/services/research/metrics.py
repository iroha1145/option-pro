"""Ranking and event metrics. These consume labels, never feed scoring."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

import numpy as np


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _nested(row: Mapping[str, Any], path: Sequence[str] | str) -> Any:
    cursor: Any = row
    parts = (path,) if isinstance(path, str) else path
    for part in parts:
        cursor = cursor.get(part) if isinstance(cursor, Mapping) else None
    return cursor


def _normal_ci(mean: float, se: float, alpha: float = 0.05) -> tuple[float, float]:
    z = 1.959963984540054
    if alpha != 0.05:
        # Inverse erf approximation is unnecessary; callers use 95%.
        z = 1.959963984540054
    return mean - z * se, mean + z * se


def spearman_rank_ic(
    scores: Sequence[Any],
    outcomes: Sequence[Any],
) -> dict[str, Any]:
    pairs = [
        (float(score), float(outcome))
        for score, outcome in zip(scores, outcomes)
        if _finite(score) is not None and _finite(outcome) is not None
    ]
    n = len(pairs)
    if n < 5:
        return {"status": "unavailable", "reason": "insufficient_pairs", "n": n, "ic": None}
    ordered_scores = sorted(score for score, _outcome in pairs)
    ordered_outcomes = sorted(outcome for _score, outcome in pairs)

    def ranks(values: list[float], ordered: list[float]) -> list[float]:
        result = []
        for value in values:
            lo = ordered.index(value)
            hi = len(ordered) - list(reversed(ordered)).index(value) - 1
            result.append((lo + hi) / 2.0)
        return result

    score_ranks = ranks([score for score, _outcome in pairs], ordered_scores)
    outcome_ranks = ranks([outcome for _score, outcome in pairs], ordered_outcomes)
    mean_s = fmean(score_ranks)
    mean_o = fmean(outcome_ranks)
    num = sum((s - mean_s) * (o - mean_o) for s, o in zip(score_ranks, outcome_ranks))
    den_s = math.sqrt(sum((s - mean_s) ** 2 for s in score_ranks))
    den_o = math.sqrt(sum((o - mean_o) ** 2 for o in outcome_ranks))
    if den_s == 0 or den_o == 0:
        return {"status": "unavailable", "reason": "constant_series", "n": n, "ic": None}
    return {"status": "active", "reason": None, "n": n, "ic": num / (den_s * den_o)}


def production_tiebreak_key(score: float, ticker: str) -> tuple[float, str]:
    """Match production `_sort_scored`: higher score, then reverse ticker."""

    return (score, ticker)


def top_k_mean(
    rows: Iterable[Mapping[str, Any]],
    *,
    score_key: str,
    outcome_key: tuple[str, ...] | str,
    k: int,
) -> dict[str, Any]:
    """Freeze Top-K by pre-outcome score, then attach labels.

    Missing labels do not replace the selected names and are not zero-filled.
    Tie-break matches production `_sort_scored` (score desc, ticker desc).
    """

    scored: list[tuple[float, float | None, str]] = []
    for row in rows:
        score = _finite(row.get(score_key))
        if score is None:
            continue
        outcome = _finite(_nested(row, outcome_key))
        scored.append((score, outcome, str(row.get("ticker") or "")))
    scored.sort(key=lambda item: production_tiebreak_key(item[0], item[2]), reverse=True)
    selected = scored[:k]
    labeled = [item for item in selected if item[1] is not None]
    missing = [item for item in selected if item[1] is None]
    universe_outcomes = [item[1] for item in scored if item[1] is not None]
    selected_tickers = [item[2] for item in selected]
    if not selected:
        return {
            "status": "unavailable",
            "k": k,
            "n_top": 0,
            "n_universe": len(universe_outcomes),
            "n_scored": len(scored),
            "selected": 0,
            "available": 0,
            "missing": 0,
            "missing_tickers": [],
            "selected_tickers": [],
            "top_mean": None,
            "universe_mean": None,
            "excess": None,
            "freeze_before_label": True,
            "protocol": "select_by_score_then_attach_labels",
        }
    top_mean = fmean(item[1] for item in labeled) if labeled else None
    universe_mean = fmean(universe_outcomes) if universe_outcomes else None
    excess = None if top_mean is None or universe_mean is None else top_mean - universe_mean
    return {
        "status": "active" if labeled else "unavailable",
        "reason": None if labeled else "selected_labels_missing",
        "k": k,
        "n_top": len(labeled),
        "n_universe": len(universe_outcomes),
        "n_scored": len(scored),
        "selected": len(selected),
        "available": len(labeled),
        "missing": len(missing),
        "missing_tickers": [item[2] for item in missing],
        "selected_tickers": selected_tickers,
        "top_mean": top_mean,
        "universe_mean": universe_mean,
        "excess": excess,
        "hit_rate": (
            sum(1 for item in labeled if item[1] is not None and item[1] > 0) / len(labeled)
            if labeled
            else None
        ),
        "beat_universe_rate": (
            sum(1 for item in labeled if universe_mean is not None and item[1] > universe_mean)
            / len(labeled)
            if labeled
            else None
        ),
        "freeze_before_label": True,
        "protocol": "select_by_score_then_attach_labels",
    }


def iid_mean_ci(values: Sequence[float], *, alpha: float = 0.05) -> dict[str, Any] | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    if len(clean) < 2:
        return None
    array = np.asarray(clean, dtype=float)
    mean = float(array.mean())
    se = float(array.std(ddof=1) / math.sqrt(len(array)))
    lo, hi = _normal_ci(mean, se, alpha)
    return {
        "method": "iid_normal",
        "n": len(clean),
        "mean": mean,
        "se": se,
        "ci_low": lo,
        "ci_high": hi,
        "ci95": [lo, hi],
        "note": "Assumes independent observations. Overlapping-horizon labels violate this.",
    }


def newey_west_mean_ci(
    values: Sequence[float],
    *,
    lag: int,
    alpha: float = 0.05,
) -> dict[str, Any] | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    if len(clean) < 2:
        return None
    array = np.asarray(clean, dtype=float)
    n = len(array)
    mean = float(array.mean())
    residuals = array - mean
    gamma0 = float(np.dot(residuals, residuals) / n)
    nw = gamma0
    max_lag = max(0, min(int(lag), n - 1))
    for j in range(1, max_lag + 1):
        weight = 1.0 - j / (max_lag + 1)
        gamma = float(np.dot(residuals[j:], residuals[:-j]) / n)
        nw += 2.0 * weight * gamma
    se = float(math.sqrt(max(nw, 0.0) / n))
    lo, hi = _normal_ci(mean, se, alpha) if se > 0 else (mean, mean)
    return {
        "method": "newey_west_hac",
        "n": n,
        "lag": max_lag,
        "mean": mean,
        "se": se,
        "ci_low": lo,
        "ci_high": hi,
        "ci95": [lo, hi],
        "note": "HAC mean CI. Pre-registered lag equals the label/hold horizon in trading days.",
    }


def moving_block_bootstrap_mean_ci(
    values: Sequence[float],
    *,
    block_size: int,
    n_bootstrap: int = 2000,
    alpha: float = 0.05,
    seed: int = 7,
) -> dict[str, Any] | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    if len(clean) < 2:
        return None
    array = np.asarray(clean, dtype=float)
    n = len(array)
    block = max(1, min(int(block_size), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block))
    means = np.empty(n_bootstrap, dtype=float)
    starts = np.arange(n)
    for i in range(n_bootstrap):
        chosen = rng.choice(starts, size=n_blocks, replace=True)
        pieces = []
        for start in chosen:
            stop = int(start) + block
            if stop <= n:
                pieces.append(array[int(start) : stop])
            else:
                pieces.append(np.concatenate([array[int(start) :], array[: stop - n]]))
        sample = np.concatenate(pieces)[:n]
        means[i] = float(sample.mean())
    mean = float(array.mean())
    lo = float(np.quantile(means, alpha / 2))
    hi = float(np.quantile(means, 1 - alpha / 2))
    return {
        "method": "moving_block_bootstrap",
        "n": n,
        "block_size": block,
        "n_bootstrap": n_bootstrap,
        "mean": mean,
        "ci_low": lo,
        "ci_high": hi,
        "ci95": [lo, hi],
        "note": "Pre-registered circular moving-block bootstrap. Block length equals the label/hold horizon.",
    }


def dependent_mean_ci(
    values: Sequence[float],
    *,
    horizon_days: int,
    alpha: float = 0.05,
    n_bootstrap: int = 2000,
    seed: int = 7,
) -> dict[str, Any] | None:
    clean = [float(value) for value in values if _finite(value) is not None]
    if len(clean) < 2:
        return None
    bootstrap = moving_block_bootstrap_mean_ci(
        clean,
        block_size=horizon_days,
        n_bootstrap=n_bootstrap,
        alpha=alpha,
        seed=seed,
    )
    return {
        "n": len(clean),
        "mean": float(np.mean(clean)),
        "horizon_days": int(horizon_days),
        "primary_method": "moving_block_bootstrap",
        "iid": iid_mean_ci(clean, alpha=alpha),
        "newey_west": newey_west_mean_ci(clean, lag=horizon_days, alpha=alpha),
        "block_bootstrap": bootstrap,
        "ci95": None if bootstrap is None else bootstrap["ci95"],
        "note": (
            "Primary CI is moving-block bootstrap with block length = horizon. "
            "HAC is a robustness check. iid is a known-biased comparison only. "
            "This is not date-clustered ordinary SE."
        ),
    }


def paired_difference_ci(
    left: Sequence[float],
    right: Sequence[float],
    *,
    horizon_days: int,
    alpha: float = 0.05,
) -> dict[str, Any] | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    diffs = [float(a) - float(b) for a, b in zip(left, right)]
    return {
        "n": len(diffs),
        "mean_diff": float(np.mean(diffs)),
        "ci": dependent_mean_ci(diffs, horizon_days=horizon_days, alpha=alpha),
        "note": (
            "Paired difference on the same dates/opportunities. "
            "Overlapping CIs of the two series are not a test of no difference."
        ),
    }


def _ordered_daily_means(values: Sequence[tuple[str, float]]) -> list[tuple[str, float]]:
    by_date: dict[str, list[float]] = {}
    for session, value in values:
        number = _finite(value)
        if number is None or not session:
            continue
        by_date.setdefault(str(session), []).append(float(number))
    return [(session, float(fmean(items))) for session, items in sorted(by_date.items()) if items]


def horizon_mean_ci(
    values: Sequence[tuple[str, float]],
    *,
    horizon_days: int = 20,
    n_bootstrap: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    """Time-ordered daily means with a pre-registered overlapping-horizon CI.

    `date_clustered_mean` is kept as a compatibility alias. It is not a
    validated date-block estimator; the primary interval is block bootstrap.
    """

    daily = _ordered_daily_means(values)
    means = [item[1] for item in daily]
    if len(means) < 2:
        return {
            "status": "unavailable",
            "clusters": len(means),
            "n_dates": len(means),
            "n_events": sum(1 for _session, value in values if _finite(value) is not None),
            "mean": means[0] if means else None,
            "se": None,
            "ci95": None,
            "method": "moving_block_bootstrap",
            "dates": [item[0] for item in daily],
        }
    ci = dependent_mean_ci(
        means,
        horizon_days=horizon_days,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )
    bootstrap = None if ci is None else ci.get("block_bootstrap")
    iid = None if ci is None else ci.get("iid")
    return {
        "status": "active",
        "clusters": len(means),
        "n_dates": len(means),
        "n_events": sum(1 for _session, value in values if _finite(value) is not None),
        "mean": None if ci is None else ci["mean"],
        "se": None if iid is None else iid.get("se"),
        "ci95": None if ci is None else ci.get("ci95"),
        "method": "moving_block_bootstrap",
        "horizon_days": horizon_days,
        "iid": iid,
        "newey_west": None if ci is None else ci.get("newey_west"),
        "block_bootstrap": bootstrap,
        "dates_head": [item[0] for item in daily[:5]],
        "dates_tail": [item[0] for item in daily[-5:]],
        "note": (
            "Daily means in real calendar order, then overlapping-horizon CI. "
            "Not an ordinary date-clustered SE and not a claim that events are independent."
        ),
    }


def trading_days_in_range(start: str, end: str) -> list[str]:
    """Inclusive NYSE sessions. Empty days stay in the calendar."""

    from datetime import date, timedelta

    from app.services.market_calendar import is_trading_day

    cursor = date.fromisoformat(start)
    last = date.fromisoformat(end)
    days: list[str] = []
    while cursor <= last:
        if is_trading_day(cursor):
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return days


def event_equal_calendar_block_ci(
    values: Sequence[tuple[str, float]],
    *,
    horizon_days: int = 20,
    n_bootstrap: int = 2000,
    seed: int = 7,
    trading_days: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Event-equal mean with blocks on real trading days.

    Days with no events contribute no events. They are not zero-return events.
    Adjacent event dates are not treated as adjacent if empty sessions sit
    between them.
    """

    clean = [
        (str(session), float(value))
        for session, value in values
        if session and _finite(value) is not None
    ]
    n_events = len(clean)
    event_mean = None if not clean else float(fmean(item[1] for item in clean))
    if n_events < 2:
        return {
            "status": "unavailable",
            "target": "event_equal",
            "n_events": n_events,
            "mean": event_mean,
            "ci95": None,
        }
    sessions = [session for session, _value in clean]
    calendar = list(trading_days) if trading_days is not None else trading_days_in_range(min(sessions), max(sessions))
    by_date: dict[str, list[float]] = {}
    for session, value in clean:
        by_date.setdefault(session, []).append(value)
    n = len(calendar)
    if n < 2:
        return {
            "status": "unavailable",
            "target": "event_equal",
            "reason": "insufficient_trading_days",
            "n_events": n_events,
            "n_trading_days": n,
            "mean": event_mean,
            "ci95": None,
        }
    block = max(1, min(int(horizon_days), n))
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block))
    means = []
    for _ in range(n_bootstrap):
        sampled_days: list[str] = []
        starts = rng.integers(0, n, size=n_blocks)
        for start in starts:
            stop = int(start) + block
            if stop <= n:
                sampled_days.extend(calendar[int(start) : stop])
            else:
                sampled_days.extend(calendar[int(start) :])
                sampled_days.extend(calendar[: stop - n])
        sample: list[float] = []
        for day in sampled_days:
            sample.extend(by_date.get(day, []))
        if sample:
            means.append(float(np.mean(sample)))
    if len(means) < 20:
        return {
            "status": "unavailable",
            "target": "event_equal",
            "reason": "bootstrap_samples_too_sparse",
            "n_events": n_events,
            "n_trading_days": n,
            "n_empty_trading_days": sum(1 for day in calendar if day not in by_date),
            "mean": event_mean,
            "ci95": None,
        }
    lo = float(np.quantile(means, 0.025))
    hi = float(np.quantile(means, 0.975))
    return {
        "status": "active",
        "target": "event_equal",
        "method": "calendar_block_event_equal",
        "n_events": n_events,
        "n_trading_days": n,
        "n_dates_with_events": len(by_date),
        "n_empty_trading_days": sum(1 for day in calendar if day not in by_date),
        "block_size": block,
        "n_bootstrap": n_bootstrap,
        "mean": event_mean,
        "ci95": [lo, hi],
        "note": (
            "Each replicate resamples contiguous NYSE sessions, then recomputes "
            "sum(returns)/n_events. Empty sessions add no zero-return events."
        ),
    }


def summarize_return_targets(
    values: Sequence[tuple[str, float]],
    *,
    horizon_days: int = 20,
    trading_days: Sequence[str] | None = None,
) -> dict[str, Any]:
    numbers = [float(value) for _session, value in values if _finite(value) is not None]
    date_ci = horizon_mean_ci(values, horizon_days=horizon_days)
    event_ci = event_equal_calendar_block_ci(
        values,
        horizon_days=horizon_days,
        trading_days=trading_days,
    )
    return {
        "n_events": len(numbers),
        "n_dates_with_events": date_ci.get("n_dates"),
        "event_equal": {
            "mean": None if not numbers else float(fmean(numbers)),
            "fail_rate": None if not numbers else sum(1 for value in numbers if value < 0) / len(numbers),
            "ci": event_ci,
        },
        "date_equal": {
            "mean": date_ci.get("mean"),
            "n_dates": date_ci.get("n_dates"),
            "ci": date_ci,
        },
        "empty_trading_days_are_not_zero_events": True,
        "note": (
            "event_equal.mean is not attached to date_equal.ci. "
            "The two targets can differ in sign."
        ),
    }


def date_clustered_mean(
    values: Sequence[tuple[str, float]],
    *,
    horizon_days: int = 20,
    n_bootstrap: int = 2000,
    seed: int = 7,
) -> dict[str, Any]:
    """Compatibility wrapper. Prefer `horizon_mean_ci` in new code."""

    return horizon_mean_ci(
        values,
        horizon_days=horizon_days,
        n_bootstrap=n_bootstrap,
        seed=seed,
    )


def _iter_ic_points(daily: Iterable[Mapping[str, Any]]) -> list[tuple[str, float]]:
    points: list[tuple[str, float]] = []
    for row in daily:
        if "ic" in row and isinstance(row.get("ic"), Mapping) and "status" in row["ic"]:
            status = row["ic"].get("status")
            value = _finite(row["ic"].get("ic"))
            session = str(row.get("signal_date") or "")
        else:
            status = row.get("status")
            value = _finite(row.get("ic"))
            session = str(row.get("signal_date") or "")
        if status != "active" or value is None:
            continue
        points.append((session, value))
    if any(session for session, _value in points):
        points.sort(key=lambda item: item[0])
    return points


def summarize_daily_ics(
    daily: Iterable[Mapping[str, Any]],
    *,
    horizon_days: int = 20,
) -> dict[str, Any]:
    points = _iter_ic_points(daily)
    values = [value for _session, value in points]
    if not values:
        return {
            "status": "unavailable",
            "days": 0,
            "mean_ic": None,
            "icir": None,
            "positive_share": None,
        }
    mean = fmean(values)
    variance = fmean((value - mean) ** 2 for value in values)
    stdev = math.sqrt(variance)
    ci = dependent_mean_ci(values, horizon_days=horizon_days)
    return {
        "status": "active",
        "days": len(values),
        "mean_ic": mean,
        "icir": None if stdev == 0 else mean / stdev,
        "positive_share": sum(1 for value in values if value > 0) / len(values),
        "min_ic": min(values),
        "max_ic": max(values),
        "horizon_days": horizon_days,
        "ci": ci,
        "ci95": None if ci is None else ci.get("ci95"),
        "note": (
            "ICIR uses the raw daily-IC standard deviation. "
            "The reported 95% interval is the overlapping-horizon block bootstrap, not iid SE."
        ),
    }
