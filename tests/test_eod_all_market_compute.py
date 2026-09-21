from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from datetime import date
import json

import pytest

from app.services.eod_limited.inference import (
    precompute_all_horizon_inputs,
    precompute_session_raws,
    precompute_theme_raws,
    score_eod_session,
)
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.panel import prepare_limited_panel
from app.services.eod_limited.worker import build_synthetic_panel
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of


def stable(value):
    return json.dumps(value, sort_keys=True, default=str)


@pytest.fixture(scope="module")
def market_inputs():
    registry = load_market_registry()
    panel = build_synthetic_panel(sessions=370, end=date(2023, 7, 10))
    panel = {sid: series for sid, series in panel.items() if sid in {"NVDA", "AMD", "SPY", "QQQ"}}
    outside = deepcopy(panel["AMD"])
    outside.security_id = outside.ticker_at_signal = "OUTSIDE"
    outside.theme_ids = (ALL_MARKET_STOCKS,)
    outside.venue_metadata = {**outside.venue_metadata, "name": "Outside sample"}
    panel["OUTSIDE"] = outside
    panel = prepare_limited_panel(panel)
    session = date(2023, 7, 10)
    themes = ["semiconductors", "ai_cloud", "etfs", ALL_MARKET_STOCKS]
    inputs = precompute_all_horizon_inputs(panel, session, registry=registry, horizons=HORIZONS, themes=themes)
    return registry, panel, session, themes, inputs


def test_market_registry_preserves_every_sealed_theme_and_declares_generic():
    sealed = load_registry()
    live = load_market_registry()
    assert len(sealed["sectors"]) == 24
    assert len(live["sectors"]) == 25
    for key, spec in sealed["sectors"].items():
        assert live["sectors"][key] == spec
    generic = live["sectors"][ALL_MARKET_STOCKS]
    for family in ALGORITHMS:
        assert generic["candidates"][family]["weights"] == sealed["base_algorithm_weights"][family]
        assert generic["candidates"][family]["min_history_sessions"] == (330 if family.startswith("D_") else 252)
    assert generic["gates"]["minimum_adv_usd"] == 20_000_000
    live["sectors"]["semiconductors"]["gates"]["minimum_adv_usd"] = -1
    assert load_registry() == sealed


def test_shared_horizon_raws_match_independent_extraction_in_every_candidate_field(market_inputs):
    registry, panel, session, themes, shared = market_inputs
    from app.services.research_eod_v1.membership import is_theme_candidate

    for horizon in HORIZONS:
        independent, clipped = precompute_session_raws(panel, session, registry=registry, horizon=horizon)
        themed = precompute_theme_raws(independent, clipped, session, registry=registry, themes=themes)
        actual_raws, actual_panel, actual_themed = shared[horizon]
        assert set(actual_panel) == set(clipped)
        for theme in themes:
            for sid, series in clipped.items():
                track = "etf" if registry["sectors"][theme].get("asset_track") == "etf" else "stock"
                if is_theme_candidate(series, sector_id=theme, session=session, target_track=track)[0]:
                    assert stable(asdict(actual_themed[theme][sid])) == stable(asdict(themed[theme][sid]))
        assert all(actual_raws[sid].residual == independent[sid].residual for sid in independent)
    assert len({id(shared[horizon][1]) for horizon in HORIZONS}) == 1


def test_snapshot_job_cache_preserves_complete_fields_for_every_family_profile_horizon(market_inputs):
    registry, panel, session, themes, shared = market_inputs
    cache = {}
    for horizon in HORIZONS:
        raws, clipped, themed = shared[horizon]
        for profile in PROFILES:
            for theme in themes:
                for algorithm in ALGORITHMS:
                    kwargs = dict(sector_id=theme, algorithm=algorithm, profile=profile, horizon=horizon,
                                  source_finalized_through=session, precomputed_raws=themed[theme],
                                  reapply_theme_gates=False, already_session_clipped=True)
                    expected = compute_snapshot(eod_evaluation_as_of(session), clipped, "fixture", registry, **kwargs)
                    actual = compute_snapshot(eod_evaluation_as_of(session), clipped, "fixture", registry,
                                              snapshot_cache=cache, **kwargs)
                    assert stable(actual) == stable(expected)


