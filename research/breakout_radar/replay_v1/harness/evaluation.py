"""Pre-registered evaluation of replayed breakout-radar events (PREREGISTRATION 4, 5, 6, 9).

Inputs are the segment outputs (``<variant>/ledger/<day>.jsonl.gz``,
``<variant>/research_bundle.json.gz``) and the frozen daily database. An event is a
trigger: the first TRIGGERED transition of an event id, on the day the scan ran (a
carry-over event triggering on D counts on D). Entry is the open of the 5-minute bar
after the scan, recorded in the ledger by the runner (``next_bar_open``; the
``--minute-store`` fallback looks it up when an older ledger lacks it); the trigger
mark is the control entry. Exits are the closes 1, 5, 20 and 63 sessions after D,
split-adjusted, with the observation rules of the screener v1.6 pack (``ok``,
``ok_bridged``, ``ok_late_exit``, ``renamed``; four censored kinds; legacy, zero and
loss bounds) applied from an intraday entry. Excess is against SPY over the same window
(SPY enters at the same bar; without SPY minute bars it enters at its close on D and the
result pack says so). Triggers of one day are equal-weighted, days are averaged, and the
Newey-West t uses lag h/5 - 1.
"""

from __future__ import annotations

import csv
import gzip
import importlib.util
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
HOLDINGS = (1, 5, 20, 63)
PRIMARY_HOLDING = 20
# 修订 7: one point per trading day, so h-day windows of adjacent days overlap on h - 1 lags.
NW_LAGS = {1: 0, 5: 4, 20: 19, 63: 62}
BOOTSTRAP_SAMPLES = 2000
BOOTSTRAP_SEED = 20261005
P1_END = "2024-12-31"
COMPLETE_YEARS = ("2022", "2023", "2024", "2025")
MIN_YEAR_DAYS = 60  # PREREGISTRATION 修订 5: a year with fewer covered days is reported, not counted in rule 2
FUNNEL_CAPS = (150, 60, 30)
SCORE_SUBSET_MIN = 60.0
TOP_PER_DAY = 10
FUNNEL_VARIANTS = ("disc5", "adv25")
FILTER_VIEWS = ("noorb", "mkt_gate", "tod")
# 修订 6: switches that act after the trigger are judged on the view that shows their effect.
METRIC_VIEW = {"confirm3": "confirmed", "chase15": "chaseable"}
SWITCH_VIEWS = ("confirmed", "chaseable")
STOCK_ENTRY_MAX_SLOTS = 6
V16_EVALUATE = (
    Path(__file__).resolve().parents[3]
    / "option_pro_us_eod_v1" / "return_pack" / "full_market_v1_6" / "scripts" / "evaluate.py"
)


def load_v16(path: Path | str = V16_EVALUATE) -> Any:
    """The screener pack's evaluate.py: observation rules, bounds and Newey-West t."""

    spec = importlib.util.spec_from_file_location("full_market_v16_evaluate", Path(path))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


V16 = load_v16()
Outcome = V16.Outcome
newey_west_t = V16.newey_west_t
OBSERVABLE_STATUSES = V16.OBSERVABLE_STATUSES
CENSORED_STATUSES = V16.CENSORED_STATUSES
EXIT_LAG_SESSIONS = V16.EXIT_LAG_SESSIONS
RENAME_LAG_SESSIONS = V16.RENAME_LAG_SESSIONS
RENAME_PRICE_RATIO = V16.RENAME_PRICE_RATIO

RULES = {
    "event": "first TRIGGERED transition per event id; the day is the scan's ET date",
    "entry": "open of the 5-minute bar after the scan (next_bar_open); control: the trigger mark",
    "exits": "closes 1, 5, 20, 63 sessions after the trigger day, split-adjusted",
    "benchmark": "SPY entered at the same bar (else its close on the trigger day), exited at the same close",
    "aggregation": "equal weight within a day, mean over observed days; Newey-West t with lag h - 1 on the daily series (修订 7); moving-block bootstrap (block h) as the overlap-robust check",
    "benchmark_alignment": "SPY taken at the stock's actual entry moment (slot after the scan plus the delay); unmatched cases are flagged, not approximated (修订 7)",
    "causality": "chaseable/extended membership is frozen at the trigger scan; extended_by_next_scan is a descriptive field only (修订 7)",
    "observation": dict(V16.RULES),
    "primary_holding": PRIMARY_HOLDING,
    "p1_end": P1_END,
    "complete_years": list(COMPLETE_YEARS),
    "score_subset_min": SCORE_SUBSET_MIN,
    "top_per_day": TOP_PER_DAY,
    "adoption": {
        "1": "h20 mean higher than the baseline in P1 and in P2 (paired on common days)",
        "2": "better in at least 3 of the 4 complete years (2022-2025); a year with fewer than 60 paired days is reported, not counted (修订 5)",
        "3": "h5 not below the baseline by more than 0.5 pp; h63 not by more than 1 pp",
        "4": "triggers per day at least half the baseline's",
        "5": "paired h20 difference has the same sign under the legacy, zero and loss bounds",
        "6": "funnel candidates (disc5, adv25): removed events must have a lower h20 than the kept ones",
        "stage2": "a combination is adopted only if it passes 1-5 and is within 0.2 pp of the best single in both periods",
    },
}


# --------------------------------------------------------------------------- inputs


@dataclass
class Trigger:
    event_id: str
    ticker: str
    day: str  # ET date of the scan that triggered
    as_of: str  # ISO UTC
    trading_date: str
    origin: str
    setup: str
    trigger_price: float | None
    next_bar_open: float | None
    benchmark_open: float | None
    alert: float | None
    strength: float | None
    market_state: str | None
    market_eligibility: str | None
    minute: int  # ET minutes since midnight
    pivot_id: str | None = None
    asset_type: str | None = None
    carryover: bool = False
    failed_at: str | None = None  # first FAILED transition after the trigger (ISO UTC)
    t1_status: str | None = None
    # PREREGISTRATION 修订 6 and 7: the switches confirm3 and chase15 act after the trigger.
    extended_at_trigger: bool = False  # EXTENDED in the trigger scan itself (causal, 修订 7)
    extended_by_next_scan: bool = False  # EXTENDED at the next scan of the day: descriptive only, never a view for adoption
    confirmed_at: str | None = None  # first CONFIRMED transition (ISO UTC), same scan or later
    confirmed_day: str | None = None
    confirmed_next_bar_open: float | None = None
    confirmed_benchmark_open: float | None = None
    confirmed_next_bar_delay: int | None = None
    confirmed_entry_at: str | None = None
    confirmed_benchmark_aligned: bool | None = None
    next_bar_delay: int | None = None  # empty slots skipped before the entry bar (0 = the next bar itself)
    benchmark_delay: int | None = None
    entry_at: str | None = None  # ISO UTC start of the bar the stock actually enters at (slot after the scan + delay)
    benchmark_aligned: bool | None = None  # SPY taken at entry_at (True), recorded at the scan's slot while the stock was delayed (False)

    @property
    def bucket(self) -> str:
        if self.minute < 10 * 60:
            return "open30"
        if self.minute < 12 * 60:
            return "morning"
        if self.minute < 15 * 60 + 30:
            return "afternoon"
        return "late"

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.ticker, self.day, self.origin)


@dataclass
class DayFunnel:
    day: str
    scans: int = 0
    prefilter: int = 0
    listed: int = 0
    structures: int = 0
    events: int = 0
    cut_150: int = 0
    cut_60: int = 0
    cut_30: int = 0
    triggers: int = 0


def _et_day(stamp: str) -> str:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(NY).date().isoformat()


def _et_minute(stamp: str) -> int:
    local = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(NY)
    return local.hour * 60 + local.minute


def entry_moment(as_of: str, delay: int | None) -> str:
    """ISO UTC start of the entry bar: the first 5-minute boundary at or after ``as_of`` plus ``delay`` slots."""

    local = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(NY)
    minute = local.hour * 60 + local.minute
    on_boundary = local.second == 0 and local.microsecond == 0 and minute % 5 == 0
    rounded = minute if on_boundary else (minute // 5 + 1) * 5
    rounded += 5 * int(delay or 0)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(minutes=rounded)
    return start.astimezone(timezone.utc).isoformat()


def read_ledger_file(path: Path) -> list[dict[str, Any]] | None:
    """Every record of one day file, or ``None`` when the file is truncated or unreadable.

    The runner writes a day file only after the day completed (atomically since 3aaa04d1's
    successor; before that a kill during the write could truncate it), so a file is either
    a whole day or garbage: the whole file is read before any record is used.
    """

    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]
    except (EOFError, OSError, ValueError):
        return None


