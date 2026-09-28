"""Forward returns and pre-registered metrics for replayed screener lists.

Entry at the open of the session after T, exit at the close h sessions after T, both raw
prices from the replay cache, split-adjusted with the same frozen split table. Excess is
against SPY over the identical window. The primary metric fills pre-decided empty top-20
slots with SPY (excess 0), so a variant cannot win by listing fewer names.

Observation rules (v1.7 PREREGISTRATION.md 修订 2 and 修订 3; the 2026-09-28 review):

* A name is *observable* when its exit can be verified from the data: a bar on T+h
  (``ok``; interior missing bars are held through when the weekly point-in-time
  directory shows the same security at entry and exit, ``ok_bridged``); no bar on T+h
  but a close within ``EXIT_LAG_SESSIONS`` sessions after it (``ok_late_exit``, SPY over
  the same actual window); or the same security trading under a new ticker
  (``renamed``, followed through ``composite_figi`` / ``cik``).
* Otherwise the name is *censored*: no sale is invented. ``censored_temporary`` (the
  ticker trades again within ``LOOKAHEAD_SESSIONS`` of its last bar), ``censored_terminal``
  (it does not, or the directory shows a different security), ``censored_unknown`` (the
  data ends first), ``censored_unverified`` (no directory to verify identity).
* ``no_entry_bar`` (no bar at T+1) and ``identity_uncertain`` (a case collision in the
  replay's ``source_tickers``) are unobservable selected names, reported apart from
  pre-decided empty slots (``top - listed``).
* Primary per-date metric: ``slot = sum(observable excess) / (observable + empty slots)``;
  unobservable names leave the denominator and are counted. Sensitivity bounds value a
  censored name at its last observed close (``slot_legacy``: the pre-fix behaviour, which
  also silently counted ``no_entry_bar`` as zero excess over ``top``), at zero excess
  (``slot_zero``) and as a total loss (``slot_loss``).
* ``no_label`` (the horizon runs past the data) still excludes the date.

    python evaluate.py --db replay.sqlite --replay /content/replay/stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --out results/stage1_reeval
"""
# No ``from __future__ import annotations``: this file is loaded by path (spec_from_file_location)
# from the sibling scripts and the tests, and dataclasses resolve string annotations through
# sys.modules, where such a module may not be registered.
import argparse
import csv
import gzip
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

HOLDINGS = (5, 20, 63)
TOPS = (10, 20)
P1_END = "2024-12-31"
VIEWS = ("short", "mid", "long")
# Pre-registered constants (v1.7 PREREGISTRATION.md 修订 3), recorded in every output.
EXIT_LAG_SESSIONS = 5       # a missing bar on T+h sells at the first close within this many sessions after
LOOKAHEAD_SESSIONS = 63     # temporary vs terminal: does the ticker trade again within this window after its last bar
RENAME_LAG_SESSIONS = 5     # a renamed ticker's first bar must fall within this many sessions after the old last bar
RENAME_PRICE_RATIO = (0.5, 2.0)  # first new open / last old close outside this range is not a verifiable continuation
OBSERVABLE_STATUSES = ("ok", "ok_bridged", "ok_late_exit", "renamed")
CENSORED_STATUSES = ("censored_temporary", "censored_terminal", "censored_unknown", "censored_unverified")
UNOBSERVABLE_STATUSES = CENSORED_STATUSES + ("no_entry_bar", "identity_uncertain")
BOUNDS = ("legacy", "zero", "loss")
RULES = {
    "exit_lag_sessions": EXIT_LAG_SESSIONS, "lookahead_sessions": LOOKAHEAD_SESSIONS,
    "rename_lag_sessions": RENAME_LAG_SESSIONS, "rename_price_ratio": list(RENAME_PRICE_RATIO),
    "observable_statuses": list(OBSERVABLE_STATUSES), "censored_statuses": list(CENSORED_STATUSES),
    "primary": "sum(observable excess) / (observable + pre-decided empty slots); unobservable names removed",
    "bounds": {"legacy": "censored at the last observed close over the shortened window (pre-fix behaviour)",
               "zero": "censored at zero excess, kept in the denominator",
               "loss": "censored as a total loss (return -1) against SPY over the full window"},
}


