"""SPY must cover the window the all-market scorers read, not only the target session.

The external review of 2026-09-28 (section A) found that the publish gate proved
only SPY's bar on the target session. Every stock's D residual and the relative
M hook read SPY over a window, so one missing bar there degrades every stock at
once, even at full coverage. These tests pin:

1. the window is the scorers' own: removing one SPY bar changes a scorer's output
   exactly when the bar lies in the derived window, and the history rule is the
   residual's;
2. a gap inside the window refuses publication at 100% coverage and keeps the
   previous snapshot byte for byte; a gap outside it publishes;
3. an unusable price is refused, and which check refuses it;
4. a history too short for the residual is refused;
5. the reason reaches the task details, the task status API and the action API.
"""
from __future__ import annotations

import asyncio
import dataclasses
from datetime import date
from types import MappingProxyType

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strength as strength_api
from app.api import worker_actions
from app.services.eod_limited import inference, worker
from app.services.eod_limited.benchmark_window import benchmark_window, window_problems
from app.services.eod_limited.full_market_tuning import (
    WINDOWS, TuningPolicy, momentum_grid, prepare_full_market_context,
)
from app.services.eod_limited.live_config import LIVE_CONFIG, V16_CONFIG
from app.services.eod_limited.market_registry import load_market_registry
from app.services.eod_limited.panel import prepare_limited_panel
from app.services.eod_limited.store import read_batch, snapshot_path
from app.services.research_eod_v1.constants import (
    HORIZONS, PROFILES, RESIDUAL_FIT_WINDOW, RESIDUAL_HISTORY_MIN, RESIDUAL_SUM_END, RESIDUAL_SUM_START,
)
from app.services.research_eod_v1.fixtures import trading_days_ending
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.residual import residual_benchmark_sessions, residual_raw_momentum
from app.services.research_eod_v1.snapshot import industry_g_inputs
from app.worker.state import WorkerStateRepository, _details_json, _validate_action_detail
from app.worker.tasks import StrengthRefreshTask
from eod_v17_fixtures import build_panel
from tests.test_eod_all_market_data import END, _install_provider
from tests.test_eod_v17_publish_gate import (
    ALL_VIEWS, OLD_VERSION, PREVIOUS, SESSION, _fast_scoring, _grouped, _listing, _market_directory,
    _publish_previous, _run_to_gate, _serve_market,
)

WINDOW_REASON = "all_market_benchmark_window_incomplete"
WINDOW = benchmark_window(SESSION, horizons=HORIZONS, policy=LIVE_CONFIG.tuning_policy())
# The first residual session, a mid-window session every reader uses, and one of
# the skipped sessions after the residual window, where no scorer fails.
INSIDE = {
    "first_residual_session": WINDOW.sessions[0],
    "read_by_residual_and_m": WINDOW.sessions[-100],
    "after_the_residual_window": WINDOW.sessions[-3],
}
BEFORE_WINDOW = [day for day in trading_days_ending(SESSION, inference.SESSION_PANEL_BARS) if day < WINDOW.sessions[0]]

_PER_BAR_ARRAYS = ("open", "high", "low", "close", "raw_close", "volume", "dollar_volume", "tri",
                   "raw_open", "bar_partial", "bar_halted", "vendor_tri")
_PER_BAR_LISTS = ("dates", "economic_known_at", "source_published_at", "retrieved_at", "finalized_at",
                  "vintage_status", "price_adjustment", "volume_adjustment")


def _without(series, *days):
    """``series`` with no bar on ``days``, every other bar untouched."""
    dropped = set(days)
    keep = [index for index, day in enumerate(series.dates) if day not in dropped]
    assert len(keep) == len(series.dates) - len(dropped), "every removed day had a bar"
    changes = {name: np.asarray(getattr(series, name))[keep]
               for name in _PER_BAR_ARRAYS if getattr(series, name) is not None}
    changes.update({name: type(getattr(series, name))(getattr(series, name)[index] for index in keep)
                    for name in _PER_BAR_LISTS if getattr(series, name)})
    return dataclasses.replace(series, **changes)


