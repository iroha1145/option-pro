"""The radar evaluation on synthetic ledgers and a synthetic daily database.

Checks the trigger extraction, the intraday entry and split-adjusted exits, the SPY
excess, the censoring statuses, the daily aggregation with its bounds, the views,
the funnel counts and the pre-registered adoption rules.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "research" / "breakout_radar" / "replay_v1"
if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))

from harness import evaluation as ev  # noqa: E402

NY = ZoneInfo("America/New_York")
SESSIONS = [date(2024, 1, 2) + timedelta(days=n) for n in range(0, 120)]
SESSIONS = [d for d in SESSIONS if d.weekday() < 5][:80]


def _daily_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        "CREATE TABLE market_sessions (session_date TEXT PRIMARY KEY, fetched_at TEXT, rows INTEGER, x TEXT, status TEXT);"
        "CREATE TABLE raw_daily_bars (ticker TEXT, session_date TEXT, timestamp_ms INTEGER, open REAL, high REAL, low REAL, close REAL, volume REAL, vwap REAL, transactions INTEGER, PRIMARY KEY (ticker, session_date));"
        "CREATE TABLE splits (ticker TEXT, execution_date TEXT, split_from REAL, split_to REAL, id TEXT);"
        "CREATE TABLE split_captures (id TEXT);"
    )
    for day in SESSIONS:
        connection.execute("INSERT INTO market_sessions VALUES (?, ?, ?, ?, ?)", (day.isoformat(), "x", 3, "x", "OK"))
    for index, day in enumerate(SESSIONS):
        # SPY drifts up 0.1% a session; GOOD rises 1% a session; SPLT doubles its price at a 2:1 split
        # on session 30 (raw price halves); GONE stops trading after session 12.
        spy = 400 * (1.001 ** index)
        good = 10 * (1.01 ** index)
        splt_raw = 20 * (1.005 ** index) / (2 if index >= 30 else 1)
        rows = [("SPY", spy), ("GOOD", good), ("SPLT", splt_raw)]
        if index <= 12:
            rows.append(("GONE", 5.0))
        for ticker, close in rows:
            connection.execute(
                "INSERT INTO raw_daily_bars VALUES (?, ?, 0, ?, ?, ?, ?, 1000000, ?, 10)",
                (ticker, day.isoformat(), close * 0.99, close * 1.01, close * 0.98, close, close),
            )
    connection.execute("INSERT INTO splits VALUES ('SPLT', ?, 1.0, 2.0, 'E1')", (SESSIONS[30].isoformat(),))
    connection.commit()
    connection.close()


def _scan_record(day: date, hour: int, minute: int, events: list[dict], transitions: list[dict], *, prefilter=120, listed=50, structures=35, benchmark_open=None) -> dict:
    stamp = datetime(day.year, day.month, day.day, hour, minute, tzinfo=NY)
    return {
        "variant": "baseline", "as_of": stamp.isoformat(), "session": "regular", "kind": "regular", "warmup": False,
        "status": "completed", "scan_run_id": f"scan_{day}_{hour}{minute}", "prefilter_count": prefilter,
        "candidate_count": listed, "event_count": len(events), "structures": [{"ticker": f"T{i}"} for i in range(structures)],
        "events": events, "transitions": transitions, "benchmark_next_bar_open": benchmark_open,
    }


def _event(event_id: str, ticker: str, day: date, price: float, origin: str = "DAILY_BASE_BREAKOUT", *, alert=70.0, strength=50.0, next_open=None, eligibility="allowed") -> dict:
    return {
        "event_id": event_id, "ticker": ticker, "trading_date": day.isoformat(), "origin_setup_type": origin, "setup_type": origin,
        "lifecycle_state": "TRIGGERED", "event_price": price, "next_bar_open": next_open, "pivot_id": f"p-{ticker}",
        "asset_type": "common_stock", "carryover": False,
        "scores": {"alert_priority_score": alert, "intrinsic_strength_score": strength},
        "features": {"market_shape_state": "BULL_TREND", "market_eligibility": eligibility},
    }


def _write_ledger(folder: Path, day: date, records: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / f"{day.isoformat()}.jsonl.gz", "wt") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


@pytest.fixture(scope="module")
def world(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("radar_eval")
    _daily_db(root / "daily.sqlite")
    d0, d1 = SESSIONS[2], SESSIONS[3]
    good0 = 10 * (1.01 ** 2)
    # Baseline: day 0 has GOOD (next bar open = its close that day, entered at 10:05), SPLT and GONE; day 1 has GOOD again (ORB).
    base = root / "seg1" / "baseline"
    _write_ledger(base / "ledger", d0, [
        _scan_record(d0, 9, 35, [], [], benchmark_open=None),
        _scan_record(
            d0, 10, 2,
            [_event("e1", "GOOD", d0, good0 * 0.99, next_open=good0), _event("e2", "SPLT", d0, 20 * 1.005 ** 2, next_open=20 * 1.005 ** 2, alert=40),
             _event("e3", "GONE", d0, 5.0, next_open=5.0, alert=65, strength=70)],
            [{"event_id": "e1", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"},
             {"event_id": "e2", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"},
             {"event_id": "e3", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"}],
            benchmark_open=400 * 1.001 ** 2, prefilter=170, listed=70, structures=40,
        ),
        _scan_record(d0, 10, 7, [_event("e1", "GOOD", d0, good0)], [{"event_id": "e1", "from_state": "TRIGGERED", "to_state": "TRIGGERED", "reason": "again", "evidence_at": "x"}]),
        _scan_record(d0, 15, 55, [_event("e3", "GONE", d0, 4.9)], [{"event_id": "e3", "from_state": "TRIGGERED", "to_state": "FAILED", "reason": "x", "evidence_at": datetime(d0.year, d0.month, d0.day, 15, 55, tzinfo=NY).isoformat()}]),
    ])
    good1 = 10 * (1.01 ** 3)
    _write_ledger(base / "ledger", d1, [
        _scan_record(d1, 9, 40, [_event("e4", "GOOD", d1, good1, origin="OPENING_RANGE_BREAKOUT", next_open=good1, alert=80)],
                     [{"event_id": "e4", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"}], benchmark_open=400 * 1.001 ** 3),
        _scan_record(d1, 12, 0, [], [], prefilter=90, listed=40, structures=20),
        {"variant": "baseline", "as_of": datetime(d1.year, d1.month, d1.day, 16, 30, tzinfo=NY).isoformat(), "kind": "t1", "warmup": False, "status": "completed"},
    ])
    with gzip.open(base / "research_bundle.json.gz", "wt") as handle:
        json.dump({"events": [], "shadows": [], "transitions": [], "heads": [], "t1_current": [{"event_id": "e1", "status": "met"}, {"event_id": "e2", "status": "not_met"}]}, handle)
    # A candidate that dropped SPLT and GONE (a narrower funnel) but kept GOOD both days.
    cand = root / "seg1" / "disc5"
    _write_ledger(cand / "ledger", d0, [
        _scan_record(d0, 10, 2, [_event("c1", "GOOD", d0, good0 * 0.99, next_open=good0)],
                     [{"event_id": "c1", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"}], benchmark_open=400 * 1.001 ** 2),
    ])
    _write_ledger(cand / "ledger", d1, [
        _scan_record(d1, 9, 40, [_event("c2", "GOOD", d1, good1, origin="OPENING_RANGE_BREAKOUT", next_open=good1)],
                     [{"event_id": "c2", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"}], benchmark_open=400 * 1.001 ** 3),
    ])
    # A warm-up day in a second segment directory must be ignored, and a duplicated day taken once.
    seg2 = root / "seg2" / "baseline"
    _write_ledger(seg2 / "ledger", d1, [
        {**_scan_record(d1, 9, 40, [_event("dup", "GOOD", d1, good1, next_open=good1)], [{"event_id": "dup", "from_state": "WATCHING", "to_state": "TRIGGERED", "reason": "x", "evidence_at": "x"}]), "warmup": True},
    ])
    return {"root": root, "db": root / "daily.sqlite", "dirs": [root / "seg1", root / "seg2"], "d0": d0, "d1": d1}


def test_triggers_and_funnel_are_read_once_per_day_and_carry_the_fields(world: dict) -> None:
    triggers, funnel, info = ev.read_variant(world["dirs"], "baseline")
    assert [t.event_id for t in triggers] == ["e1", "e2", "e3", "e4"]
    assert info["skipped_duplicate_days"] == 0 and info["scans"] == 6  # the t1 record and the warm-up scan are skipped
    e1, e3, e4 = triggers[0], triggers[2], triggers[3]
    assert e1.day == world["d0"].isoformat() and e1.minute == 10 * 60 + 2 and e1.bucket == "morning"
    assert e1.next_bar_open == pytest.approx(10 * 1.01 ** 2) and e1.benchmark_open == pytest.approx(400 * 1.001 ** 2)
    assert e1.t1_status == "met" and e3.t1_status is None
    assert e3.failed_at is not None and e1.failed_at is None
    assert e4.origin == "OPENING_RANGE_BREAKOUT" and e4.bucket == "open30"
    day0 = funnel[world["d0"].isoformat()]
    # Structures per scan were 35, 40, 35, 35: cuts at 30 of 5, 10, 5 and 5.
    assert (day0.scans, day0.triggers, day0.cut_150, day0.cut_60, day0.cut_30) == (4, 3, 20, 10, 25)


def test_intraday_outcomes_split_adjust_censor_and_measure_excess(world: dict) -> None:
    connection = sqlite3.connect(f"file:{world['db']}?mode=ro", uri=True)
    prices = ev.RadarPrices(connection, SESSIONS[0].isoformat())
    triggers, _f, _i = ev.read_variant(world["dirs"], "baseline")
    by_id = {t.event_id: t for t in triggers}
    # GOOD: entered at the day-0 close level, exits 20 sessions later; excess = 1.01^20 - 1.001^20.
    good = ev.evaluate_trigger(by_id["e1"], prices, 20, "next_bar")
    assert good.status == "ok" and good.benchmark_basis == "bar"
    assert good.ret == pytest.approx(1.01 ** 20 - 1) and good.excess == pytest.approx((1.01 ** 20 - 1) - (1.001 ** 20 - 1))
    # SPLT: the 2:1 split inside the window halves raw prices; the adjusted return is the 0.5%-a-session drift.
    splt = ev.evaluate_trigger(by_id["e2"], prices, 63, "next_bar")
    assert splt.status == "ok" and splt.ret == pytest.approx(1.005 ** 63 - 1, rel=1e-6)
    # GONE: no bars after session 12 -> censored (no directory to verify), a loss bound exists, no excess.
    gone = ev.evaluate_trigger(by_id["e3"], prices, 20, "next_bar")
    assert gone.status == "censored_unverified" and gone.excess is None and gone.loss_excess is not None and gone.loss_excess < -0.9
    assert gone.legacy_excess is not None and gone.failed_by_next_close is True
    # Control entry at the trigger mark (1% lower) gives a higher return than the next bar open.
    control = ev.evaluate_trigger(by_id["e1"], prices, 20, "trigger_mark")
    assert control.excess > good.excess and control.benchmark_basis == "bar"
    # Without a SPY bar the benchmark enters at its close on the trigger day and says so.
    by_id["e1"].benchmark_open = None
    prices._intraday_outcomes.clear()
    assert ev.evaluate_trigger(by_id["e1"], prices, 20, "next_bar").benchmark_basis == "close"
    by_id["e1"].benchmark_open = 400 * 1.001 ** 2
    # T1 entry at the next session's open (session 3, open = 0.99 x close), exit at the close of session 2 + 5.
    t1 = ev.evaluate_trigger(by_id["e1"], prices, 5, "t1_next_open")
    assert t1.status == "ok" and t1.ret == pytest.approx(1.01 ** 7 / (1.01 ** 3 * 0.99) - 1, rel=1e-6)
    # Horizons past the data are unlabelled.
    assert ev.evaluate_trigger(by_id["e4"], prices, 63 + 60, "next_bar").status == "no_label"


def test_daily_points_bounds_views_and_summary(world: dict) -> None:
    connection = sqlite3.connect(f"file:{world['db']}?mode=ro", uri=True)
    prices = ev.RadarPrices(connection, SESSIONS[0].isoformat())
    triggers, funnel, info = ev.read_variant(world["dirs"], "baseline")
    evaluation = ev.evaluate_variant("baseline", triggers, funnel, info, prices)
    points = evaluation.points[("all", 20, "next_bar")]
    assert [p["day"] for p in points] == [world["d0"].isoformat(), world["d1"].isoformat()]
    day0 = points[0]
    assert day0["n"] == 3 and day0["observable"] == 2 and day0["statuses"]["censored_unverified"] == 1
    assert day0["zero"] == pytest.approx(day0["mean"] * 2 / 3) and day0["loss"] < day0["zero"]
    assert day0["failed_next_close"] == 1 and day0["failed_known"] == 3
    summary = ev.summarize(points, 20)
    assert summary["days"] == 2 and summary["triggers"] == 4 and summary["n_censored_unverified"] == 1
    assert summary["hit_rate"] == pytest.approx(3 / 3) and summary["failed_by_next_close_share"] == pytest.approx(1 / 4)
    assert summary["t"] is None  # two days are too few for a t statistic
    assert ev.newey_west_t([0.01, 0.02, 0.015, 0.012, 0.03], 3) > 0
    # Views: noorb drops the ORB trigger, tod drops the 09:40 one, alert60 keeps 70/65/80, dedup keeps one GOOD per day.
    assert {t.event_id for t in ev.select_view(triggers, "noorb")} == {"e1", "e2", "e3"}
    assert {t.event_id for t in ev.select_view(triggers, "tod")} == {"e1", "e2", "e3"}
    assert {t.event_id for t in ev.select_view(triggers, "alert60")} == {"e1", "e3", "e4"}
    assert {t.event_id for t in ev.select_view(triggers, "strength60")} == {"e3"}
    assert {t.event_id for t in ev.select_view(triggers, "t1")} == {"e1"}
    assert len(ev.select_view(triggers, "dedup")) == 4 and len(ev.select_view(triggers, "top10")) == 4
    assert "origin:OPENING_RANGE_BREAKOUT" in ev.group_views(triggers) and "bucket:open30" in ev.group_views(triggers)
    assert ("t1", 20, "t1_next_open") in evaluation.points and ("all", 20, "trigger_mark") in evaluation.points
    funnel_summary = ev.funnel_summary(funnel)
    assert funnel_summary["days"] == 2 and funnel_summary["days_with_cut_150"] == 1 and funnel_summary["triggers_per_day"] == 2.0


def test_adoption_rules_pair_on_common_days_and_check_the_funnel_candidate(world: dict) -> None:
    connection = sqlite3.connect(f"file:{world['db']}?mode=ro", uri=True)
    prices = ev.RadarPrices(connection, SESSIONS[0].isoformat())
    evaluations = {}
    for name in ("baseline", "disc5"):
        triggers, funnel, info = ev.read_variant(world["dirs"], name)
        evaluations[name] = ev.evaluate_variant(name, triggers, funnel, info, prices, views=["all", *ev.FILTER_VIEWS])
    verdicts = ev.decisions(evaluations, "baseline")
    disc5 = verdicts["variants"]["disc5"]
    assert disc5["paired_days"] == 2 and disc5["rule6_ok"] is True  # SPLT (removed) earned less than GOOD (kept)
    assert disc5["rule4_ok"] is True and disc5["triggers_per_day"] == {"candidate": 1.0, "baseline": 2.0}
    # 2024 is a complete year, so it is compared; both days fall in P1, so rule 1 (P1 and P2) cannot pass.
    assert disc5["rule2_years_compared"] == 1 and disc5["rule1_both_periods_up"] is False and disc5["adopt"] is False
    assert set(verdicts["filters"]) == set(ev.FILTER_VIEWS)
    # Synthetic paired series exercise the period rules directly.
    days = [d.isoformat() for d in SESSIONS[:8]]
    base = {h: [{"day": d, "n": 4, "observable": 4, "mean": 0.01, "hits": 3, "legacy": 0.009, "zero": 0.008, "loss": 0.005, "failed_next_close": 0, "failed_known": 4, "statuses": {}, "benchmark_close_fallback": 0} for d in days] for h in ev.HOLDINGS}
    cand = {h: [{**p, "mean": 0.02, "legacy": 0.019, "zero": 0.018, "loss": 0.015} for p in base[h]] for h in ev.HOLDINGS}
    ev.P1_END = days[3]
    try:
        verdict = ev.decide(cand, base)
    finally:
        ev.P1_END = "2024-12-31"
    assert verdict["rule1_both_periods_up"] and verdict["rule3_ok"] and verdict["rule4_ok"] and verdict["rule5_ok"]
    assert verdict["h20_diff_pp"]["ALL"] == pytest.approx(1.0)
    combo = ev.stage2_verdict({**verdict, "h20_diff_pp": {"ALL": 1.0, "P1": 0.9, "P2": 0.95}}, {"single": {**verdict, "adopt": True}})
    assert combo["best_single"] == "single" and combo["within_0_2pp"] is True


def test_run_writes_the_result_pack_and_tables(world: dict) -> None:
    out = world["root"] / "eval"
    pack = ev.run(world["dirs"], ["baseline", "disc5"], world["db"], out, baseline="baseline", stage2={"disc5": "disc5"})
    assert (out / "metrics.csv").exists() and (out / "events_h20.csv").exists() and (out / "README_tables.md").exists()
    assert pack["coverage"]["baseline"]["triggers"] == 4 and pack["coverage"]["baseline"]["triggers_without_next_bar_open"] == 0
    assert pack["funnel"]["baseline"]["per_scan"]["cut_150"] == pytest.approx(20 / 6, abs=1e-3)
    text = (out / "README_tables.md").read_text()
    assert "| baseline |" in text and "| disc5 |" in text and "取舍" in text
    assert "disc5" in pack["decisions"]["stage2"]
