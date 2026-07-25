"""Stock-level macro fit (incremental review, Phase 1).

The whole reason this exists: adding one composite score to every stock changes
no ordering at all. These tests are organised around that claim -- the same
macro reading has to push different sectors in different directions, and it has
to decline to answer when it cannot see enough to say anything.
"""

from __future__ import annotations

import pytest

from app.services.macro_conditions.exposures import (
    EXPOSURE_VERSION,
    SECTOR_EXPOSURES,
    covered_sectors,
    exposures_for,
    referenced_factors,
)
from app.services.macro_conditions.linkage import (
    BREAKOUT_PRIORITY_SHADOW_CAP,
    MIN_EXPOSURE_COVERAGE,
    STRENGTH_SHADOW_CAP,
    compute_macro_fit,
    macro_technical_gap,
    shadow_alert_priority_adjustment,
    shadow_ranking_adjustment,
    structural_macro_score,
    tailwind_label,
)
from app.services.macro_conditions.registry import FACTORS, MODULES


def _rows(**scores: float) -> list[dict]:
    return [
        {"factor_id": factor_id, "score": score, "confidence": 1.0}
        for factor_id, score in scores.items()
    ]


def _full_profile(sector: str, score: float) -> list[dict]:
    """Every factor this sector is exposed to, all at one score."""

    return _rows(**{factor_id: score for factor_id in exposures_for(sector)})


# ---------------- the registry describes things that exist ----------------


def test_every_referenced_factor_exists_in_the_macro_registry() -> None:
    """A stale factor id degrades into "no exposure" without ever failing."""

    known = {spec.factor_id for spec in FACTORS}
    unknown = sorted(referenced_factors() - known)
    assert unknown == [], f"exposures reference factors that do not exist: {unknown}"


def test_every_beta_is_a_defensible_magnitude() -> None:
    for sector, betas in SECTOR_EXPOSURES.items():
        for factor_id, beta in betas.items():
            assert -1.0 <= beta <= 1.0, f"{sector}.{factor_id} = {beta} is outside [-1, 1]"
            assert beta != 0.0, (
                f"{sector}.{factor_id} is written as 0; omit it instead. "
                "An absent entry says 'we do not claim an exposure', which is "
                "different from 'we measured it and it is zero'."
            )


def test_a_basket_sector_has_no_exposure_profile() -> None:
    """An ETF is whatever it holds; there is no defensible single exposure."""

    assert exposures_for("etfs") == {}
    assert compute_macro_fit(_rows(wti_oil=20.0), sector_id="etfs").score is None


def test_an_unknown_sector_scores_nothing_rather_than_fifty() -> None:
    fit = compute_macro_fit(_rows(wti_oil=20.0), sector_id="not_a_sector")
    assert fit.score is None, "a missing exposure profile is missing information"
    assert fit.tailwind is None


# ---------------- one environment, different directions ----------------


def test_high_oil_helps_producers_and_hurts_airlines() -> None:
    """The case the review names: a module total would give both the same sign.

    ``wti_oil`` scores *low* oil as supportive for broad risk assets. Applying
    the external-shock module total to every sector would tell an energy
    producer that expensive oil is bad for it.
    """

    environment = _rows(
        wti_oil=15.0,                 # oil is expensive
        natural_gas=25.0,
        oil_volatility_deviation=40.0,
        broad_dollar_index=60.0,
        hy_credit=55.0,
        risk_vs_safe=55.0,
        high_beta_preference=55.0,
        nfci=55.0,
        real_rate_level=55.0,
    )

    energy = compute_macro_fit(environment, sector_id="energy")
    airlines = compute_macro_fit(environment, sector_id="airlines")

    assert energy.score is not None and airlines.score is not None
    assert energy.score > 50.0, "expensive oil is a tailwind for a producer"
    assert airlines.score < energy.score, (
        "the same reading cannot be equally good for producers and for fuel buyers"
    )
    assert "wti_oil" in energy.supporting
    assert "wti_oil" in airlines.opposing


