"""Sub-segments that continue a stopped 5-year run (PREREGISTRATION 修订 6).

    python continuation_segments.py --completed results/full_partial_2026-09-29/completed_days_by_segment.json \
        --length 10 --split 2 --out /content/continuation

For every original 32-day segment, the days after its last completed day (plus any day
dropped because not every variant finished it) are cut into sub-segments of ``--length``
trading days; each starts at the first missing day with one warm-up day before it.
Writes ``segments.txt`` (one ``<start> <end>`` per line, longest-running first) or, with
``--split N``, ``segments_1.txt`` .. ``segments_N.txt`` balanced by estimated hours
(1.22 trading days per hour per process before 2025-08-01, 0.6 from then on: the rates
measured on 2026-09-29).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))

from app.services.market_calendar import is_trading_day  # noqa: E402

FIRST_DAY = date(2021, 10, 4)
LAST_DAY = date(2026, 9, 25)
ORIGINAL_SEGMENT_DAYS = 32
RATE_FAST = 1.22  # trading days per hour per process, segments starting before SLOW_FROM
RATE_SLOW = 0.6
SLOW_FROM = date(2025, 8, 1)


def trading_days(first: date = FIRST_DAY, last: date = LAST_DAY) -> list[date]:
    out = []
    day = first
    while day <= last:
        if is_trading_day(day):
            out.append(day)
        day += timedelta(days=1)
    return out


def remaining_by_segment(completed: dict, days: list[date]) -> list[tuple[str, list[date]]]:
    segments = completed["segments"] if "segments" in completed else completed
    out = []
    for index in range(0, len(days), ORIGINAL_SEGMENT_DAYS):
        chunk = days[index:index + ORIGINAL_SEGMENT_DAYS]
        key = f"seg_{chunk[0].isoformat()}"
        done = set(segments.get(key, {}).get("days", []))
        remaining = [d for d in chunk if d.isoformat() not in done]
        if remaining:
            out.append((key, remaining))
    return out


def sub_segments(remaining: list[tuple[str, list[date]]], length: int, slow_length: int | None = None) -> list[tuple[date, date, float]]:
    """(start, end, estimated hours) for every sub-segment; the warm-up day is counted in the hours.

    Sub-segments in the slow regime (first day on or after ``SLOW_FROM``) take ``slow_length``
    days so that no single sub-segment becomes the critical path of a 24-hour session.
    """

    out = []
    for _key, days in remaining:
        index = 0
        while index < len(days):
            slow = days[index] >= SLOW_FROM
            size = slow_length if (slow and slow_length) else length
            chunk = days[index:index + size]
            rate = RATE_SLOW if slow else RATE_FAST
            out.append((chunk[0], chunk[-1], round((len(chunk) + 1) / rate, 2)))
            index += size
    return sorted(out, key=lambda item: (-item[2], item[0]))


def balanced_split(items: list[tuple[date, date, float]], parts: int) -> list[list[tuple[date, date, float]]]:
    buckets: list[list[tuple[date, date, float]]] = [[] for _ in range(parts)]
    loads = [0.0] * parts
    for item in items:  # longest first, to the lightest bucket
        target = loads.index(min(loads))
        buckets[target].append(item)
        loads[target] += item[2]
    return buckets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--completed", type=Path, required=True)
    parser.add_argument("--length", type=int, default=10)
    parser.add_argument("--slow-length", type=int, default=6, help="sub-segment length for days on or after 2025-08-01 (0 = same as --length)")
    parser.add_argument("--split", type=int, default=1)
    parser.add_argument("--parallel", type=int, default=40, help="processes per machine, for the wall-time estimate")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    completed = json.loads(args.completed.read_text())
    days = trading_days()
    remaining = remaining_by_segment(completed, days)
    items = sub_segments(remaining, args.length, args.slow_length or None)
    args.out.mkdir(parents=True, exist_ok=True)
    total_days = sum(len(d) for _k, d in remaining)
    total_hours = sum(item[2] for item in items)
    print(f"remaining trading days {total_days} in {len(remaining)} original segments -> {len(items)} sub-segments of up to {args.length}; "
          f"process-hours {total_hours:.0f} (warm-ups included)")
    groups = balanced_split(items, args.split) if args.split > 1 else [items]
    for number, group in enumerate(groups, 1):
        name = "segments.txt" if args.split == 1 else f"segments_{number}.txt"
        (args.out / name).write_text("".join(f"{start.isoformat()} {end.isoformat()}\n" for start, end, _h in group))
        hours = sum(h for _s, _e, h in group)
        # Longest-first list scheduling on --parallel workers: the wall time is at least the
        # total divided by the workers and at least the longest sub-segment.
        print(f"  {name}: {len(group)} sub-segments, process-hours {hours:.0f}, wall at {args.parallel} processes about "
              f"{max(hours / args.parallel, max(h for _s, _e, h in group)):.1f} h")
    print("DONE continuation_segments")


if __name__ == "__main__":
    main()
