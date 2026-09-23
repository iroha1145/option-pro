"""Algorithm tests for the bounded full-market tuning hook (v1.5)."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import math
import random

import pytest

from app.services.eod_limited.full_market_tuning import (
    ATR_MULTIPLIER, ATR_REFERENCE_UNAVAILABLE, EXTENDED_STATE, EXTENDED_STATE_PROFILES, EXTENDED_WAIT_REASON, M_ALPHA, M_DELTA_CAP,
    MIN_REFERENCE_N, R_NEUTRAL_PROFILES, R_NEUTRAL_VALUE, SIGMA_FLOOR, StockInput, WINDOWS, WINDOW_WEIGHTS,
    apply_entry_states, atr_threshold, build_context, reference_eligible, tune_snapshot,
)

DAY = date(2026, 9, 18)
# Synthetic exchange grid only: it is not presented as a real holiday calendar.
CALENDAR = [DAY - timedelta(days=i) for i in reversed(range(400))]
A = "A_trend_quality"
B = "B_confirmed_base_breakout"
C = "C_trend_pullback"
D = "D_residual_momentum"


def inputs(n=40):
    return [StockInput(
        f"UNSEEN-{i:03}", DAY, "stock", 50.0, 30_000_000.0, 2.0 + i / 100,
        400, True, False, False, True, True, True,
        {day: 50.0 * (1 + (i + 1) * 0.0001) ** j for j, day in enumerate(CALENDAR)},
    ) for i in range(n)]


def benchmark():
    return {day: 100.0 * 1.0002 ** i for i, day in enumerate(CALENDAR)}


def context(data=None, horizon="mid", spy=True):
    return build_context(inputs() if data is None else data, session=DAY, horizon=horizon,
                         calendar=CALENDAR, benchmark_closes=benchmark() if spy else None)


def registry():
    return {"profiles": {
        "conservative": {"atr_absolute_cap_pct": 6.0, "atr_sector_median_multiplier": 1.25},
        "balanced": {"atr_absolute_cap_pct": 8.0, "atr_sector_median_multiplier": 1.75},
        "aggressive": {"atr_absolute_cap_pct": 12.0, "atr_sector_median_multiplier": 2.5},
    }}


def row(sid="UNSEEN-039", family=D, m=50.0, common=(), setup=(), **extra):
    result = {
        "security_id": sid, "stock_or_etf_track": "stock", "session_date": DAY.isoformat(),
        "algorithm_id": family, "sector_context": "all_market_stocks", "atr_pct": 2.0,
        "status": "rejected" if common or setup else "eligible",
        "gate_results": {"common": tuple(common), "setup": tuple(setup), "venue": "OK"},
        "rejection_reasons": list(dict.fromkeys((*common, *setup))),
        "factors": {"T": 75.0, "M": m, "S": 70.0, "B": 65.0, "P": 80.0, "V": 71.0, "R": 90.0, "G": None},
        "configured_weights": {"T": .2, "M": .3, "S": .1, "B": .05, "P": .05, "V": .1, "R": .15, "G": .05},
        "observed_feature_coverage": .95, "common_gate_checks": {"atr": False, "structure": True},
    }
    result.update(extra)
    return result


def apply(source=None, ctx=None, profile="balanced"):
    ctx = context() if ctx is None else ctx
    return tune_snapshot({"session_date": DAY.isoformat(), "rows": [row() if source is None else source]},
                         ctx, registry=registry(), profile=profile, horizon=ctx.horizon)["rows"][0]


def weighted(r):
    w = r["configured_weights"]
    coverage = sum(w[k] for k, v in r["factors"].items() if v is not None)
    return sum(w[k] * v / coverage for k, v in r["factors"].items() if v is not None)


# ------------------------------------------------------------------ M blend


@pytest.mark.parametrize("profile,alpha", list(M_ALPHA.items()))
@pytest.mark.parametrize("family", [A, B, C, D])
def test_only_M_and_R_can_change_and_only_for_tuned_families(profile, alpha, family):
    source = row(family=family)
    result = apply(source, profile=profile)
    for key, value in source["factors"].items():
        if key in {"M", "R"}:
            continue
        assert result["factors"][key] == value
    tuned = family in {A, D} and alpha > 0
    assert (result["factors"]["M"] != source["factors"]["M"]) == tuned
    assert result["full_market_tuning"]["alpha"] == (alpha if family in {A, D} else 0.0)


@pytest.mark.parametrize("old", [0.0, 12.5, 50.0, 88.0, 100.0])
@pytest.mark.parametrize("profile", list(M_ALPHA))
def test_bounded_delta_and_explicit_weighted_score_budget(old, profile):
    source = row(m=old)
    result = apply(source, profile=profile)
    delta = result["factors"]["M"] - old
    assert abs(delta) <= M_DELTA_CAP + 1e-12
    assert 0 <= result["factors"]["M"] <= 100
    # Same effective weight/coverage before and after: the score moves only by the
    # M blend and, for balanced/aggressive, by the R neutralisation. No hidden bonus.
    w = source["configured_weights"]
    coverage = sum(w[k] for k, v in source["factors"].items() if v is not None)
    r_delta = result["factors"]["R"] - source["factors"]["R"]
    assert weighted(result) - weighted(source) == pytest.approx((w["M"] * delta + w["R"] * r_delta) / coverage)


@pytest.mark.parametrize("m", [None, float("nan"), float("inf"), True, -1.0, 101.0])
def test_does_not_fill_or_launder_missing_original_M(m):
    result = apply(row(m=m), profile="aggressive")
    assert result["full_market_tuning"]["reason"] == "original_M_missing_or_invalid"
    actual = result["factors"]["M"]
    assert actual is m or actual == m


@pytest.mark.parametrize("horizon", list(WINDOWS))
def test_windows_skip_the_latest_month_on_long_legs_and_end_at_the_session(horizon):
    ctx = context(horizon=horizon)
    assert ctx.windows == WINDOWS[horizon]
    assert ctx.window_starts == tuple(CALENDAR[-n - 1] for n, _skip in WINDOWS[horizon])
    n, skip = WINDOWS[horizon][0]
    # Flat-volatility synthetic series: the floor binds and the target is the plain
    # relative return divided by the floor.
    expected = ((1.004 ** (n - skip)) - (1.0002 ** (n - skip))) / SIGMA_FLOOR
    assert ctx.relative_returns["UNSEEN-039"][0] == pytest.approx(expected)
    assert ctx.momentum_percentiles["UNSEEN-039"] == pytest.approx(100)
    assert sum(WINDOW_WEIGHTS[horizon]) == pytest.approx(1.0)


def test_three_horizons_genuinely_observe_different_information():
    data = inputs()
    prices = {day: 50.0 for day in CALENDAR}
    # 50 until 250 sessions ago, 80 from then on, 120 in the latest 15 sessions.
    # short: the 63-day leg (no skip) sees the jump; mid/long skip it. The 252-day
    # leg (start 50 -> 80) carries the earlier run-up, the 126-day leg is flat, and
    # long weights the 252-day leg more than mid does.
    for day in CALENDAR[-250:-15]:
        prices[day] = 80.0
    for day in CALENDAR[-15:]:
        prices[day] = 120.0
    data[0] = replace(data[0], closes=prices)
    targets = {h: context(data, horizon=h).momentum_percentiles[data[0].security_id] for h in WINDOWS}
    assert targets["short"] > targets["mid"]
    assert targets["long"] != targets["mid"]


def test_volatility_scaling_prefers_the_smoother_path_for_the_same_return():
    data = inputs()
    smooth = {day: 50.0 * 1.003 ** j for j, day in enumerate(CALENDAR)}
    rough = dict(smooth)
    for j, day in enumerate(CALENDAR):
        rough[day] = smooth[day] * (1.08 if j % 2 else 1.0)  # same endpoints, wild path
    rough[CALENDAR[-1]] = smooth[CALENDAR[-1]]
    rough[CALENDAR[0]] = smooth[CALENDAR[0]]
    data[0] = replace(data[0], closes=smooth)
    data[1] = replace(data[1], closes=rough)
    ctx = context(data)
    assert ctx.momentum_percentiles[data[0].security_id] > ctx.momentum_percentiles[data[1].security_id]


def test_ETFs_cannot_move_stock_reference_scores_or_gates():
    data = inputs()
    before = context(data)
    for i in range(100):
        data.append(replace(data[0], security_id=f"FUND-{i}", asset_track="etf", atr_pct=.001,
                            closes={day: 1000.0 ** (j / 100) for j, day in enumerate(CALENDAR)}))
    after = context(data)
    assert before.digest == after.digest
    assert before.reference_ids == after.reference_ids
    assert dict(before.momentum_percentiles) == dict(after.momentum_percentiles)
    assert apply(ctx=before) == apply(ctx=after)
    fund = row(sid="FUND-0", stock_or_etf_track="etf", common=("HIGH_ATR", "EXTENDED"))
    assert apply(fund, after, "aggressive") == fund


@pytest.mark.parametrize("field,value", [
    ("raw_close", 4.99), ("raw_close", None), ("raw_close", True),
    ("adv20", 19_999_999), ("adv20", float("inf")), ("adv20", None),
    ("atr_pct", 0), ("atr_pct", float("nan")),
    ("history_sessions", 251), ("history_sessions", True),
    ("currently_tradable", False), ("halted", True), ("zero_volume", True),
    ("complete_bar", False), ("source_available", False), ("venue_eligible", False),
    ("asset_track", "unknown"), ("asset_track", "etf"),
])
def test_reference_exclusions(field, value):
    bad = replace(inputs()[0], **{field: value})
    assert not reference_eligible(bad)
    data = inputs()
    data[0] = bad
    assert bad.security_id not in context(data).reference_ids


# ------------------------------------------------------------------ HIGH_ATR


def test_G1_threshold_uses_override_multiplier_absolute_cap_and_upper_median():
    data = inputs()
    ctx = context(data)
    assert ctx.median_atr_pct == 2.2  # 40 names: sorted index 20, not mean 2.195.
    threshold = 2.2 * ATR_MULTIPLIER["balanced"]
    equal = apply(row(atr_pct=threshold, common=("HIGH_ATR",)), ctx)
    assert "HIGH_ATR" not in equal["rejection_reasons"]
    assert equal["atr_threshold_pct"] == pytest.approx(threshold)
    assert equal["atr_multiplier_source"] == "v1.5_override"
    assert "HIGH_ATR" in apply(row(atr_pct=threshold + .00001), ctx)["rejection_reasons"]
    high_context = context([replace(item, atr_pct=20.0) for item in data])
    assert apply(ctx=high_context)["atr_threshold_pct"] == 8.0
    # Conservative keeps the registry multiplier untouched.
    assert atr_threshold(registry(), "conservative", 2.2) == (pytest.approx(2.2 * 1.25), 1.25, "registry")
    assert atr_threshold(registry(), "aggressive", 2.2) == (pytest.approx(2.2 * 3.0), 3.0, "v1.5_override")


def test_thin_pool_does_not_relax_into_a_pass():
    ctx = context(inputs(MIN_REFERENCE_N - 1))
    result = apply(row(sid="UNSEEN-000"), ctx)
    assert ctx.median_atr_pct is None
    assert ATR_REFERENCE_UNAVAILABLE in result["rejection_reasons"]
    assert result["factors"]["M"] == 50.0


# ------------------------------------------------------------------ EXTENDED as entry state


def test_extended_moves_to_entry_state_and_other_rejections_survive():
    reasons = ("EXTENDED", "LOW_ADV", "SHORT_HISTORY", "UNRESOLVED_UPTHRUST",
               "DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED", "HIGH_ATR")
    source = row(common=reasons, setup=("HIGH_ATR", "LOW_EVENT_RVOL", "TOO_FAR_FROM_BASE"))
    result = apply(source, profile="aggressive")
    kept = set(source["rejection_reasons"]) - {"EXTENDED"}
    assert kept <= set(result["rejection_reasons"])
    assert "EXTENDED" not in result["gate_results"]["common"]
    assert result["entry_state"] == EXTENDED_STATE
    assert result["entry_gate_reasons"] == ["EXTENDED"]
    assert result["gate_results"]["setup"] == source["gate_results"]["setup"]
    assert result["status"] == "rejected"


def test_extended_only_row_is_no_longer_a_common_rejection_but_never_eligible():
    result = apply(row(common=("EXTENDED",)))
    assert result["gate_results"]["common"] == ()
    assert "EXTENDED" not in result["rejection_reasons"]
    assert result["entry_state"] == EXTENDED_STATE
    # Simulate the existing rescore promoting it, then the post-hook.
    promoted = {**result, "status": "eligible", "rejection_reasons": []}
    after = apply_entry_states({"rows": [promoted, {**result, "status": "watch", "rejection_reasons": ["X"]},
                                         {**result, "status": "rejected"}, row(sid="OK")]})["rows"]
    assert after[0]["status"] == "watch" and after[0]["rejection_reasons"] == [EXTENDED_WAIT_REASON]
    assert after[1]["status"] == "watch" and after[1]["rejection_reasons"] == ["X", EXTENDED_WAIT_REASON]
    assert after[2]["status"] == "rejected"
    assert after[3] == row(sid="OK")


def test_conservative_keeps_extended_as_a_hard_rejection():
    assert "conservative" not in EXTENDED_STATE_PROFILES
    result = apply(row(common=("EXTENDED",)), profile="conservative")
    assert "EXTENDED" in result["gate_results"]["common"] and "EXTENDED" in result["rejection_reasons"]
    assert result["entry_state"] == "ok"
    assert result["factors"]["M"] == 50.0 and result["factors"]["R"] == 90.0
    assert result["atr_multiplier_source"] == "registry"


def test_non_extended_rows_keep_entry_state_ok():
    result = apply()
    assert result["entry_state"] == "ok"
    assert result["entry_gate_reasons"] == []
    assert apply_entry_states({"rows": [result]})["rows"][0] == result


# ------------------------------------------------------------------ R neutralisation


@pytest.mark.parametrize("profile", list(M_ALPHA))
def test_R_is_neutralised_only_for_balanced_and_aggressive(profile):
    result = apply(profile=profile)
    if profile in R_NEUTRAL_PROFILES:
        assert result["factors"]["R"] == R_NEUTRAL_VALUE
        assert result["full_market_tuning"]["R_neutralized"] is True
    else:
        assert result["factors"]["R"] == 90.0
        assert result["full_market_tuning"]["R_neutralized"] is False
    assert result["full_market_tuning"]["R_original"] == 90.0


def test_missing_R_stays_missing_and_coverage_is_untouched():
    source = row(); source["factors"]["R"] = None
    result = apply(source)
    assert result["factors"]["R"] is None
    assert result["full_market_tuning"]["R_neutralized"] is False


def test_R_neutralisation_applies_to_every_family_including_B_and_C():
    for family in (A, B, C, D):
        assert apply(row(family=family), profile="aggressive")["factors"]["R"] == R_NEUTRAL_VALUE


# ------------------------------------------------------------------ invariants kept from v1.4


def test_new_module_never_assigns_eligibility_or_volume_qualification():
    source = row(common=("HIGH_ATR",), capability_flags={"volume_verified": False})
    result = apply(source)
    assert "HIGH_ATR" not in result["rejection_reasons"]
    assert result["status"] == "rejected"  # Only the existing rescore can decide.
    assert result["capability_flags"] == source["capability_flags"]


def test_missing_benchmark_is_fallback_not_fake_zero_return():
    ctx = context(spy=False)
    assert ctx.median_atr_pct is not None
    assert not ctx.momentum_percentiles
    result = apply(ctx=ctx, profile="aggressive")
    assert result["factors"]["M"] == 50.0
    assert result["full_market_tuning"]["reason"] == "new_momentum_unavailable_keep_original"


def test_missing_internal_bar_not_only_endpoint_is_rejected():
    data = inputs()
    prices = dict(data[0].closes)
    del prices[CALENDAR[-40]]
    data[0] = replace(data[0], closes=prices)
    ctx = context(data)
    assert data[0].security_id in ctx.reference_ids
    assert data[0].security_id not in ctx.momentum_percentiles


def test_partial_reference_observation_uses_original_factor():
    data = inputs()
    data[0] = replace(data[0], source_available=False)
    result = apply(row(sid=data[0].security_id), context(data))
    assert result["factors"]["M"] == 50.0


def test_future_bars_do_not_change_context():
    original = context()
    data = [replace(item, closes={**item.closes, DAY + timedelta(days=1): 1e20}) for item in inputs()]
    future = context(data)
    assert original.digest == future.digest
    assert dict(original.relative_returns) == dict(future.relative_returns)


def test_order_invariance_and_case_preserving_identifiers():
    data = inputs()
    data.extend([replace(data[0], security_id="BCPC"), replace(data[1], security_id="BCpC")])
    original = context(data)
    random.Random(174).shuffle(data)
    shuffled = context(data)
    assert original.digest == shuffled.digest
    assert {"BCPC", "BCpC"} <= original.stock_ids


def test_ties_are_neutral_and_not_order_dependent():
    data = [replace(item, closes=inputs()[0].closes) for item in inputs()]
    assert all(value == pytest.approx(50.0) for value in context(data).momentum_percentiles.values())


def test_duplicate_ids_and_mixed_sessions_fail_explicitly():
    data = inputs()
    with pytest.raises(ValueError, match="security_id"):
        context(data + [data[0]])
    data[0] = replace(data[0], session=DAY - timedelta(days=1))
    with pytest.raises(ValueError, match="session mismatch"):
        context(data)


def test_same_context_is_idempotent_and_different_profile_cannot_stack():
    ctx = context()
    result = apply(ctx=ctx)
    assert apply(result, ctx) == result
    with pytest.raises(ValueError, match="stack"):
        apply(result, ctx, profile="aggressive")


def test_theme_names_do_not_change_policy_or_reference():
    source = row()
    themed = {**source, "sector_context": "semiconductors"}
    first, second = apply(source), apply(themed)
    for key in ("factors", "atr_threshold_pct", "full_market_tuning", "entry_state"):
        assert first[key] == second[key]


def test_rows_are_not_added_removed_reranked_or_topk_filtered():
    ctx = context()
    rows = [row(sid="UNSEEN-030"), row(sid="UNSEEN-000"), row(sid="OUTSIDE", stock_or_etf_track="etf")]
    payload = {"session_date": DAY.isoformat(), "rows": rows}
    before = deepcopy(payload)
    out = tune_snapshot(payload, ctx, registry=registry(), profile="balanced", horizon="mid")
    assert payload == before
    assert [r["security_id"] for r in out["rows"]] == [r["security_id"] for r in rows]
    assert out["rows"][-1] == rows[-1]
    assert out["full_market_tuning"]["extended_is_entry_state"] is True


def test_invalid_context_identity_rejected():
    with pytest.raises(ValueError, match="mismatch"):
        tune_snapshot({"session_date": "2024-01-01", "rows": []}, context(), registry=registry(), profile="balanced", horizon="mid")
    with pytest.raises(ValueError, match="mismatch"):
        tune_snapshot({"session_date": DAY.isoformat(), "rows": []}, context(), registry=registry(), profile="balanced", horizon="long")


def test_source_reject_without_factors_is_not_repaired():
    source = row(factors={}, common=("SOURCE_UNAVAILABLE",), status="rejected")
    assert apply(source) == source


def test_sigma_floor_never_produces_non_finite_targets():
    ctx = context()
    assert all(math.isfinite(v) for pair in ctx.relative_returns.values() for v in pair)