def _repriced(series, day, value, fields=("close", "tri")):
    """``series`` with ``value`` in ``fields`` on ``day``; the loader's ``tri`` is its close."""
    index = series.dates.index(day)
    changes = {}
    for name in fields:
        array = np.array(getattr(series, name), dtype=float)
        array[index] = value
        changes[name] = array
    return dataclasses.replace(series, **changes)


def _refusal(monkeypatch, tmp_path, panel):
    """The live run's refusal at full coverage, or None when it went on to score."""
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))
    return _run_to_gate(monkeypatch, tmp_path)


# ── 1. The window is the scorers' own ───────────────────────────────────────


@pytest.fixture(scope="module")
def scored_inputs():
    panel = prepare_limited_panel(build_panel())
    return inference.precompute_session_raws(
        panel, SESSION, registry=load_market_registry(), horizon="mid", include_setup=False,
    )


def test_the_live_window_is_the_residual_span_with_every_momentum_window_inside():
    # Target session back to the extra prior day the first residual return needs.
    span = RESIDUAL_SUM_START + RESIDUAL_FIT_WINDOW + 2
    assert WINDOW.sessions == tuple(trading_days_ending(SESSION, span))
    assert (span, WINDOW.sessions[0]) == (321, date(2025, 6, 10))
    priced, trailing = residual_benchmark_sessions(SESSION)
    assert len(trailing) == RESIDUAL_SUM_END and trailing[-1] == SESSION
    assert priced + trailing == WINDOW.sessions
    reads = {read.reader: read for read in WINDOW.reads}
    assert (reads["D_residual"].field, reads["D_residual_grid_end"].field) == ("tri", None)
    for horizon in HORIZONS:
        read = reads[f"M_relative_{horizon}"]
        assert read.sessions == momentum_grid(SESSION, horizon)
        assert len(read.sessions) == max(n for n, _skip in WINDOWS[horizon]) + 1
        assert (read.field, read.complete_bar) == ("close", True)
        assert set(read.sessions) <= set(WINDOW.sessions)
    assert WINDOW.min_history == RESIDUAL_HISTORY_MIN
    assert WINDOW.lookback_start == trading_days_ending(SESSION, inference.SESSION_PANEL_BARS)[0]
    # The v1.6 policy blends M too; a policy that blends no M leaves only the residual.
    assert benchmark_window(SESSION, horizons=HORIZONS, policy=V16_CONFIG.tuning_policy()) == WINDOW
    unblended = TuningPolicy(m_alpha=MappingProxyType({profile: 0.0 for profile in PROFILES}))
    assert [read.reader for read in benchmark_window(SESSION, horizons=HORIZONS, policy=unblended).reads] == [
        "D_residual", "D_residual_grid_end",
    ]


def test_one_missing_spy_bar_changes_a_scorer_exactly_when_it_is_in_the_window(scored_inputs):
    raws, clipped = scored_inputs
    stock, spy = clipped["NEW000"], clipped["SPY"]
    assert len(spy.dates) == inference.SESSION_PANEL_BARS  # the whole look-back
    residual = residual_raw_momentum(stock, spy, {})
    assert residual.status == "OK"
    contexts = {horizon: prepare_full_market_context(raws, clipped, session=SESSION, horizon=horizon)
                for horizon in HORIZONS}
    assert all(context.benchmark_status == "ok" and context.momentum_percentiles for context in contexts.values())
    changed = set()
    for day in spy.dates:
        reduced = _without(spy, day)
        panel = {**clipped, "SPY": reduced}
        if residual_raw_momentum(stock, reduced, {}) != residual or any(
            prepare_full_market_context(raws, panel, session=SESSION, horizon=horizon).digest != context.digest
            for horizon, context in contexts.items()
        ):
            changed.add(day)
    assert changed == set(WINDOW.sessions)