def test_unclassified_stock_is_scored_and_compaction_preserves_decisions(market_inputs):
    registry, panel, session, themes, shared = market_inputs
    raws, clipped, themed = shared["mid"]
    kwargs = dict(registry=registry, themes=themes, precomputed_raws=raws,
                  clipped_panel=clipped, precomputed_theme_raws=themed)
    complete = score_eod_session(panel, session, **kwargs)
    compact = score_eod_session(panel, session, compact=True, **kwargs)
    assert compact["scored_security_count"] == len(panel)
    assert compact["security_status"] == complete["security_status"]
    for field in ("eligible_n", "watch_n", "rejected_n", "composite_n"):
        assert compact[field] == complete[field]
    outside = [row for block in complete["family_results"] for row in block["rows"] if row["security_id"] == "OUTSIDE"]
    assert len(outside) == 4
    assert all(row["sector_context"] == ALL_MARKET_STOCKS for row in outside)
    assert all(row["name"] == "Outside sample" and row["display_sector_id"] is None for row in outside)
    for full_block, small_block in zip(complete["family_results"], compact["family_results"]):
        assert full_block["rejection_counts"] == small_block["rejection_counts"]
        expected = [{k: v for k, v in row.items() if k not in {"pivots", "frozen_setup"}}
                    for row in full_block["rows"] if row["status"] in {"eligible", "watch"}]
        assert stable(small_block["rows"]) == stable(expected)
    assert all("frozen_setup" not in row and "pivots" not in row for row in compact["watch_list"])


def test_common_features_are_extracted_once_per_security(market_inputs, monkeypatch):
    import app.services.eod_limited.inference as inference
    registry, panel, session, themes, _ = market_inputs
    original = inference.extract_raw
    calls = []
    def counted(series, **kwargs):
        calls.append((series.security_id, kwargs["include_setup"]))
        return original(series, **kwargs)
    monkeypatch.setattr(inference, "extract_raw", counted)
    precompute_all_horizon_inputs(panel, session, registry=registry, horizons=HORIZONS, themes=themes)
    assert sorted(calls) == sorted((sid, False) for sid in panel)


def test_four_spawn_workers_from_thread_preserve_370_session_geometry_and_all_scores(market_inputs):
    from concurrent.futures import ThreadPoolExecutor
    import multiprocessing

    registry, original_panel, session, themes, _ = market_inputs
    panel = dict(original_panel)
    # More than four eight-security chunks exercise all four spawned workers
    # with long histories, not just idle processes and short-history returns.
    for index in range(35):
        series = deepcopy(original_panel["OUTSIDE"])
        series.security_id = series.ticker_at_signal = f"LONG{index:03d}"
        panel[series.security_id] = series
    serial = precompute_all_horizon_inputs(panel, session, registry=registry, horizons=HORIZONS, themes=themes)
    before = {process.pid for process in multiprocessing.active_children()}
    # The production worker itself calls inference from a background thread.
    with ThreadPoolExecutor(max_workers=1) as thread:
        parallel = thread.submit(
            precompute_all_horizon_inputs, panel, session, registry=registry,
            horizons=HORIZONS, themes=themes, geometry_workers=4,
        ).result(timeout=90)
    assert {process.pid for process in multiprocessing.active_children()} <= before
    saw_events = False
    serial_cache, parallel_cache = {}, {}
    for horizon in HORIZONS:
        expected_raws, expected_panel, expected_themes = serial[horizon]
        actual_raws, actual_panel, actual_themes = parallel[horizon]
        assert set(actual_panel) == set(expected_panel)
        assert stable({sid: asdict(raw) for sid, raw in actual_raws.items()}) == stable({sid: asdict(raw) for sid, raw in expected_raws.items()})
        for theme in themes:
            # Includes noncandidate references, multi-theme NVDA geometry,
            # frozen events, gates, nullable values, and all raw fields.
            assert stable({sid: asdict(raw) for sid, raw in actual_themes[theme].items()}) == stable({sid: asdict(raw) for sid, raw in expected_themes[theme].items()})
            saw_events |= any(bool((raw.frozen_setup or {}).get("events")) for raw in actual_themes[theme].values())
        for profile in PROFILES:
            expected = score_eod_session(
                panel, session, registry=registry, themes=themes, horizon=horizon, profile=profile,
                precomputed_raws=expected_raws, clipped_panel=expected_panel,
                precomputed_theme_raws=expected_themes, snapshot_cache=serial_cache,
            )
            actual = score_eod_session(
                panel, session, registry=registry, themes=themes, horizon=horizon, profile=profile,
                precomputed_raws=actual_raws, clipped_panel=actual_panel,
                precomputed_theme_raws=actual_themes, snapshot_cache=parallel_cache,
            )
            expected.pop("generated_at"); actual.pop("generated_at")
            assert stable(actual) == stable(expected)
    assert saw_events, "the long-history comparison must exercise platform lifecycle events"