def read_ledger_records(folder: Path) -> Iterable[dict[str, Any]]:
    for path in sorted(folder.glob("*.jsonl.gz")):
        records = read_ledger_file(path)
        if records:
            yield from records


def read_t1_from_db(path: Path) -> dict[str, str]:
    """``breakout_t1_current`` of a variant's SQLite database (a killed segment writes no bundle)."""

    if not Path(path).exists():
        return {}
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        exists = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='breakout_t1_current'").fetchone()
        if not exists:
            return {}
        return {str(event_id): str(status) for event_id, status in connection.execute("SELECT event_id, status FROM breakout_t1_current")}
    finally:
        connection.close()


Lookup = Callable[..., "tuple[float, int] | None"]


def store_lookups(store: Any) -> tuple[Lookup, Lookup]:
    """Entry-price lookups on a minute store for ledgers that lack the recorded values.

    Both follow PREREGISTRATION 修订 6: the stock enters at the first bar at or after the
    next slot within ``STOCK_ENTRY_MAX_SLOTS`` empty slots, SPY at its first bar that day.
    Each returns ``(open, delay)`` or ``None``.
    """

    def entry_lookup(ticker: str, as_of: str) -> tuple[float, int] | None:
        stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
        return store.next_bar(ticker, stamp, max_slots=STOCK_ENTRY_MAX_SLOTS)

    def benchmark_lookup(as_of: str) -> tuple[float, int] | None:
        stamp = datetime.fromisoformat(as_of.replace("Z", "+00:00")).astimezone(timezone.utc)
        return store.next_bar("SPY", stamp, max_slots=None)

    return entry_lookup, benchmark_lookup


def read_variant(
    replay_dirs: list[Path], variant: str, *, entry_lookup: Lookup | None = None,
    benchmark_lookup: Lookup | None = None, db_dir: Path | None = None,
) -> tuple[list[Trigger], dict[str, DayFunnel], dict[str, Any]]:
    """Triggers, per-day funnel counts and T1 statuses of one variant across segment directories.

    Warm-up scans are skipped; a day present in two directories is taken from the first;
    a truncated day file is skipped and counted. ``entry_lookup(ticker, as_of)`` and
    ``benchmark_lookup(as_of)`` (see ``store_lookups``) backfill ``next_bar_open`` and the
    SPY entry where a ledger lacks them: ledgers written before the runner recorded them,
    CONFIRMED-only scans of the first round, and the round-1 store's SPY gaps (DATA_SPEC
    20.15). Recorded values are kept; the backfills are counted in ``info["backfills"]``.
    Without a research bundle (a killed segment) T1 statuses come from
    ``db_dir/<segment>/<variant>.sqlite``.
    """

    folder_name = variant.replace("+", "_")
    triggers: dict[str, Trigger] = {}
    failed: dict[str, str] = {}
    funnel: dict[str, DayFunnel] = {}
    days_by_dir: dict[str, Path] = {}
    t1_status: dict[str, str] = {}
    info: dict[str, Any] = {"directories": [], "scans": 0, "skipped_duplicate_days": 0, "corrupt_files": [],
                            "segments_without_bundle": 0, "degraded_scans": 0, "degraded_days": [], "segment_starts": []}
    degraded_days: set[str] = set()
    state: dict[str, Any] = {"watch_extended": {}, "entry_lookup": entry_lookup, "benchmark_lookup": benchmark_lookup,
                             "backfills": Counter()}
    for replay_dir in replay_dirs:
        variant_dir = replay_dir / folder_name
        if not variant_dir.is_dir():
            continue
        info["directories"].append(str(replay_dir))
        bundle_path = variant_dir / "research_bundle.json.gz"
        if bundle_path.exists():
            with gzip.open(bundle_path, "rt", encoding="utf-8") as handle:
                bundle = json.load(handle)
            for row in bundle.get("t1_current") or []:
                t1_status.setdefault(str(row.get("event_id")), str(row.get("status")))
        else:
            info["segments_without_bundle"] += 1
            if db_dir is not None:
                for event_id, status in read_t1_from_db(Path(db_dir) / replay_dir.name / f"{folder_name}.sqlite").items():
                    t1_status.setdefault(event_id, status)
        first_day_of_dir = None
        for path in sorted((variant_dir / "ledger").glob("*.jsonl.gz")):
            records = read_ledger_file(path)
            if records is None:
                info["corrupt_files"].append(str(path))
                continue
            state["watch_extended"] = {}
            for record in records:
                if record.get("kind") == "t1" or record.get("warmup"):
                    continue
                day = _et_day(record["as_of"])
                owner = days_by_dir.setdefault(day, replay_dir)
                if owner != replay_dir:
                    info["skipped_duplicate_days"] += 1
                    continue
                if first_day_of_dir is None:
                    first_day_of_dir = day
                if record.get("status") in ("degraded", "exception") or record.get("error_code"):
                    info["degraded_scans"] += 1
                    degraded_days.add(day)
                _absorb_record(record, day, triggers, failed, funnel, state)
                info["scans"] += 1
        if first_day_of_dir is not None:
            info["segment_starts"].append(first_day_of_dir)
    for event_id, trigger in triggers.items():
        trigger.failed_at = failed.get(event_id)
        trigger.t1_status = t1_status.get(event_id)
    ordered = sorted(triggers.values(), key=lambda t: (t.as_of, t.event_id))
    info["triggers"] = len(ordered)
    info["days"] = len(funnel)
    info["degraded_days"] = sorted(degraded_days)
    info["backfills"] = {key: state["backfills"][key] for key in
                         ("entry_triggers", "benchmark_triggers", "entry_confirmed", "benchmark_confirmed",
                          "benchmark_realigned_triggers", "benchmark_realigned_confirmed",
                          "benchmark_misaligned_triggers", "benchmark_misaligned_confirmed")}
    return ordered, funnel, info