def test_a_gap_after_the_residual_window_moves_it_without_an_error(scored_inputs):
    """Why the skipped sessions are required although the residual reads no price there."""
    _raws, clipped = scored_inputs
    stock, spy = clipped["NEW000"], clipped["SPY"]
    moved = residual_raw_momentum(stock, _without(spy, INSIDE["after_the_residual_window"]), {})
    assert moved.status == "OK" and moved.raw != residual_raw_momentum(stock, spy, {}).raw
    assert residual_raw_momentum(stock, _without(spy, WINDOW.sessions[1]), {}).status == "MISSING_DAY_RETURN"


@pytest.mark.parametrize("where", ["read_by_residual_only", "read_by_residual_and_m"])
def test_a_partial_spy_bar_is_missing_only_where_its_reader_skips_it(scored_inputs, where):
    raws, clipped = scored_inputs
    stock, spy = clipped["NEW000"], clipped["SPY"]
    day = WINDOW.sessions[3] if where == "read_by_residual_only" else INSIDE["read_by_residual_and_m"]
    flags = np.zeros(len(spy.dates), dtype=bool)
    flags[spy.dates.index(day)] = True
    partial = dataclasses.replace(spy, bar_partial=flags)
    # The residual reads a partial bar's price; the M hook drops the bar.
    assert residual_raw_momentum(stock, partial, {}) == residual_raw_momentum(stock, spy, {})
    status = prepare_full_market_context(raws, {**clipped, "SPY": partial}, session=SESSION, horizon="mid").benchmark_status
    skipped = where == "read_by_residual_and_m"
    assert status == ("unavailable_or_incomplete" if skipped else "ok")
    expected = [{"benchmark": "SPY", "problem": "missing_bar", "sessions": [day.isoformat()]}] if skipped else []
    assert window_problems("SPY", partial, WINDOW) == expected


def test_the_history_minimum_counts_bars_in_the_scorers_look_back(scored_inputs):
    _raws, clipped = scored_inputs
    stock, spy = clipped["NEW000"], clipped["SPY"]
    spare = inference.SESSION_PANEL_BARS - RESIDUAL_HISTORY_MIN  # old gaps a full history absorbs
    assert len(BEFORE_WINDOW) > spare
    enough = _without(spy, *BEFORE_WINDOW[:spare])
    short = _without(spy, *BEFORE_WINDOW[: spare + 1])
    assert residual_raw_momentum(stock, enough, {}) == residual_raw_momentum(stock, spy, {})
    assert residual_raw_momentum(stock, short, {}).status == "UNALIGNED_BENCHMARK"
    assert window_problems("SPY", enough, WINDOW) == []
    assert window_problems("SPY", short, WINDOW) == [{
        "benchmark": "SPY", "problem": "insufficient_history", "sessions": [],
        "history": RESIDUAL_HISTORY_MIN - 1, "min_history": RESIDUAL_HISTORY_MIN,
    }]


def test_g_reads_only_whether_spy_has_its_63_session_return(scored_inputs):
    """G (industry modes) ranks peer means minus SPY's return: its value cannot move a rank."""
    raws, _clipped = scored_inputs
    xref = {sid: raw for sid, raw in raws.items() if raw.asset_track == "stock"}
    groups = {
        "industries": {sid: f"group{index % 3}" for index, sid in enumerate(sorted(xref))},
        "parents": {sid: "parent" for sid in xref},
        "tracks": {sid: "stock" for sid in xref},
    }
    high, _ = industry_g_inputs(xref, spy_m63=0.25, **groups)
    low, _ = industry_g_inputs(xref, spy_m63=-0.4, **groups)
    absent, _ = industry_g_inputs(xref, spy_m63=None, **groups)
    assert high == low and any(value is not None for value in high.values())
    assert all(value is None for value in absent.values())


# ── 2. A gap inside the window refuses at full coverage; one outside does not ─


