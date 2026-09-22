"""Exercise the default v1.4 scorer with synthetic, previously unseen stocks."""
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from app.services.eod_limited import COMPUTE_VERSION, MODE_ID, PURPOSE_SYNTHETIC
from app.services.eod_limited.full_market_tuning import (
    ATR_REFERENCE_UNAVAILABLE, M_ALPHA, TUNED_FAMILIES, WINDOWS,
    prepare_full_market_context,
)
from app.services.eod_limited.inference import (
    UNIVERSE_VERSION, precompute_all_horizon_inputs, score_eod_session,
)
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.price_only import apply_price_only_track
from app.services.eod_limited.project import project_strength_payload
from app.services.eod_limited.worker import build_synthetic_panel
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.research_eod_v1.fixtures import make_series, structured_close
from app.services.research_eod_v1.snapshot import compute_snapshot


@pytest.fixture(scope="module")
def market():
    session = date(2026, 9, 18)
    seed = build_synthetic_panel(sessions=370, end=session)
    panel = {sid: seed[sid] for sid in ("SPY", "QQQ")}
    for index in range(40):
        sid = f"NEW{index:03}"
        themes = (ALL_MARKET_STOCKS,) + (("semiconductors",) if index < 3 else ())
        panel[sid] = make_series(
            sid, seed["SPY"].dates,
            structured_close(370, 30 + index, .04 + .004 * index, 14 + index % 7),
            theme_ids=themes,
        )
    registry = load_market_registry()
    themes = [ALL_MARKET_STOCKS, "semiconductors", "etfs"]
    shared = precompute_all_horizon_inputs(
        panel, session, registry=registry, horizons=HORIZONS, themes=themes,
    )
    return registry, panel, session, themes, shared


def score(market, *, profile="balanced", horizon="mid", inputs=None, themes=None, algorithms=None):
    registry, panel, session, default_themes, shared = market
    raws, clipped, themed = shared[horizon] if inputs is None else inputs
    return score_eod_session(
        panel, session, registry=registry, profile=profile, horizon=horizon,
        themes=default_themes if themes is None else themes, algorithms=algorithms,
        purpose=PURPOSE_SYNTHETIC, precomputed_raws=raws, clipped_panel=clipped,
        precomputed_theme_raws=themed,
    )


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("horizon", HORIZONS)
def test_default_scorer_preserves_score_budget_and_fund_rows_in_all_nine_views(market, profile, horizon):
    registry, _, session, _, shared = market
    before_registry = deepcopy(registry)
    raws, clipped, themed = shared[horizon]
    residuals = {sid: deepcopy(raw.residual) for sid, raw in raws.items()}
    result = score(market, profile=profile, horizon=horizon)
    assert result["mode"] == MODE_ID == "eod_limited_v1"
    assert result["compute_version"] == COMPUTE_VERSION == "limited-all-market-v1.4"
    assert result["scored_security_count"] == 42
    summary = result["full_market_tuning"]
    assert summary["stock_input_n"] == summary["atr_reference_n"] == 40
    assert summary["momentum_reference_n"] == 40
    assert summary["momentum_windows"] == list(WINDOWS[horizon])
    assert summary["benchmark_status"] == "ok"
    assert not any(result["capability_flags"].values())
    assert result["eligible_n"] == result["composite_n"] == 0

    changed_families = set()
    scored_rows = 0
    for block in result["family_results"]:
        theme, family = block["theme_id"], block["algorithm_id"]
        original = compute_snapshot(
            eod_evaluation_as_of(session), clipped, UNIVERSE_VERSION, registry,
            sector_id=theme, algorithm=family, profile=profile, horizon=horizon,
            source_finalized_through=session, precomputed_raws=themed[theme],
            reapply_theme_gates=False, already_session_clipped=True, snapshot_cache={},
        )
        baseline = apply_price_only_track(
            original, registry=registry, theme_id=theme, family=family,
            profile=profile, horizon=horizon,
        )
        original_rows = {row["security_id"]: row for row in baseline["rows"]}
        assert set(original_rows) == {row["security_id"] for row in block["rows"]}
        for row in block["rows"]:
            old = original_rows[row["security_id"]]
            if row.get("stock_or_etf_track") == "etf":
                # These four display fields are added after either scoring path.
                display = {"price", "close", "name", "display_sector_id"}
                assert {k: v for k, v in row.items() if k not in display} == {
                    k: v for k, v in old.items() if k not in display
                }
            tuning = row.get("full_market_tuning")
            if tuning is not None:
                assert tuning["context_hash"] == summary["context_hash"]
                assert row["atr_reference_n"] == 40
                cap = registry["profiles"][profile]["atr_absolute_cap_pct"]
                multiplier = registry["profiles"][profile]["atr_sector_median_multiplier"]
                assert row["atr_threshold_pct"] == pytest.approx(
                    min(cap, multiplier * summary["atr_reference_median"])
                )
                assert row["gate_results"]["setup"] == old["gate_results"]["setup"]
                for factor in "TSBPVRG":
                    assert row["factors"][factor] == old["factors"][factor]
                assert row["factors"]["G"] is None
                assert row["configured_weights"] == old["configured_weights"]
                assert row["track_weights"] == old["track_weights"]
                assert row["effective_weights"] == old["effective_weights"]
                assert row["observed_feature_coverage"] == old["observed_feature_coverage"]
                assert row["weight_provenance_id"] == old["weight_provenance_id"]
                assert tuning["alpha"] == (M_ALPHA[profile] if family in TUNED_FAMILIES else 0)
                assert abs(tuning["M_delta"]) <= 10
                if abs(tuning["M_delta"]) > 1e-10:
                    changed_families.add(family)
                if old["score"] is None:
                    assert row["score"] is None
                else:
                    assert row["score"] - old["score"] == pytest.approx(
                        old["effective_weights"]["M"] * tuning["M_delta"], abs=1e-10,
                    )
            if row.get("score") is not None:
                scored_rows += 1
                assert sum(row["score_components"].values()) == pytest.approx(row["score"])
            else:
                assert row["score_components"] == {}
            assert row["capability_flags"] == old["capability_flags"]
    assert scored_rows > 0
    assert changed_families == (set() if profile == "conservative" else set(TUNED_FAMILIES))
    public_rows = project_strength_payload(result, parameters={})["rows"]
    for row in public_rows:
        assert sum(row["score_components"].values()) == pytest.approx(row["sort_score"])
    assert registry == before_registry
    assert {sid: raw.residual for sid, raw in raws.items()} == residuals


