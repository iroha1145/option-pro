from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

from app.services.eod_limited import market_data as md
from app.services.eod_limited.market_registry import load_market_registry
from app.services.market_calendar import prior_trading_sessions

SCRIPTS = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/full_market_v1_6/scripts"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"v16_{name}", SCRIPTS / f"{name}.py")
    if spec.name in sys.modules:
        return sys.modules[spec.name]
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


evaluate = _load("evaluate")
replay = _load("replay")
paired = _load("paired")
backtest = _load("export_backtest")

SESSIONS = ["2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17", "2026-09-18", "2026-09-21"]
# A longer calendar for holdings and ledgers: 90 real trading sessions ending 2026-09-25.
LONG = [day.isoformat() for day in [*prior_trading_sessions(date(2026, 9, 25), 89), date(2026, 9, 25)]]


def _cache(tmp_path: Path, bars: dict[str, dict[str, tuple[float, float]]], splits=(), sessions=SESSIONS) -> sqlite3.Connection:
    connection = md._connect(tmp_path / "replay.sqlite")
    with connection:
        for day in sessions:
            connection.execute("INSERT INTO market_sessions VALUES (?, 'x', 1, 'x', 'OK')", (day,))
        for ticker, series in bars.items():
            for day, (open_, close) in series.items():
                connection.execute(
                    "INSERT INTO raw_daily_bars VALUES (?, ?, 1, ?, ?, ?, ?, 1000, NULL, NULL)",
                    (ticker, day, open_, max(open_, close), min(open_, close), close))
        connection.executemany("INSERT INTO splits VALUES (?, ?, ?, ?, 'x')", splits)
    return connection


def _flat(days, price: float, open_price: float | None = None) -> dict[str, tuple[float, float]]:
    return {day: (price if open_price is None else open_price, price) for day in days}


def _directory(tmp_path: Path, snapshots: dict[str, list[dict]]) -> "evaluate.Directory":
    folder = tmp_path / "directory"
    folder.mkdir(exist_ok=True)
    for label, rows in snapshots.items():
        with gzip.open(folder / f"{label}.json.gz", "wt") as handle:
            json.dump({"results": rows}, handle)
    return evaluate.Directory(folder)


def _row(ticker, score=90.0, track="stock", sources=None):
    return {"ticker": ticker, "sort_score": score, "stock_or_etf_track": track,
            "source_tickers": [ticker] if sources is None else sources}


# ---------------------------------------------------------------- legacy rule (kept, labelled)


def test_legacy_forward_enters_next_open_and_adjusts_splits_inside_the_holding(tmp_path):
    bars = {
        "SPY": {day: (100.0, 100.0) for day in SESSIONS},
        # 2-for-1 split executes on 2026-09-17: raw prices halve from that session on.
        "AAA": {"2026-09-14": (10.0, 10.0), "2026-09-15": (10.0, 10.5), "2026-09-16": (10.5, 11.0),
                "2026-09-17": (5.5, 6.0), "2026-09-18": (6.0, 6.0)},
    }
    prices = evaluate.Prices(_cache(tmp_path, bars, [("AAA", "2026-09-17", 1.0, 2.0)]), SESSIONS[0])
    value, status, exit_day = prices.forward_legacy("AAA", "2026-09-14", 4)
    assert (status, exit_day) == ("ok", "2026-09-18")
    assert value == pytest.approx(6.0 / (10.0 * 0.5) - 1)
    # A split on the entry session is already in the entry open.
    value, _, _ = prices.forward_legacy("AAA", "2026-09-16", 2)
    assert value == pytest.approx(6.0 / 5.5 - 1)
    # The verified observation agrees when every bar is present.
    outcome = prices.observe("AAA", "2026-09-14", 4)
    assert outcome.status == "ok" and outcome.ret == pytest.approx(value := 6.0 / 5.0 - 1) and outcome.legacy_ret == pytest.approx(value)


def test_legacy_forward_back_fills_the_gap_and_the_observation_refuses_to(tmp_path):
    """The review's first synthetic case: a gap inside the holding is not a completed sale."""
    bars = {
        "SPY": {day: (100.0, 100.0 + i) for i, day in enumerate(SESSIONS)},
        # BBB is missing on 2026-09-17 and trades again later.
        "BBB": {"2026-09-14": (20.0, 20.0), "2026-09-15": (20.0, 21.0), "2026-09-16": (21.0, 22.0),
                "2026-09-18": (40.0, 40.0), "2026-09-21": (41.0, 41.0)},
    }
    prices = evaluate.Prices(_cache(tmp_path, bars), SESSIONS[0])
    value, status, exit_day = prices.forward_legacy("BBB", "2026-09-14", 4)
    assert (status, exit_day) == ("gap_exit", "2026-09-16")  # the pre-fix back-fill
    assert value == pytest.approx(22.0 / 20.0 - 1)
    assert prices.forward_legacy("BBB", "2026-09-16", 1)[1] == "no_entry_bar"
    assert prices.forward_legacy("BBB", "2026-09-18", 5)[1] == "no_label"
    # Without a directory the gap cannot be verified: censored, no return, legacy value kept apart.
    outcome = prices.observe("BBB", "2026-09-14", 4)
    assert outcome.status == "censored_unverified" and outcome.ret is None and not outcome.observable
    assert outcome.last_day == "2026-09-18" and outcome.first_gap_day == "2026-09-17" and outcome.interior_gaps == 1
    assert (outcome.legacy_status, outcome.legacy_ret) == ("gap_exit", pytest.approx(0.1))
    assert prices.observe("BBB", "2026-09-16", 1).status == "no_entry_bar"
    assert prices.observe("BBB", "2026-09-18", 5).status == "no_label"