def test_tight_real_rates_hurt_duration_and_barely_touch_defensives() -> None:
    environment = _rows(
        real_rate_level=8.0,          # real rates are high
        real_curve_10y_5y=20.0,
        fed_net_liquidity=45.0,
        net_liquidity_momentum_13w=45.0,
        high_beta_preference=45.0,
        risk_vs_safe=45.0,
        broad_dollar_index=50.0,
        hy_credit=50.0,
        funding_fragmentation_21d=50.0,
        ig_credit=50.0,
        term_premium_30y_10y=50.0,
    )

    biotech = compute_macro_fit(environment, sector_id="biotech")
    healthcare = compute_macro_fit(environment, sector_id="healthcare")

    assert biotech.score is not None and healthcare.score is not None
    assert biotech.score < 50.0
    assert abs(biotech.score - 50.0) > abs(healthcare.score - 50.0), (
        "a pre-revenue sector must be more rate-sensitive than a defensive one"
    )
    assert "real_rate_level" in biotech.opposing


def test_a_uniformly_neutral_environment_scores_neutral_everywhere() -> None:
    for sector in covered_sectors():
        fit = compute_macro_fit(_full_profile(sector, 50.0), sector_id=sector)
        assert fit.score == 50.0, f"{sector} drifted off neutral: {fit.score}"
        assert fit.tailwind == "中性"


# ---------------- declining to answer ----------------


def test_a_thin_profile_returns_none_not_fifty() -> None:
    """50 is a claim about the environment, not an admission of ignorance."""

    betas = exposures_for("software")
    one_factor = _rows(**{next(iter(betas)): 90.0})

    fit = compute_macro_fit(one_factor, sector_id="software")
    assert fit.score is None
    assert fit.confidence < MIN_EXPOSURE_COVERAGE
    assert fit.effective_weight > 0.0, "the partial evidence is still reported"


def test_missing_factor_scores_are_skipped_not_treated_as_neutral() -> None:
    betas = exposures_for("software")
    rows = [
        {"factor_id": factor_id, "score": None, "confidence": 1.0}
        for factor_id in betas
    ]
    assert compute_macro_fit(rows, sector_id="software").score is None


def test_low_confidence_factors_shrink_the_result_toward_neutral() -> None:
    """A fit built on half-trusted inputs must not read as strongly."""

    betas = exposures_for("software")
    confident = [
        {"factor_id": factor_id, "score": 90.0, "confidence": 1.0}
        for factor_id in betas
    ]
    hesitant = [
        {"factor_id": factor_id, "score": 90.0, "confidence": 0.6}
        for factor_id in betas
    ]

    strong = compute_macro_fit(confident, sector_id="software")
    weak = compute_macro_fit(hesitant, sector_id="software")

    assert strong.score is not None and weak.score is not None
    assert strong.score > weak.score > 50.0
    assert weak.confidence < strong.confidence


# ---------------- shadow adjustments stay capped ----------------


@pytest.mark.parametrize("score", [0.0, 25.0, 50.0, 75.0, 100.0])
def test_shadow_adjustments_never_exceed_their_caps(score: float) -> None:
    betas = exposures_for("software")
    fit = compute_macro_fit(
        [{"factor_id": f, "score": score, "confidence": 1.0} for f in betas],
        sector_id="software",
    )
    assert abs(shadow_ranking_adjustment(fit)) <= STRENGTH_SHADOW_CAP
    assert abs(shadow_alert_priority_adjustment(fit)) <= BREAKOUT_PRIORITY_SHADOW_CAP


def test_an_unavailable_fit_moves_nothing() -> None:
    fit = compute_macro_fit(_rows(wti_oil=10.0), sector_id="etfs")
    assert shadow_ranking_adjustment(fit) == 0.0
    assert shadow_alert_priority_adjustment(fit) == 0.0


def test_the_cap_is_small_enough_not_to_overrule_price_evidence() -> None:
    """Three points cannot reorder stocks that differ meaningfully on their own."""

    assert STRENGTH_SHADOW_CAP <= 3.0
    assert BREAKOUT_PRIORITY_SHADOW_CAP <= 4.0


# ---------------- structural macro, and the two-dimensional read ----------------