def newey_west_t(values: list[float], lag: int) -> float | None:
    n = len(values)
    if n < 3:
        return None
    mean = sum(values) / n
    centered = [value - mean for value in values]
    variance = sum(value * value for value in centered) / n
    for step in range(1, min(lag, n - 1) + 1):
        weight = 1 - step / (lag + 1)
        variance += 2 * weight * sum(centered[i] * centered[i - step] for i in range(step, n)) / n
    return mean / math.sqrt(variance / n) if variance > 0 else None


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


class Directory:
    """Weekly point-in-time directory snapshots (``YYYY-MM-DD.json.gz`` with ``results`` rows).

    The snapshot for a day is the latest label on or before it, as the replay chose the
    universe. Identity is ``composite_figi`` when both rows carry one, else ``cik``.
    """

    def __init__(self, folder: Path | str) -> None:
        self.folder = Path(folder)
        self.labels = sorted(path.name[:-8] for path in self.folder.glob("20*.json.gz"))
        if not self.labels:
            raise ValueError(f"no directory snapshots under {self.folder}")
        self._rows: dict[str, dict[str, dict]] = {}
        self._by_identity: dict[str, dict[str, list[str]]] = {}

    def label_for(self, day: str) -> str | None:
        eligible = [label for label in self.labels if label <= day]
        return eligible[-1] if eligible else None

    def labels_after(self, day: str) -> list[str]:
        return [label for label in self.labels if label > day]

    def rows(self, label: str) -> dict[str, dict]:
        if label not in self._rows:
            payload = json.loads(gzip.open(self.folder / f"{label}.json.gz").read())
            rows = {str(row.get("ticker") or ""): row for row in payload["results"]}
            self._rows[label] = rows
            by_identity: dict[str, list[str]] = defaultdict(list)
            for ticker, row in rows.items():
                for key in self.identity_keys(row):
                    by_identity[key].append(ticker)
            self._by_identity[label] = dict(by_identity)
        return self._rows[label]

    def row(self, ticker: str, day: str) -> dict | None:
        label = self.label_for(day)
        return None if label is None else self.rows(label).get(ticker)

    @staticmethod
    def identity_keys(row: Mapping[str, Any] | None) -> list[str]:
        if not row:
            return []
        keys = []
        figi = _text(row.get("composite_figi"))
        cik = _text(row.get("cik"))
        if figi:
            keys.append(f"figi:{figi}")
        if cik:
            keys.append(f"cik:{cik}")
        return keys

    @staticmethod
    def same_identity(left: Mapping[str, Any] | None, right: Mapping[str, Any] | None) -> bool | None:
        """True / False when verifiable, None when either side lacks a comparable identifier."""
        if not left or not right:
            return None
        left_figi, right_figi = _text(left.get("composite_figi")), _text(right.get("composite_figi"))
        if left_figi and right_figi:
            return left_figi == right_figi
        left_cik, right_cik = _text(left.get("cik")), _text(right.get("cik"))
        if left_cik and right_cik:
            return left_cik == right_cik
        return None

    def successors(self, row: Mapping[str, Any], entry_label: str, label: str) -> list[str]:
        """Tickers in ``label`` with ``row``'s identity that were not listed under that ticker in ``entry_label``."""
        rows = self.rows(label)
        entry_rows = self.rows(entry_label)
        old = str(row.get("ticker") or "")
        out: list[str] = []
        for key in self.identity_keys(row):
            for ticker in self._by_identity[label].get(key, ()):
                if ticker == old or ticker in entry_rows or ticker in out:
                    continue
                if self.same_identity(row, rows[ticker]):
                    out.append(ticker)
        return out