# ---------------------------------------------------------------- verified observations


@pytest.fixture
def long_market(tmp_path):
    """Ninety sessions; SPY drifts up 0.1 a day; several stocks with gaps, renames and delistings."""
    spy = {day: (100.0 + 0.1 * i, 100.0 + 0.1 * i) for i, day in enumerate(LONG)}
    bars = {"SPY": spy}
    # HOLD trades every day.
    bars["HOLD"] = {day: (50.0 + 0.5 * i, 50.0 + 0.5 * i) for i, day in enumerate(LONG)}
    # GAPIN misses sessions 10-12 and trades again: held through when identity is verified.
    bars["GAPIN"] = {day: (30.0, 30.0 + i * 0.1) for i, day in enumerate(LONG) if i not in (10, 11, 12)}
    # LATE misses the exit session (index 20 for a 20-session holding from index 0) and trades on 21.
    bars["LATE"] = {day: (10.0, 10.0 + 0.05 * i) for i, day in enumerate(LONG) if i != 20}
    # TEMP stops trading after session 15 and reappears at session 40 (a long halt).
    bars["TEMP"] = {day: (20.0, 20.0) for i, day in enumerate(LONG) if i <= 15 or i >= 40}
    # GONE stops trading after session 15 for good (delisted).
    bars["GONE"] = {day: (15.0, 15.0) for i, day in enumerate(LONG) if i <= 15}
    # OLD is renamed NEW after session 15 at the same price level; NEW trades from session 17.
    bars["OLD"] = {day: (40.0, 40.0 + i) for i, day in enumerate(LONG) if i <= 15}
    bars["NEW"] = {day: (40.0 + i, 40.0 + i) for i, day in enumerate(LONG) if i >= 17}
    # REUSED: the ticker trades again after a gap but under a different company (new CIK).
    bars["REUSED"] = {day: (5.0, 5.0) for i, day in enumerate(LONG) if i <= 15 or i >= 18}
    # ENDGAP stops trading at session 80: the data ends before the lookahead window does.
    bars["ENDGAP"] = {day: (8.0, 8.0) for i, day in enumerate(LONG) if i <= 80}
    connection = _cache(tmp_path, bars, sessions=LONG)
    company = lambda ticker, cik, figi=None: {"ticker": ticker, "cik": cik, "composite_figi": figi, "type": "CS", "active": True}
    early = [company("HOLD", "1", "BBG1"), company("GAPIN", "2", "BBG2"), company("LATE", "3"), company("TEMP", "4"),
             company("GONE", "5"), company("OLD", "6", "BBG6"), company("REUSED", "7"), company("ENDGAP", "8"),
             {"ticker": "SPY", "cik": None, "composite_figi": "BBGSPY", "type": "ETF", "active": True}]
    later = [row for row in early if row["ticker"] not in {"OLD", "GONE", "REUSED"}] + \
        [company("NEW", "6", "BBG6"), company("REUSED", "70")]
    directory = _directory(tmp_path, {LONG[0]: early, LONG[16]: later, LONG[40]: later})
    return evaluate.Prices(connection, LONG[0], directory), connection


def test_observation_statuses_cover_every_gap_case(long_market):
    prices, _ = long_market
    signal = LONG[0]
    hold = prices.observe("HOLD", signal, 20)
    assert hold.status == "ok" and hold.exit_day == LONG[20] and hold.ret == pytest.approx(60.0 / 50.5 - 1)
    gapin = prices.observe("GAPIN", signal, 20)
    assert gapin.status == "ok_bridged" and gapin.interior_gaps == 3 and gapin.exit_day == LONG[20]
    assert gapin.ret == pytest.approx((30.0 + 2.0) / 30.0 - 1)
    assert gapin.legacy_status == "gap_exit" and gapin.legacy_exit_day == LONG[9]  # what the old code sold at
    late = prices.observe("LATE", signal, 20)
    assert late.status == "ok_late_exit" and late.exit_lag == 1 and late.exit_day == LONG[21]
    assert late.ret == pytest.approx((10.0 + 0.05 * 21) / 10.0 - 1)
    temp = prices.observe("TEMP", signal, 20)
    assert temp.status == "censored_temporary" and temp.ret is None and temp.last_day == LONG[15]
    gone = prices.observe("GONE", signal, 20)
    assert gone.status == "censored_terminal" and gone.last_day == LONG[15]
    renamed = prices.observe("OLD", signal, 20)
    assert renamed.status == "renamed" and renamed.followed_ticker == "NEW" and renamed.exit_day == LONG[20]
    assert renamed.ret == pytest.approx(60.0 / 40.0 - 1) and renamed.detail["first_new_day"] == LONG[17]
    reused = prices.observe("REUSED", signal, 20)
    assert reused.status == "censored_terminal" and reused.detail == {"identity": "changed"}
    end = prices.observe("ENDGAP", LONG[70], 15)
    assert end.status == "censored_unknown"
    # Observations are memoised per (ticker, signal, holding).
    assert prices.observe("HOLD", signal, 20) is hold