def test_structural_macro_excludes_credit_and_risk() -> None:
    """Credit and risk price off the instruments the technical regime already reads.

    HYG, LQD, KRE, VIX, SPY/TLT and IWM/SPY appear in both, so counting them
    again would weight one signal twice under two names.
    """

    modules = [
        {"module_id": module.module_id, "score": 80.0 if module.module_id in
         {"liquidity", "funding", "treasury", "rates"} else 10.0}
        for module in MODULES
    ]
    assert structural_macro_score(modules) == 80.0


def test_structural_macro_is_none_when_nothing_structural_is_scored() -> None:
    assert structural_macro_score([{"module_id": "credit", "score": 70.0}]) is None
    assert structural_macro_score([]) is None


def test_the_gap_separates_price_running_ahead_from_macro_leading() -> None:
    assert macro_technical_gap(80.0, 40.0) == 40.0
    assert macro_technical_gap(35.0, 70.0) == -35.0
    # A missing side is not a zero gap.
    assert macro_technical_gap(None, 70.0) is None
    assert macro_technical_gap(80.0, None) is None


def test_tailwind_labels_are_bucketed_not_invented() -> None:
    assert tailwind_label(None) is None
    assert tailwind_label(70.0) == "顺风"
    assert tailwind_label(50.0) == "中性"
    assert tailwind_label(20.0) == "逆风"


def test_the_payload_names_its_version() -> None:
    """A fit from a different exposure version is a different quantity."""

    fit = compute_macro_fit(_full_profile("software", 70.0), sector_id="software")
    payload = fit.as_payload()
    assert payload["macro_fit_version"] == EXPOSURE_VERSION
    assert payload["macro_fit_shadow"] == fit.score
    assert set(payload) == {
        "macro_fit_shadow",
        "macro_fit_confidence",
        "macro_fit_version",
        "macro_tailwind",
        "macro_supporting_factors",
        "macro_opposing_factors",
        "macro_fit_effective_weight",
    }


# ---------------- the shadow attachment changes nothing ----------------


def test_shadow_attachment_leaves_every_production_field_untouched() -> None:
    """The one property that makes this safe to ship.

    Intrinsic strength, market fit, profile fit and ranking_score are the
    production numbers. Macro is an annotation until forward validation says
    otherwise, so the attachment may only add fields.
    """

    from app.services.strength import scanner

    production_fields = {
        "ranking_score": 72.5,
        "final_score": 72.5,
        "strength_score": 72.5,
        "intrinsic_strength_score": 68.0,
        "market_fit_score": 60.0,
        "profile_fit_score": 55.0,
        "score_short": 70.0,
        "breakout_quality_score": 64.0,
    }
    rows = [
        {"ticker": "XOM", "primary_sector_id": "energy", **production_fields},
        {"ticker": "DAL", "primary_sector_id": "airlines", **production_fields},
    ]
    before = [dict(row) for row in rows]

    scanner._attach_macro_fit_shadow(rows)

    for original, updated in zip(before, rows):
        for field, value in production_fields.items():
            assert updated[field] == value, (
                f"{field} changed on {updated['ticker']}: {value} -> {updated[field]}"
            )
        assert set(original) <= set(updated), "the attachment may only add fields"


def test_shadow_attachment_degrades_quietly_when_macro_is_unreadable() -> None:
    """A screener scan must not fail because the macro snapshot is missing.

    Rows carry no fit, which the interface shows as "no macro read" rather than
    as a neutral 50.
    """

    from app.services.strength import scanner

    rows = [{"ticker": "NVDA", "primary_sector_id": "semiconductors", "ranking_score": 80.0}]
    meta = scanner._attach_macro_fit_shadow(rows)

    assert meta["available"] is False
    assert meta["reason"], "an unavailable linkage has to say why"
    assert rows[0]["ranking_score"] == 80.0


def test_shadow_ranking_score_stays_inside_the_score_range() -> None:
    from app.services.strength import scanner

    assert scanner._shadow_ranking_score(99.5, 3.0) == 100.0
    assert scanner._shadow_ranking_score(1.0, -3.0) == 0.0
    assert scanner._shadow_ranking_score(None, 3.0) is None
    assert scanner._shadow_ranking_score(70.0, 0.0) == 70.0