def _absorb_record(record: dict[str, Any], day: str, triggers: dict[str, Trigger], failed: dict[str, str],
                   funnel: dict[str, DayFunnel], state: dict[str, Any]) -> None:
    entry_lookup = state.get("entry_lookup")
    benchmark_lookup = state.get("benchmark_lookup")
    backfills: Counter = state["backfills"]

    def benchmark_at_entry(event: dict[str, Any], entry_delay: int | None, kind: str) -> tuple[float | None, int | None, str, bool | None]:
        """SPY at the stock's actual entry moment (修订 7): (open, delay, entry_at, aligned).

        With a zero stock delay the scan's recorded SPY bar is that moment. When the stock
        rolled forward, a per-event value the runner recorded at the entry moment is used,
        else the minute store; without either the recorded scan-slot value stays, flagged.
        """

        entry_at = entry_moment(record["as_of"], entry_delay)
        recorded = _float(record.get("benchmark_next_bar_open"))
        recorded_delay = _int_or_none(record.get("benchmark_next_bar_delay_slots"))
        if (entry_delay or 0) == 0 and recorded is not None:
            # The recorded value is SPY's first bar at or after the scan's slot, which is the entry moment.
            return recorded, recorded_delay, entry_at, True
        at_entry = _float(event.get("benchmark_open_at_entry"))
        if at_entry is not None:
            return at_entry, _int_or_none(event.get("benchmark_delay_at_entry")), entry_at, True
        if benchmark_lookup is not None:
            found = benchmark_lookup(entry_at)
            if found is not None:
                if recorded is None:
                    backfills[f"benchmark_{kind}"] += 1
                else:
                    backfills[f"benchmark_realigned_{kind}"] += 1
                return float(found[0]), int(found[1]), entry_at, True
        if recorded is None:
            return None, None, entry_at, None
        backfills[f"benchmark_misaligned_{kind}"] += 1
        return recorded, recorded_delay, entry_at, False

    def entry_at_record(event: dict[str, Any], kind: str) -> tuple[float | None, int | None]:
        value = _float(event.get("next_bar_open"))
        delay = _int_or_none(event.get("next_bar_delay_slots"))
        if value is None and entry_lookup is not None:
            found = entry_lookup(str(event.get("ticker")), record["as_of"])
            if found is not None:
                value, delay = float(found[0]), int(found[1])
                backfills[f"entry_{kind}"] += 1
        return value, delay

    events_by_id = {e.get("event_id"): e for e in record.get("events") or []}
    # Events triggered at the previous scan of the day: an EXTENDED state now is later information
    # (修订 7); it is kept as a descriptive field and never changes the causal membership.
    for event_id in list(state["watch_extended"]):
        event = events_by_id.get(event_id)
        if event is not None and str(event.get("lifecycle_state")) == "EXTENDED" and event_id in triggers:
            triggers[event_id].extended_by_next_scan = True
    state["watch_extended"] = {}
    states_now: dict[str, set[str]] = defaultdict(set)
    for transition in record.get("transitions") or []:
        states_now[str(transition.get("event_id"))].add(str(transition.get("to_state")))
    for event_id, states in states_now.items():
        if "EXTENDED" in states and event_id in triggers and triggers[event_id].as_of == record["as_of"]:
            triggers[event_id].extended_at_trigger = True
    if True:
        if True:
            bucket = funnel.setdefault(day, DayFunnel(day))
            bucket.scans += 1
            prefilter = int(record.get("prefilter_count") or 0)
            listed = int(record.get("candidate_count") or 0)
            structures = len(record.get("structures") or [])
            bucket.prefilter += prefilter
            bucket.listed += listed
            bucket.structures += structures
            bucket.events += int(record.get("event_count") or 0)
            bucket.cut_150 += max(0, prefilter - FUNNEL_CAPS[0])
            bucket.cut_60 += max(0, listed - FUNNEL_CAPS[1])
            bucket.cut_30 += max(0, structures - FUNNEL_CAPS[2])
            for transition in record.get("transitions") or []:
                event_id = str(transition.get("event_id"))
                to_state = str(transition.get("to_state"))
                if to_state == "TRIGGERED" and event_id not in triggers:
                    event = events_by_id.get(event_id)
                    if event is None:
                        continue
                    scores = event.get("scores") or {}
                    features = event.get("features") or {}
                    origin = str(event.get("origin_setup_type") or event.get("setup_type") or "")
                    next_open, delay = entry_at_record(event, "triggers")
                    benchmark_open, benchmark_delay, entry_at, aligned = benchmark_at_entry(event, delay, "triggers")
                    triggers[event_id] = Trigger(
                        event_id=event_id,
                        ticker=str(event.get("ticker")).upper(),
                        day=day,
                        as_of=record["as_of"],
                        trading_date=str(event.get("trading_date")),
                        origin=origin,
                        setup=str(event.get("setup_type") or ""),
                        trigger_price=_float(event.get("event_price")),
                        next_bar_open=next_open,
                        benchmark_open=benchmark_open,
                        alert=_float(scores.get("alert_priority_score")),
                        strength=_float(scores.get("intrinsic_strength_score")),
                        market_state=features.get("market_shape_state"),
                        market_eligibility=features.get("market_eligibility"),
                        minute=_et_minute(record["as_of"]),
                        pivot_id=event.get("pivot_id"),
                        asset_type=event.get("asset_type"),
                        carryover=bool(event.get("carryover")),
                        extended_at_trigger=(
                            str(event.get("lifecycle_state")) == "EXTENDED"
                            or "extended_from_pivot" in (event.get("warnings") or [])
                            or "EXTENDED" in states_now.get(event_id, set())
                        ),
                        next_bar_delay=delay,
                        benchmark_delay=benchmark_delay,
                        entry_at=entry_at,
                        benchmark_aligned=aligned,
                    )
                    bucket.triggers += 1
                    state["watch_extended"][event_id] = day
                elif to_state == "FAILED" and event_id in triggers and event_id not in failed:
                    failed[event_id] = str(transition.get("evidence_at") or record["as_of"])
            for event_id, states in states_now.items():
                trigger = triggers.get(event_id)
                if trigger is None or "CONFIRMED" not in states or trigger.confirmed_at is not None:
                    continue
                event = events_by_id.get(event_id)
                confirmed_open, confirmed_delay = (None, None) if event is None else entry_at_record(event, "confirmed")
                benchmark_open, _bd, confirmed_entry_at, aligned = benchmark_at_entry(event or {}, confirmed_delay, "confirmed")
                trigger.confirmed_at = record["as_of"]
                trigger.confirmed_day = day
                trigger.confirmed_next_bar_open = confirmed_open
                trigger.confirmed_next_bar_delay = confirmed_delay
                trigger.confirmed_entry_at = confirmed_entry_at
                trigger.confirmed_benchmark_open = benchmark_open
                trigger.confirmed_benchmark_aligned = aligned


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def completed_days(loaded: Mapping[str, tuple[list[Trigger], dict[str, DayFunnel], dict[str, Any]]]) -> dict[str, Any]:
    """The days every variant completed and no variant degraded on (RUN_SPEC section 6).

    A day file exists only once the runner finished the day, so the set of readable day
    files is the set of completed days per variant; the evaluation uses their intersection
    so paired comparisons see the same days, and drops days with a degraded scan.
    """

    per_variant = {name: set(funnel) for name, (_t, funnel, _i) in loaded.items()}
    common = set.intersection(*per_variant.values()) if per_variant else set()
    degraded = {day for _n, (_t, _f, info) in loaded.items() for day in info.get("degraded_days", [])}
    kept = sorted(common - degraded)
    return {
        "days": kept,
        "per_variant_days": {name: len(days) for name, days in per_variant.items()},
        "dropped_not_in_every_variant": sorted(set.union(*per_variant.values()) - common) if per_variant else [],
        "dropped_degraded": sorted(common & degraded),
        "per_year": dict(Counter(day[:4] for day in kept)),
        "p1_days": sum(1 for day in kept if day <= P1_END),
        "p2_days": sum(1 for day in kept if day > P1_END),
    }


def restrict(triggers: list[Trigger], funnel: dict[str, DayFunnel], days: Iterable[str]) -> tuple[list[Trigger], dict[str, DayFunnel]]:
    allowed = set(days)
    return [t for t in triggers if t.day in allowed], {day: f for day, f in funnel.items() if day in allowed}


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# --------------------------------------------------------------------------- prices


