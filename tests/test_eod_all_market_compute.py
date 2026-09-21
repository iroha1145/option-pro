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