def test_rename_following_needs_a_continuous_price_and_a_nearby_first_bar(tmp_path):
    bars = {"SPY": _flat(LONG, 100.0), "OLD": {day: (40.0, 40.0) for i, day in enumerate(LONG) if i <= 15},
            # JUMP reappears at a quarter of the price without a split: not a verifiable continuation.
            "JUMP": {day: (10.0, 10.0) for i, day in enumerate(LONG) if i >= 17},
            "OLD2": {day: (40.0, 40.0) for i, day in enumerate(LONG) if i <= 15},
            # FAR first trades too long after OLD2's last bar.
            "FAR": {day: (40.0, 40.0) for i, day in enumerate(LONG) if i >= 25}}
    company = lambda ticker, cik: {"ticker": ticker, "cik": cik, "composite_figi": None, "type": "CS", "active": True}
    directory = _directory(tmp_path, {LONG[0]: [company("OLD", "1"), company("OLD2", "2")],
                                      LONG[16]: [company("JUMP", "1"), company("FAR", "2")]})
    prices = evaluate.Prices(_cache(tmp_path, bars, sessions=LONG), LONG[0], directory)
    assert prices.observe("OLD", LONG[0], 20).status == "censored_terminal"
    assert prices.observe("OLD2", LONG[0], 20).status == "censored_terminal"
    # A 4-for-1 split filed under the new ticker on its first day explains the level change.
    prices = evaluate.Prices(_cache(tmp_path / "b", bars, [("JUMP", LONG[17], 1.0, 4.0)], sessions=LONG), LONG[0], directory)
    outcome = prices.observe("OLD", LONG[0], 20)
    assert outcome.status == "renamed" and outcome.followed_ticker == "JUMP"
    assert outcome.ret == pytest.approx(10.0 / (40.0 * 1.0 / 4.0) - 1) == pytest.approx(0.0)


# ---------------------------------------------------------------- per-date metric, bounds, coverage


def test_slot_metric_removes_unobservable_names_and_reports_bounds_and_empty_slots(long_market):
    prices, _ = long_market
    signal = LONG[0]
    record = {"session": signal, "lists": {"v15/balanced/mid": {"rows": [
        _row("HOLD"), _row("GONE"), _row("BCPC", sources=["BCPC", "BCpC"]),
    ]}}}
    series, counts = evaluate.evaluate([record], prices)
    point = series[("v15/balanced/mid", "mixed", 20, 20)][0]
    spy = prices.forward_to("SPY", signal, LONG[20])
    hold_excess = (60.0 / 50.5 - 1) - spy
    assert point["observable"] == 1 and point["unobservable"] == 2 and point["empty_slots"] == 17
    assert point["statuses"] == {"ok": 1, "censored_terminal": 1, "identity_uncertain": 1}
    # Primary: the censored and the ambiguous name leave the denominator; empty slots stay at SPY.
    assert point["slot"] == pytest.approx(hold_excess / 18)
    # Legacy: GONE sold at its last close over a shortened window, BCPC skipped, all over 20 slots.
    gone_legacy = (15.0 / 15.0 - 1) - prices.forward_to("SPY", signal, LONG[15])
    assert point["slot_legacy"] == pytest.approx((hold_excess + gone_legacy) / 20)
    assert point["slot_zero"] == pytest.approx(hold_excess / 19)
    assert point["slot_loss"] == pytest.approx((hold_excess + (-1.0 - spy)) / 19)
    assert point["unfilled"] == pytest.approx(hold_excess) and point["hit"] == 1.0
    assert counts[("v15/balanced/mid", "mixed", 20, 20)]["label_censored_terminal"] == 1
    assert counts[("v15/balanced/mid", "mixed", 20, 20)]["legacy_gap_exit"] == 1
    assert counts[("v15/balanced/mid", "mixed", 20, 20)]["label_identity_uncertain"] == 1
    summary = evaluate.summarize(series[("v15/balanced/mid", "mixed", 20, 20)], 20)
    assert summary["observable_share"] == pytest.approx(1 / 3, abs=1e-4) and summary["empty_slots"] == 17
    assert summary["tail_5pct_mean_pct"] == summary["worst_day_pct"] == pytest.approx(100 * hold_excess / 18, abs=1e-3)
    assert summary["n_censored_terminal"] == 1 and summary["n_ok"] == 1 and summary["n_identity_uncertain"] == 1
    coverage = evaluate.coverage_rows(series, top=20, holding=20)
    assert [row["list_type"] for row in coverage] == ["mixed", "stock"]
    assert coverage[:1] == [{"variant": "v15", "profile": "balanced", "list_type": "mixed", "top": 20, "holding": 20,
                         "days": 1, "days_observable": 1, "selected_names": 3, "observable_names": 1,
                         "observable_share": pytest.approx(1 / 3, abs=1e-4), "empty_slots": 17,
                         **{f"n_{s}": (1 if s in {"ok", "censored_terminal", "identity_uncertain"} else 0)
                            for s in evaluate.OBSERVABLE_STATUSES + evaluate.UNOBSERVABLE_STATUSES}}]


