"""Deterministic macro exposures per sector.

The point of a stock-level macro fit is that one macro environment must push
different stocks in different directions. Adding the same composite score to
every stock changes no ordering at all -- it only shifts the whole result set,
which looks like an effect and is not one.

So this registry answers one question, per sector, per macro factor:

    beta in [-1, 1]: does a *supportive* reading of this factor help or hurt
    this kind of company, and how much?

Three properties this file is built around:

1. **Deterministic.** No model infers a company's macro exposure. A beta is a
   reviewable number in version control, not an opinion regenerated per run.

2. **Factor level, never module level.** The external-shock module is the clear
   case: low oil scores as supportive for broad risk assets, and that same
   reading is a headwind for an energy producer. Applying a module total would
   give producers and airlines the same sign. Every beta here attaches to a
   factor id.

3. **Versioned.** Changing a beta changes what a macro fit means, so it changes
   ``EXPOSURE_VERSION`` too. The score carries that version with it.

The sector ids are the ones the strength universe already uses; the factor ids
are the ones the macro registry already publishes. Both are asserted against
their sources in the tests, because a registry that silently references a factor
that no longer exists degrades into "no exposure" without ever failing.
"""

from __future__ import annotations

from typing import Mapping

#: Bump on any beta change. A macro fit produced under a different version is a
#: different quantity and must not be compared with one produced under this.
EXPOSURE_VERSION = "macro-linkage-v1"

#: Factors whose beta is 0 for every sector are omitted rather than written as
#: zero: an absent entry says "this factor does not differentiate sectors in a
#: way we are prepared to defend", which is a different statement from "we
#: measured it and it is zero".
_Betas = Mapping[str, float]

