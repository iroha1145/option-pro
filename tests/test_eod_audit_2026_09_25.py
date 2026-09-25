"""Regressions for the 2026-09-25 audit: calendar, all-market data layer, EOD store.

Each block names the audit item (W-3, W-10, ...) and, where one exists, the
probe under option-pro-full-review-2026-09-25-repro/ that first showed it.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from types import SimpleNamespace
import zlib
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services import massive
from app.services import eod_limited
from app.services.eod_limited import (
    context_snapshot,
    diagnostic_store,
    diagnostics,
    full_market_tuning,
    inference,
    market_data,
    store,
    worker,
)
from app.services.eod_limited.market_data import AllMarketDataError, load_all_market_panel
from app.services.eod_limited.panel import prepare_limited_panel
from app.services.market_calendar import (
    early_close_minutes,
    is_trading_day,
    last_completed_trading_day,
    previous_trading_day,
)
from app.services.research_eod_v1 import calendar_asof, snapshot as research_snapshot
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import make_series, structured_close, trading_days_ending
from app.services.research_eod_v1.series import SecuritySeries

ET = ZoneInfo("America/New_York")


def _directory_row(ticker: str, provider_type: str = "CS", exchange: str = "XNAS") -> dict:
    return {
        "ticker": ticker, "name": f"{ticker} Inc", "market": "stocks", "type": provider_type,
        "primary_exchange": exchange, "locale": "us", "currency_symbol": "USD", "active": True,
    }


def _grouped(day: str, tickers: tuple[str, ...] = ("AAAA", "BBBB")) -> dict:
    session = date.fromisoformat(day)
    stamp = int(datetime(session.year, session.month, session.day, 16, tzinfo=ET).timestamp() * 1000)
    rows = [
        {"T": ticker, "t": stamp, "o": 10.0, "h": 11.0, "l": 9.0, "c": 10.5, "v": 1e6, "vw": 10.2, "n": 100}
        for ticker in tickers
    ]
    return {"status": "OK", "adjusted": False, "resultsCount": len(rows), "results": rows}


def _stored_sessions(root: Path) -> set[str]:
    with sqlite3.connect(root / "eod-limited-v1" / market_data.DB_NAME) as connection:
        return {str(row[0]) for row in connection.execute("SELECT session_date FROM market_sessions")}


# ── W-3: unscheduled closures and empty trading days ─────────────────────────


def test_carter_closure_is_never_requested_and_the_full_window_loads(monkeypatch, tmp_path) -> None:
    """probe_calendar_closure_loader.py: every run used to fail on 2025-01-09."""
    closure = date(2025, 1, 9)
    calls: Counter[str] = Counter()

    def fake_get(path, params=None):
        calls[path] += 1
        if path == "/v3/reference/tickers":
            return {"status": "OK", "results": [
                _directory_row("AAA", "CS", "XNYS"), _directory_row("SPY", "ETF", "ARCX"),
            ]}
        if path.startswith("/v2/aggs/grouped/locale/us/market/stocks/"):
            day = path.rsplit("/", 1)[1]
            if date.fromisoformat(day) == closure:
                # Shape Massive returned for 2025-01-09: zero records, no results list.
                return {"status": "OK", "resultsCount": 0, "adjusted": False, "queryCount": 0}
            return _grouped(day, ("AAA", "SPY"))
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        raise AssertionError(path)

    monkeypatch.setattr(massive, "_get", fake_get)
    end = date(2025, 6, 30)
    sessions = market_data._required_sessions(end)
    assert closure not in sessions and sessions[0] < closure
    for _attempt in range(2):
        panel, _coverage, manifest = load_all_market_panel(end=end, root=tmp_path)
        assert set(panel) == {"AAA", "SPY"}
        assert manifest["source_dates"]["count"] == market_data.HISTORY_SESSIONS
    assert calls[f"/v2/aggs/grouped/locale/us/market/stocks/{closure.isoformat()}"] == 0
    stored = _stored_sessions(tmp_path)
    assert len(stored) == market_data.HISTORY_SESSIONS
    assert closure.isoformat() not in stored


def _small_window(monkeypatch, *, history: int, concurrency: int) -> None:
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", history)
    monkeypatch.setattr(market_data, "MAX_PROVIDER_CONCURRENCY", concurrency)
    monkeypatch.setattr(market_data, "RETRY_DELAY_SECONDS", 0)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: [_directory_row("AAAA"), _directory_row("BBBB")])


def test_empty_trading_day_gets_a_dated_reason_and_later_days_are_still_fetched(monkeypatch, tmp_path) -> None:
    # 2026-09-10 plays an unscheduled closure the calendar does not know yet.
    _small_window(monkeypatch, history=6, concurrency=2)
    end = date(2026, 9, 16)
    sessions = [day.isoformat() for day in market_data._required_sessions(end)]
    assert sessions == ["2026-09-09", "2026-09-10", "2026-09-11", "2026-09-14", "2026-09-15", "2026-09-16"]
    calls: Counter[str] = Counter()

    def fake_get(path, params=None):
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        day = path.rsplit("/", 1)[1]
        calls[day] += 1
        if day == "2026-09-10":
            return {"status": "OK", "resultsCount": 0, "adjusted": False}
        return _grouped(day)

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(AllMarketDataError) as captured:
        load_all_market_panel(end=end, root=tmp_path)
    error = captured.value
    assert error.reason_code == "EMPTY_TRADING_SESSION"
    assert error.failed_sessions == (("2026-09-10", "EMPTY_TRADING_SESSION"),)
    assert "2026-09-10" in str(error) and "EMPTY_TRADING_SESSION" in str(error)
    assert isinstance(error.__cause__, massive.MassiveError) and error.__cause__.code == "empty_session"
    # Every other day was requested once and committed, the batch-mate included.
    assert calls == Counter({day: 1 for day in sessions})
    assert _stored_sessions(tmp_path) == set(sessions) - {"2026-09-10"}


def test_every_failed_day_is_listed_oldest_first(monkeypatch, tmp_path) -> None:
    _small_window(monkeypatch, history=6, concurrency=2)

    def fake_get(path, params=None):
        day = path.rsplit("/", 1)[1]
        if day == "2026-09-15":
            return {**_grouped(day), "resultsCount": 99}
        if day == "2026-09-10":
            return {"status": "OK", "results": []}
        return _grouped(day)

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(AllMarketDataError, match=r"2026-09-10 \(EMPTY_TRADING_SESSION\) and 1 more") as captured:
        load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)
    assert captured.value.failed_sessions == (
        ("2026-09-10", "EMPTY_TRADING_SESSION"),
        ("2026-09-15", "MASSIVE_PROTOCOL"),
    )


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (massive.MassiveError("unauthorized or plan-restricted", code="plan", status=403), "MASSIVE_PLAN"),
        (massive.MassiveError("unexpected redirect", code="redirect", status=302), "MASSIVE_REDIRECT"),
        # Massive answers a closed day with 200 and no rows; a 404 means the endpoint itself is wrong.
        (massive.MassiveError("not found", code="not_found", status=404), "MASSIVE_NOT_FOUND"),
    ],
)
def test_account_wide_failure_stops_after_the_current_batch(monkeypatch, tmp_path, error, reason) -> None:
    _small_window(monkeypatch, history=6, concurrency=2)
    calls: Counter[str] = Counter()

    def fake_get(path, params=None):
        day = path.rsplit("/", 1)[1]
        calls[day] += 1
        if day == "2026-09-09":
            raise error
        return _grouped(day)

    monkeypatch.setattr(massive, "_get", fake_get)
    with pytest.raises(AllMarketDataError) as captured:
        load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)
    assert captured.value.reason_code == reason
    # Only the first batch was sent; the same failure would repeat for the rest.
    assert calls == Counter({"2026-09-09": 1, "2026-09-10": 1})
    assert _stored_sessions(tmp_path) == {"2026-09-10"}


# ── W-10: gate results are JSON booleans, not "True"/"False" strings ─────────


def test_common_gate_checks_are_python_booleans_for_numpy_inputs() -> None:
    registry = load_registry()
    raw = SimpleNamespace(
        currently_tradable=np.bool_(True), raw_close=np.float64(12.0), adv20=np.float64(4e7),
        atr_pct=np.float64(2.5), extension_atr=np.float64(3.1), structure_score=np.float64(61.0),
        history_sessions=np.int64(300), unresolved_upthrust=np.bool_(False),
        structure_invalidated=np.bool_(False),
    )
    scored = SimpleNamespace(score=np.float64(80.0), coverage=np.float64(1.0))
    checks = research_snapshot._common_gate_checks(
        raw, registry, "semiconductors", "balanced", "A_trend_quality", scored,
        SimpleNamespace(eligible=np.bool_(True)), np.float64(2.0),
    )
    assert all(value is None or type(value) is bool for value in checks.values()), checks
    assert checks["extension"] is False and checks["atr"] is True and checks["history"] is True
    json.dumps(checks)  # no default= hook needed any more


def test_json_writers_store_numpy_scalars_as_json_values(tmp_path) -> None:
    """probe_numpy_bool_serialization.py: default=str wrote "True" and "7"."""
    payload = {"np_bool": np.bool_(True), "np_int": np.int64(7), "np_float": np.float32(1.5), "py": False}
    target = tmp_path / "x.json"
    store._atomic_write(target, payload)
    assert json.loads(target.read_text()) == {"np_bool": True, "np_int": 7, "np_float": 1.5, "py": False}
    assert diagnostic_store._decoded(diagnostic_store._encoded(payload)) == {
        "np_bool": True, "np_int": 7, "np_float": 1.5, "py": False,
    }
    assert store.json_default(date(2026, 9, 18)) == "2026-09-18"


def _walk_string_booleans(value, path: str, hits: Counter) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _walk_string_booleans(item, f"{path}.{key}", hits)
    elif isinstance(value, list):
        for item in value:
            _walk_string_booleans(item, f"{path}[]", hits)
    elif isinstance(value, str) and value in {"True", "False"}:
        hits[path] += 1


def test_live_market_path_publishes_no_string_booleans_and_one_volume_label(monkeypatch, tmp_path) -> None:
    """probe_market_path_gate_strings.py: 1,512 stock and 144 ETF gate results were strings."""
    session = date(2026, 9, 18)
    days = trading_days_ending(session, 370)
    rng = np.random.default_rng(7)
    panel = {}
    for index in range(60):
        sid = f"S{index:02d}"
        noise = (1 + 0.01 * rng.standard_normal(len(days))).cumprod() ** 0.1
        close = structured_close(len(days), 20 + index, 0.05 + 0.002 * index, 12 + index % 9) * noise
        panel[sid] = make_series(
            sid, days, close, theme_ids=("all_market_stocks",), industry_id=None,
            parent_industry_id=None, volume=2_000_000 + 10_000 * index,
        ).with_close_price_return()
    for sid, start in (("SPY", 400), ("QQQ", 350), ("E01", 50), ("E02", 60)):
        panel[sid] = make_series(
            sid, days, structured_close(len(days), start, 0.08, 15), theme_ids=("etfs",),
            asset_track="etf", security_type="ETF", industry_id=None, parent_industry_id=None,
            volume=5_000_000,
        ).with_close_price_return()
    manifest = {
        "status": "complete", "eligible_count": len(panel), "complete_bar_count": len(panel),
        "source_hash": "probe", "volume_session_scope": market_data.VOLUME_SCOPE,
    }
    monkeypatch.setattr(market_data, "load_all_market_panel", lambda **_kwargs: (panel, [], dict(manifest)))

    outcome = worker.run_eod_limited_job(session=session, root=tmp_path, refresh_context=False)
    assert outcome["status"] == "RAN"
    batch = json.loads(store.snapshot_path(tmp_path).read_text())
    hits: Counter = Counter()
    _walk_string_booleans(batch, "batch", hits)
    database = tmp_path / "eod-limited-v1" / batch["diagnostics"]["path"]
    tracks: Counter = Counter()
    scopes: set = set()
    with sqlite3.connect(database) as connection:
        for (payload,) in connection.execute("SELECT payload FROM paths"):
            row = json.loads(zlib.decompress(payload))
            _walk_string_booleans(row, "diag", hits)
            tracks[row.get("stock_or_etf_track")] += 1
            scopes.add(row.get("volume_scope"))
            extension = (row.get("common_gate_checks") or {}).get("extension")
            assert extension is None or type(extension) is bool
    assert tracks["stock"] and tracks["etf"]
    assert not hits, hits.most_common(5)
    # W-19: rows and views carry the same single volume label.
    assert market_data.VOLUME_SCOPE is eod_limited.VOLUME_SCOPE
    assert scopes == {eod_limited.VOLUME_SCOPE}
    assert {variant["volume_scope"] for variant in batch["variants"].values()} == {eod_limited.VOLUME_SCOPE}


# ── W-11: diagnostics retention and orphaned temp files ──────────────────────


def _touch(path: Path, *, age_seconds: float) -> Path:
    path.write_bytes(b"x")
    moment = time.time() - age_seconds
    os.utime(path, (moment, moment))
    return path


def _generation(directory: Path, index: int, *, age_seconds: float) -> Path:
    return _touch(directory / f"diagnostics-{index:032x}.sqlite", age_seconds=age_seconds)


def test_prune_keeps_the_newest_generations_and_sweeps_old_orphans(tmp_path) -> None:
    """probe_retention.py: all 12 generations and every orphan survived."""
    directory = store.snapshot_dir(tmp_path)
    directory.mkdir(parents=True)
    generations = [_generation(directory, index, age_seconds=index * 12 * 3600) for index in range(12)]
    month = 30 * 86400
    orphans = [
        _touch(directory / f"diagnostics-{'a' * 32}.sqlite.tmp", age_seconds=month),
        _touch(directory / f"diagnostics-{'b' * 32}.sqlite.tmp-journal", age_seconds=month),
        _touch(directory / "batch.jsonab12cd.tmp", age_seconds=month),
    ]
    diagnostic_store.prune_old_generations(root=tmp_path, active_name=generations[0].name)
    assert sorted(path.name for path in directory.iterdir()) == sorted(path.name for path in generations[:3])
    assert not any(path.exists() for path in orphans)


def test_age_is_only_a_floor_and_unrelated_files_are_never_touched(tmp_path) -> None:
    directory = store.snapshot_dir(tmp_path)
    directory.mkdir(parents=True)
    fresh = [_generation(directory, index, age_seconds=60 * index) for index in range(5)]
    active = _generation(directory, 99, age_seconds=10 * 86400)
    young_orphan = _touch(directory / f"diagnostics-{'c' * 32}.sqlite.tmp", age_seconds=600)
    old = 30 * 86400
    untouched = [
        _touch(directory / market_data.DB_NAME, age_seconds=old),
        _touch(directory / f"{market_data.DB_NAME}-wal", age_seconds=old),
        _touch(directory / f"{market_data.DB_NAME}-shm", age_seconds=old),
        _touch(directory / "notes.tmp", age_seconds=old),
        _touch(directory / "diagnostics-not-a-generation.sqlite.tmp", age_seconds=old),
    ]
    diagnostic_store.prune_old_generations(root=tmp_path, active_name=active.name)
    # Younger than the floor, so kept although beyond keep=3; the active one is never removed.
    assert all(path.exists() for path in [*fresh, active, young_orphan, *untouched])


def test_a_failed_delete_is_recorded_and_the_sweep_continues(monkeypatch, tmp_path) -> None:
    directory = store.snapshot_dir(tmp_path)
    directory.mkdir(parents=True)
    generations = [_generation(directory, index, age_seconds=86400 * (index + 1)) for index in range(6)]
    stuck = generations[3]
    orphan = _touch(directory / "batch.jsonzz.tmp", age_seconds=86400)
    original_unlink = Path.unlink

    def unlink(self, *args, **kwargs):
        if self.name == stuck.name:
            raise PermissionError("read-only volume")
        return original_unlink(self, *args, **kwargs)

    recorded = []
    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(diagnostic_store, "record_fallback_failure", lambda stage, exc, **_: recorded.append((stage, type(exc))))
    diagnostic_store.prune_old_generations(root=tmp_path, active_name=generations[0].name)
    assert recorded == [("eod_diagnostics_prune", PermissionError)]
    assert [path.exists() for path in generations] == [True, True, True, True, False, False]
    assert not orphan.exists()


def test_worker_records_housekeeping_and_context_failures(monkeypatch, tmp_path) -> None:
    recorded = []
    monkeypatch.setattr(worker, "record_fallback_failure", lambda stage, exc, **_: recorded.append((stage, type(exc))))

    def failing_prune(**_kwargs):
        raise PermissionError("read-only volume")

    def failing_context(**_kwargs):
        raise RuntimeError("context source down")

    monkeypatch.setattr(worker, "prune_old_generations", failing_prune)
    monkeypatch.setattr(context_snapshot, "refresh_context_snapshot", failing_context)
    session = date(2026, 9, 18)
    result = worker.run_eod_limited_job(
        session=session, panel=worker.build_synthetic_panel(sessions=5, end=session),
        root=tmp_path, themes=["semiconductors"], algorithms=["A_trend_quality"], refresh_context=True,
    )
    assert result["status"] == "RAN"
    assert result["context"]["status"] == "UNAVAILABLE"
    assert recorded == [("eod_diagnostics_prune", PermissionError), ("eod_context_refresh", RuntimeError)]


# ── W-12: the inference panel is clipped once and unchanged series are reused ─


@pytest.fixture
def slice_calls(monkeypatch):
    calls: list[str] = []
    original = SecuritySeries.slice_through

    def counting(self, session):
        calls.append(self.security_id)
        return original(self, session)

    monkeypatch.setattr(SecuritySeries, "slice_through", counting)
    return calls


def test_series_ending_at_the_session_are_reused_without_any_copy(slice_calls) -> None:
    session = date(2026, 9, 18)
    panel = worker.build_synthetic_panel(sessions=400, end=session)
    clipped = inference.session_panel(panel, session)
    assert set(clipped) == set(panel)
    assert slice_calls == []
    # last_n still trims the 400-bar fixture to the 370-bar warmup window.
    assert all(len(series.dates) == inference.WARMUP_SESSIONS + 40 for series in clipped.values())
    exact = worker.build_synthetic_panel(sessions=inference.WARMUP_SESSIONS + 40, end=session)
    assert all(inference.session_panel(exact, session)[sid] is exact[sid] for sid in exact)
    assert slice_calls == []


def test_series_past_the_session_are_sliced_exactly_once(slice_calls) -> None:
    session = date(2026, 9, 18)
    later = worker.build_synthetic_panel(sessions=380, end=date(2026, 9, 25))
    clipped = inference.session_panel(later, session)
    assert Counter(slice_calls) == Counter({sid: 1 for sid in later})
    assert all(series.dates[-1] == session for series in clipped.values())


def test_a_split_after_the_session_forces_a_real_slice(slice_calls) -> None:
    session = date(2026, 9, 18)
    series = worker.build_synthetic_panel(sessions=370, end=session)["NVDA"]
    series.splits = ((date(2026, 9, 21), 4.0),)
    clipped = inference.session_panel({"NVDA": series}, session)["NVDA"]
    assert slice_calls == ["NVDA"] and clipped is not series and clipped.splits == ()


def test_precompute_session_raws_does_not_copy_the_all_market_panel(slice_calls) -> None:
    session = date(2026, 9, 18)
    panel = prepare_limited_panel(
        worker.build_synthetic_panel(sessions=inference.WARMUP_SESSIONS + 40, end=session)
    )
    _raws, clipped = inference.precompute_session_raws(
        panel, session, registry=load_registry(), horizon="mid", include_setup=False,
    )
    assert slice_calls == []
    assert all(clipped[sid] is panel[sid] for sid in panel)


def test_loaded_panel_shares_one_date_list(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(
        market_data, "_fetch_directory",
        lambda: [_directory_row("AAAA"), _directory_row("BBBB"), _directory_row("GAPS")],
    )

    def fake_get(path, params=None):
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        day = path.rsplit("/", 1)[1]
        return _grouped(day, ("AAAA", "BBBB") if day == "2026-09-15" else ("AAAA", "BBBB", "GAPS"))

    monkeypatch.setattr(massive, "_get", fake_get)
    panel, _coverage, _manifest = load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)
    assert panel["AAAA"].dates is panel["BBBB"].dates
    assert panel["GAPS"].dates is not panel["AAAA"].dates
    assert panel["GAPS"].dates == [date(2026, 9, 14), date(2026, 9, 16)]
    assert panel["GAPS"].dates[-1] is panel["AAAA"].dates[-1]


# ── W-13: a session counts as complete only after the settle buffer ─────────


@pytest.mark.parametrize(
    ("local_now", "expected"),
    [
        (datetime(2026, 9, 18, 16, 30, tzinfo=ET), date(2026, 9, 17)),
        (datetime(2026, 9, 18, 16, 59, tzinfo=ET), date(2026, 9, 17)),
        (datetime(2026, 9, 18, 17, 0, tzinfo=ET), date(2026, 9, 18)),
        (datetime(2026, 11, 27, 13, 59, tzinfo=ET), date(2026, 11, 25)),  # half day after Thanksgiving
        (datetime(2026, 11, 27, 14, 0, tzinfo=ET), date(2026, 11, 27)),
        (datetime(2026, 9, 19, 10, 0, tzinfo=ET), date(2026, 9, 18)),
        (datetime(2026, 9, 21, 0, 30, tzinfo=ET), date(2026, 9, 18)),
        (datetime(2025, 1, 9, 18, 0, tzinfo=ET), date(2025, 1, 8)),  # Carter closure
    ],
)
def test_inference_session_waits_for_the_settle_buffer(local_now, expected) -> None:
    assert calendar_asof.LIVE_SETTLE_BUFFER == timedelta(minutes=60)
    assert worker.resolve_inference_session(local_now) == expected
    assert worker.resolve_inference_session(local_now.astimezone(timezone.utc)) == expected
    # The read side can apply the identical rule without importing the worker.
    assert calendar_asof.settled_eod_session(local_now) == expected


@pytest.mark.parametrize(
    ("local_now", "expected"),
    [
        (datetime(2026, 9, 18, 16, 5, tzinfo=ET), date(2026, 9, 17)),
        (datetime(2026, 9, 18, 17, 5, tzinfo=ET), date(2026, 9, 18)),
    ],
)
def test_live_runs_request_the_settled_session(monkeypatch, tmp_path, local_now, expected) -> None:
    # Scheduled and manual refreshes both reach run_eod_limited_job without a session.
    requested = []

    class Stop(Exception):
        pass

    def capture(**kwargs):
        requested.append(kwargs["end"])
        raise Stop

    monkeypatch.setattr(market_data, "load_all_market_panel", capture)
    with pytest.raises(Stop):
        worker.run_eod_limited_job(root=tmp_path, now=local_now)
    assert requested == [expected]


# ── W-19: coverage classes, retries, fetch threads, durable writes ───────────


def test_coverage_accounting_check_is_not_vacuous(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 3)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: [_directory_row("AAAA"), _directory_row("BBBB")])
    monkeypatch.setattr(
        massive, "_get",
        lambda path, params=None: {"status": "OK", "results": []} if path == "/stocks/v1/splits"
        else _grouped(path.rsplit("/", 1)[1]),
    )
    original = market_data._load_panel

    def double_counted(*args, **kwargs):
        panel, coverage, missing, short, residual = original(*args, **kwargs)
        return panel, coverage, missing + 1, short, residual

    monkeypatch.setattr(market_data, "_load_panel", double_counted)
    with pytest.raises(AllMarketDataError, match="accounting is inconsistent"):
        load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)


class _Jitter:
    def __init__(self, factor: float) -> None:
        self.factor = factor

    def uniform(self, low: float, high: float) -> float:
        assert (low, high) == (1.0, 1.5)
        return self.factor


@pytest.mark.parametrize(
    ("attempt", "retry_after", "factor", "expected"),
    [
        (0, None, 1.0, 0.5),
        (1, None, 1.0, 1.0),
        (1, None, 1.5, 1.5),
        (0, 7.0, 1.0, 7.0),
        (0, 7.0, 1.5, 10.5),
        (0, 120.0, 1.0, 30.0),  # a Retry-After hint is honoured only up to the cap
        (6, None, 1.0, 30.0),
    ],
)
def test_retry_delay_is_capped_exponential_backoff_with_jitter(monkeypatch, attempt, retry_after, factor, expected) -> None:
    monkeypatch.setattr(market_data, "_retry_jitter", _Jitter(factor))
    error = massive.MassiveError("rate limited", code="rate_limited", status=429, retry_after=retry_after)
    assert market_data._retry_delay(attempt, error) == pytest.approx(expected)


def test_provider_get_waits_as_told_and_never_retries_a_redirect(monkeypatch) -> None:
    monkeypatch.setattr(market_data, "_retry_jitter", _Jitter(1.0))
    sleeps: list[float] = []
    monkeypatch.setattr(market_data.time, "sleep", sleeps.append)
    replies = iter([
        massive.MassiveError("rate limited", code="rate_limited", status=429, retry_after=3.0),
        massive.MassiveError("http 503", code="http", status=503),
        {"status": "OK", "results": []},
    ])

    def fake_get(path, params=None):
        reply = next(replies)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(massive, "_get", fake_get)
    assert market_data._provider_get("/x", {}) == {"status": "OK", "results": []}
    assert sleeps == [3.0, 1.0]

    def redirect(path, params=None):
        raise massive.MassiveError("unexpected redirect", code="redirect", status=302)

    monkeypatch.setattr(massive, "_get", redirect)
    with pytest.raises(massive.MassiveError, match="redirect"):
        market_data._provider_get("/x", {})
    assert sleeps == [3.0, 1.0]


def test_eod_fetch_threads_leave_one_massive_request_slot(monkeypatch, tmp_path) -> None:
    assert market_data.MAX_PROVIDER_CONCURRENCY == massive.MAX_CONCURRENT_REQUESTS - 1
    width = market_data.MAX_PROVIDER_CONCURRENCY
    monkeypatch.setattr(market_data, "HISTORY_SESSIONS", 2 * width)
    monkeypatch.setattr(market_data, "_fetch_directory", lambda: [_directory_row("AAAA")])
    lock = threading.Lock()
    active = 0
    peak = 0
    # A full first batch must run side by side; a wider pool would exceed the peak.
    first_batch = threading.Barrier(width, timeout=10)
    seen = 0

    def fake_get(path, params=None):
        nonlocal active, peak, seen
        if path == "/stocks/v1/splits":
            return {"status": "OK", "results": []}
        with lock:
            active += 1
            peak = max(peak, active)
            seen += 1
            wait = seen <= width
        try:
            if wait:
                first_batch.wait()
            return _grouped(path.rsplit("/", 1)[1], ("AAAA",))
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(massive, "_get", fake_get)
    load_all_market_panel(end=date(2026, 9, 16), root=tmp_path)
    assert peak == width


def test_snapshot_writes_fsync_the_file_and_its_directory(monkeypatch, tmp_path) -> None:
    kinds: list[str] = []
    original = os.fsync

    def recording(fd):
        kinds.append("dir" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return original(fd)

    monkeypatch.setattr(os, "fsync", recording)
    store.publish_batch({"served_session": "2026-09-18", "variants": {}}, root=tmp_path)
    assert kinds == ["file", "dir"]
    # The context snapshot is written by the same function.
    assert context_snapshot._atomic_write is store._atomic_write


# ── cleanup: one implementation per concept ─────────────────────────────────


def _old_last_completed_session(as_of: datetime) -> date:
    local = as_of.astimezone(ET)
    candidate = local.date()
    close = early_close_minutes(candidate) or 16 * 60
    if not is_trading_day(candidate) or local.hour * 60 + local.minute < close:
        candidate = previous_trading_day(candidate, include_start=False)
    return candidate


def test_research_last_completed_session_keeps_its_rule_on_the_shared_calendar() -> None:
    starts = (date(2025, 1, 7), date(2026, 3, 6), date(2026, 10, 30), date(2026, 11, 24), date(2026, 12, 23))
    for start in starts:
        moment = datetime(start.year, start.month, start.day, tzinfo=ET)
        for _ in range(5 * 48):
            assert calendar_asof.last_completed_session(moment) == _old_last_completed_session(moment)
            assert calendar_asof.last_completed_session(moment) == last_completed_trading_day(moment)
            moment += timedelta(minutes=30)
    with pytest.raises(ValueError, match="timezone-aware"):
        calendar_asof.last_completed_session(datetime(2026, 9, 18, 17, 0))


def test_split_and_directory_pages_share_one_cursor_validator(monkeypatch) -> None:
    assert not hasattr(market_data, "_split_cursor")
    monkeypatch.setattr(massive, "get_settings", lambda: SimpleNamespace(massive_base_url="https://api.massive.com"))
    url = "https://api.massive.com/stocks/v1/splits?cursor=abc&apiKey=discard"
    assert massive._page_cursor(url, path="/stocks/v1/splits") == "abc"
    with pytest.raises(massive.MassiveError) as captured:
        massive._page_cursor(url, path="/v3/reference/tickers")
    assert captured.value.code == "protocol"


def test_finite_number_has_one_definition() -> None:
    assert diagnostics.finite_number is full_market_tuning.finite_number
    finite = full_market_tuning.finite_number
    assert finite(np.float32(1.5)) == 1.5 and finite(np.int64(3)) == 3.0
    for value in (True, np.bool_(True), "1.0", None, float("nan"), float("inf")):
        assert finite(value) is None


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        ({"status": "OK", "adjusted": False, "resultsCount": 0}, "empty_session"),
        ({"status": "OK", "adjusted": False, "results": []}, "empty_session"),
        ({"status": "OK", "adjusted": False, "results": None, "resultsCount": 0}, "empty_session"),
        # The provider says rows exist but sent none: a broken reply, not a closed day.
        ({"status": "OK", "adjusted": False, "results": [], "resultsCount": 12}, "protocol"),
        ({"status": "OK", "adjusted": False, "resultsCount": 12}, "protocol"),
        ({"status": "OK", "adjusted": False, "results": {"T": "AAAA"}}, "protocol"),
    ],
)
def test_only_a_truly_empty_trading_day_is_reported_as_empty(monkeypatch, payload, code) -> None:
    monkeypatch.setattr(market_data, "_provider_get", lambda *_args, **_kwargs: payload)
    with pytest.raises(massive.MassiveError) as captured:
        market_data._fetch_grouped_session(date(2026, 9, 16))
    assert captured.value.code == code


def test_reuse_happens_only_when_a_slice_would_be_identical() -> None:
    from dataclasses import fields, replace
    from itertools import product

    session = date(2026, 9, 18)
    days = trading_days_ending(session, 30)
    base = make_series("X", days, structured_close(len(days), 50, 0.1, 10))

    def differing_field(left, right):
        for item in fields(SecuritySeries):
            a, b = getattr(left, item.name), getattr(right, item.name)
            if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
                if (a is None) != (b is None) or (a is not None and not np.array_equal(a, b, equal_nan=True)):
                    return item.name
            elif a != b:
                return item.name
        return None

    reused = 0
    for halted, last_bar_halted, dividend_after, split_after, events in product(
        (False, True), (None, True, False), (False, True), (False, True), (False, True),
    ):
        series = replace(base)
        series.halted = halted
        if last_bar_halted is not None:
            series.bar_halted = np.array([False] * (len(days) - 1) + [last_bar_halted])
        series.dividends = ((days[5], 0.5),) + (((date(2026, 9, 25), 0.5),) if dividend_after else ())
        series.splits = ((days[3], 2.0),) + (((date(2026, 9, 23), 3.0),) if split_after else ())
        series.dividend_events = ({"ex_date": days[4].isoformat(), "amount": 0.1},) if events else ()
        for cutoff in (session, date(2026, 9, 21), days[-5]):
            through = inference._through(series, cutoff)
            reused += through is series
            assert differing_field(through, series.slice_through(cutoff)) is None
    assert reused == 8  # both late cutoffs x the four consistent halt flags, no later events