def test_a_date_with_nothing_observable_is_none_not_zero(long_market):
    prices, _ = long_market
    record = {"session": LONG[0], "lists": {"v15/balanced/mid": {"rows": [_row("GONE"), _row("TEMP")]}}}
    series, _ = evaluate.evaluate([record], prices)
    point = series[("v15/balanced/mid", "mixed", 20, 20)][0]
    assert point["slot"] is None  # nothing observable: no primary value, not zero
    # The bounds are the full-sample sensitivity and value every censored name: they exist on such a day.
    spy = prices.forward_to("SPY", LONG[0], LONG[20])
    assert point["slot_zero"] == pytest.approx(0.0) and point["slot_loss"] == pytest.approx(2 * (-1.0 - spy) / 20)
    assert point["slot_legacy"] is not None  # the pre-fix number still exists, labelled legacy
    summary = evaluate.summarize([point], 20)
    assert summary["mean_slot_pct"] is None and summary["mean_slot_loss_pct"] is not None
    only_failed_entries = {"session": LONG[0], "lists": {"v15/balanced/mid": {"rows": [_row("BCPC", sources=["BCPC", "BCpC"])]}}}
    point = evaluate.evaluate([only_failed_entries], prices)[0][("v15/balanced/mid", "mixed", 20, 20)][0]
    assert point["slot"] is None and point["slot_zero"] == 0.0  # 19 empty slots at SPY, the collision removed
    empty = {"session": LONG[0], "lists": {"v15/balanced/mid": {"rows": []}}}
    series, _ = evaluate.evaluate([empty], prices)
    assert series[("v15/balanced/mid", "mixed", 20, 20)][0]["slot"] == 0.0  # twenty pre-decided empty slots


def test_paired_comparisons_drop_dates_one_side_cannot_observe_and_report_levels(long_market):
    prices, _ = long_market
    records = []
    for signal in (LONG[0], LONG[5], LONG[10], LONG[15]):
        lists = {}
        for view in ("short", "mid", "long"):
            lists[f"v15/balanced/{view}"] = {"rows": [_row("HOLD")]}
            lists[f"cand/balanced/{view}"] = {"rows": [_row("GONE" if signal == LONG[10] else "HOLD")]}
        records.append({"session": signal, "lists": lists})
    series, _ = evaluate.evaluate(records, prices)
    rows = paired.paired_rows(series, "v15")
    diff = next(r for r in rows if r["comparison"] == "variant_minus_baseline_mixed" and r["metric"] == "slot"
                and r["holding"] == 20 and r["period"] == "ALL")
    assert diff["days"] == 3 and diff["days_removed"] == 1 and diff["mean_diff_pct"] == 0.0
    legacy = next(r for r in rows if r["comparison"] == "variant_minus_baseline_mixed" and r["metric"] == "slot_legacy"
                  and r["holding"] == 20 and r["period"] == "ALL")
    assert legacy["days"] == 4 and legacy["days_removed"] == 0 and legacy["mean_diff_pct"] < 0
    level = next(r for r in rows if r["comparison"] == "level_mixed" and r["variant"] == "v15" and r["metric"] == "slot"
                 and r["holding"] == 20 and r["period"] == "ALL")
    assert level["days"] == 4 and level["mean_diff_pct"] > 0 and level["nw_t"] is not None


# ---------------------------------------------------------------- ledger


def _records(signals, names, key="v15/balanced/mid"):
    return [{"session": signal, "lists": {key: {"rows": [_row(name) for name in names]}}} for signal in signals]


def test_ledger_keeps_the_overnight_move_the_legacy_curve_lost(tmp_path):
    """The review's second synthetic case: 100 -> 50 overnight between two flat weeks."""
    sessions = LONG[:20]
    bars = {"SPY": _flat(sessions, 100.0),
            "X": {**{day: (100.0, 100.0) for day in sessions[1:6]}, **{day: (50.0, 50.0) for day in sessions[6:]}}}
    prices = evaluate.Prices(_cache(tmp_path, bars, sessions=sessions), sessions[0])
    records = _records([sessions[0], sessions[5]], ["X"])
    ranked = {(key, "mixed", record["session"]): ["X"] for record in records for key in record["lists"]}
    spy_week = {record["session"]: prices.forward_legacy("SPY", record["session"], 5)[0] for record in records}
    legacy = backtest.legacy_overlap_curve([r["session"] for r in records], ranked, "v15/balanced/mid", "mixed", prices,
                                           spy_week, n_cohorts=2, top=1)
    assert [round(p["portfolio"], 6) for p in legacy] == [1.0, 1.0]  # the old cohort shows 0% in both weeks
    ledger = backtest.ledger_curve(records, prices, "v15/balanced/mid", "mixed", holding=10, spacing=5, top=1)
    by_day = {point["date"]: point for point in ledger}
    assert ledger[0]["date"] == sessions[1] and by_day[sessions[5]]["value"] == pytest.approx(1.0)
    assert by_day[sessions[6]]["value"] == pytest.approx(0.25 + 0.5)  # sleeve 1 halves overnight, sleeve 2 just bought
    assert by_day[sessions[10]]["value"] == pytest.approx(0.75)  # sleeve 1 sold at the T+10 close for 0.25
    assert by_day[sessions[15]]["value"] == pytest.approx(0.75) and by_day[sessions[15]]["invested_sleeves"] == 0
    assert by_day[sessions[6]]["first_fully_invested"] == sessions[6]