@pytest.mark.parametrize("where", sorted(INSIDE))
def test_one_missing_spy_bar_in_the_window_blocks_publication_at_full_coverage(monkeypatch, tmp_path, where):
    before = _publish_previous(tmp_path)
    served = read_batch(tmp_path)
    panel = build_panel()
    day = INSIDE[where]
    panel["SPY"] = _without(panel["SPY"], day)
    assert has_complete_session_bar(panel["SPY"], SESSION)  # the target-day check alone passes
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal is not None, "published with a gap in SPY's window"
    assert refusal["publish"] == {
        "ok": False, "reason": WINDOW_REASON, "integrity": "stale_previous_retained",
        "served_session": PREVIOUS, "attempted_session": SESSION.isoformat(),
        "benchmark_problems": [{"benchmark": "SPY", "problem": "missing_bar", "sessions": [day.isoformat()]}],
        "benchmark_window": {"first_session": "2025-06-10", "last_session": SESSION.isoformat(),
                             "session_count": 321, "min_history": RESIDUAL_HISTORY_MIN},
    }
    assert (refusal["coverage"]["complete_bar_count"], refusal["coverage"]["eligible_count"]) == (66, 66)
    assert refusal["served_session"] == PREVIOUS and refusal["available_variants"] == ALL_VIEWS
    assert snapshot_path(tmp_path).read_bytes() == before
    assert _listing(tmp_path) == ["batch.json"]
    kept = read_batch(tmp_path)
    assert (kept["served_session"], kept["compute_version"], kept["published_at"]) == (
        PREVIOUS, OLD_VERSION, served["published_at"],
    )


@pytest.mark.parametrize("day", [BEFORE_WINDOW[-1], BEFORE_WINDOW[0]], ids=["just_before", "oldest"])
def test_a_missing_spy_bar_outside_the_window_still_publishes(monkeypatch, tmp_path, day):
    _publish_previous(tmp_path)
    panel = build_panel()
    panel["SPY"] = _without(panel["SPY"], day)
    assert window_problems("SPY", panel["SPY"], WINDOW) == []
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))
    _fast_scoring(monkeypatch, panel)
    outcome = worker.run_eod_limited_job(session=SESSION, root=tmp_path, refresh_context=False)
    assert outcome["status"] == "RAN" and outcome["publish"]["ok"] is True
    assert read_batch(tmp_path)["served_session"] == SESSION.isoformat()


def test_a_coverage_refusal_also_names_the_window_gap(monkeypatch, tmp_path):
    panel = build_panel()
    panel["SPY"] = _without(panel["SPY"], INSIDE["read_by_residual_and_m"])
    _serve_market(monkeypatch, panel, eligible=100, complete=len(panel))
    refusal = _run_to_gate(monkeypatch, tmp_path)
    assert refusal["publish"]["reason"] == "all_market_coverage_incomplete"
    assert [item["problem"] for item in refusal["publish"]["benchmark_problems"]] == ["missing_bar"]


# ── 3. Unusable prices, and which check refuses them ─────────────────────────


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize(
    ("day", "fields"),
    [(WINDOW.sessions[3], ["tri"]), (INSIDE["read_by_residual_and_m"], ["close", "tri"])],
    ids=["read_by_residual_only", "read_by_residual_and_m"],
)
def test_an_unusable_spy_price_in_the_window_is_refused_by_the_window_check(monkeypatch, tmp_path, value, day, fields):
    """A panel that did not come through the loader's own validation (``_valid_ohlc``)."""
    panel = build_panel()
    panel["SPY"] = _repriced(panel["SPY"], day, value)
    assert has_complete_session_bar(panel["SPY"], SESSION)
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal["publish"]["reason"] == WINDOW_REASON
    assert refusal["publish"]["benchmark_problems"] == [
        {"benchmark": "SPY", "problem": "invalid_price", "sessions": [day.isoformat()], "fields": fields},
    ]


def test_a_zero_close_on_the_target_session_passes_the_old_check_and_fails_the_window(monkeypatch, tmp_path):
    panel = build_panel()
    panel["SPY"] = _repriced(panel["SPY"], SESSION, 0.0)
    assert has_complete_session_bar(panel["SPY"], SESSION)  # finite is all the target-day check asks
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal["publish"]["reason"] == WINDOW_REASON
    assert refusal["publish"]["benchmark_problems"] == [
        {"benchmark": "SPY", "problem": "invalid_price", "sessions": [SESSION.isoformat()], "fields": ["close"]},
    ]
    assert "missing_benchmarks" not in refusal["publish"]