class RadarPrices(V16.Prices):
    """The screener pack's observation rules from an intraday entry on the trigger day."""

    def __init__(self, connection: sqlite3.Connection, start: str, directory: Any = None) -> None:
        super().__init__(connection, start, directory)
        self._intraday_outcomes: dict[tuple[str, str, int, float], Outcome] = {}

    def _legacy_intraday(self, ticker: str, signal_day: str, holding: int, entry_price: float) -> tuple[float | None, str, str | None]:
        i = self.index[signal_day]
        exit_i = i + holding
        if exit_i >= len(self.sessions):
            return None, "no_label", None
        bars = self.series(ticker)
        last = i
        for j in range(i + 1, exit_i + 1):
            if self._has_bar(bars, j):
                last = j
            else:
                break
        if last == i:
            return None, "no_exit_bar", None
        exit_day = self.sessions[last]
        adjusted_entry = entry_price * self.split_factor(ticker, signal_day, exit_day)
        return bars[exit_day][1] / adjusted_entry - 1, ("ok" if last == exit_i else "gap_exit"), exit_day

    def _renamed_intraday(self, ticker: str, signal_day: str, last_i: int, exit_i: int, entry_price: float) -> Outcome | None:
        if self.directory is None:
            return None
        entry_label = self.directory.label_for(signal_day)
        old_row = None if entry_label is None else self.directory.rows(entry_label).get(ticker)
        if not old_row or not self.directory.identity_keys(old_row):
            return None
        last_day = self.sessions[last_i]
        old_bars = self.series(ticker)
        for label in self.directory.labels_after(last_day)[:3]:
            for candidate in self.directory.successors(old_row, entry_label, label):
                new_bars = self.series(candidate)
                first_i = self._first_bar_after(new_bars, last_i, RENAME_LAG_SESSIONS)
                if first_i is None:
                    continue
                exit_j = exit_i if self._has_bar(new_bars, exit_i) else self._first_bar_after(new_bars, exit_i, EXIT_LAG_SESSIONS)
                if exit_j is None or exit_j < first_i:
                    continue
                first_day, exit_day = self.sessions[first_i], self.sessions[exit_j]
                ratio = new_bars[first_day][0] / old_bars[last_day][1]
                for execution, split_from, split_to in self.splits.get(candidate, ()):
                    if last_day < execution <= first_day:
                        ratio *= split_to / split_from
                if not RENAME_PRICE_RATIO[0] <= ratio <= RENAME_PRICE_RATIO[1]:
                    continue
                adjusted_entry = entry_price * self.split_factor(ticker, signal_day, last_day) * self.split_factor(candidate, last_day, exit_day)
                return Outcome(ticker, "renamed", new_bars[exit_day][1] / adjusted_entry - 1, signal_day, exit_day,
                               last_day, None, exit_j - exit_i, 0, candidate,
                               detail={"first_new_day": first_day, "price_ratio": round(ratio, 4)})
        return None

    def observe_intraday(self, ticker: str, signal_day: str, holding: int, entry_price: float) -> Outcome:
        """Outcome of buying ``ticker`` at ``entry_price`` during ``signal_day`` and holding ``holding`` sessions."""

        key = (ticker, signal_day, holding, float(entry_price))
        cached = self._intraday_outcomes.get(key)
        if cached is not None:
            return cached
        outcome = self._observe_intraday(ticker, signal_day, holding, entry_price)
        legacy_ret, legacy_status, legacy_exit = self._legacy_intraday(ticker, signal_day, holding, entry_price)
        outcome.legacy_ret, outcome.legacy_status, outcome.legacy_exit_day = legacy_ret, legacy_status, legacy_exit
        self._intraday_outcomes[key] = outcome
        return outcome

    def _observe_intraday(self, ticker: str, signal_day: str, holding: int, entry_price: float) -> Outcome:
        if signal_day not in self.index:
            return Outcome(ticker, "no_entry_bar", entry_day=signal_day)
        i = self.index[signal_day]
        exit_i = i + holding
        if exit_i >= len(self.sessions):
            return Outcome(ticker, "no_label")
        if entry_price <= 0:
            return Outcome(ticker, "no_entry_bar", entry_day=signal_day)
        bars = self.series(ticker)
        present = [j for j in range(i + 1, exit_i + 1) if self._has_bar(bars, j)]
        if not present:
            if not self._has_bar(bars, i):
                return Outcome(ticker, "no_entry_bar", entry_day=signal_day)
            status = "censored_unverified" if self.directory is None else self._censored(ticker, signal_day, i)
            return Outcome(ticker, status, entry_day=signal_day, last_day=signal_day, first_gap_day=self.sessions[i + 1])
        last_i = present[-1]
        last_day = self.sessions[last_i]
        missing = [j for j in range(i + 1, exit_i + 1) if not self._has_bar(bars, j)]
        first_gap_day = self.sessions[missing[0]] if missing else None
        interior_gaps = len(missing) - (0 if last_i == exit_i else exit_i - last_i)
        exit_j = exit_i if last_i == exit_i else self._first_bar_after(bars, exit_i, EXIT_LAG_SESSIONS)
        if exit_j is not None:
            exit_day = self.sessions[exit_j]
            gapped = interior_gaps > 0 or exit_j != exit_i
            if gapped:
                same = self._identity_check(ticker, signal_day, exit_day)
                if same is None:
                    return Outcome(ticker, "censored_unverified", entry_day=signal_day, last_day=last_day,
                                   first_gap_day=first_gap_day, interior_gaps=interior_gaps)
                if same is False:
                    return Outcome(ticker, "censored_terminal", entry_day=signal_day, last_day=last_day,
                                   first_gap_day=first_gap_day, interior_gaps=interior_gaps, detail={"identity": "changed"})
            status = "ok" if not gapped else ("ok_bridged" if exit_j == exit_i else "ok_late_exit")
            adjusted_entry = entry_price * self.split_factor(ticker, signal_day, exit_day)
            return Outcome(ticker, status, bars[exit_day][1] / adjusted_entry - 1, signal_day, exit_day, last_day,
                           first_gap_day, exit_j - exit_i, interior_gaps)
        renamed = self._renamed_intraday(ticker, signal_day, last_i, exit_i, entry_price)
        if renamed is not None:
            renamed.interior_gaps = interior_gaps
            renamed.first_gap_day = first_gap_day
            return renamed
        status = "censored_unverified" if self.directory is None else self._censored(ticker, signal_day, last_i)
        return Outcome(ticker, status, entry_day=signal_day, last_day=last_day, first_gap_day=first_gap_day,
                       interior_gaps=interior_gaps)

    def benchmark_return(self, signal_day: str, exit_day: str, entry_price: float | None) -> tuple[float | None, str]:
        """SPY's return from ``entry_price`` (the same bar) or from its close on the signal day."""

        bars = self.series("SPY")
        if exit_day not in bars:
            return None, "no_spy_exit"
        basis = "bar"
        if entry_price is None or entry_price <= 0:
            if signal_day not in bars or not bars[signal_day][1]:
                return None, "no_spy_entry"
            entry_price = bars[signal_day][1]
            basis = "close"
        adjusted_entry = entry_price * self.split_factor("SPY", signal_day, exit_day)
        return bars[exit_day][1] / adjusted_entry - 1, basis


# --------------------------------------------------------------------------- outcomes per trigger


@dataclass
class TriggerResult:
    trigger: Trigger
    holding: int
    entry_kind: str  # "next_bar" | "trigger_mark" | "t1_next_open"
    status: str
    ret: float | None = None
    excess: float | None = None
    exit_day: str | None = None
    benchmark_basis: str | None = None
    legacy_excess: float | None = None
    loss_excess: float | None = None  # censored valued as a total loss against SPY over the full window
    failed_by_next_close: bool | None = None

    @property
    def observable(self) -> bool:
        return self.status in OBSERVABLE_STATUSES

    @property
    def censored(self) -> bool:
        return self.status in CENSORED_STATUSES


def failed_by_next_close(trigger: Trigger, prices: RadarPrices) -> bool | None:
    if trigger.day not in prices.index:
        return None
    i = prices.index[trigger.day]
    if i + 1 >= len(prices.sessions):
        return None
    if trigger.failed_at is None:
        return False
    failed_day = _et_day(trigger.failed_at)
    return failed_day <= prices.sessions[i + 1]


def evaluate_trigger(trigger: Trigger, prices: RadarPrices, holding: int, entry_kind: str) -> TriggerResult:
    if entry_kind == "t1_next_open":
        if trigger.day not in prices.index:
            return TriggerResult(trigger, holding, entry_kind, "no_entry_bar")
        outcome = prices.observe(trigger.ticker, trigger.day, holding)
        entry_price = None
        spy_entry = None
    else:
        entry_price = trigger.next_bar_open if entry_kind == "next_bar" else trigger.trigger_price
        if entry_price is None or entry_price <= 0:
            return TriggerResult(trigger, holding, entry_kind, "no_entry_bar")
        outcome = prices.observe_intraday(trigger.ticker, trigger.day, holding, entry_price)
        # SPY at the stock's actual entry moment (修订 7). The trigger-mark control values the stock at
        # the last complete bar's close but still uses this SPY entry: a stated mismatch, labelled below.
        spy_entry = trigger.benchmark_open
    result = TriggerResult(trigger, holding, entry_kind, outcome.status, ret=outcome.ret, exit_day=outcome.exit_day)
    result.failed_by_next_close = failed_by_next_close(trigger, prices)
    if outcome.status == "no_label":
        return result

    def excess_over(exit_day: str, ret: float) -> tuple[float | None, str]:
        if entry_kind == "t1_next_open":
            try:
                return ret - prices.forward_to("SPY", trigger.day, exit_day), "next_open"
            except (KeyError, ZeroDivisionError):
                return None, "no_spy"
        spy_ret, basis = prices.benchmark_return(trigger.day, exit_day, spy_entry)
        if basis == "bar":
            if entry_kind == "trigger_mark":
                basis = "bar_control"  # SPY at the next bar, stock at the trigger mark: not the same moment
            elif trigger.benchmark_aligned is False:
                basis = "bar_misaligned"  # SPY recorded at the scan's slot while the stock rolled forward
        return (None if spy_ret is None else ret - spy_ret), basis

    if outcome.observable and outcome.ret is not None and outcome.exit_day:
        result.excess, result.benchmark_basis = excess_over(outcome.exit_day, outcome.ret)
    if outcome.legacy_ret is not None and outcome.legacy_exit_day:
        result.legacy_excess, _basis = excess_over(outcome.legacy_exit_day, outcome.legacy_ret)
    if result.censored:
        full_exit = prices.sessions[prices.index[trigger.day] + holding]
        result.loss_excess, _basis = excess_over(full_exit, -1.0)
    return result


# --------------------------------------------------------------------------- views and aggregation


def view_filters() -> dict[str, Callable[[Trigger], bool]]:
    return {
        "all": lambda t: True,
        "noorb": lambda t: t.origin != "OPENING_RANGE_BREAKOUT",
        "mkt_gate": lambda t: str(t.market_eligibility or "") not in ("caution", "restricted"),
        "tod": lambda t: t.bucket in ("morning", "afternoon"),
        "alert60": lambda t: (t.alert or 0.0) >= SCORE_SUBSET_MIN,
        "strength60": lambda t: (t.strength or 0.0) >= SCORE_SUBSET_MIN,
    }