def test_ledger_applies_splits_costs_empty_slots_and_the_spy_benchmark(tmp_path):
    sessions = LONG[:12]
    bars = {"SPY": _flat(sessions, 100.0, 100.0),
            # SPLIT: 2-for-1 on sessions[3]; raw price halves, the holding is worth the same.
            "SPLIT": {**{day: (20.0, 20.0) for day in sessions[:3]}, **{day: (10.0, 10.0) for day in sessions[3:]}},
            "FLAT": _flat(sessions, 10.0)}
    prices = evaluate.Prices(_cache(tmp_path, bars, [("SPLIT", sessions[3], 1.0, 2.0)], sessions=sessions), sessions[0])
    records = _records([sessions[0]], ["SPLIT"])
    curve = backtest.ledger_curve(records, prices, "v15/balanced/mid", "mixed", holding=5, spacing=5, top=2)
    # Slot 1 holds SPLIT (unchanged value through the split); slot 2 is a pre-decided empty slot in SPY.
    assert all(point["value"] == pytest.approx(1.0) for point in curve)
    costly = backtest.ledger_curve(_records([sessions[0]], ["FLAT"]), prices, "v15/balanced/mid", "mixed",
                                   holding=5, spacing=5, top=1, cost_bps=100.0)
    assert costly[-1]["value"] == pytest.approx(0.99 * 0.99)
    assert costly[0]["value"] == pytest.approx(0.99)  # the entry cost is paid on day one, the exit cost at the sale
    benchmark = backtest.ledger_curve(records, prices, "v15/balanced/mid", "mixed", holding=5, spacing=5, top=2,
                                      benchmark=True, cost_bps=100.0)
    assert benchmark[-1]["value"] == pytest.approx(0.99 * 0.99)  # SPY pays the same costs under the same rules
    # A name with no bar at T+1 stays in cash; an identity collision too. SPY rises, so buying it would show.
    records = [{"session": sessions[0], "lists": {"v15/balanced/mid": {"rows": [
        _row("FLAT"), {"ticker": "BCPC", "sort_score": 1.0, "stock_or_etf_track": "stock", "source_tickers": ["BCPC", "BCpC"]},
        _row("LATER")]}}}]
    bars["LATER"] = {day: (10.0, 10.0) for day in sessions[2:]}
    bars["SPY"] = {day: (100.0 + 5 * i, 100.0 + 5 * i) for i, day in enumerate(sessions)}
    prices = evaluate.Prices(_cache(tmp_path / "c", bars, sessions=sessions), sessions[0])
    curve = backtest.ledger_curve(records, prices, "v15/balanced/mid", "mixed", holding=5, spacing=5, top=3)
    assert all(point["value"] == pytest.approx(1.0) for point in curve)
    with_empty = backtest.ledger_curve(_records([sessions[0]], ["FLAT"]), prices, "v15/balanced/mid", "mixed",
                                       holding=5, spacing=5, top=2)
    assert with_empty[-1]["value"] > 1.0  # the pre-decided empty slot did buy the rising SPY


def test_ledger_applies_a_split_on_the_entry_session_once(tmp_path):
    """The entry open is already post-split; the valuation loop must not multiply the shares again."""
    sessions = LONG[:8]
    spy = _flat(sessions, 100.0)
    for name, before, after, split in (("FWD", 20.0, 10.0, (1.0, 2.0)), ("REV", 1.0, 10.0, (10.0, 1.0))):
        bars = {"SPY": spy, name: {**{sessions[0]: (before, before)}, **{day: (after, after) for day in sessions[1:]}}}
        prices = evaluate.Prices(_cache(tmp_path / name, bars, [(name, sessions[1], *split)], sessions=sessions), sessions[0])
        assert prices.observe(name, sessions[0], 5).ret == pytest.approx(0.0)
        curve = backtest.ledger_curve(_records([sessions[0]], [name]), prices, "v15/balanced/mid", "mixed",
                                      holding=5, spacing=5, top=1)
        assert all(point["value"] == pytest.approx(1.0) for point in curve), name
    # A split on the day after entry is applied once and the value is unchanged.
    bars = {"SPY": spy, "MID": {**{day: (20.0, 20.0) for day in sessions[:2]}, **{day: (10.0, 10.0) for day in sessions[2:]}}}
    prices = evaluate.Prices(_cache(tmp_path / "mid", bars, [("MID", sessions[2], 1.0, 2.0)], sessions=sessions), sessions[0])
    curve = backtest.ledger_curve(_records([sessions[0]], ["MID"]), prices, "v15/balanced/mid", "mixed", holding=5, spacing=5, top=1)
    assert all(point["value"] == pytest.approx(1.0) for point in curve)


def test_ledger_keeps_censored_shares_and_applies_a_successor_split_at_the_switch(tmp_path):
    sessions = LONG[:30]
    spy = _flat(sessions, 100.0)
    # GONE stops at index 3; a later "split" under the reused symbol must not touch the frozen position.
    bars = {"SPY": spy, "GONE": {day: (10.0, 10.0) for day in sessions[1:4]}}
    prices = evaluate.Prices(_cache(tmp_path / "a", bars, [("GONE", sessions[6], 1.0, 4.0)], sessions=sessions), sessions[0])
    curve = backtest.ledger_curve(_records([sessions[0]], ["GONE"]), prices, "v15/balanced/mid", "mixed", holding=8, spacing=5, top=1)
    by_day = {point["date"]: point["value"] for point in curve}
    assert by_day[sessions[7]] == pytest.approx(0.5 + 0.5)  # two sleeves; the censored slot stays at its last close
    # OLD becomes NEW with a 4-for-1 split filed under NEW on its first day (price 40 -> 10): the ledger applies it once.
    bars = {"SPY": spy, "OLD": {day: (40.0, 40.0) for i, day in enumerate(sessions) if 1 <= i <= 15},
            "NEW": {day: (10.0, 10.0) for i, day in enumerate(sessions) if i >= 17}}
    company = lambda ticker, cik: {"ticker": ticker, "cik": cik, "composite_figi": None, "type": "CS", "active": True}
    directory = _directory(tmp_path, {sessions[0]: [company("OLD", "1")], sessions[16]: [company("NEW", "1")]})
    prices = evaluate.Prices(_cache(tmp_path / "b", bars, [("NEW", sessions[17], 1.0, 4.0)], sessions=sessions), sessions[0], directory)
    outcome = prices.observe("OLD", sessions[0], 20)
    assert outcome.status == "renamed" and outcome.ret == pytest.approx(0.0)
    curve = backtest.ledger_curve(_records([sessions[0]], ["OLD"]), prices, "v15/balanced/mid", "mixed", holding=20, spacing=5, top=1)
    by_day = {point["date"]: point["value"] for point in curve}
    assert by_day[sessions[16]] == pytest.approx(1.0) and by_day[sessions[17]] == pytest.approx(1.0)
    assert by_day[sessions[20]] == pytest.approx(1.0)  # 4 sleeves; only the first holds anything