def test_reference_precedes_theme_filter_and_small_input_fails_closed(market):
    full = score(market, themes=["semiconductors"], algorithms=["A_trend_quality"])
    assert full["full_market_tuning"]["atr_reference_n"] == 40
    candidates = [row for row in full["family_results"][0]["rows"] if row.get("factors")]
    assert len(candidates) == 3
    assert all(row["atr_reference_n"] == 40 for row in candidates)
    raws, clipped, themed = market[4]["mid"]
    ids = {f"NEW{index:03}" for index in range(29)} | {"SPY", "QQQ"}
    small = (
        {sid: raw for sid, raw in raws.items() if sid in ids},
        {sid: series for sid, series in clipped.items() if sid in ids},
        {theme: {sid: raw for sid, raw in values.items() if sid in ids} for theme, values in themed.items()},
    )
    result = score(market, inputs=small, themes=[ALL_MARKET_STOCKS], algorithms=["A_trend_quality"])
    assert result["full_market_tuning"]["atr_reference_n"] == 29
    candidates = [row for row in result["family_results"][0]["rows"] if row.get("factors")]
    assert len(candidates) == 29
    for row in candidates:
        assert row["status"] == "rejected"
        assert ATR_REFERENCE_UNAVAILABLE in row["rejection_reasons"]
        assert row["full_market_tuning"]["M_delta"] == 0


@pytest.mark.parametrize("family", sorted(TUNED_FAMILIES))
def test_new_momentum_cannot_repair_missing_original_factor_in_default_scorer(market, family):
    raws, clipped, themed = market[4]["mid"]
    sid = "NEW039"
    def without_m(raw):
        return replace(raw, residual=replace(raw.residual, raw=None)) if family.startswith("D_") else replace(raw, m63=None)
    changed = dict(raws)
    changed[sid] = without_m(raws[sid])
    changed_themes = {theme: {**values, sid: without_m(values[sid])} for theme, values in themed.items()}
    result = score(
        market, profile="aggressive", inputs=(changed, clipped, changed_themes),
        themes=[ALL_MARKET_STOCKS], algorithms=[family],
    )
    row = next(row for row in result["family_results"][0]["rows"] if row["security_id"] == sid)
    assert row["full_market_tuning"]["M_window_target"] is not None
    assert row["full_market_tuning"]["reason"] == "original_M_missing_or_invalid"
    assert row["factors"]["M"] is None
    assert row["score"] is None and row["score_components"] == {}
    assert row["status"] == "rejected"
    assert result["composite_n"] == 0


def test_adapter_rejects_missing_internal_price_and_unknown_asset(market):
    registry, _, session, _, shared = market
    raws, clipped, _ = shared["long"]
    changed = deepcopy(clipped)
    changed["NEW039"].close[-21] = float("nan")
    changed["NEW038"].asset_track = "unknown"
    ctx = prepare_full_market_context(raws, changed, session=session, horizon="long")
    assert len(ctx.reference_ids) == 39
    assert len(ctx.momentum_reference_ids) == 38
    assert "NEW038" not in ctx.stock_ids
    assert "NEW039" in ctx.reference_ids and "NEW039" not in ctx.momentum_percentiles
    changed["SPY"].close[-21] = float("nan")
    ctx = prepare_full_market_context(raws, changed, session=session, horizon="long")
    assert len(ctx.reference_ids) == 39
    assert ctx.benchmark_status == "unavailable_or_incomplete"
    assert not ctx.momentum_percentiles