@dataclass
class Outcome:
    ticker: str
    status: str
    ret: float | None = None
    entry_day: str | None = None
    exit_day: str | None = None          # actual exit close used for ``ret`` (observable statuses)
    last_day: str | None = None          # last observed bar on or before T+h
    first_gap_day: str | None = None     # first session in [T+1, T+h] without a bar (None when there is none)
    exit_lag: int = 0                    # sessions after T+h for ok_late_exit
    interior_gaps: int = 0               # missing bars strictly inside the holding that were held through
    followed_ticker: str | None = None   # renamed: the ticker the position was followed into
    legacy_ret: float | None = None      # pre-fix value: sold at the last close before the first gap
    legacy_status: str = ""
    legacy_exit_day: str | None = None
    detail: dict = field(default_factory=dict)

    @property
    def observable(self) -> bool:
        return self.status in OBSERVABLE_STATUSES

    @property
    def censored(self) -> bool:
        return self.status in CENSORED_STATUSES


class Prices:
    def __init__(self, connection: sqlite3.Connection, start: str, directory: Optional[Directory] = None) -> None:
        self.connection = connection
        self.start = start
        self.directory = directory
        self.sessions = [row[0] for row in connection.execute(
            "SELECT session_date FROM market_sessions ORDER BY session_date")]
        self.index = {day: i for i, day in enumerate(self.sessions)}
        self.splits: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for ticker, execution, split_from, split_to in connection.execute(
                "SELECT ticker, execution_date, split_from, split_to FROM splits"):
            self.splits[ticker].append((execution, split_from, split_to))
        self.bars: dict[str, dict[str, tuple[float, float]]] = {}
        self._outcomes: dict[tuple[str, str, int], Outcome] = {}

    def series(self, ticker: str) -> dict[str, tuple[float, float]]:
        if ticker not in self.bars:
            self.bars[ticker] = {day: (open_, close) for day, open_, close in self.connection.execute(
                "SELECT session_date, open, close FROM raw_daily_bars WHERE ticker = ? AND session_date >= ?",
                (ticker, self.start))}
        return self.bars[ticker]

    def split_factor(self, ticker: str, entry_day: str, exit_day: str) -> float:
        """Multiply an entry price by this to compare it with a close on ``exit_day``."""
        factor = 1.0
        for execution, split_from, split_to in self.splits.get(ticker, ()):
            if entry_day < execution <= exit_day:
                factor *= split_from / split_to
        return factor

    def forward_legacy(self, ticker: str, signal_day: str, holding: int) -> tuple[float | None, str, str | None]:
        """The pre-fix rule, kept for the legacy bound and the legacy curves.

        Stops at the first missing bar inside [T+1, T+h] and "sells" at the last close
        before the gap (``gap_exit``). Not an executable exit; see the module docstring.
        """
        i = self.index[signal_day]
        entry_i, exit_i = i + 1, i + holding
        if exit_i >= len(self.sessions):
            return None, "no_label", None
        bars = self.series(ticker)
        entry_day = self.sessions[entry_i]
        if entry_day not in bars or not bars[entry_day][0]:
            return None, "no_entry_bar", None
        last = entry_i
        for j in range(entry_i, exit_i + 1):
            if self.sessions[j] in bars and bars[self.sessions[j]][1]:
                last = j
            else:
                break
        exit_day = self.sessions[last]
        entry_price = bars[entry_day][0] * self.split_factor(ticker, entry_day, exit_day)
        status = "ok" if last == exit_i else "gap_exit"
        return bars[exit_day][1] / entry_price - 1, status, exit_day

    def forward_to(self, ticker: str, signal_day: str, exit_day: str) -> float:
        """Return for the benchmark over a holding that already ended on ``exit_day``."""
        entry_day = self.sessions[self.index[signal_day] + 1]
        bars = self.series(ticker)
        entry_price = bars[entry_day][0] * self.split_factor(ticker, entry_day, exit_day)
        return bars[exit_day][1] / entry_price - 1

    def _has_bar(self, bars: Mapping[str, tuple[float, float]], j: int) -> bool:
        day = self.sessions[j]
        return day in bars and bool(bars[day][1])

    def _first_bar_after(self, bars: Mapping[str, tuple[float, float]], start_i: int, max_lag: int) -> int | None:
        """Index of the first bar in (start_i, start_i + max_lag], within the data."""
        for j in range(start_i + 1, min(start_i + max_lag, len(self.sessions) - 1) + 1):
            if self._has_bar(bars, j):
                return j
        return None

    def _identity_check(self, ticker: str, entry_day: str, later_day: str) -> bool | None:
        if self.directory is None:
            return None
        return Directory.same_identity(self.directory.row(ticker, entry_day), self.directory.row(ticker, later_day))

    def _renamed(self, ticker: str, entry_day: str, last_i: int, exit_i: int) -> Outcome | None:
        """Follow the same security into a new ticker when the directory and the bars verify it."""
        if self.directory is None:
            return None
        entry_label = self.directory.label_for(entry_day)
        old_row = None if entry_label is None else self.directory.rows(entry_label).get(ticker)
        if not old_row or not Directory.identity_keys(old_row):
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
                # Splits filed under the new ticker on its first day explain a level change.
                for execution, split_from, split_to in self.splits.get(candidate, ()):
                    if last_day < execution <= first_day:
                        ratio *= split_to / split_from
                if not RENAME_PRICE_RATIO[0] <= ratio <= RENAME_PRICE_RATIO[1]:
                    continue
                entry_price = old_bars[entry_day][0] * self.split_factor(ticker, entry_day, last_day) \
                    * self.split_factor(candidate, last_day, exit_day)
                return Outcome(ticker, "renamed", new_bars[exit_day][1] / entry_price - 1, entry_day, exit_day,
                               last_day, None, exit_j - exit_i, 0, candidate,
                               detail={"first_new_day": first_day, "price_ratio": round(ratio, 4)})
        return None

    def _censored(self, ticker: str, entry_day: str, last_i: int) -> str:
        """temporary / terminal / unknown after the last observed bar, using the lookahead window."""
        bars = self.series(ticker)
        later = self._first_bar_after(bars, last_i, LOOKAHEAD_SESSIONS)
        if later is not None:
            same = self._identity_check(ticker, entry_day, self.sessions[later])
            return "censored_terminal" if same is False else "censored_temporary"
        if last_i + LOOKAHEAD_SESSIONS > len(self.sessions) - 1:
            return "censored_unknown"
        return "censored_terminal"

    def observe(self, ticker: str, signal_day: str, holding: int) -> Outcome:
        """Verified outcome of buying ``ticker`` at T+1 open and holding ``holding`` sessions."""
        key = (ticker, signal_day, holding)
        cached = self._outcomes.get(key)
        if cached is not None:
            return cached
        outcome = self._observe(ticker, signal_day, holding)
        legacy_ret, legacy_status, legacy_exit = self.forward_legacy(ticker, signal_day, holding)
        outcome.legacy_ret, outcome.legacy_status, outcome.legacy_exit_day = legacy_ret, legacy_status, legacy_exit
        self._outcomes[key] = outcome
        return outcome

    def _observe(self, ticker: str, signal_day: str, holding: int) -> Outcome:
        i = self.index[signal_day]
        entry_i, exit_i = i + 1, i + holding
        if exit_i >= len(self.sessions):
            return Outcome(ticker, "no_label")
        bars = self.series(ticker)
        entry_day = self.sessions[entry_i]
        if entry_day not in bars or not bars[entry_day][0]:
            return Outcome(ticker, "no_entry_bar", entry_day=entry_day)
        present = [j for j in range(entry_i, exit_i + 1) if self._has_bar(bars, j)]
        if not present:
            return Outcome(ticker, "no_entry_bar", entry_day=entry_day)
        last_i = present[-1]
        last_day = self.sessions[last_i]
        missing = [j for j in range(entry_i, exit_i + 1) if not self._has_bar(bars, j)]
        first_gap_day = self.sessions[missing[0]] if missing else None
        interior_gaps = len(missing) - (0 if last_i == exit_i else exit_i - last_i)
        exit_j: int | None = exit_i if last_i == exit_i else self._first_bar_after(bars, exit_i, EXIT_LAG_SESSIONS)
        if exit_j is not None:
            exit_day = self.sessions[exit_j]
            gapped = interior_gaps > 0 or exit_j != exit_i
            if gapped:
                same = self._identity_check(ticker, entry_day, exit_day)
                if same is None:
                    return Outcome(ticker, "censored_unverified", entry_day=entry_day, last_day=last_day,
                                   first_gap_day=first_gap_day, interior_gaps=interior_gaps)
                if same is False:
                    return Outcome(ticker, "censored_terminal", entry_day=entry_day, last_day=last_day,
                                   first_gap_day=first_gap_day, interior_gaps=interior_gaps,
                                   detail={"identity": "changed"})
            status = "ok" if not gapped else ("ok_bridged" if exit_j == exit_i else "ok_late_exit")
            entry_price = bars[entry_day][0] * self.split_factor(ticker, entry_day, exit_day)
            return Outcome(ticker, status, bars[exit_day][1] / entry_price - 1, entry_day, exit_day, last_day,
                           first_gap_day, exit_j - exit_i, interior_gaps)
        renamed = self._renamed(ticker, entry_day, last_i, exit_i)
        if renamed is not None:
            renamed.interior_gaps = interior_gaps
            renamed.first_gap_day = first_gap_day
            return renamed
        if self.directory is None:
            status = "censored_unverified"
        else:
            status = self._censored(ticker, entry_day, last_i)
        return Outcome(ticker, status, entry_day=entry_day, last_day=last_day, first_gap_day=first_gap_day,
                       interior_gaps=interior_gaps)


