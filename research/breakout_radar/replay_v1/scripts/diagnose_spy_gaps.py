"""Why do triggers lack a SPY entry bar? Group the misses and check the minute store's SPY coverage.

    python diagnose_spy_gaps.py --events /content/eval/full_partial/events_h20.csv \
        --minute-store /content/minute_store --out /content/eval/spy_gaps.json

``events_h20.csv`` is the per-trigger file evaluate.py writes (``benchmark_basis`` is
"close" when the SPY bar was missing). For every such trigger the script looks at SPY's
slots that day: whether SPY has any bar that day, whether the exact slot exists, and how
many empty slots separate the scan from SPY's next bar. Counts by ET hour, year and
origin tell gaps in the data apart from a rule that was too strict.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

PACK = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(PACK))

from harness.stores import SLOTS_PER_DAY, MinuteStore  # noqa: E402

NY = ZoneInfo("America/New_York")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--minute-store", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    store = MinuteStore(args.minute_store)
    rows = list(csv.DictReader(open(args.events)))
    missing = [r for r in rows if r.get("benchmark_basis") == "close"]
    by_hour: Counter = Counter()
    by_year: Counter = Counter()
    by_origin: Counter = Counter()
    spy_state: Counter = Counter()
    delays: Counter = Counter()
    examples: list[dict] = []
    day_cache: dict = {}
    for row in missing:
        as_of = datetime.fromisoformat(row["as_of"].replace("Z", "+00:00")).astimezone(NY)
        by_hour[as_of.hour] += 1
        by_year[as_of.year] += 1
        by_origin[row.get("origin")] += 1
        day = as_of.date()
        if day not in day_cache:
            slots = store.day_slots("SPY", day) if store.has_bars_between("SPY", day, day) else None
            day_cache[day] = slots
        slots = day_cache[day]
        minute = as_of.hour * 60 + as_of.minute
        target = minute if as_of.second == 0 and minute % 5 == 0 else (minute // 5 + 1) * 5
        slot = (target - 4 * 60) // 5
        if slots is None:
            spy_state["spy_has_no_bars_that_day"] += 1
            kind = "no_spy_day"
        elif slot >= SLOTS_PER_DAY:
            spy_state["slot_at_or_after_2000"] += 1
            kind = "after_close"
        elif np.isfinite(slots[0][slot]):
            spy_state["exact_slot_present_rule_mismatch"] += 1
            kind = "present"
        else:
            later = np.flatnonzero(np.isfinite(slots[0][slot:]))
            spy_state["exact_slot_missing"] += 1
            delays[int(later[0]) if len(later) else -1] += 1
            kind = "gap"
        if len(examples) < 20:
            examples.append({"as_of": row["as_of"], "ticker": row.get("ticker"), "kind": kind})
    summary = {
        "triggers": len(rows), "without_spy_bar": len(missing), "share": round(len(missing) / len(rows), 4) if rows else None,
        "by_et_hour": dict(sorted(by_hour.items())), "by_year": dict(sorted(by_year.items())), "by_origin": dict(by_origin.most_common()),
        "spy_state": dict(spy_state), "empty_slots_before_next_spy_bar": dict(sorted(delays.items())),
        "spy_days_in_store": len(store._days_by_ticker.get("SPY", [])),
        "spy_in_coverage_table": "SPY" in store._days_by_ticker,
        "examples": examples,
    }
    args.out.write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    print("DONE diagnose_spy_gaps")


if __name__ == "__main__":
    main()