@pytest.mark.parametrize("value", [float("nan"), float("inf")])
def test_a_non_finite_close_on_the_target_session_is_refused_by_the_target_day_check(monkeypatch, tmp_path, value):
    panel = build_panel()
    panel["SPY"] = _repriced(panel["SPY"], SESSION, value)
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal["publish"]["reason"] == "all_market_benchmark_missing"
    assert refusal["publish"]["missing_benchmarks"] == ["SPY"]
    assert "benchmark_problems" not in refusal["publish"]  # the window check skips a benchmark without its bar


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf")])
@pytest.mark.parametrize("day", ["2026-09-15", END.isoformat()], ids=["history_day", "target_day"])
def test_the_real_loader_drops_spy_with_an_unusable_close_before_the_gate(monkeypatch, tmp_path, value, day):
    """In production an unusable close never reaches the window check: the loader rejects the series."""
    grouped = _grouped(late=())
    grouped[day] = [{**row, "c": value} if row["T"] == "SPY" else row for row in grouped[day]]
    _install_provider(monkeypatch, _market_directory(), grouped)
    refusal = _run_to_gate(monkeypatch, tmp_path, session=END)
    assert refusal["publish"]["reason"] == "all_market_benchmark_missing"
    assert refusal["publish"]["missing_benchmarks"] == ["SPY"]
    assert "benchmark_problems" not in refusal["publish"]
    coverage = refusal["coverage"]
    assert (coverage["invalid_count"], coverage["complete_bar_count"], coverage["eligible_count"]) == (1, 31, 32)


# ── 4. A history too short for the residual ──────────────────────────────────


def test_a_spy_history_that_starts_inside_the_window_is_refused(monkeypatch, tmp_path):
    panel = build_panel()
    panel["SPY"] = panel["SPY"].last_n(200)
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal["publish"]["reason"] == WINDOW_REASON
    # Sessions before the first bar are a short history, not missing bars.
    assert refusal["publish"]["benchmark_problems"] == [{
        "benchmark": "SPY", "problem": "insufficient_history",
        "sessions": [day.isoformat() for day in WINDOW.sessions[:-200]],
        "history": 200, "min_history": RESIDUAL_HISTORY_MIN,
    }]


def test_too_few_old_spy_bars_are_refused_although_the_window_is_complete(monkeypatch, tmp_path):
    spare = inference.SESSION_PANEL_BARS - RESIDUAL_HISTORY_MIN
    panel = build_panel()
    panel["SPY"] = _without(panel["SPY"], *BEFORE_WINDOW[: spare + 1])
    refusal = _refusal(monkeypatch, tmp_path, panel)
    assert refusal["publish"]["reason"] == WINDOW_REASON
    assert [(item["problem"], item["history"]) for item in refusal["publish"]["benchmark_problems"]] == [
        ("insufficient_history", RESIDUAL_HISTORY_MIN - 1),
    ]
    panel["SPY"] = _without(build_panel()["SPY"], *BEFORE_WINDOW[:spare])
    assert _refusal(monkeypatch, tmp_path / "enough", panel) is None


def test_the_real_loader_history_must_reach_back_over_the_window(monkeypatch, tmp_path):
    """The loader test double keeps three sessions: coverage is complete, SPY's history is not."""
    _install_provider(monkeypatch, _market_directory(), _grouped(late=()))
    refusal = _run_to_gate(monkeypatch, tmp_path, session=END)
    assert refusal["publish"]["reason"] == WINDOW_REASON
    (problem,) = refusal["publish"]["benchmark_problems"]
    window = benchmark_window(END, horizons=HORIZONS, policy=LIVE_CONFIG.tuning_policy())
    assert (problem["problem"], problem["history"]) == ("insufficient_history", 3)
    assert problem["sessions"] == [day.isoformat() for day in window.sessions[:-3]]


# ── 5. The reason reaches the task details and both status APIs ──────────────