def test_ledger_sleeves_follow_the_calendar_and_report_passive_spy(tmp_path):
    sessions = LONG[:25]
    bars = {"SPY": {day: (100.0 + i, 100.0 + i) for i, day in enumerate(sessions)}, "UP": {day: (10.0 + i, 10.0 + i) for i, day in enumerate(sessions)}}
    prices = evaluate.Prices(_cache(tmp_path, bars, sessions=sessions), sessions[0])
    # Signals at index 0 and 10; the date at index 5 has no record, so its sleeve stays idle.
    curve = backtest.ledger_curve(_records([sessions[0], sessions[10]], ["UP"]), prices, "v15/balanced/mid", "mixed", holding=10, spacing=5, top=1)
    by_day = {point["date"]: point for point in curve}
    assert by_day[sessions[6]]["invested_sleeves"] == 1  # sleeve 0 holds UP; sleeve 1 idle (no record at index 5)
    assert by_day[sessions[11]]["invested_sleeves"] == 1  # index 10 maps back to sleeve 0, re-entered after its exit at 10
    assert by_day[sessions[11]]["value"] == pytest.approx(0.5 * (20.0 / 11.0) * (21.0 / 21.0) + 0.5)
    assert curve[0]["spy_passive"] == pytest.approx(1.0) and by_day[sessions[20]]["spy_passive"] == pytest.approx(120.0 / 101.0)


def test_ledger_values_censored_positions_under_the_three_bounds(tmp_path):
    sessions = LONG[:15]
    spy = {day: (100.0 + i, 100.0 + i) for i, day in enumerate(sessions)}
    bars = {"SPY": spy, "GONE": {day: (10.0, 10.0) for day in sessions[1:4]}}  # last bar at index 3
    prices = evaluate.Prices(_cache(tmp_path, bars, sessions=sessions), sessions[0])
    records = _records([sessions[0]], ["GONE"])
    values = {}
    for bound in evaluate.BOUNDS:
        curve = backtest.ledger_curve(records, prices, "v15/balanced/mid", "mixed", holding=8, spacing=5, top=1, bound=bound)
        values[bound] = {point["date"]: point["value"] for point in curve}
    # ceil(8 / 5) = 2 sleeves: the second sleeve never gets a signal and idles at 0.5.
    outcome = prices.observe("GONE", sessions[0], 8)
    assert outcome.status == "censored_unverified" and outcome.first_gap_day == sessions[4]
    assert values["legacy"][sessions[8]] == pytest.approx(0.5 + 0.5)
    assert values["zero"][sessions[8]] == pytest.approx(0.5 + 0.5 * spy[sessions[8]][1] / spy[sessions[3]][1])
    assert values["loss"][sessions[8]] == pytest.approx(0.5)
    assert values["legacy"][sessions[4]] == pytest.approx(1.0) and values["loss"][sessions[4]] == pytest.approx(0.5)
    assert values["loss"][sessions[3]] == pytest.approx(1.0)  # worthless only from the first missing bar


def test_legacy_weekly_curve_reads_the_legacy_slot(tmp_path):
    points = [{"day": "2026-09-14", "slot": None, "slot_legacy": 0.02}, {"day": "2026-09-21", "slot": 0.5, "slot_legacy": -0.01}]
    curve = backtest.legacy_weekly_curve(points, {"2026-09-14": 0.01, "2026-09-21": 0.0})
    assert [round(p["portfolio"], 6) for p in curve] == [round(1.03, 6), round(1.03 * 0.99, 6)]
    assert [round(p["spy"], 6) for p in curve] == [1.01, 1.01]