def select_view(triggers: list[Trigger], view: str) -> list[Trigger]:
    """The triggers a named view keeps; group views are ``origin:X``, ``bucket:X``, ``market:X``."""

    if view == "dedup":
        seen: set[tuple[str, str]] = set()
        kept = []
        for trigger in triggers:  # already ordered by time
            key = (trigger.ticker, trigger.day)
            if key not in seen:
                seen.add(key)
                kept.append(trigger)
        return kept
    if view == "top10":
        by_day: dict[str, list[Trigger]] = defaultdict(list)
        for trigger in triggers:
            by_day[trigger.day].append(trigger)
        kept = []
        for day in sorted(by_day):
            ranked = sorted(by_day[day], key=lambda t: (-(t.alert or 0.0), t.as_of, t.ticker))
            kept.extend(ranked[:TOP_PER_DAY])
        return kept
    if view == "t1":
        return [t for t in triggers if str(t.t1_status or "") == "met"]
    if view == "confirmed":
        # 修订 6: entry at the first bar after the first CONFIRMED transition; the entry day is
        # the day of that scan, so a next-day confirmation of a carry-over event counts there.
        kept = []
        for t in triggers:
            if t.confirmed_at is None or t.confirmed_day is None:
                continue
            copy = replace(
                t, day=t.confirmed_day, as_of=t.confirmed_at, next_bar_open=t.confirmed_next_bar_open,
                benchmark_open=t.confirmed_benchmark_open, minute=_et_minute(t.confirmed_at),
                next_bar_delay=t.confirmed_next_bar_delay, entry_at=t.confirmed_entry_at,
                benchmark_aligned=t.confirmed_benchmark_aligned, benchmark_delay=None,
            )
            kept.append(copy)
        return sorted(kept, key=lambda t: (t.as_of, t.event_id))
    if view == "chaseable":
        return [t for t in triggers if not t.extended_at_trigger]
    if view == "extended":
        return [t for t in triggers if t.extended_at_trigger]
    if view == "extended_by_next_scan":  # descriptive only (修订 7): uses information after the entry
        return [t for t in triggers if t.extended_by_next_scan]
    if ":" in view:
        kind, value = view.split(":", 1)
        attribute = {"origin": "origin", "bucket": "bucket", "market": "market_state"}[kind]
        return [t for t in triggers if str(getattr(t, attribute) or "") == value]
    return [t for t in triggers if view_filters()[view](t)]


def group_views(triggers: list[Trigger]) -> list[str]:
    names = []
    for kind, attribute in (("origin", "origin"), ("bucket", "bucket"), ("market", "market_state")):
        for value in sorted({str(getattr(t, attribute) or "") for t in triggers}):
            if value:
                names.append(f"{kind}:{value}")
    return names


def day_points(results: list[TriggerResult]) -> list[dict[str, Any]]:
    """One point per day: equal-weighted excess of observable triggers plus the bounds."""

    by_day: dict[str, list[TriggerResult]] = defaultdict(list)
    for result in results:
        by_day[result.trigger.day].append(result)
    points = []
    for day in sorted(by_day):
        rows = by_day[day]
        if any(r.status == "no_label" for r in rows):
            continue
        observable = [r.excess for r in rows if r.observable and r.excess is not None]
        censored = [r for r in rows if r.censored]
        legacy = [r.legacy_excess for r in rows if r.legacy_excess is not None]
        zero = observable + [0.0 for _ in censored]
        loss = observable + [r.loss_excess for r in censored if r.loss_excess is not None]
        statuses = Counter(r.status for r in rows)
        failed = [r.failed_by_next_close for r in rows if r.failed_by_next_close is not None]
        points.append({
            "day": day,
            "n": len(rows),
            "observable": len(observable),
            "mean": statistics.fmean(observable) if observable else None,
            "hits": sum(1 for value in observable if value > 0),
            "legacy": statistics.fmean(legacy) if legacy else None,
            "zero": statistics.fmean(zero) if zero else None,
            "loss": statistics.fmean(loss) if loss else None,
            "failed_next_close": sum(1 for value in failed if value),
            "failed_known": len(failed),
            "statuses": dict(statuses),
            "benchmark_close_fallback": sum(1 for r in rows if r.benchmark_basis == "close"),
            "benchmark_misaligned": sum(1 for r in rows if r.benchmark_basis == "bar_misaligned"),
        })
    return points


def block_bootstrap(values: list[float], block: int, samples: int = BOOTSTRAP_SAMPLES, seed: int = BOOTSTRAP_SEED) -> dict[str, float | None]:
    """Moving-block bootstrap of the mean of a daily series whose h-day windows overlap (修订 7).

    Blocks of ``block`` consecutive observed days are drawn with replacement until the
    resample has the original length; the standard error, the t-like ratio and the 2.5/97.5
    percentiles of the resampled means are returned. Deterministic for a given seed.
    """

    n = len(values)
    block = max(1, int(block))
    starts = n - block + 1
    if n < 3 or starts < 2:  # nothing to resample: too few days for one block to move
        return {"se": None, "t": None, "ci_low": None, "ci_high": None}
    import random

    rng = random.Random(seed)
    mean = statistics.fmean(values)
    means = []
    for _ in range(samples):
        picked: list[float] = []
        while len(picked) < n:
            start = rng.randrange(starts)
            picked.extend(values[start:start + block])
        means.append(statistics.fmean(picked[:n]))
    means.sort()
    se = statistics.pstdev(means)
    return {
        "se": se,
        "t": mean / se if se > 0 else None,
        "ci_low": means[int(0.025 * (samples - 1))],
        "ci_high": means[int(0.975 * (samples - 1))],
    }


