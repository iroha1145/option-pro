"""The radar replay harness on synthetic frozen data.

Covers the settings-hash proof, the proxy labelling, memo byte-identity, the one-day
warm-up identity (k = 1) and lockstep variants. Real smoke-window checks against the
production export are run by the pack's scripts, not here.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "research" / "breakout_radar" / "replay_v1"
if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))

from app.services.eod_limited import market_data as md  # noqa: E402
from app.services.market_calendar import is_trading_day  # noqa: E402

from harness.runner import RunConfig, run_segment, settings_grid  # noqa: E402
from harness.settings import PRODUCTION_CONFIG_HASH, build_settings, production_field_hash  # noqa: E402
from harness.stores import build_minute_store  # noqa: E402

NY = ZoneInfo("America/New_York")
DAY1, DAY2, DAY3 = date(2026, 7, 8), date(2026, 7, 9), date(2026, 7, 10)
BENCHMARKS = ("SPY", "QQQ", "IWM", "RSP")
STOCKS = ("TA", "TB", "TC", "TD", "TE", "TF")
# Which stock jumps on which day, at which regular-session bar (0 = 09:30 bar).
BREAKOUTS = {"TA": (DAY1, 6), "TB": (DAY2, 6), "TC": (DAY3, 6)}
PREMARKET_GAPPER = ("TD", DAY2)


def _sessions_ending(end: date, count: int) -> list[date]:
    days: list[date] = []
    day = end
    while len(days) < count:
        if is_trading_day(day):
            days.append(day)
        day -= timedelta(days=1)
    return sorted(days)


def _daily_path(ticker: str, sessions: list[date], rng: np.random.Generator) -> np.ndarray:
    n = len(sessions)
    if ticker in BENCHMARKS:
        return 400 + np.cumsum(rng.normal(0.4, 2.0, n))
    # A base: oscillation under a resistance near 102 with several touches.
    base = 100 + 1.5 * np.sin(np.arange(n) / 3.0) + rng.normal(0, 0.15, n)
    return base


def _write_daily_db(path: Path, sessions: list[date], rng: np.random.Generator, closes: dict[str, np.ndarray]) -> None:
    connection = md._connect(path)
    with connection:
        for day in sessions:
            connection.execute(
                "INSERT INTO market_sessions VALUES (?, ?, ?, ?, ?)",
                (day.isoformat(), "2026-09-28T00:00:00+00:00", len(closes), "x", "OK"),
            )
        for ticker, series in closes.items():
            for index, day in enumerate(sessions):
                close = float(series[index])
                # A breakout day's daily bar shows the jump; earlier bars stay in the base.
                event = BREAKOUTS.get(ticker)
                if event is not None and day == event[0]:
                    close = 106.0
                if ticker == PREMARKET_GAPPER[0] and day == PREMARKET_GAPPER[1]:
                    close = 108.0
                high = close + 0.6
                low = close - 0.6
                volume = 3_000_000.0 if ticker in BENCHMARKS else 1_000_000.0
                if event is not None and day == event[0]:
                    volume = 2_500_000.0  # a breakout day trades 2.5x its average: yesterday's relative volume passes 1.5
                connection.execute(
                    "INSERT INTO raw_daily_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (ticker, day.isoformat(), 0, close - 0.1, high, low, close, volume, close, 100),
                )
    connection.close()


def _minute_page(ticker: str, sessions: list[date], rng: np.random.Generator, level: float) -> dict:
    results = []
    for day in sessions:
        base = datetime(day.year, day.month, day.day, 4, 0, tzinfo=NY)
        jump_day, jump_bar = BREAKOUTS.get(ticker, (None, None))
        gap_day = PREMARKET_GAPPER[1] if ticker == PREMARKET_GAPPER[0] else None
        for slot in range(192):
            start = base + timedelta(minutes=5 * slot)
            minute = 4 * 60 + 5 * slot
            regular = 9 * 60 + 30 <= minute < 16 * 60
            price = level
            volume = 2_000.0
            if regular:
                volume = 120_000.0
                bar = (minute - 9 * 60 - 30) // 5
                if jump_day == day and bar >= jump_bar:
                    price = level * 1.06 + 0.02 * (bar - jump_bar)
                    volume = 260_000.0
            if gap_day == day and minute >= 7 * 60:
                price = level * 1.08 if not regular else level * 1.085
                volume = 25_000.0 if not regular else 200_000.0
            if minute >= 16 * 60:
                volume = 500.0
            noise = rng.normal(0, 0.02)
            results.append(
                {
                    "v": volume,
                    "vw": price,
                    "o": price + noise,
                    "c": price + noise,
                    "h": price + abs(noise) + 0.05,
                    "l": price - abs(noise) - 0.05,
                    "t": int(start.timestamp() * 1000),
                    "n": 10,
                }
            )
    return {"ticker": ticker, "queryCount": len(results), "resultsCount": len(results), "adjusted": False, "results": results, "status": "OK"}


def _write_directory(folder: Path, label: date) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    rows = [
        {"ticker": t, "name": f"{t} Corp", "type": "CS", "primary_exchange": "XNAS", "cik": str(i), "active": True}
        for i, t in enumerate(STOCKS, 1)
    ] + [
        {"ticker": t, "name": f"{t} Trust", "type": "ETF", "primary_exchange": "ARCX", "active": True}
        for t in BENCHMARKS
    ]
    with gzip.open(folder / f"{label.isoformat()}.json.gz", "wt") as handle:
        json.dump({"results": rows}, handle)


def _write_fred(folder: Path, sessions: list[date]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / "VIXCLS.csv", "w") as handle:
        handle.write("observation_date,VIXCLS\n")
        for day in sessions:
            handle.write(f"{day.isoformat()},18.5\n")
    with open(folder / "DGS10.csv", "w") as handle:
        handle.write("observation_date,DGS10\n")
        for day in sessions:
            handle.write(f"{day.isoformat()},4.3\n")


@pytest.fixture(scope="module")
def frozen(tmp_path_factory) -> dict:
    root = tmp_path_factory.mktemp("radar_frozen")
    rng = np.random.default_rng(11)
    sessions = _sessions_ending(DAY3, 300)
    closes = {ticker: _daily_path(ticker, sessions, rng) for ticker in (*BENCHMARKS, *STOCKS)}
    _write_daily_db(root / "daily.sqlite", sessions, rng, closes)
    raw = root / "raw" / "2026-07"
    raw.mkdir(parents=True)
    minute_sessions = sessions[-26:]
    with open(root / "manifest.jsonl", "w") as manifest:
        for ticker in STOCKS:
            level = float(closes[ticker][-27])
            page = _minute_page(ticker, minute_sessions, rng, level)
            name = f"2026-07/{ticker}_{minute_sessions[0]}_{minute_sessions[-1]}_p0.json.gz"
            with gzip.open(raw.parent / name, "wt") as handle:
                json.dump(page, handle)
            manifest.write(json.dumps({"ticker": ticker, "from": minute_sessions[0].isoformat(), "to": minute_sessions[-1].isoformat(),
                                       "part": 0, "http": 200, "status": "OK", "count": len(page["results"]), "file": name, "complete": True}) + "\n")
    build_minute_store(root / "manifest.jsonl", root / "raw", root / "minute_store")
    _write_directory(root / "directory", DAY1 - timedelta(days=9))
    _write_fred(root / "fred", sessions)
    return {"root": root, "daily": root / "daily.sqlite", "minute": root / "minute_store", "fred": root / "fred", "directory": root / "directory"}


def _config(frozen: dict, name: str, *, start: date, end: date, warmup: int, variants: list[str], memo: bool = True) -> RunConfig:
    return RunConfig(
        daily_db=frozen["daily"], minute_store=frozen["minute"], fred=frozen["fred"],
        out=frozen["root"] / "runs" / name, db_dir=frozen["root"] / "db" / name, start=start, end=end,
        variants=variants, warmup_days=warmup, grid="settings", directory=frozen["directory"],
        metadata_mode="directory", market_cap_source="none", memo=memo, full_snapshots=True,
        regular_step_minutes=30, premarket_step_minutes=60, label=name,
    )


def _read_jsonl(path: Path) -> list[dict]:
    with gzip.open(path, "rt") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _snapshots(out: Path, variant: str, day: date) -> list[dict]:
    return _read_jsonl(out / variant / "snapshots" / f"{day.isoformat()}.jsonl.gz")


def test_settings_grid_matches_the_documented_cadence() -> None:
    grid = settings_grid(DAY1)
    kinds = [kind for _stamp, kind in grid]
    assert kinds.count("premarket") == 32 and kinds.count("regular") == 77 and kinds.count("t1") == 1
    assert grid[0][0].astimezone(NY).strftime("%H:%M") == "04:10"
    assert [stamp.astimezone(NY).strftime("%H:%M") for stamp, kind in grid if kind == "regular"][:2] == ["09:35", "09:40"]
    assert grid[-1][0].astimezone(NY).strftime("%H:%M") == "16:30"
    early = settings_grid(date(2026, 11, 27))  # day after Thanksgiving closes at 13:00
    assert [kind for _s, kind in early].count("regular") == 41


def test_baseline_hashes_like_production_and_variants_do_not(tmp_path) -> None:
    baseline = build_settings("baseline", tmp_path / "b.sqlite")
    assert production_field_hash(baseline) == PRODUCTION_CONFIG_HASH
    assert baseline.range_persistence_mode == "enabled" and baseline.allow_otc is False
    assert production_field_hash(build_settings("confirm3", tmp_path / "c.sqlite")) != PRODUCTION_CONFIG_HASH
    tuned = build_settings("rvol2", tmp_path / "r.sqlite")
    assert production_field_hash(tuned) == PRODUCTION_CONFIG_HASH
    assert tuned.research_overrides == {"strong_single_rvol_min": 2.0}
    assert build_settings("hybrid_otc", tmp_path / "h.sqlite").allow_otc is True


def test_contiguous_run_is_labelled_and_finds_the_designed_breakouts(frozen: dict) -> None:
    summary = run_segment(_config(frozen, "contiguous", start=DAY1, end=DAY3, warmup=0, variants=["baseline", "confirm3"]))
    assert summary["degraded"] == []
    assert summary["variants"]["baseline"]["production_field_hash"] == PRODUCTION_CONFIG_HASH
    assert summary["truncated_days"] == {"baseline": [], "confirm3": []}
    out = frozen["root"] / "runs" / "contiguous"
    records = [r for day in (DAY1, DAY2, DAY3) for r in _read_jsonl(out / "baseline" / "ledger" / f"{day.isoformat()}.jsonl.gz")]
    scans = [r for r in records if r["kind"] != "t1"]
    # Test cadence: pre-market every 60 minutes (05:00..09:00), regular every 30 (10:00..15:30).
    assert len(scans) == 3 * (5 + 12) and all(r["status"] == "completed" for r in scans)
    assert all(c["source"] == "replay_proxy" for r in scans for c in r["candidates"])
    triggered = {(e["ticker"], e["trading_date"]) for r in scans for e in r["events"] if e["triggered_at"]}
    assert {("TA", DAY1.isoformat()), ("TB", DAY2.isoformat()), ("TC", DAY3.isoformat())} <= triggered
    gap = [e for r in scans for e in r["events"] if e["ticker"] == "TD" and e["origin_setup_type"] == "PREMARKET_GAP"]
    assert gap, "the pre-market gapper must enter through the pre-market scans"
    with sqlite3.connect(summary["variants"]["baseline"]["db"]) as connection:
        providers = {row[0] for row in connection.execute("SELECT provider FROM breakout_scan_runs")}
        snapshot_providers = {row[0] for row in connection.execute("SELECT provider FROM breakout_provider_snapshots")}
    assert providers == {"replay_proxy"} and snapshot_providers == {"replay_proxy"}
    # Lockstep variant: same identities, hash differs, confirmation bars differ where it bites.
    confirm = [r for day in (DAY1, DAY2, DAY3) for r in _read_jsonl(out / "confirm3" / "ledger" / f"{day.isoformat()}.jsonl.gz") if r["kind"] != "t1"]
    assert {e["event_id"] for r in confirm for e in r["events"]} == {e["event_id"] for r in scans for e in r["events"]}
    assert summary["variants"]["confirm3"]["production_field_hash"] != PRODUCTION_CONFIG_HASH


def test_one_warmup_day_reproduces_the_contiguous_run_byte_for_byte(frozen: dict) -> None:
    run_segment(_config(frozen, "contiguous_ref", start=DAY1, end=DAY3, warmup=0, variants=["baseline"]))
    run_segment(_config(frozen, "warm1", start=DAY3, end=DAY3, warmup=1, variants=["baseline"]))
    reference = _snapshots(frozen["root"] / "runs" / "contiguous_ref", "baseline", DAY3)
    warmed = _snapshots(frozen["root"] / "runs" / "warm1", "baseline", DAY3)
    assert reference and json.dumps(reference, sort_keys=True) == json.dumps(warmed, sort_keys=True)


def test_discovery_proxy_reads_a_delayed_view_with_yesterdays_values_before_the_roll(frozen: dict) -> None:
    from datetime import datetime

    from app.services.breakouts.models import MarketSession

    from harness.discovery import ReplayDiscoveryProvider
    from harness.stores import DailyStore, DirectoryMetadata, MinuteStore

    settings = build_settings("baseline", frozen["root"] / "stale.sqlite")
    stores = dict(minute_store=MinuteStore(frozen["minute"]), daily_store=DailyStore(frozen["daily"]),
                  metadata=DirectoryMetadata(frozen["directory"]), market_cap_source="none")
    delayed = ReplayDiscoveryProvider(settings, **stores)  # production's 15-minute view
    live = ReplayDiscoveryProvider(settings, tv_delay_minutes=0, **stores)  # real-time sensitivity run
    context = delayed.day_context(DAY1)
    index = context.tickers.index("TA")
    assert np.isfinite(context.previous_relvol[index]) and abs(context.previous_relvol[index] - 1.0) < 0.05

    def rows(provider, session, stamp):
        return {row[0]: row for row in provider._tradingview_rows(session, stamp)}

    # TA jumps 6% at the seventh regular bar (10:00-10:05) on 2.2x volume. Real time lists it
    # at 10:20 (cumulative relative volume 1.9); the delayed view (bars complete by 10:05) has
    # only one heavy bar and does not; by 10:35 it does, well above yesterday's 1.0.
    assert "TA" in rows(live, MarketSession.REGULAR, datetime(2026, 7, 8, 10, 20, tzinfo=NY))
    assert "TA" not in rows(delayed, MarketSession.REGULAR, datetime(2026, 7, 8, 10, 20, tzinfo=NY))
    late = rows(delayed, MarketSession.REGULAR, datetime(2026, 7, 8, 10, 35, tzinfo=NY))
    assert "TA" in late and late["TA"][8] > 1.5
    # TB jumped on DAY2. On DAY3 at 09:40 the view (09:25) has no regular bar yet, so TB is
    # still listed with yesterday's close and change; by 10:35 today's flat prices took over.
    stale = rows(delayed, MarketSession.REGULAR, datetime(2026, 7, 10, 9, 40, tzinfo=NY))
    assert "TB" in stale and abs(stale["TB"][5] - 106.0) < 1e-6 and stale["TB"][6] > 3.0
    assert "TB" not in rows(delayed, MarketSession.REGULAR, datetime(2026, 7, 10, 10, 35, tzinfo=NY))
    assert "TB" not in rows(live, MarketSession.REGULAR, datetime(2026, 7, 10, 9, 40, tzinfo=NY))
    # TD gapped 8% in DAY2's pre-market. On DAY3 at 04:10 the view (03:55) has no pre-market bar
    # yet, so TD carries yesterday's pre-market close and change; by 07:30 it is a non-mover.
    gap = rows(delayed, MarketSession.PREMARKET, datetime(2026, 7, 10, 4, 10, tzinfo=NY))
    assert "TD" in gap and gap["TD"][7] > 5.0 and gap["TD"][8] > 0
    assert "TD" not in rows(delayed, MarketSession.PREMARKET, datetime(2026, 7, 10, 7, 30, tzinfo=NY))


def test_day_files_reproduce_the_ticker_file_slots_and_the_window_keeps_bars(frozen: dict) -> None:
    from harness.stores import MinuteStore

    store = MinuteStore(frozen["minute"])
    assert store.has_day_files and (frozen["minute"] / "days").is_dir()
    checked = 0
    for ticker in store.tickers:
        for day in (DAY1, DAY2, DAY3):
            fast = store.day_slots(ticker, day)
            slow = store.day_slots_from_ticker_file(ticker, day)
            assert (fast is None) == (slow is None)
            if fast is not None:
                np.testing.assert_array_equal(fast[0], slow[0])
                np.testing.assert_array_equal(fast[1], slow[1])
                checked += 1
    assert checked >= 3 * len(STOCKS)
    # A windowed store keeps only the segment's bars (padded by a day for the UTC boundary)
    # in memory, and the same bars inside the window.
    windowed = MinuteStore(frozen["minute"], cache_tickers=4, window=(DAY3, DAY3))
    full = store.bars("TA", DAY1, DAY3)
    part = windowed.bars("TA", DAY1, DAY3)
    assert len(part) < len(full) and part.equals(full.loc[part.index])
    assert windowed.bars("TA", DAY3, DAY3).equals(store.bars("TA", DAY3, DAY3))


def test_memo_off_is_byte_identical_to_memo_on(frozen: dict) -> None:
    on = run_segment(_config(frozen, "memo_on", start=DAY2, end=DAY3, warmup=1, variants=["baseline"], memo=True))
    off = run_segment(_config(frozen, "memo_off", start=DAY2, end=DAY3, warmup=1, variants=["baseline"], memo=False))
    assert sum(v["hits"] for v in on["memo_stats"].values()) > 0 and off["memo_stats"] == {}
    for day in (DAY2, DAY3):
        left = _snapshots(frozen["root"] / "runs" / "memo_on", "baseline", day)
        right = _snapshots(frozen["root"] / "runs" / "memo_off", "baseline", day)
        assert json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)
