"""The point-in-time shares request planner: tiers, cadence and convergence."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "research" / "breakout_radar" / "replay_v1" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pit_shares_requests as planner  # noqa: E402


def _days(start: date, count: int) -> list[date]:
    out = []
    day = start
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def test_first_round_asks_once_per_ticker_at_its_first_needed_day() -> None:
    days = _days(date(2024, 1, 2), 40)
    assert planner.requests_for(days, []) == [(days[0], "initial")]


def test_small_caps_refresh_monthly_and_large_caps_yearly() -> None:
    days = _days(date(2024, 1, 2), 260)
    small = [(days[0], 3e8)]
    large = [(days[0], 5e10)]
    small_requests = planner.requests_for(days, small)
    large_requests = planner.requests_for(days, large)
    assert small_requests and all(reason == "refresh_31d" for _day, reason in small_requests)
    # About one request per month for a year of needed days.
    assert 9 <= len(small_requests) <= 13
    assert large_requests == [] or all(reason == "refresh_366d" for _d, reason in large_requests)
    assert len(large_requests) <= 1


def test_planner_converges_when_samples_cover_the_cadence() -> None:
    days = _days(date(2024, 1, 2), 120)
    samples = [(day, 1e9) for day in days[::60]]  # quarterly cadence for the $500M-$2B tier
    assert planner.requests_for(days, samples) == []
    sparse = [(days[0], 1e9)]
    pending = planner.requests_for(days, sparse)
    assert pending and pending[0][1] == "refresh_92d"
    covered = sparse + [(day, 1e9) for day, _reason in pending]
    assert planner.requests_for(days, sorted(covered)) == []


def test_needed_days_apply_the_type_filter(tmp_path) -> None:
    import gzip
    import json
    import sqlite3

    db = tmp_path / "daily.sqlite"
    connection = sqlite3.connect(db)
    connection.executescript(
        """
        CREATE TABLE raw_daily_bars (ticker TEXT, session_date TEXT, timestamp_ms INTEGER, open REAL, high REAL,
                                     low REAL, close REAL, volume REAL, vwap REAL, transactions INTEGER);
        CREATE TABLE splits (ticker TEXT, execution_date TEXT, split_from REAL, split_to REAL, provider_id TEXT);
        """
    )
    days = _days(date(2024, 3, 1), 40)
    for ticker in ("AAA", "WWW"):
        for index, day in enumerate(days):
            close = 10.0 + (0.6 if index == len(days) - 1 else 0.0)  # +6% on the last day
            volume = 1_000_000.0 if index < len(days) - 1 else 5_000_000.0
            connection.execute(
                "INSERT INTO raw_daily_bars VALUES (?,?,?,?,?,?,?,?,?,?)",
                (ticker, day.isoformat(), 0, close, close + 0.2, close - 0.2, close, volume, close, 1),
            )
    connection.commit()
    connection.close()
    folder = tmp_path / "directory"
    folder.mkdir()
    with gzip.open(folder / "2024-02-26.json.gz", "wt") as handle:
        json.dump({"results": [{"ticker": "AAA", "type": "CS"}, {"ticker": "WWW", "type": "WARRANT"}]}, handle)
    needed = planner.needed_days(db, days[0], days[-1], planner.Directory(folder))
    assert list(needed) == ["AAA"] and needed["AAA"] == [days[-1]]