def resolve(row: dict) -> tuple[str | None, str]:
    sources = row.get("source_tickers") or []
    if len(sources) == 1:
        return sources[0], "ok"
    if len(sources) > 1:
        return None, "case_collision"
    return None, "unresolved"


def evaluate(records: list[dict], prices: Prices,
             row_metrics: Callable[[list[dict]], dict] | None = None) -> tuple[dict, dict]:
    """Per list key, list type, top N and holding: dated points with the primary metric, bounds and coverage.

    ``row_metrics(rows)`` may add per-date diagnostics computed from the listed rows.
    """
    series: dict = defaultdict(list)
    counts: dict = defaultdict(lambda: defaultdict(int))
    spy_cache: dict = {}

    def spy_excess(day: str, exit_day: str, ret: float) -> float:
        key = (day, exit_day)
        if key not in spy_cache:
            spy_cache[key] = prices.forward_to("SPY", day, exit_day)
        return ret - spy_cache[key]

    for record in records:
        day = record["session"]
        for key, block in record["lists"].items():
            ranked = block["rows"]
            variants = {"mixed": ranked, "stock": [row for row in ranked if row.get("stock_or_etf_track") == "stock"]}
            for list_type, rows in variants.items():
                for top in TOPS:
                    names = rows[:top]
                    for holding in HOLDINGS:
                        tally = counts[(key, list_type, top, holding)]
                        excesses: list[float] = []
                        legacy_excesses: list[float] = []
                        bound_zero: list[float] = []
                        bound_loss: list[float] = []
                        statuses: Counter = Counter()
                        hits = 0
                        labelled = True
                        for row in names:
                            ticker, why = resolve(row)
                            tally[f"resolve_{why}"] += 1
                            if ticker is None:
                                statuses["identity_uncertain"] += 1
                                tally["label_identity_uncertain"] += 1
                                continue
                            outcome = prices.observe(ticker, day, holding)
                            tally[f"label_{outcome.status}"] += 1
                            tally[f"legacy_{outcome.legacy_status}"] += 1
                            if outcome.status == "no_label":
                                labelled = False
                                break
                            statuses[outcome.status] += 1
                            if outcome.legacy_ret is not None:
                                legacy_excesses.append(spy_excess(day, outcome.legacy_exit_day, outcome.legacy_ret))
                            if outcome.observable:
                                excess = spy_excess(day, outcome.exit_day, outcome.ret)
                                excesses.append(excess)
                                bound_zero.append(excess)
                                bound_loss.append(excess)
                                hits += excess > 0
                            elif outcome.censored:
                                full_exit = prices.sessions[prices.index[day] + holding]
                                bound_zero.append(0.0)
                                bound_loss.append(spy_excess(day, full_exit, -1.0))
                        if not labelled or (not names and prices.index[day] + holding >= len(prices.sessions)):
                            continue
                        observable = len(excesses)
                        empty = top - len(names)
                        unobservable = len(names) - observable
                        primary = None if (observable == 0 and names) else sum(excesses) / (observable + empty)
                        point = {
                            "day": day,
                            "slot": primary,
                            "slot_legacy": sum(legacy_excesses) / top,
                            "slot_zero": None if (observable == 0 and names) else sum(bound_zero) / (len(bound_zero) + empty),
                            "slot_loss": None if (observable == 0 and names) else sum(bound_loss) / (len(bound_loss) + empty),
                            "unfilled": sum(excesses) / observable if observable else None,
                            "hit": hits / observable if observable else None,
                            "listed": len(names),
                            "observable": observable,
                            "unobservable": unobservable,
                            "empty_slots": empty,
                            "statuses": dict(statuses),
                            "tickers": [row["ticker"] for row in names],
                        }
                        if row_metrics is not None:
                            point.update(row_metrics(names))
                        series[(key, list_type, top, holding)].append(point)
    return series, counts