def test_parallel_geometry_crosses_128_security_boundary_and_keeps_short_history(market_inputs):
    registry, panel, session, _themes, _shared = market_inputs
    expanded = dict(panel)
    for index in range(130):
        short = deepcopy(panel["OUTSIDE"].last_n(18))
        short.security_id = short.ticker_at_signal = f"SHORT{index:03d}"
        expanded[short.security_id] = short
    raws, clipped = precompute_session_raws(expanded, session, registry=registry, horizon="mid", include_setup=False)
    expected = precompute_theme_raws(raws, clipped, session, registry=registry)
    actual = precompute_theme_raws(raws, clipped, session, registry=registry, geometry_workers=4)
    assert len(clipped) > 128
    for theme in registry["sectors"]:
        assert list(actual[theme]) == list(raws), "every theme retains the entire reference pool"
        assert stable({sid: asdict(raw) for sid, raw in actual[theme].items()}) == stable({sid: asdict(raw) for sid, raw in expected[theme].items()})
    for index in range(130):
        raw = actual[ALL_MARKET_STOCKS][f"SHORT{index:03d}"]
        assert raw.b_status == "insufficient_history"
        assert raw.frozen_setup is None


@pytest.mark.parametrize("workers", [0, -1, 5, True, False, None, 1.0, "4"])
def test_geometry_worker_count_is_strictly_bounded(workers):
    registry = load_market_registry()
    with pytest.raises(ValueError, match="geometry_workers"):
        precompute_theme_raws({}, {}, date(2023, 7, 10), registry=registry, geometry_workers=workers)
    with pytest.raises(ValueError, match="geometry_workers"):
        precompute_all_horizon_inputs({}, date(2023, 7, 10), registry=registry, horizons=[], geometry_workers=workers)


def test_parallel_child_failure_aborts_without_mutating_inputs_or_leaking_children(market_inputs):
    import multiprocessing

    registry, _panel, session, _themes, shared = market_inputs
    registry = deepcopy(registry)
    registry["sectors"][ALL_MARKET_STOCKS]["gates"]["base_min_sessions"] = "invalid-window"
    raws, clipped, _ = shared["mid"]
    original = stable({sid: asdict(raw) for sid, raw in raws.items()})
    before = {process.pid for process in multiprocessing.active_children()}
    with pytest.raises(ValueError, match="invalid-window"):
        precompute_theme_raws(raws, clipped, session, registry=registry, geometry_workers=4)
    assert stable({sid: asdict(raw) for sid, raw in raws.items()}) == original
    assert {process.pid for process in multiprocessing.active_children()} <= before