def periods(points: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out = {"ALL": points, "P1": [p for p in points if p["day"] <= P1_END], "P2": [p for p in points if p["day"] > P1_END]}
    for year in sorted({p["day"][:4] for p in points}):
        out[year] = [p for p in points if p["day"][:4] == year]
    return out


def _pct(value: float | None) -> float | None:
    return None if value is None else round(100 * value, 3)


def summarize(points: list[dict[str, Any]], holding: int) -> dict[str, Any]:
    means = [p["mean"] for p in points if p["mean"] is not None]
    t_value = newey_west_t(means, NW_LAGS.get(holding, max(0, holding - 1))) if means else None
    boot = block_bootstrap(means, holding) if means else {"se": None, "t": None, "ci_low": None, "ci_high": None}
    observable = sum(p["observable"] for p in points)
    hits = sum(p["hits"] for p in points)
    statuses: Counter = Counter()
    for point in points:
        statuses.update(point["statuses"])
    failed_known = sum(p["failed_known"] for p in points)
    summary = {
        "days": len(points),
        "days_observable": len(means),
        "days_skipped_no_observation": len(points) - len(means),  # 修订 7: skipped, not zero-filled
        "mean_excess_pct": _pct(statistics.fmean(means)) if means else None,
        "t": round(t_value, 2) if t_value is not None else None,
        "nw_lag": NW_LAGS.get(holding, max(0, holding - 1)),
        "boot_se_pct": _pct(boot["se"]),
        "boot_t": round(boot["t"], 2) if boot["t"] is not None else None,
        "boot_ci95_pct": [_pct(boot["ci_low"]), _pct(boot["ci_high"])],
        "legacy_pct": _pct(statistics.fmean([p["legacy"] for p in points if p["legacy"] is not None])) if any(p["legacy"] is not None for p in points) else None,
        "zero_pct": _pct(statistics.fmean([p["zero"] for p in points if p["zero"] is not None])) if any(p["zero"] is not None for p in points) else None,
        "loss_pct": _pct(statistics.fmean([p["loss"] for p in points if p["loss"] is not None])) if any(p["loss"] is not None for p in points) else None,
        "hit_rate": round(hits / observable, 4) if observable else None,
        "triggers": sum(p["n"] for p in points),
        "triggers_per_day": round(sum(p["n"] for p in points) / len(points), 3) if points else None,
        "observable_share": round(observable / sum(p["n"] for p in points), 4) if points and sum(p["n"] for p in points) else None,
        "failed_by_next_close_share": round(sum(p["failed_next_close"] for p in points) / failed_known, 4) if failed_known else None,
        "benchmark_close_fallback": sum(p["benchmark_close_fallback"] for p in points),
        "benchmark_misaligned": sum(p.get("benchmark_misaligned", 0) for p in points),
    }
    for status in OBSERVABLE_STATUSES + CENSORED_STATUSES + ("no_entry_bar",):
        summary[f"n_{status}"] = statuses.get(status, 0)
    return summary


# --------------------------------------------------------------------------- adoption rules


def paired(cand: list[dict[str, Any]], base: list[dict[str, Any]], field_name: str = "mean") -> tuple[list[str], list[float]]:
    """Paired daily differences on the days both sides have an observable value."""

    base_by_day = {p["day"]: p for p in base}
    days, diffs = [], []
    for point in cand:
        other = base_by_day.get(point["day"])
        if other is None or point.get(field_name) is None or other.get(field_name) is None:
            continue
        days.append(point["day"])
        diffs.append(point[field_name] - other[field_name])
    return days, diffs


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def decide(
    points: Mapping[int, list[dict[str, Any]]],
    baseline: Mapping[int, list[dict[str, Any]]],
    *,
    funnel_candidate: bool = False,
    removed_mean: float | None = None,
    kept_mean: float | None = None,
) -> dict[str, Any]:
    """PREREGISTRATION section 9 for one candidate against the baseline (``points`` keyed by holding)."""

    def period_diff(holding: int, period: str, field_name: str = "mean") -> tuple[float | None, int]:
        cand = periods(points[holding]).get(period, [])
        base = periods(baseline[holding]).get(period, [])
        days, diffs = paired(cand, base, field_name)
        return _mean(diffs), len(days)

    h20_p1, n_p1 = period_diff(PRIMARY_HOLDING, "P1")
    h20_p2, n_p2 = period_diff(PRIMARY_HOLDING, "P2")
    years = {}
    years_reported = {}
    for year in COMPLETE_YEARS:
        diff, n = period_diff(PRIMARY_HOLDING, year)
        if diff is None:
            continue
        years_reported[year] = {"diff_pp": round(100 * diff, 3), "paired_days": n}
        if n >= MIN_YEAR_DAYS:
            years[year] = round(100 * diff, 3)
    h5_all, _n5 = period_diff(5, "ALL")
    h63_all, _n63 = period_diff(63, "ALL")
    h20_all, n_all = period_diff(PRIMARY_HOLDING, "ALL")
    paired_days, paired_diffs = paired(periods(points[PRIMARY_HOLDING])["ALL"], periods(baseline[PRIMARY_HOLDING])["ALL"])
    paired_t = newey_west_t(paired_diffs, NW_LAGS[PRIMARY_HOLDING]) if len(paired_diffs) >= 3 else None
    paired_boot = block_bootstrap(paired_diffs, PRIMARY_HOLDING) if len(paired_diffs) >= 3 else {"se": None, "t": None, "ci_low": None, "ci_high": None}
    base_by_day = {p["day"]: p for p in baseline[PRIMARY_HOLDING]}
    cand_by_day = {p["day"]: p for p in points[PRIMARY_HOLDING]}
    bounds = {name: period_diff(PRIMARY_HOLDING, "ALL", name)[0] for name in ("legacy", "zero", "loss")}
    cand_rate = _mean([p["n"] for p in points[PRIMARY_HOLDING]])
    base_rate = _mean([p["n"] for p in baseline[PRIMARY_HOLDING]])
    verdict = {
        "paired_days": n_all,
        "h20_diff_pp": {"ALL": _pct(h20_all), "P1": _pct(h20_p1), "P2": _pct(h20_p2)},
        # Newey-West t (lag 3) of the paired daily h20 differences: a significance figure for the
        # adoption table; the rules themselves are sign-based (section 9) and do not use it.
        "paired_t_h20": round(paired_t, 2) if paired_t is not None else None,
        "paired_nw_lag": NW_LAGS[PRIMARY_HOLDING],
        "paired_boot_t_h20": round(paired_boot["t"], 2) if paired_boot["t"] is not None else None,
        "paired_boot_ci95_pp": [_pct(paired_boot["ci_low"]), _pct(paired_boot["ci_high"])],
        "_paired_h20": [
            {"day": day, "candidate_pct": _pct(cand_by_day[day]["mean"]), "baseline_pct": _pct(base_by_day[day]["mean"]), "diff_pct": _pct(diff)}
            for day, diff in zip(paired_days, paired_diffs)
        ],
        "years_diff_pp": years,
        "years_reported": years_reported,
        "h5_diff_pp": _pct(h5_all),
        "h63_diff_pp": _pct(h63_all),
        "bounds_diff_pp": {name: _pct(value) for name, value in bounds.items()},
        "triggers_per_day": {"candidate": None if cand_rate is None else round(cand_rate, 3), "baseline": None if base_rate is None else round(base_rate, 3)},
        "rule1_both_periods_up": bool(h20_p1 is not None and h20_p2 is not None and h20_p1 > 0 and h20_p2 > 0),
        "rule2_years_better": sum(1 for value in years.values() if value > 0),
        "rule2_years_compared": len(years),
        "rule2_ok": len(years) > 0 and sum(1 for value in years.values() if value > 0) >= math.ceil(0.75 * len(years)),
        "rule3_ok": bool(h5_all is not None and h63_all is not None and h5_all >= -0.005 and h63_all >= -0.01),
        "rule4_ok": bool(cand_rate is not None and base_rate is not None and cand_rate >= 0.5 * base_rate),
        "rule5_ok": bool(h20_all is not None and all(v is not None and (v > 0) == (h20_all > 0) for v in bounds.values())),
    }
    if funnel_candidate:
        verdict["rule6_removed_mean_pp"] = _pct(removed_mean)
        verdict["rule6_kept_mean_pp"] = _pct(kept_mean)
        verdict["rule6_ok"] = bool(removed_mean is not None and kept_mean is not None and removed_mean < kept_mean)
    rules = ["rule1_both_periods_up", "rule2_ok", "rule3_ok", "rule4_ok", "rule5_ok"] + (["rule6_ok"] if funnel_candidate else [])
    verdict["adopt"] = all(bool(verdict[rule]) for rule in rules)
    return verdict


def stage2_verdict(combo: dict[str, Any], singles: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """A combination is adopted only if it passes rules 1-5 and stays within 0.2 pp of the best single in both periods."""

    passing = {name: v for name, v in singles.items() if v.get("adopt")}
    best = max(passing, key=lambda name: passing[name]["h20_diff_pp"]["ALL"] or -math.inf) if passing else None
    out = {"passes_rules_1_to_5": all(bool(combo[r]) for r in ("rule1_both_periods_up", "rule2_ok", "rule3_ok", "rule4_ok", "rule5_ok")),
           "best_single": best, "within_0_2pp": None, "adopt": False}
    if best is not None:
        margins = [
            (combo["h20_diff_pp"][period] or -math.inf) - (passing[best]["h20_diff_pp"][period] or -math.inf)
            for period in ("P1", "P2")
        ]
        out["within_0_2pp"] = all(margin >= -0.2 for margin in margins)
        out["adopt"] = bool(out["passes_rules_1_to_5"] and out["within_0_2pp"])
    return out


# --------------------------------------------------------------------------- driver


@dataclass
class VariantEvaluation:
    name: str
    triggers: list[Trigger]
    funnel: dict[str, DayFunnel]
    info: dict[str, Any]
    results: dict[tuple[str, int, str], list[TriggerResult]] = field(default_factory=dict)  # (view, holding, entry)
    points: dict[tuple[str, int, str], list[dict[str, Any]]] = field(default_factory=dict)


def evaluate_variant(name: str, triggers: list[Trigger], funnel: dict[str, DayFunnel], info: dict[str, Any],
                     prices: RadarPrices, *, views: Iterable[str] | None = None) -> VariantEvaluation:
    evaluation = VariantEvaluation(name, triggers, funnel, info)
    names = list(views) if views is not None else ["all", "dedup", "top10", *FILTER_VIEWS, "alert60", "strength60", "t1",
                                                     *SWITCH_VIEWS, "extended", "extended_by_next_scan", *group_views(triggers)]
    # 修订 7: the cache key is the full entry specification, so a view that re-enters an event
    # (confirmed) never receives another view's result and the view order cannot matter.
    cache: dict[tuple, TriggerResult] = {}
    for view in names:
        selected = select_view(triggers, view)
        entry_kinds = ["t1_next_open"] if view == "t1" else (["next_bar", "trigger_mark"] if view == "all" else ["next_bar"])
        for entry_kind in entry_kinds:
            for holding in HOLDINGS:
                results = []
                for trigger in selected:
                    key = (trigger.event_id, holding, entry_kind, trigger.day, trigger.as_of,
                           trigger.next_bar_open, trigger.benchmark_open, trigger.trigger_price)
                    result = cache.get(key)
                    if result is None:
                        result = evaluate_trigger(trigger, prices, holding, entry_kind)
                        cache[key] = result
                    results.append(result)
                evaluation.results[(view, holding, entry_kind)] = results
                evaluation.points[(view, holding, entry_kind)] = day_points(results)
    return evaluation


def funnel_summary(funnel: Mapping[str, DayFunnel]) -> dict[str, Any]:
    days = list(funnel.values())
    if not days:
        return {"days": 0}
    scans = sum(d.scans for d in days)
    return {
        "days": len(days),
        "scans": scans,
        "per_scan": {
            "prefilter": round(sum(d.prefilter for d in days) / scans, 2),
            "listed": round(sum(d.listed for d in days) / scans, 2),
            "structures": round(sum(d.structures for d in days) / scans, 2),
            "events": round(sum(d.events for d in days) / scans, 2),
            "cut_150": round(sum(d.cut_150 for d in days) / scans, 3),
            "cut_60": round(sum(d.cut_60 for d in days) / scans, 3),
            "cut_30": round(sum(d.cut_30 for d in days) / scans, 3),
        },
        "days_with_cut_150": sum(1 for d in days if d.cut_150),
        "days_with_cut_60": sum(1 for d in days if d.cut_60),
        "days_with_cut_30": sum(1 for d in days if d.cut_30),
        "triggers_per_day": round(sum(d.triggers for d in days) / len(days), 3),
    }


def metric_rows(evaluation: VariantEvaluation) -> list[dict[str, Any]]:
    rows = []
    for (view, holding, entry_kind), points in sorted(evaluation.points.items()):
        for period, subset in periods(points).items():
            if not subset:
                continue
            rows.append({"variant": evaluation.name, "view": view, "entry": entry_kind, "holding": holding,
                         "period": period, **summarize(subset, holding)})
    return rows


def removed_and_kept(candidate: VariantEvaluation, baseline: VariantEvaluation, view: str = "all") -> tuple[float | None, float | None]:
    """h20 excess of the baseline's triggers the candidate dropped versus those it kept (rule 6), on ``view``."""

    kept_keys = {t.key for t in select_view(candidate.triggers, view)}
    removed, kept = [], []
    for result in baseline.results.get((view, PRIMARY_HOLDING, "next_bar"), []):
        if not result.observable or result.excess is None:
            continue
        (kept if result.trigger.key in kept_keys else removed).append(result.excess)
    return _mean(removed), _mean(kept)


def metric_view(name: str) -> str:
    """The view a candidate is judged on: confirm3 on confirmed entries, chase15 on the chaseable set."""

    for part in name.split("+"):
        if part in METRIC_VIEW:
            return METRIC_VIEW[part]
    return "all"


def decisions(evaluations: Mapping[str, VariantEvaluation], baseline_name: str, stage2: Mapping[str, str] | None = None) -> dict[str, Any]:
    baseline = evaluations[baseline_name]
    out: dict[str, Any] = {"baseline": baseline_name, "rules": RULES["adoption"], "variants": {}, "filters": {}, "stage2": {}}
    for name, evaluation in evaluations.items():
        if name == baseline_name:
            continue
        view = metric_view(name)
        points = {h: evaluation.points[(view, h, "next_bar")] for h in HOLDINGS}
        base_points = {h: baseline.points[(view, h, "next_bar")] for h in HOLDINGS}
        parts = name.split("+")
        funnel_candidate = any(part in FUNNEL_VARIANTS for part in parts) or "chase15" in parts
        removed_mean, kept_mean = removed_and_kept(evaluation, baseline, view) if funnel_candidate else (None, None)
        verdict = decide(points, base_points, funnel_candidate=funnel_candidate, removed_mean=removed_mean, kept_mean=kept_mean)
        verdict["metric_view"] = view
        if view == "confirmed":
            verdict["confirmed_share"] = {
                "candidate": round(len(select_view(evaluation.triggers, "confirmed")) / len(evaluation.triggers), 4) if evaluation.triggers else None,
                "baseline": round(len(select_view(baseline.triggers, "confirmed")) / len(baseline.triggers), 4) if baseline.triggers else None,
            }
        if view == "chaseable":
            verdict["extended_share"] = {
                "candidate": round(sum(1 for t in evaluation.triggers if t.extended_at_trigger) / len(evaluation.triggers), 4) if evaluation.triggers else None,
                "baseline": round(sum(1 for t in baseline.triggers if t.extended_at_trigger) / len(baseline.triggers), 4) if baseline.triggers else None,
            }
        out["variants"][name] = verdict
    base_points = {h: baseline.points[("all", h, "next_bar")] for h in HOLDINGS}
    for view in FILTER_VIEWS:
        points = {h: baseline.points[(view, h, "next_bar")] for h in HOLDINGS}
        out["filters"][view] = decide(points, base_points)
    for combo, parts in (stage2 or {}).items():
        if combo in out["variants"]:
            singles = {part: out["variants"][part] for part in parts.split("+") if part in out["variants"]}
            out["stage2"][combo] = stage2_verdict(out["variants"][combo], singles)
    return out


# --------------------------------------------------------------------------- output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def event_rows(evaluation: VariantEvaluation, holding: int = PRIMARY_HOLDING) -> list[dict[str, Any]]:
    rows = []
    control = {r.trigger.event_id: r for r in evaluation.results.get(("all", holding, "trigger_mark"), [])}
    for result in evaluation.results.get(("all", holding, "next_bar"), []):
        t = result.trigger
        rows.append({
            "variant": evaluation.name, "event_id": t.event_id, "ticker": t.ticker, "day": t.day, "as_of": t.as_of,
            "origin": t.origin, "setup": t.setup, "bucket": t.bucket, "market_state": t.market_state,
            "alert": t.alert, "strength": t.strength, "trigger_price": t.trigger_price, "next_bar_open": t.next_bar_open,
            "status": result.status, "exit_day": result.exit_day, "ret": result.ret, "excess": result.excess,
            "benchmark_basis": result.benchmark_basis,
            "control_excess": control[t.event_id].excess if t.event_id in control else None,
            "failed_by_next_close": result.failed_by_next_close, "t1_status": t.t1_status,
        })
    return rows


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def readme_tables(rows: list[dict[str, Any]], verdicts: dict[str, Any], funnels: Mapping[str, dict[str, Any]], baseline: str,
                  completion: Mapping[str, Any] | None = None) -> str:
    def find(variant: str, view: str, entry: str, holding: int, period: str) -> dict[str, Any] | None:
        return next((r for r in rows if (r["variant"], r["view"], r["entry"], r["holding"], r["period"]) == (variant, view, entry, holding, period)), None)

    variants = sorted({r["variant"] for r in rows}, key=lambda n: (n != baseline, n))
    years = sorted({r["period"] for r in rows if r["period"].isdigit()})
    lines: list[str] = []
    if completion:
        lines += ["## 评估覆盖的交易日", "", "| 年份 | 完成的天数 |", "|---|---|"]
        lines += [f"| {year} | {count} |" for year, count in sorted(completion.get("per_year", {}).items())]
        lines += [f"| P1（到 {P1_END}） | {completion.get('p1_days')} |", f"| P2 | {completion.get('p2_days')} |",
                  f"| 合计 | {len(completion.get('days', []))} |", "",
                  f"不是每个配置都完成的天：{len(completion.get('dropped_not_in_every_variant', []))}；有降级扫描而剔除的天：{len(completion.get('dropped_degraded', []))}。", ""]
    lines += ["## 主指标：触发后 20 个交易日对 SPY 的超额（按日等权，百分点；括号内 NW t，滞后 19；块自助 t 见 metrics.csv 的 boot_t）", "",
             "| 配置 | 全期 | P1 | P2 | " + " | ".join(years) + " | 每日触发 | 命中率 |", "|---|---|---|---|" + "---|" * len(years) + "---|---|"]
    for variant in variants:
        cells = []
        for period in ("ALL", "P1", "P2", *years):
            row = find(variant, "all", "next_bar", PRIMARY_HOLDING, period)
            cells.append("—" if row is None or row["mean_excess_pct"] is None else f"{row['mean_excess_pct']:.2f} ({_fmt(row['t'])})")
        row_all = find(variant, "all", "next_bar", PRIMARY_HOLDING, "ALL")
        lines.append(f"| {variant} | " + " | ".join(cells) + f" | {_fmt(row_all['triggers_per_day'] if row_all else None)} | {_fmt(row_all['hit_rate'] if row_all else None)} |")
    lines += ["", "## 次指标（全期）：1、5、63 日超额，触发价入场对照，次日收盘前失败的比例，三个删失情景", "",
              "| 配置 | 1 日 | 5 日 | 63 日 | 20 日（触发价入场） | 次日前失败 | legacy / zero / loss |", "|---|---|---|---|---|---|---|"]
    for variant in variants:
        h1 = find(variant, "all", "next_bar", 1, "ALL"); h5 = find(variant, "all", "next_bar", 5, "ALL"); h63 = find(variant, "all", "next_bar", 63, "ALL")
        ctrl = find(variant, "all", "trigger_mark", PRIMARY_HOLDING, "ALL"); h20 = find(variant, "all", "next_bar", PRIMARY_HOLDING, "ALL")
        bounds = "—" if h20 is None else f"{_fmt(h20['legacy_pct'])} / {_fmt(h20['zero_pct'])} / {_fmt(h20['loss_pct'])}"
        lines.append(f"| {variant} | {_fmt(h1 and h1['mean_excess_pct'])} | {_fmt(h5 and h5['mean_excess_pct'])} | {_fmt(h63 and h63['mean_excess_pct'])} | {_fmt(ctrl and ctrl['mean_excess_pct'])} | {_fmt(h20 and h20['failed_by_next_close_share'])} | {bounds} |")
    lines += ["", f"## 基线 {baseline} 的切分（20 日超额，全期）", "", "| 视图 | 天数 | 触发数 | 超额 | NW t | 命中率 |", "|---|---|---|---|---|---|"]
    for row in sorted((r for r in rows if r["variant"] == baseline and r["holding"] == PRIMARY_HOLDING and r["period"] == "ALL" and r["entry"] in ("next_bar", "t1_next_open")), key=lambda r: r["view"]):
        lines.append(f"| {row['view']} | {row['days']} | {row['triggers']} | {_fmt(row['mean_excess_pct'])} | {_fmt(row['t'])} | {_fmt(row['hit_rate'])} |")
    lines += ["", "## 漏斗（每次扫描平均）", "", "| 配置 | 150 行前 | 列出 | 有基底 | 事件 | 超 150 | 超 60 | 超 30 | 每日触发 |", "|---|---|---|---|---|---|---|---|---|"]
    for variant in variants:
        f = funnels.get(variant) or {}
        p = f.get("per_scan") or {}
        lines.append(f"| {variant} | {_fmt(p.get('prefilter'))} | {_fmt(p.get('listed'))} | {_fmt(p.get('structures'))} | {_fmt(p.get('events'))} | {_fmt(p.get('cut_150'))} | {_fmt(p.get('cut_60'))} | {_fmt(p.get('cut_30'))} | {_fmt(f.get('triggers_per_day'))} |")
    lines += ["", "## 取舍（预登记第 9 节，与基线按共同日配对，差值为百分点；配对 t 是 NW 滞后 19，块自助 t 的块长 20，都只作报告）", "",
              "| 候选 | 配对天数 | 20 日差 全期 / P1 / P2 | 配对 NW t | 块自助 t | 年份更好 | 5 日差 | 63 日差 | 三情景同号 | 每日触发 | 采纳 |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, v in [*verdicts.get("variants", {}).items(), *verdicts.get("filters", {}).items()]:
        d = v["h20_diff_pp"]
        label = name if v.get("metric_view", "all") == "all" else f"{name}（视图 {v['metric_view']}）"
        lines.append(f"| {label} | {v['paired_days']} | {_fmt(d['ALL'])} / {_fmt(d['P1'])} / {_fmt(d['P2'])} | {_fmt(v.get('paired_t_h20'))} | {_fmt(v.get('paired_boot_t_h20'))} | {v['rule2_years_better']}/{v['rule2_years_compared']} | {_fmt(v['h5_diff_pp'])} | {_fmt(v['h63_diff_pp'])} | {'是' if v['rule5_ok'] else '否'} | {_fmt(v['triggers_per_day']['candidate'])} 对 {_fmt(v['triggers_per_day']['baseline'])} | {'是' if v['adopt'] else '否'} |")
    for name, v in verdicts.get("stage2", {}).items():
        lines.append(f"| {name}（组合） | — | 规则 1 到 5 {'过' if v['passes_rules_1_to_5'] else '不过'}；最好单项 {v['best_single']}；两段差距在 0.2 内 {_fmt(v['within_0_2pp'])} | | | | | | | | {'是' if v['adopt'] else '否'} |")
    return "\n".join(lines) + "\n"


def _read_variant_job(args: tuple[list[str], str, str | None, str | None]) -> tuple[str, tuple[list[Trigger], dict[str, DayFunnel], dict[str, Any]]]:
    replay_dirs, name, minute_store, db_dir = args
    entry_lookup = benchmark_lookup = None
    if minute_store is not None:
        from .stores import MinuteStore

        entry_lookup, benchmark_lookup = store_lookups(MinuteStore(minute_store))
    return name, read_variant([Path(p) for p in replay_dirs], name, entry_lookup=entry_lookup, benchmark_lookup=benchmark_lookup,
                              db_dir=None if db_dir is None else Path(db_dir))


def run(
    replay_dirs: list[Path], variants: list[str], db_path: Path, out: Path, *, baseline: str = "baseline",
    directory: Path | None = None, minute_store: Path | None = None, stage2: Mapping[str, str] | None = None,
    write_events: bool = True, db_dir: Path | None = None, workers: int = 1,
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    jobs = [([str(p) for p in replay_dirs], name, None if minute_store is None else str(minute_store), None if db_dir is None else str(db_dir)) for name in variants]
    if workers > 1 and len(variants) > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=min(workers, len(variants))) as pool:
            loaded = dict(pool.map(_read_variant_job, jobs))
    else:
        loaded = dict(_read_variant_job(job) for job in jobs)
    completion = completed_days(loaded)
    if not completion["days"]:
        raise SystemExit("no day was completed by every variant")
    loaded = {name: (*restrict(triggers, funnel, completion["days"]), info) for name, (triggers, funnel, info) in loaded.items()}
    if not any(triggers for triggers, _f, _i in loaded.values()):
        raise SystemExit("no triggers in the replay outputs")
    start = completion["days"][0]
    prices = RadarPrices(connection, start, V16.Directory(directory) if directory else None)
    evaluations = {name: evaluate_variant(name, *loaded[name], prices) for name in variants}
    rows = [row for evaluation in evaluations.values() for row in metric_rows(evaluation)]
    write_csv(out / "metrics.csv", rows)
    verdicts = decisions(evaluations, baseline, stage2) if baseline in evaluations else {}
    # The paired daily series behind every verdict go to CSV files, not into the JSON pack.
    for group in ("variants", "filters"):
        for name, verdict in verdicts.get(group, {}).items():
            series = verdict.pop("_paired_h20", None)
            if series:
                write_csv(out / f"paired_h20_{name}.csv", series)
    funnels = {name: funnel_summary(e.funnel) for name, e in evaluations.items()}
    if write_events:
        write_csv(out / "events_h20.csv", [row for e in evaluations.values() for row in event_rows(e)])
    pack = {
        "rules": RULES,
        "inputs": {"replay_dirs": [str(p) for p in replay_dirs], "variants": variants, "db": str(db_path),
                   "directory": None if directory is None else str(directory), "minute_store": None if minute_store is None else str(minute_store),
                   "db_dir": None if db_dir is None else str(db_dir), "identity_verification": directory is not None},
        "completed_days": completion,
        "coverage": {name: {**e.info, "days_evaluated": len(e.funnel), "days_first": min(e.funnel) if e.funnel else None,
                            "days_last": max(e.funnel) if e.funnel else None,
                            "triggers_without_next_bar_open": sum(1 for t in e.triggers if t.next_bar_open is None),
                            "triggers_without_benchmark_open": sum(1 for t in e.triggers if t.benchmark_open is None),
                            "triggers_entry_delayed": sum(1 for t in e.triggers if (t.next_bar_delay or 0) > 0),
                            "triggers_benchmark_delayed": sum(1 for t in e.triggers if (t.benchmark_delay or 0) > 0),
                            "triggers_confirmed": sum(1 for t in e.triggers if t.confirmed_at is not None),
                            "triggers_extended_at_trigger": sum(1 for t in e.triggers if t.extended_at_trigger),
                            "triggers_extended_by_next_scan": sum(1 for t in e.triggers if t.extended_by_next_scan),
                            "triggers_benchmark_misaligned": sum(1 for t in e.triggers if t.benchmark_aligned is False),
                            "confirmed_benchmark_misaligned": sum(1 for t in e.triggers if t.confirmed_benchmark_aligned is False)}
                     for name, e in evaluations.items()},
        "funnel": funnels,
        "metrics": rows,
        "decisions": verdicts,
    }
    (out / "result_pack.json").write_text(json.dumps(pack, indent=1, default=str))
    (out / "README_tables.md").write_text(readme_tables(rows, verdicts, funnels, baseline, completion))
    return pack
