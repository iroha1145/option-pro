"""Shared research statistics. Average-rank Spearman; grouped IC; event ledgers.

Do not copy a second Spearman into scripts. Constant or thin samples stay null.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

STATISTICS_VERSION = "us-eod-research-stats-v1.1"
IC_MIN_CROSS_SECTION = 10
IC_GROUP_KEYS = (
    "signal_session",
    "theme_id",
    "algorithm",
    "profile",
    "horizon",
    "label_horizon",
)


@dataclass(frozen=True)
class SpearmanResult:
    value: float | None
    n: int
    reason: str | None

    def as_tuple(self) -> tuple[float | None, int, str | None]:
        return self.value, self.n, self.reason


def average_ranks(values: Sequence[float]) -> np.ndarray:
    """Competition midranks starting at 1. Ties share the mean rank. No jitter."""

    data = np.asarray(values, dtype=float)
    order = np.argsort(data, kind="mergesort")
    ranks = np.empty(data.size, dtype=float)
    cursor = 0
    while cursor < data.size:
        end = cursor + 1
        while end < data.size and data[order[end]] == data[order[cursor]]:
            end += 1
        ranks[order[cursor:end]] = (cursor + 1 + end) / 2.0
        cursor = end
    return ranks


def _pearson(left: np.ndarray, right: np.ndarray) -> float | None:
    if left.size != right.size or left.size < 2:
        return None
    left = left - left.mean()
    right = right - right.mean()
    denom = float(np.sqrt(np.dot(left, left) * np.dot(right, right)))
    if denom == 0.0 or not np.isfinite(denom):
        return None
    value = float(np.dot(left, right) / denom)
    return None if not np.isfinite(value) else value


def spearman(xs: Sequence[float], ys: Sequence[float]) -> SpearmanResult:
    """Average-rank Spearman. Pair order must not matter. No ticker-as-rank."""

    if len(xs) != len(ys):
        return SpearmanResult(None, 0, "LENGTH_MISMATCH")
    left: list[float] = []
    right: list[float] = []
    for x_raw, y_raw in zip(xs, ys):
        try:
            x_val = float(x_raw)
            y_val = float(y_raw)
        except (TypeError, ValueError):
            continue
        if np.isfinite(x_val) and np.isfinite(y_val):
            left.append(x_val)
            right.append(y_val)
    n = len(left)
    if n < 2:
        return SpearmanResult(None, n, "SAMPLE_TOO_SMALL")
    x_arr = np.asarray(left, dtype=float)
    y_arr = np.asarray(right, dtype=float)
    if np.unique(x_arr).size < 2:
        return SpearmanResult(None, n, "CONSTANT_SCORE")
    if np.unique(y_arr).size < 2:
        return SpearmanResult(None, n, "CONSTANT_LABEL")
    value = _pearson(average_ranks(x_arr), average_ranks(y_arr))
    if value is None:
        return SpearmanResult(None, n, "UNDEFINED_CORRELATION")
    return SpearmanResult(value, n, None)


def grouped_ic(
    rows: Iterable[Mapping[str, Any]],
    *,
    min_n: int = IC_MIN_CROSS_SECTION,
    score_field: str = "score",
    label_field: str = "label",
) -> list[dict[str, Any]]:
    """IC only inside one (session, theme, family, profile, horizon, label)."""

    buckets: dict[tuple[Any, ...], list[tuple[float, float]]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(name) for name in IC_GROUP_KEYS)
        score = row.get(score_field)
        label = row.get(label_field)
        if score is None or label is None:
            continue
        try:
            score_f = float(score)
            label_f = float(label)
        except (TypeError, ValueError):
            continue
        if np.isfinite(score_f) and np.isfinite(label_f):
            buckets[key].append((score_f, label_f))
    out: list[dict[str, Any]] = []
    for key, pairs in sorted(buckets.items(), key=lambda item: tuple(str(part) for part in item[0])):
        result = spearman([a for a, _ in pairs], [b for _, b in pairs])
        defined = result.value if result.n >= min_n and result.reason is None else None
        reason = result.reason
        if result.n < min_n:
            reason = "CROSS_SECTION_BELOW_N"
        elif defined is None and reason is None:
            reason = "UNDEFINED_CORRELATION"
        record = {name: value for name, value in zip(IC_GROUP_KEYS, key)}
        record.update(
            {
                "ic": defined,
                "n": result.n,
                "undefined_reason": None if defined is not None else reason,
                "statistics_version": STATISTICS_VERSION,
            }
        )
        out.append(record)
    return out


def aggregate_ic_by_date(group_rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mean of already-grouped daily ICs. Never re-pool pairs across days."""

    by_date: dict[str, list[float]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for row in group_rows:
        session = str(row.get("signal_session"))
        counts[session] += int(row.get("n") or 0)
        if row.get("ic") is None:
            continue
        by_date[session].append(float(row["ic"]))
    out: dict[str, dict[str, Any]] = {}
    for session, values in by_date.items():
        out[session] = {
            "mean_grouped_ic": float(np.mean(values)) if values else None,
            "defined_groups": len(values),
            "pair_n_sum": counts[session],
            "note": "mean of within-group ICs; pairs were not pooled",
        }
    return out


def factor_ic_universe(row: Mapping[str, Any]) -> bool:
    """Scorer universe: finite score. Not the final LOW_SCORE-truncated book."""

    score = row.get("score")
    if score is None:
        return False
    try:
        return bool(np.isfinite(float(score)))
    except (TypeError, ValueError):
        return False


def event_span_key(row: Mapping[str, Any]) -> tuple[str, str, str, str, str]:
    return (
        str(row.get("security_id")),
        str(row.get("algorithm")),
        str(row.get("profile")),
        str(row.get("horizon")),
        str(row.get("label_horizon") if row.get("label_horizon") is not None else row.get("score_horizon") or "mid"),
    )


def register_independent_events(
    rows: Sequence[Mapping[str, Any]],
    *,
    continuous_calendar: bool,
) -> dict[str, Any]:
    """Trading-session overlap groups. Weekend/holiday gaps are not new events.

    The count is 去重事件组数. It is not N_eff. Calendar-day gap>1 is SUPERSEDED.
    """

    if not continuous_calendar:
        return {
            "independent_events": None,
            "deduped_event_groups": None,
            "sampled_eligible_set_changes": None,
            "usable_for_independent_sample": False,
            "definition": "SECURITY_FAMILY_HORIZON_LABEL_TRADING_SESSION_OVERLAP",
            "reason": "SAMPLED_STREAM_CANNOT_CONFIRM_CONTINUITY",
            "old_count_4514": "SUPERSEDED_EVENT_COUNT",
        }
    from app.services.research_eod_v1.event_groups import cluster_fragments, count_groups

    by_key: dict[tuple[str, str, str, str, str], list[date]] = defaultdict(list)
    for row in rows:
        if row.get("final_eligible") is not True and row.get("status") != "eligible":
            continue
        session = row.get("signal_session") or row.get("session_date")
        if session is None:
            continue
        day = session if isinstance(session, date) else date.fromisoformat(str(session)[:10])
        by_key[event_span_key(row)].append(day)
    groups = 0
    overlap_groups = 0
    for key, days in by_key.items():
        fragments = cluster_fragments(days)
        groups += len(fragments)
        label_h = 5
        try:
            label_h = int(key[-1])
        except (TypeError, ValueError):
            label_h = 5
        overlap_groups += count_groups(days, label_horizon=label_h)["label_horizon_overlap_groups"]
    return {
        "independent_events": None,
        "deduped_event_groups": groups,
        "label_horizon_overlap_groups": overlap_groups,
        "consecutive_trigger_fragments": groups,
        "sampled_eligible_set_changes": None,
        "usable_for_independent_sample": False,
        "definition": "SECURITY_FAMILY_HORIZON_LABEL_TRADING_SESSION_OVERLAP",
        "reason": "DEDUPE_GROUP_IS_NOT_N_EFF",
        "old_count_4514": "SUPERSEDED_EVENT_COUNT",
    }


def snapshot_identity_key(
    *,
    session: str,
    theme_id: str,
    algorithm: str,
    profile: str,
    horizon: str,
    security_id: str | None = None,
) -> str:
    parts = [session, theme_id, algorithm, profile, horizon]
    if security_id:
        parts.append(security_id)
    return "|".join(parts)