def test_export_backtest_writes_legacy_and_ledger_curves_with_stated_assumptions(tmp_path, monkeypatch):
    sessions = LONG[:30]
    bars = {"SPY": {day: (100.0 + i, 100.0 + i) for i, day in enumerate(sessions)},
            "UP": {day: (10.0 + 0.2 * i, 10.0 + 0.2 * i) for i, day in enumerate(sessions)},
            "GONE": {day: (5.0, 5.0) for i, day in enumerate(sessions) if i <= 8}}
    _cache(tmp_path, bars, sessions=sessions)
    replay_dir = tmp_path / "replay"
    replay_dir.mkdir()
    for signal in (sessions[0], sessions[5], sessions[10]):
        lists = {f"v15/balanced/{view}": {"n": 2, "watch_n": 2, "rows": [_row("UP"), _row("GONE")]} for view in ("short", "mid", "long")}
        record = {"session": signal, "status": "scored", "gate_ok": True, "lists": lists}
        with gzip.open(replay_dir / f"{signal}.json.gz", "wt") as handle:
            json.dump(record, handle)
    out = tmp_path / "out"
    monkeypatch.setattr(sys, "argv", ["export_backtest.py", "--db", str(tmp_path / "replay.sqlite"), "--replay", str(replay_dir),
                                      "--out", str(out), "--variants", "v15", "--cost-bps", "10"])
    backtest.main()
    curves = list(__import__("csv").DictReader((out / "equity_curves.csv").open()))
    kinds = {row["holding"] for row in curves}
    assert kinds == {"legacy_weekly", "legacy_overlap13w", "ledger_h63", "ledger_weekly"}
    assert {row["cost_bps"] for row in curves if row["holding"].startswith("ledger")} == {"0.0", "10.0"}
    assert {row["bound"] for row in curves if row["holding"] == "ledger_h63"} == {"legacy", "zero", "loss"}
    assert {row["bound"] for row in curves if row["holding"] == "ledger_weekly"} == {"legacy", "zero", "loss"}
    ledger_rows = [row for row in curves if row["holding"].startswith("ledger")]
    assert all(row["spy_passive"] != "" and row["relative_passive"] != "" for row in ledger_rows)
    assert all(row["bound"] == "legacy" and row["cost_bps"] == "0" for row in curves if row["holding"].startswith("legacy"))
    end = json.loads((out / "ledger_end_values.json").read_text())
    assert end["cost_bps"] == 10 and end["rules"]["empty_slot"] == "SPY" and end["identity_verification"] is False
    assert {item["holding"] for item in end["end_values"]} == {"ledger_h63", "ledger_weekly"}
    weekly = [item for item in end["end_values"] if item["holding"] == "ledger_weekly"]
    assert {item["bound"] for item in weekly} == {"legacy", "zero", "loss"}
    assert all(item["spy_passive"] and item["relative_passive"] and item["start_date"] < item["end_date"] for item in end["end_values"])
    daily = list(__import__("csv").DictReader((out / "daily_series.csv").open()))
    assert {"slot_excess_pct", "slot_legacy_pct", "slot_loss_pct", "observable", "unobservable", "empty_slots"} <= set(daily[0])
    # GONE is censored without a directory: the primary excess leaves it out, the legacy column keeps the back-fill.
    point = next(row for row in daily if row["holding"] == "20" and row["list_type"] == "stock" and row["view"] == "mid")
    assert point["unobservable"] == "1" and point["observable"] == "1" and point["empty_slots"] == "18"
    assert point["slot_excess_pct"] != point["slot_legacy_pct"]


def test_result_pack_reads_legacy_and_fixed_results_and_lists_unverifiable_data(tmp_path, monkeypatch):
    pack_module = _load("result_pack")
    legacy, fixed = tmp_path / "stage1", tmp_path / "stage1_reeval"
    legacy.mkdir(); fixed.mkdir()
    header = "list_type,profile,variant,h63_ALL,h63_P1,h63_P2,h20_ALL,h5_ALL,median_listed\n"
    (legacy / "primary.csv").write_text(header + "stock,balanced,v15,0.5,0.4,0.6,0.1,0.0,20\nstock,balanced,tilt_b,0.9,0.8,1.0,0.1,0.0,20\n")
    (fixed / "primary.csv").write_text(
        "list_type,profile,variant,h63_ALL,h63_P1,h63_P2,h63_ALL_legacy,h63_ALL_zero,h63_ALL_loss,h20_ALL,h5_ALL,median_listed,observable_share_h63\n"
        "stock,balanced,v15,0.3,0.2,0.4,0.5,0.25,-0.1,0.1,0.0,20,0.95\nstock,balanced,tilt_b,0.7,0.6,0.8,0.9,0.6,0.2,0.1,0.0,20,0.94\n")
    (fixed / "coverage.csv").write_text("variant,profile,list_type,top,holding,days,observable_share\nv15,balanced,stock,20,63,165,0.95\n")
    (fixed / "paired.csv").write_text(
        "comparison,metric,variant,profile,holding,period,days,days_removed,mean_diff_pct,nw_t,share_days_positive\n"
        "variant_minus_baseline_stock,slot,tilt_b,balanced,63,ALL,160,5,0.4,1.3,0.55\n"
        "level_stock,slot,tilt_b,balanced,63,ALL,160,5,0.7,0.5,0.52\n"
        "level_stock,slot,v15,balanced,63,ALL,160,5,0.3,0.2,0.5\n"
        "variant_minus_baseline_stock,slot,tilt_b,balanced,20,ALL,165,0,0.1,0.4,0.5\n")
    (fixed / "rules.json").write_text(json.dumps({"exit_lag_sessions": 5}))
    backtest = tmp_path / "backtest"
    backtest.mkdir()
    (backtest / "ledger_end_values.json").write_text(json.dumps({"cost_bps": 10, "rules": {"empty_slot": "SPY"},
                                                                 "identity_verification": True, "end_values": [{"relative": 1.1}]}))
    out = tmp_path / "pack.json"
    monkeypatch.setattr(sys, "argv", ["result_pack.py", "--stage", f"stage1={fixed}:{legacy}", "--compare", "tilt_b=v15:tilt_b:balanced",
                                      "--backtest", str(backtest), "--out", str(out), "--note", "synthetic"])
    pack_module.main()
    pack = json.loads(out.read_text())
    stage = pack["stages"][0]
    tilt = next(v for v in stage["variants"] if v["variant"] == "tilt_b")
    assert tilt["h63_pre_fix"] == 0.9 and tilt["h63_primary"] == 0.7 and tilt["h63_fix_delta"] == pytest.approx(-0.2)
    assert tilt["h63_bound_loss"] == 0.2 and tilt["observable_share_h63"] == 0.94
    assert stage["rules"] == {"exit_lag_sessions": 5} and stage["coverage"][0]["observable_share"] == 0.95
    assert len(stage["paired_h63"]) == 3  # the 20-session row is left out
    rows = pack["comparisons"]["tilt_b"]["rows"]
    assert {row["what"] for row in rows} == {"tilt_b minus v15, stock list", "tilt_b own excess over SPY, stock list",
                                             "v15 own excess over SPY, stock list"}
    assert pack["comparisons"]["tilt_b"]["baseline_verified"] is False  # no decision.json in this synthetic stage
    assert pack["ledgers"][0]["cost_bps"] == 10 and pack["ledgers"][0]["end_values"] == [{"relative": 1.1}]
    assert pack["notes"] == ["synthetic"] and len(pack["unverifiable_execution_data"]) >= 5