#: What a *supportive* reading means for each factor it appears under, so the
#: sign of every beta below can be checked by reading rather than by inference:
#:
#:   fed_net_liquidity / bank_reserves / net_liquidity_momentum_13w
#:       high score = more reserves available to the financial system
#:   real_rate_level / real_curve_10y_5y
#:       high score = *lower* real rates (the factor scores support, not level)
#:   collateral_repo_friction / corridor_friction_* / funding_fragmentation_21d
#:       high score = less funding friction
#:   hy_credit / ig_credit / nfci
#:       high score = easier credit
#:   regional_banks_vs_spy
#:       high score = regional banks leading
#:   vix / vix_term_structure / risk_vs_safe / high_beta_preference
#:       high score = more risk appetite
#:   broad_dollar_index
#:       high score = *weaker* dollar
#:   wti_oil / natural_gas
#:       high score = *lower* energy prices
#:   term_premium_30y_10y / curve_curvature_abs
#:       high score = a steeper, more normal curve
#:   breakeven_10y
#:       high score = inflation expectations anchored
SECTOR_EXPOSURES: Mapping[str, _Betas] = {
    # Long-duration growth: the classic real-rate and liquidity trade.
    "software": {
        "fed_net_liquidity": 0.7,
        "net_liquidity_momentum_13w": 0.6,
        "real_rate_level": 0.9,
        "real_curve_10y_5y": 0.5,
        "high_beta_preference": 0.7,
        "risk_vs_safe": 0.5,
        "broad_dollar_index": 0.3,
        "hy_credit": 0.3,
    },
    "semiconductors": {
        "fed_net_liquidity": 0.6,
        "net_liquidity_momentum_13w": 0.5,
        "real_rate_level": 0.7,
        "high_beta_preference": 0.8,
        "risk_vs_safe": 0.6,
        # Semis sell abroad; a weaker dollar helps reported revenue.
        "broad_dollar_index": 0.5,
        "hy_credit": 0.3,
    },
    "ai_cloud": {
        "fed_net_liquidity": 0.7,
        "net_liquidity_momentum_13w": 0.6,
        "real_rate_level": 0.8,
        "high_beta_preference": 0.7,
        "risk_vs_safe": 0.5,
        "broad_dollar_index": 0.3,
    },
    "social_internet": {
        "fed_net_liquidity": 0.5,
        "real_rate_level": 0.6,
        "high_beta_preference": 0.6,
        "risk_vs_safe": 0.5,
        "broad_dollar_index": 0.4,
    },
    # No near-term cash flows at all: the most rate- and liquidity-sensitive.
    "biotech": {
        "fed_net_liquidity": 0.8,
        "net_liquidity_momentum_13w": 0.7,
        "real_rate_level": 0.9,
        "high_beta_preference": 0.8,
        "risk_vs_safe": 0.6,
        "hy_credit": 0.5,
        "funding_fragmentation_21d": 0.3,
    },
    "crypto": {
        "fed_net_liquidity": 0.9,
        "net_liquidity_momentum_13w": 0.8,
        "real_rate_level": 0.7,
        "high_beta_preference": 0.9,
        "risk_vs_safe": 0.8,
        "vix": 0.5,
    },
    # Banks: funding conditions and the curve they earn on.
    "finance": {
        "collateral_repo_friction": 0.6,
        "corridor_friction_1": 0.5,
        "funding_fragmentation_21d": 0.7,
        "term_premium_30y_10y": 0.6,
        "hy_credit": 0.6,
        "ig_credit": 0.5,
        "rate_volatility_10y_21d": 0.4,
        "regional_banks_vs_spy": 0.4,
        "nfci": 0.5,
    },
    "fintech": {
        "fed_net_liquidity": 0.5,
        "real_rate_level": 0.6,
        "hy_credit": 0.6,
        "high_beta_preference": 0.7,
        "funding_fragmentation_21d": 0.5,
        "risk_vs_safe": 0.5,
    },
    # Rate-sensitive income: long real rates dominate.
    "real_estate": {
        "real_rate_level": 0.9,
        "real_curve_10y_5y": 0.6,
        "term_premium_30y_10y": -0.4,
        "ig_credit": 0.6,
        "hy_credit": 0.4,
        "nfci": 0.4,
    },
    "utilities": {
        "real_rate_level": 0.8,
        "term_premium_30y_10y": -0.3,
        "ig_credit": 0.5,
        # Defensive: a risk-on tape is a relative headwind.
        "high_beta_preference": -0.4,
        "risk_vs_safe": -0.3,
        "natural_gas": -0.3,
    },
    "telecom": {
        "real_rate_level": 0.6,
        "ig_credit": 0.5,
        "high_beta_preference": -0.3,
        "risk_vs_safe": -0.2,
    },
    # Energy producers: the sign that a module total would get backwards.
    "energy": {
        # wti_oil scores *low* oil as supportive, so a producer's beta is
        # negative. This is the case the review calls out by name.
        "wti_oil": -0.9,
        "natural_gas": -0.6,
        "oil_volatility_deviation": -0.3,
        "broad_dollar_index": 0.5,
        "hy_credit": 0.3,
    },
    "airlines": {
        # Fuel is the cost line: low oil is a genuine tailwind here.
        "wti_oil": 0.8,
        "hy_credit": 0.6,
        "risk_vs_safe": 0.5,
        "high_beta_preference": 0.5,
        "nfci": 0.4,
    },
    "automotive": {
        "wti_oil": 0.4,
        "real_rate_level": 0.6,
        "hy_credit": 0.6,
        "high_beta_preference": 0.5,
        "broad_dollar_index": 0.4,
    },
    "ev_supply": {
        "fed_net_liquidity": 0.6,
        "real_rate_level": 0.7,
        "high_beta_preference": 0.8,
        "hy_credit": 0.5,
        "broad_dollar_index": 0.4,
    },
    "industrials": {
        "hy_credit": 0.6,
        "ig_credit": 0.4,
        "broad_dollar_index": 0.6,
        "risk_vs_safe": 0.5,
        "nfci": 0.4,
        "term_premium_30y_10y": 0.3,
    },
    "defense_aero": {
        # Programme-funded revenue: the least macro-sensitive of the cyclicals.
        "hy_credit": 0.3,
        "risk_vs_safe": 0.2,
        "broad_dollar_index": 0.2,
    },
    "retail": {
        "wti_oil": 0.5,
        "hy_credit": 0.5,
        "real_rate_level": 0.5,
        "risk_vs_safe": 0.4,
        "breakeven_10y": 0.3,
    },
    "luxury": {
        "high_beta_preference": 0.6,
        "risk_vs_safe": 0.5,
        "broad_dollar_index": 0.5,
        "hy_credit": 0.4,
    },
    "media_streaming": {
        "real_rate_level": 0.5,
        "high_beta_preference": 0.5,
        "risk_vs_safe": 0.4,
        "hy_credit": 0.4,
    },
    "consumer_electronics": {
        "real_rate_level": 0.4,
        "broad_dollar_index": 0.5,
        "high_beta_preference": 0.4,
        "risk_vs_safe": 0.3,
    },
    # Defensive: the reference case, deliberately close to macro-neutral.
    "healthcare": {
        "real_rate_level": 0.3,
        "high_beta_preference": -0.3,
        "risk_vs_safe": -0.2,
        "broad_dollar_index": 0.2,
    },
    "china_adr": {
        "broad_dollar_index": 0.8,
        "high_beta_preference": 0.6,
        "risk_vs_safe": 0.5,
        "fx_realized_volatility_63d": 0.4,
        "fed_net_liquidity": 0.4,
    },
    # A basket is whatever it holds; no defensible single exposure.
    "etfs": {},
}

#: Sectors with no entry get no macro fit rather than a neutral 50. A missing
#: exposure is missing information, and 50 is a claim about the environment.
DEFAULT_EXPOSURES: _Betas = {}


def exposures_for(sector_id: str | None) -> _Betas:
    """Betas for a sector id, or an empty mapping when we have none."""

    if not sector_id:
        return DEFAULT_EXPOSURES
    return SECTOR_EXPOSURES.get(sector_id, DEFAULT_EXPOSURES)


def covered_sectors() -> tuple[str, ...]:
    """Sector ids this registry can score, in declaration order."""

    return tuple(key for key, betas in SECTOR_EXPOSURES.items() if betas)


def referenced_factors() -> frozenset[str]:
    """Every factor id any sector refers to."""

    return frozenset(
        factor_id for betas in SECTOR_EXPOSURES.values() for factor_id in betas
    )


__all__ = [
    "DEFAULT_EXPOSURES",
    "EXPOSURE_VERSION",
    "SECTOR_EXPOSURES",
    "covered_sectors",
    "exposures_for",
    "referenced_factors",
]