@pytest.fixture
def no_variant_work(monkeypatch):
    from app.services.strength import variant_demand

    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: [])
    monkeypatch.setattr(variant_demand, "complete_strength_variant_demand", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(strength_api, "list_recent_strength_variant_parameters", lambda *_args, **_kwargs: [])


def _live_runner(monkeypatch, tmp_path, spy):
    """The task's EOD runner on a fully covered live panel with this SPY series."""
    panel = build_panel()
    panel["SPY"] = spy
    _serve_market(monkeypatch, panel, eligible=len(panel), complete=len(panel))

    def runner(**kwargs):
        return worker.run_eod_limited_job(**kwargs, session=SESSION, root=tmp_path, refresh_context=False)

    return runner


def _gap_runner(monkeypatch, tmp_path, *days):
    return _live_runner(monkeypatch, tmp_path, _without(build_panel()["SPY"], *days))


def _worst_spy():
    """Every problem at once, each on more sessions than the task details list."""
    spy = build_panel()["SPY"].last_n(200)  # 121 required sessions before its first bar
    gaps = spy.dates[10:40]
    spy = _without(spy, *gaps)
    unusable = spy.dates[50:80]
    for day in unusable:
        spy = _repriced(spy, day, 0.0)
    return spy, gaps, unusable


def test_the_refusal_reaches_the_task_details_and_the_status_api(monkeypatch, tmp_path, no_variant_work):
    before = _publish_previous(tmp_path)
    day = INSIDE["read_by_residual_and_m"]
    task = StrengthRefreshTask(eod_runner=_gap_runner(monkeypatch, tmp_path, day), clock=lambda: 1_800_000_000.0)
    result = asyncio.run(task())

    assert (result.status, result.error_code) == ("degraded", "eod_limited_input_unavailable")
    details = result.details
    assert details["result"] == "stale_previous_retained" and details["published"] is False
    assert details["reason"] == WINDOW_REASON
    assert (details["attempted_session"], details["served_session"]) == (SESSION.isoformat(), PREVIOUS)
    assert details["score_data_through"] == PREVIOUS
    assert details["benchmark_problems"] == [
        {"benchmark": "SPY", "problem": "missing_bar", "session_count": 1, "sessions": [day.isoformat()]},
    ]
    assert details["benchmark_window"] == {"first_session": "2025-06-10", "last_session": SESSION.isoformat(),
                                           "session_count": 321, "min_history": RESIDUAL_HISTORY_MIN}
    assert "missing_benchmarks" not in details
    _validate_action_detail(dict(details))
    assert snapshot_path(tmp_path).read_bytes() == before

    repository, token = _worker_repository(tmp_path, monkeypatch)
    repository.record_task("test-worker", token, "strength_refresh", enabled=True, status=result.status,
                           error_code=result.error_code, details=result.details)
    with TestClient(_worker_app()) as client:
        response = client.get("/api/worker/status")
    assert response.status_code == 200, response.text
    state = next(item for item in response.json()["tasks"] if item["task_name"] == "strength_refresh")
    assert (state["status"], state["error_code"]) == ("degraded", "eod_limited_input_unavailable")
    for key in ("result", "reason", "attempted_session", "served_session", "benchmark_problems", "benchmark_window"):
        assert state["details"][key] == details[key], key


def test_many_failing_sessions_are_cut_with_their_count(monkeypatch, tmp_path, no_variant_work):
    _publish_previous(tmp_path)
    spy, gaps, unusable = _worst_spy()
    task = StrengthRefreshTask(eod_runner=_live_runner(monkeypatch, tmp_path, spy), clock=lambda: 1_800_000_000.0)
    details = asyncio.run(task()).details
    before_history = [day.isoformat() for day in WINDOW.sessions[:-200]]
    assert details["benchmark_problems"] == [
        {"benchmark": "SPY", "problem": "missing_bar", "session_count": 30,
         "sessions": [day.isoformat() for day in gaps[:20]]},
        {"benchmark": "SPY", "problem": "invalid_price", "session_count": 30,
         "sessions": [day.isoformat() for day in unusable[:20]], "fields": ["close", "tri"]},
        {"benchmark": "SPY", "problem": "insufficient_history", "session_count": len(before_history),
         "sessions": before_history[:20], "history": 170, "min_history": RESIDUAL_HISTORY_MIN},
    ]
    _validate_action_detail(dict(details))
    assert len(_details_json(details).encode()) < 2_500  # the status row holds 16 KiB


def test_a_refused_action_round_fits_the_status_row(monkeypatch, tmp_path, no_variant_work):
    """The round's details repeat the refusal in each action completion.

    A second request while one is queued joins it (``already_running``), so one
    round settles one API action: the worst refusal twice must fit the 16 KiB row.
    """
    _publish_previous(tmp_path)
    runner = _live_runner(monkeypatch, tmp_path, _worst_spy()[0])
    repository, token = _worker_repository(tmp_path, monkeypatch)
    with TestClient(_worker_app()) as client:
        for top in (10, 40):
            posted = client.post("/api/worker/actions/strength_refresh", json={
                "parameters": {**strength_api.DEFAULT_STRENGTH_SCAN_PARAMETERS, "top": top, "include_options": False},
                "idempotency_key": f"benchmark-window-top-{top}",
            })
            assert posted.json()["reason"] == ("queued" if top == 10 else "already_running")
    claimed = repository.claim_actions("test-worker", token, "strength_refresh")
    result = asyncio.run(StrengthRefreshTask(eod_runner=runner).run_for_actions(claimed))
    (completion,) = result.details["action_completions"]
    assert completion["result"]["reason"] == result.details["reason"] == WINDOW_REASON
    assert len(completion["result"]["benchmark_problems"]) == 3
    assert len(_details_json(result.details).encode()) < 16 * 1024 // 2
    repository.record_task("test-worker", token, "strength_refresh", enabled=True, status=result.status,
                           error_code=result.error_code, details=result.details)


def test_a_manual_refresh_action_carries_the_refusal(monkeypatch, tmp_path, no_variant_work):
    _publish_previous(tmp_path)
    day = INSIDE["after_the_residual_window"]
    runner = _gap_runner(monkeypatch, tmp_path, day)
    repository, token = _worker_repository(tmp_path, monkeypatch)
    parameters = {**strength_api.DEFAULT_STRENGTH_SCAN_PARAMETERS, "include_options": False}
    with TestClient(_worker_app()) as client:
        posted = client.post("/api/worker/actions/strength_refresh", json={
            "parameters": parameters, "idempotency_key": "benchmark-window-refusal",
        })
        assert posted.status_code == 202, posted.text
        request_id = posted.json()["request_id"]
        claimed = repository.claim_actions("test-worker", token, "strength_refresh")
        result = asyncio.run(StrengthRefreshTask(eod_runner=runner).run_for_actions(claimed))
        (completion,) = result.details["action_completions"]
        assert completion["succeeded"] is False
        # What WorkerSupervisor._finish_action_completion stores for this action.
        repository.finish_actions("test-worker", token, [request_id], succeeded=False,
                                  error_code=completion["error_code"], details={"result": completion["result"]})
        action = client.get(f"/api/worker/actions/{request_id}").json()
    assert (action["status"], action["error_code"]) == ("failed", "eod_limited_input_unavailable")
    stored = action["details"]["result"]
    assert stored["reason"] == WINDOW_REASON
    assert (stored["attempted_session"], stored["served_session"]) == (SESSION.isoformat(), PREVIOUS)
    assert stored["benchmark_problems"] == [
        {"benchmark": "SPY", "problem": "missing_bar", "session_count": 1, "sessions": [day.isoformat()]},
    ]


def _worker_repository(tmp_path, monkeypatch):
    """The repository ``/api/worker`` reads (``DATA_DIR/optix-worker.db``), holding the lease."""
    root = tmp_path / "worker-state"
    root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(root))
    repository = WorkerStateRepository(root / "optix-worker.db")
    repository.initialize()
    token = repository.acquire("test-worker", lease_seconds=300)
    assert token is not None
    repository.record_task("test-worker", token, "strength_refresh", enabled=True, status="idle")
    return repository, token


def _worker_app() -> FastAPI:
    app = FastAPI()
    app.include_router(worker_actions.router)
    app.include_router(strength_api.router)
    return app