def test_result_pack_resolves_a_comparison_in_the_stage_evaluated_against_its_baseline(tmp_path, monkeypatch):
    pack_module = _load("result_pack")
    header = "comparison,metric,variant,profile,holding,period,days,days_removed,mean_diff_pct,nw_t,share_days_positive\n"
    stage1, stage2 = tmp_path / "stage1", tmp_path / "stage2"
    for folder in (stage1, stage2):
        folder.mkdir()
        (folder / "primary.csv").write_text("list_type,profile,variant,h63_ALL\nstock,conservative,v16,-2.9\n")
    # Stage 1 was evaluated against v16 and holds v16's own level, plus cons17's difference.
    (stage1 / "decision.json").write_text(json.dumps({"baseline": "v16"}))
    (stage1 / "paired.csv").write_text(header +
        "level_stock,slot,v16,conservative,63,ALL,165,0,-2.908,-2.98,0.3\n"
        "variant_minus_baseline_stock,slot,cons17,conservative,63,ALL,163,2,3.519,3.95,0.7\n"
        "stock_minus_mixed,slot,v16,conservative,63,ALL,165,0,0.343,2.69,0.6\n")
    # Stage 2 was evaluated against v16 too and holds S1's difference.
    (stage2 / "decision.json").write_text(json.dumps({"baseline": "v16"}))
    (stage2 / "paired.csv").write_text(header +
        "level_stock,slot,v16,conservative,63,ALL,137,0,-2.884,-2.48,0.3\n"
        "variant_minus_baseline_stock,slot,cons17+nofund,conservative,63,ALL,136,1,3.748,3.59,0.7\n"
        "level_stock,slot,cons17+nofund,conservative,63,ALL,136,1,0.86,0.75,0.5\n"
        "variant_minus_baseline_stock,slot,cons17,conservative,63,ALL,136,1,3.9,3.7,0.7\n")
    # A third directory evaluated against V1 for the V2 rule.
    vs_v1 = tmp_path / "vs_v1"
    vs_v1.mkdir()
    (vs_v1 / "primary.csv").write_text("list_type,profile,variant,h63_ALL\nstock,conservative,cons17+nofund,0.86\n")
    (vs_v1 / "decision.json").write_text(json.dumps({"baseline": "cons17+nofund"}))
    (vs_v1 / "paired.csv").write_text(header +
        "variant_minus_baseline_stock,slot,cons17_atr125+nofund,conservative,63,ALL,160,3,-0.5,-1.1,0.45\n"
        "level_stock,slot,cons17+nofund,conservative,63,ALL,160,3,0.86,0.75,0.5\n")
    out = tmp_path / "pack.json"
    monkeypatch.setattr(sys, "argv", ["result_pack.py", "--stage", f"stage1={stage1}", "--stage", f"stage2={stage2}",
                                      "--stage", f"v2_vs_v1={vs_v1}",
                                      "--compare", "S1=v16:cons17+nofund:conservative",
                                      "--compare", "cons17=v16:cons17:conservative",
                                      "--compare", "cons17_stage2=v16:cons17:conservative@stage2",
                                      "--compare", "cons17_elsewhere=v16:cons17:conservative@v2_vs_v1",
                                      "--compare", "stock_only=v16:v16:conservative",
                                      "--compare", "V2=cons17+nofund:cons17_atr125+nofund:conservative",
                                      "--compare", "missing=v16:nobody:conservative", "--out", str(out)])
    pack_module.main()
    pack = json.loads(out.read_text())
    assert pack["stages"][0]["baseline"] == "v16" and pack["stages"][2]["baseline"] == "cons17+nofund"
    s1 = pack["comparisons"]["S1"]
    assert s1["stage"] == "stage2"  # not stage1, whose only matching rows were the baseline's own level
    assert [row["mean_diff_pct"] for row in s1["rows"] if row["comparison"] == "variant_minus_baseline_stock"] == [3.748]
    assert pack["comparisons"]["cons17"]["stage"] == "stage1" and pack["comparisons"]["cons17"]["baseline_verified"] is True
    # The same candidate appears in two stages: the first wins unless @stage pins the comparison.
    pinned = pack["comparisons"]["cons17_stage2"]
    assert pinned["stage"] == "stage2"
    assert [row["mean_diff_pct"] for row in pinned["rows"] if row["comparison"] == "variant_minus_baseline_stock"] == [3.9]
    assert "cons17_elsewhere" not in pack["comparisons"]  # pinned to a stage evaluated against another baseline
    assert [row["comparison"] for row in pack["comparisons"]["stock_only"]["rows"]] == ["stock_minus_mixed"]
    v2 = pack["comparisons"]["V2"]
    assert v2["stage"] == "v2_vs_v1" and v2["rows"][0]["mean_diff_pct"] == -0.5
    assert "missing" not in pack["comparisons"]


# ---------------------------------------------------------------- replay helpers (unchanged)


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
