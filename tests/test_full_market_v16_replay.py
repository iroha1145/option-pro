from __future__ import annotations

import importlib.util
import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app.services.eod_limited import market_data as md
from app.services.eod_limited.market_registry import load_market_registry

SCRIPTS = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/full_market_v1_6/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"v16_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = _load("evaluate")
replay = _load("replay")

SESSIONS = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]


def _cache(tmp_path: Path, bars: dict[str, dict[str, tuple[float, float]]], splits=()) -> sqlite3.Connection:
    connection = md._connect(tmp_path / "replay.sqlite")
    with connection:
        for day in SESSIONS:
            connection.execute("INSERT INTO market_sessions VALUES (?, 'x', 1, 'x', 'OK')", (day,))
        for ticker, series in bars.items():
            for day, (open_, close) in series.items():
                connection.execute(
                    "INSERT INTO raw_daily_bars VALUES (?, ?, 1, ?, ?, ?, ?, 1000, NULL, NULL)",
                    (ticker, day, open_, max(open_, close), min(open_, close), close))
        connection.executemany("INSERT INTO splits VALUES (?, ?, ?, ?, 'x')", splits)
    return connection


def test_forward_return_enters_next_open_and_adjusts_splits_inside_the_holding(tmp_path):
    bars = {
        "SPY": {day: (100.0, 100.0) for day in SESSIONS},
        # 2-for-1 split executes on 2026-09-17: raw prices halve from that session on.
        "AAA": {"2026-09-14": (10.0, 10.0), "2026-09-15": (10.0, 10.5), "2026-09-16": (10.5, 11.0),
                "2026-09-17": (5.5, 6.0), "2026-09-18": (6.0, 6.0)},
    }
    prices = evaluate.Prices(_cache(tmp_path, bars, [("AAA", "2026-09-17", 1.0, 2.0)]), SESSIONS[0])
    value, status, exit_day = prices.forward("AAA", "2026-09-14", 4)
    assert (status, exit_day) == ("ok", "2026-09-18")
    assert value == pytest.approx(6.0 / (10.0 * 0.5) - 1)
    # A split on the entry session is already in the entry open.
    value, _, _ = prices.forward("AAA", "2026-09-16", 2)
    assert value == pytest.approx(6.0 / 5.5 - 1)


def test_forward_return_stops_at_the_first_gap_and_never_bridges_it(tmp_path):
    bars = {
        "SPY": {day: (100.0, 100.0 + i) for i, day in enumerate(SESSIONS)},
        # BBB is missing on 2026-09-17 and trades again later: the holding ends on 09-16.
        "BBB": {"2026-09-14": (20.0, 20.0), "2026-09-15": (20.0, 21.0), "2026-09-16": (21.0, 22.0),
                "2026-09-18": (40.0, 40.0), "2026-09-21": (41.0, 41.0)},
    }
    prices = evaluate.Prices(_cache(tmp_path, bars), SESSIONS[0])
    value, status, exit_day = prices.forward("BBB", "2026-09-14", 4)
    assert (status, exit_day) == ("gap_exit", "2026-09-16")
    assert value == pytest.approx(22.0 / 20.0 - 1)
    assert prices.forward("BBB", "2026-09-16", 1)[1] == "no_entry_bar"
    assert prices.forward("BBB", "2026-09-18", 5)[1] == "no_label"


def test_slot_filled_excess_counts_missing_names_as_spy_and_skips_case_collisions(tmp_path):
    bars = {
        "SPY": {day: (100.0, 100.0) for day in SESSIONS},
        "AAA": {day: (10.0, 11.0) for day in SESSIONS},
    }
    prices = evaluate.Prices(_cache(tmp_path, bars), SESSIONS[0])
    record = {"session": "2026-09-14", "lists": {"v15/balanced/mid": {"rows": [
        {"ticker": "AAA", "source_tickers": ["AAA"], "stock_or_etf_track": "stock"},
        {"ticker": "BCPC", "source_tickers": ["BCPC", "BCpC"], "stock_or_etf_track": "stock"},
    ]}}}
    series, counts = evaluate.evaluate([record], prices)
    point = series[("v15/balanced/mid", "mixed", 20, 5)][0]
    assert point["slot"] == pytest.approx(0.10 / 20)
    assert point["unfilled"] == pytest.approx(0.10)
    assert point["listed"] == 2
    assert counts[("v15/balanced/mid", "mixed", 20, 5)]["resolve_case_collision"] == 1


def test_tilted_registry_changes_only_balanced_and_aggressive_trend_momentum_and_stability():
    base = load_market_registry()
    tilted = replay.tilted_registry(base, {"T": 0.6, "M": 1.6, "R": 0.0})
    for profile in ("balanced", "aggressive"):
        before, after = base["profiles"][profile]["factor_tilt"], tilted["profiles"][profile]["factor_tilt"]
        assert after[0] == pytest.approx(before[0] * 0.6)
        assert after[1] == pytest.approx(before[1] * 1.6)
        assert after[6] == 0.0
        assert after[2:6] == before[2:6] and after[7] == before[7]
    assert tilted["profiles"]["conservative"] == base["profiles"]["conservative"]
    assert base == load_market_registry()  # the base registry is not mutated


def test_replay_dates_step_through_trading_sessions_only():
    dates = replay.replay_dates(date(2026, 9, 3), date(2026, 9, 18), 5)
    assert dates == ["2026-09-03", "2026-09-11", "2026-09-18"]  # 2026-09-07 is Labor Day


def test_live_registry_carries_the_v16_tilts_and_matches_the_replayed_candidate():
    from app.services.eod_limited.market_registry import LIVE_PROFILE_TILT_MULTIPLIERS
    from app.services.research_eod_v1.config_load import load_registry

    sealed, live = load_registry(), load_market_registry()
    assert LIVE_PROFILE_TILT_MULTIPLIERS == {"balanced": replay.VARIANTS["tilt_b"],
                                             "aggressive": replay.VARIANTS["tilt_b"]}
    for profile in ("balanced", "aggressive"):
        before, after = sealed["profiles"][profile]["factor_tilt"], live["profiles"][profile]["factor_tilt"]
        assert after[0] == pytest.approx(before[0] * 0.5)
        assert after[1] == pytest.approx(before[1] * 2.0)
        assert after[2:] == before[2:]
    assert live["profiles"]["conservative"] == sealed["profiles"]["conservative"]
    assert live["live_profile_tilt_multipliers"] == LIVE_PROFILE_TILT_MULTIPLIERS