def _mean_pct(values: list) -> float | None:
    clean = [value for value in values if value is not None]
    return round(100 * statistics.fmean(clean), 3) if clean else None


def summarize(points: list[dict], holding: int) -> dict:
    slot = [point["slot"] for point in points if point["slot"] is not None]
    unfilled = [point["unfilled"] for point in points if point["unfilled"] is not None]
    hits = [point["hit"] for point in points if point["hit"] is not None]
    listed = [point["listed"] for point in points]
    statuses: Counter = Counter()
    for point in points:
        statuses.update(point.get("statuses") or {})
    selected = sum(listed)
    observable = sum(point["observable"] for point in points)
    t_value = newey_west_t(slot, max(0, holding // 5 - 1))
    summary = {
        "days": len(points),
        "days_observable": len(slot),
        "mean_slot_pct": round(100 * statistics.fmean(slot), 3) if slot else None,
        "t_slot": round(t_value, 2) if t_value is not None else None,
        "mean_slot_legacy_pct": _mean_pct([point["slot_legacy"] for point in points]),
        "mean_slot_zero_pct": _mean_pct([point["slot_zero"] for point in points]),
        "mean_slot_loss_pct": _mean_pct([point["slot_loss"] for point in points]),
        "mean_unfilled_pct": _mean_pct(unfilled),
        "hit_rate": round(statistics.fmean(hits), 3) if hits else None,
        "median_listed": statistics.median(listed) if listed else None,
        "selected_names": selected,
        "observable_names": observable,
        "observable_share": round(observable / selected, 4) if selected else None,
        "empty_slots": sum(point["empty_slots"] for point in points),
    }
    for status in OBSERVABLE_STATUSES + UNOBSERVABLE_STATUSES:
        summary[f"n_{status}"] = statuses.get(status, 0)
    return summary


def turnover(points: list[dict], top: int) -> float | None:
    changes = []
    for before, after in zip(points, points[1:]):
        a, b = set(before["tickers"][:top]), set(after["tickers"][:top])
        if a or b:
            changes.append(1 - len(a & b) / max(len(a), len(b)))
    return round(statistics.fmean(changes), 3) if changes else None


def periods(points: list[dict]) -> dict[str, list[dict]]:
    out = {"ALL": points, "P1": [p for p in points if p["day"] <= P1_END],
           "P2": [p for p in points if p["day"] > P1_END]}
    for year in sorted({p["day"][:4] for p in points}):
        out[year] = [p for p in points if p["day"][:4] == year]
    return out


def coverage_rows(series: Mapping, *, top: int = 20, holding: int = 63) -> list[dict]:
    """One row per variant, profile and list type: observable share and every missing reason."""
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for (key, list_type, top_n, holding_n), points in series.items():
        if top_n != top or holding_n != holding:
            continue
        variant, profile, _view = key.split("/")
        grouped[(variant, profile, list_type)].extend(points)
    rows = []
    for (variant, profile, list_type), points in sorted(grouped.items()):
        summary = summarize(points, holding)
        rows.append({"variant": variant, "profile": profile, "list_type": list_type, "top": top, "holding": holding,
                     "days": summary["days"], "days_observable": summary["days_observable"],
                     "selected_names": summary["selected_names"], "observable_names": summary["observable_names"],
                     "observable_share": summary["observable_share"], "empty_slots": summary["empty_slots"],
                     **{key: value for key, value in summary.items() if key.startswith("n_")}})
    return rows


def load_records(folders: list[Path], *, dates_from: str | None = None) -> tuple[list[dict], dict]:
    """Merge replay directories by date (one per machine); shared views must be row-identical."""
    merged: dict[str, dict] = {}
    skipped: dict = defaultdict(int)
    for folder in folders:
        for path in sorted(folder.glob("20*.json.gz")):
            record = json.load(gzip.open(path))
            if dates_from and record["session"] < dates_from:
                continue
            if record.get("status") != "scored" or not record.get("gate_ok"):
                skipped[record.get("status") if record.get("status") != "scored" else "gate_failed"] += 1
                continue
            existing = merged.get(record["session"])
            if existing is None:
                merged[record["session"]] = record
                continue
            for key, block in record["lists"].items():
                if key in existing["lists"]:
                    left = [(row["ticker"], round(float(row["sort_score"]), 9)) for row in existing["lists"][key]["rows"]]
                    right = [(row["ticker"], round(float(row["sort_score"]), 9)) for row in block["rows"]]
                    if left != right:
                        raise SystemExit(f"{record['session']} {key}: the two replay directories disagree")
                else:
                    existing["lists"][key] = block
    return [merged[day] for day in sorted(merged)], dict(skipped)


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def decide(cand: dict, ref: dict, years: list[str]) -> dict:
    def up(key: str) -> bool:
        return cand.get(key) is not None and ref.get(key) is not None and cand[key] > ref[key]
    compared = [y for y in years if cand.get(f"h63_{y}") is not None and ref.get(f"h63_{y}") is not None]
    return {
        "P1_up": up("h63_P1"),
        "P2_up": up("h63_P2"),
        "years_better": sum(1 for y in compared if cand[f"h63_{y}"] > ref[f"h63_{y}"]),
        "years_compared": len(compared),
        "h20_within_1pp": cand["h20_ALL"] is not None and ref["h20_ALL"] is not None
        and cand["h20_ALL"] >= ref["h20_ALL"] - 1.0,
        "length_ok": (cand["median_listed"] or 0) >= 0.5 * (ref["median_listed"] or 0),
        "smaller_period_gain": round(min((cand["h63_P1"] or 0) - (ref["h63_P1"] or 0),
                                         (cand["h63_P2"] or 0) - (ref["h63_P2"] or 0)), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append",
                        help="replay directory; repeat to merge the date halves of two machines")
    parser.add_argument("--directory", type=Path,
                        help="weekly point-in-time directory folder; without it gaps cannot be verified and are censored")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", default="v15")
    parser.add_argument("--dates-from", help="only use replay dates on or after this day")
    args = parser.parse_args()
    records, skipped = load_records(args.replay, dates_from=args.dates_from)
    if not records:
        raise SystemExit("no scored replay dates")
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = Directory(args.directory) if args.directory else None
    prices = Prices(connection, records[0]["session"], directory)
    series, counts = evaluate(records, prices)

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for (key, list_type, top, holding), points in sorted(series.items()):
        variant, profile, view = key.split("/")
        for period, subset in periods(points).items():
            if not subset:
                continue
            summary = summarize(subset, holding)
            summary["days_short"] = sum(1 for point in subset if point["listed"] < top)
            rows.append({"variant": variant, "profile": profile, "view": view, "list_type": list_type,
                         "top": top, "holding": holding, "period": period, **summary,
                         "turnover": turnover(subset, top)})
    write_csv(args.out / "metrics.csv", rows)
    with (args.out / "label_status.json").open("w") as handle:
        json.dump({"|".join(map(str, key)): dict(value) for key, value in counts.items()}, handle, indent=1)
    write_csv(args.out / "coverage.csv", coverage_rows(series))
    with (args.out / "rules.json").open("w") as handle:
        json.dump({**RULES, "directory": None if directory is None else str(directory.folder),
                   "identity_verification": directory is not None}, handle, indent=1)
    print(f"dates used {len(records)}, skipped {skipped}, first {records[0]['session']}, "
          f"last {records[-1]['session']}; metrics rows {len(rows)}; identity verification "
          f"{'on' if directory else 'OFF (gaps censored as unverified)'}")

    def primary(variant: str, profile: str, period: str, list_type: str, holding: int = 63,
                field_name: str = "mean_slot_pct") -> float | None:
        values = [row[field_name] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == period
                  and row["holding"] == holding and row["top"] == 20 and row["list_type"] == list_type
                  and row[field_name] is not None]
        return round(statistics.fmean(values), 3) if len(values) == len(VIEWS) else None

    def secondary(variant: str, profile: str, list_type: str, field_name: str) -> float | None:
        values = [row[field_name] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == "ALL"
                  and row["holding"] == 20 and row["top"] == 20 and row["list_type"] == list_type
                  and row[field_name] is not None]
        return round(statistics.fmean(values), 3) if values else None

    baseline = args.baseline
    variants = sorted({row["variant"] for row in rows}, key=lambda name: (name != baseline, name))
    years = sorted({row["period"] for row in rows if row["period"].isdigit()})
    table = []
    for list_type in ("mixed", "stock"):
        for profile in ("conservative", "balanced", "aggressive"):
            for variant in variants:
                if primary(variant, profile, "ALL", list_type) is None:
                    continue
                entry = {"list_type": list_type, "profile": profile, "variant": variant}
                for period in ("ALL", "P1", "P2", *years):
                    entry[f"h63_{period}"] = primary(variant, profile, period, list_type)
                for bound in BOUNDS:
                    entry[f"h63_ALL_{bound}"] = primary(variant, profile, "ALL", list_type, field_name=f"mean_slot_{bound}_pct")
                entry["h20_ALL"] = primary(variant, profile, "ALL", list_type, holding=20)
                entry["h5_ALL"] = primary(variant, profile, "ALL", list_type, holding=5)
                entry["median_listed"] = secondary(variant, profile, list_type, "median_listed")
                entry["observable_share_h63"] = primary(variant, profile, "ALL", list_type, field_name="observable_share")
                table.append(entry)
    write_csv(args.out / "primary.csv", table)
    print("\nprimary metric: top-20 slot-filled excess vs SPY on observable names, % per signal, mean of the three views")
    for entry in table:
        print("  " + ", ".join(f"{k} {v}" for k, v in entry.items()))

    def lookup(list_type: str, profile: str, variant: str) -> dict | None:
        return next((e for e in table if (e["list_type"], e["profile"], e["variant"]) == (list_type, profile, variant)),
                    None)

    verdicts: dict = {"baseline": baseline, "rules": RULES, f"variant_vs_{baseline}_mixed": {},
                      f"variant_vs_{baseline}_stock": {}, "stock_vs_mixed": {}}
    for list_type in ("mixed", "stock"):
        print(f"\npre-registered decision checks, variant vs {baseline} on the {list_type} list (balanced, aggressive):")
        for variant in variants:
            if variant == baseline:
                continue
            for profile in ("balanced", "aggressive"):
                cand, ref = lookup(list_type, profile, variant), lookup(list_type, profile, baseline)
                if cand and ref:
                    verdict = decide(cand, ref, years)
                    verdicts[f"variant_vs_{baseline}_{list_type}"][f"{variant}/{profile}"] = verdict
                    print(f"  {variant} {profile}: {verdict}")
    print("\nstock-only top 20 vs the mixed list of the same variant:")
    for variant in variants:
        for profile in ("conservative", "balanced", "aggressive"):
            cand, ref = lookup("stock", profile, variant), lookup("mixed", profile, variant)
            if cand and ref:
                verdict = decide(cand, ref, years)
                verdicts["stock_vs_mixed"][f"{variant}/{profile}"] = verdict
                print(f"  {variant} {profile}: {verdict}")
    with (args.out / "decision.json").open("w") as handle:
        json.dump(verdicts, handle, indent=1)
    print("DONE evaluate")


if __name__ == "__main__":
    main()
