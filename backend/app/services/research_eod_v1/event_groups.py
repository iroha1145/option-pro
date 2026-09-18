"""Shared event clustering. Fragments and H-overlap groups are different counts."""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import next_session, shift_sessions

EVENT_RULE_VERSION = "us-eod-event-groups-v1.1"
FRAGMENT_NAME = "consecutive_trigger_fragments"
OVERLAP_NAME = "label_horizon_overlap_groups"


def session_distance(start: date, end: date) -> int:
    if end < start:
        return session_distance(end, start)
    cursor = start
    steps = 0
    while cursor < end:
        cursor = next_session(cursor)
        steps += 1
        if steps > 10_000:
            raise ValueError("session_distance exceeded 10000 steps")
    return steps


def label_interval(signal: date, label_horizon: int) -> tuple[date, date]:
    """Inclusive [signal, signal+H sessions]. Endpoints are kept."""

    return signal, shift_sessions(signal, int(label_horizon))


def intervals_overlap_or_touch(left: tuple[date, date], right: tuple[date, date]) -> bool:
    return not (left[1] < right[0] or right[1] < left[0])


def cluster_fragments(days: Sequence[date]) -> list[list[date]]:
    ordered = sorted(set(days))
    if not ordered:
        return []
    groups = [[ordered[0]]]
    previous = ordered[0]
    for day in ordered[1:]:
        if next_session(previous) == day:
            groups[-1].append(day)
        else:
            groups.append([day])
        previous = day
    return groups


def cluster_overlap_groups(days: Sequence[date], *, label_horizon: int) -> list[list[date]]:
    """Transitive union of H-session return windows. One quiet day does not split H=20."""

    ordered = sorted(set(days))
    if not ordered:
        return []
    groups: list[list[date]] = [[ordered[0]]]
    current_interval = label_interval(ordered[0], label_horizon)
    for day in ordered[1:]:
        candidate = label_interval(day, label_horizon)
        if intervals_overlap_or_touch(current_interval, candidate):
            groups[-1].append(day)
            current_interval = (current_interval[0], max(current_interval[1], candidate[1]))
        else:
            groups.append([day])
            current_interval = candidate
    return groups


def count_groups(days: Sequence[date], *, label_horizon: int) -> dict[str, Any]:
    fragments = cluster_fragments(days)
    overlaps = cluster_overlap_groups(days, label_horizon=label_horizon)
    return {
        "consecutive_trigger_fragments": len(fragments),
        "label_horizon_overlap_groups": len(overlaps),
        "independent_events": None,
        "usable_for_independent_sample": False,
        "old_count_4514": "SUPERSEDED_EVENT_COUNT",
        "event_rule_version": EVENT_RULE_VERSION,
        "label_horizon": int(label_horizon),
        "endpoint_rule": "inclusive_signal_to_signal_plus_H_sessions",
    }


class EventLedger:
    """Streaming ledger used by both stats helpers and the ablation driver."""

    def __init__(self) -> None:
        self._days: dict[tuple[Any, ...], list[date]] = {}

    def add(self, key: tuple[Any, ...], day: date) -> None:
        bucket = self._days.setdefault(key, [])
        if bucket and day < bucket[-1]:
            raise ValueError("event sessions must be nondecreasing")
        if bucket and day == bucket[-1]:
            return
        bucket.append(day)

    def counts(self, *, label_horizon: int, predicate=None) -> dict[str, int]:
        fragments = 0
        overlaps = 0
        for key, days in self._days.items():
            if predicate is not None and not predicate(key):
                continue
            horizon = int(key[key.index("label") + 1]) if "label" in key else label_horizon
            # key layout is defined by the caller; prefer the stored horizon when present
            del horizon
            counted = count_groups(days, label_horizon=label_horizon)
            fragments += counted["consecutive_trigger_fragments"]
            overlaps += counted["label_horizon_overlap_groups"]
        return {
            FRAGMENT_NAME: fragments,
            OVERLAP_NAME: overlaps,
        }

    def counts_by_key_horizon(self, *, horizon_index: int) -> dict[str, int]:
        fragments = 0
        overlaps = 0
        for key, days in self._days.items():
            label_h = int(key[horizon_index])
            counted = count_groups(days, label_horizon=label_h)
            fragments += counted["consecutive_trigger_fragments"]
            overlaps += counted["label_horizon_overlap_groups"]
        return {
            FRAGMENT_NAME: fragments,
            OVERLAP_NAME: overlaps,
        }

    def items(self) -> Iterable[tuple[tuple[Any, ...], list[date]]]:
        return self._days.items()


def cluster_id_for_signal(
    days: Sequence[date],
    signal: date,
    *,
    label_horizon: int,
) -> str:
    groups = cluster_overlap_groups(days, label_horizon=label_horizon)
    for index, group in enumerate(groups):
        if signal in group:
            return f"overlap:{group[0].isoformat()}:{index}"
    return f"overlap:{signal.isoformat()}:orphan"
