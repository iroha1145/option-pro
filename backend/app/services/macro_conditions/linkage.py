"""Stock- and sector-level macro fit.

Shadow only. Nothing here touches intrinsic strength, breakout quality, or any
production ranking; it produces a parallel number so the idea can be measured
against outcomes before it is allowed to move anything.

The shape, from the incremental review:

    z_f        = (score_f - 50) / 50          in [-1, 1]
    MacroFit_i = 50 + 50 * sum(w_f q_f b_if z_f) / sum(w_f q_f |b_if|)
    MacroFit*  = 50 + (MacroFit_i - 50) * confidence_i

where ``w_f`` is the factor's share of the composite under equal module
weighting, ``q_f`` the factor's own confidence, and ``b_if`` the sector
exposure. Too thin an observed profile yields ``None`` -- never 50, which is a
claim about the environment rather than an admission that we cannot say.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Optional, Sequence

from .exposures import EXPOSURE_VERSION, exposures_for
from .registry import FACTORS_BY_ID, MODULES_BY_ID

#: Minimum share of a sector's declared exposure that must actually be
#: observable before a fit is reported. Below this the score rests on a fraction
#: of the profile it claims to describe and reads far more precisely than it is.
#: Expressed as a share rather than an absolute weight so it does not silently
#: change meaning when the factor registry grows.
MIN_EXPOSURE_COVERAGE = 0.5

#: How far a shadow adjustment may move a ranking score, in points. The review
#: fixes these caps: macro must never overrule a stock's own price evidence.
STRENGTH_SHADOW_CAP = 3.0
BREAKOUT_PRIORITY_SHADOW_CAP = 4.0

#: Tailwind labels. Deliberately coarse: the underlying number is a percentile
#: blend, and three buckets is about what it can support.
TAILWIND_STRONG = 65.0
TAILWIND_WEAK = 35.0


@dataclass(frozen=True, slots=True)
class MacroFit:
    """One stock's or sector's macro fit, plus why."""

    score: Optional[float]
    confidence: float
    version: str
    tailwind: Optional[str]
    #: Factor ids that pushed the score up, strongest first.
    supporting: tuple[str, ...]
    #: Factor ids that pushed it down, strongest first.
    opposing: tuple[str, ...]
    #: Summed w*q*|beta| actually used; reported so a thin fit is visible.
    effective_weight: float

    def as_payload(self) -> dict[str, Any]:
        return {
            "macro_fit_shadow": self.score,
            "macro_fit_confidence": round(self.confidence, 4),
            "macro_fit_version": self.version,
            "macro_tailwind": self.tailwind,
            "macro_supporting_factors": list(self.supporting),
            "macro_opposing_factors": list(self.opposing),
            "macro_fit_effective_weight": round(self.effective_weight, 4),
        }


UNAVAILABLE = MacroFit(
    score=None,
    confidence=0.0,
    version=EXPOSURE_VERSION,
    tailwind=None,
    supporting=(),
    opposing=(),
    effective_weight=0.0,
)


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


#: Factors per module, computed once. Modules are equal-weighted in the
#: composite, and factors are equal-weighted inside their module, so a factor's
#: share is 1/modules * 1/factors-in-its-module. Counting factors flat instead
#: would let the funding module (six factors) outweigh treasury (three) purely
#: by population, which is not what the composite does.
_MODULE_FACTOR_COUNTS: dict[str, int] = {}
for _spec in FACTORS_BY_ID.values():
    _MODULE_FACTOR_COUNTS[_spec.module_id] = _MODULE_FACTOR_COUNTS.get(_spec.module_id, 0) + 1


def _factor_weight(factor_id: str) -> float:
    """This factor's share of the composite, under equal module weighting."""

    spec = FACTORS_BY_ID.get(factor_id)
    if spec is None:
        # A registry entry pointing at a factor that no longer exists must not
        # quietly count as zero-weight evidence; it counts as nothing at all.
        return 0.0
    siblings = _MODULE_FACTOR_COUNTS.get(spec.module_id, 0)
    if siblings <= 0 or not MODULES_BY_ID:
        return 0.0
    return 1.0 / (len(MODULES_BY_ID) * siblings)


def tailwind_label(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    if score >= TAILWIND_STRONG:
        return "顺风"
    if score <= TAILWIND_WEAK:
        return "逆风"
    return "中性"


def compute_macro_fit(
    factor_rows: Iterable[Mapping[str, Any]],
    *,
    sector_id: str | None,
    max_drivers: int = 3,
) -> MacroFit:
    """Macro fit for one sector's exposure profile.

    ``factor_rows`` are published factor snapshots: mappings with at least
    ``factor_id``, ``score`` and ``confidence``. Rows whose score is missing are
    skipped rather than treated as neutral -- a missing factor is missing, and
    scoring it 50 would quietly pull every fit toward the middle.
    """

    betas = exposures_for(sector_id)
    if not betas:
        return UNAVAILABLE

    numerator = 0.0
    denominator = 0.0
    contributions: list[tuple[float, str]] = []
    # The denominator of coverage is the sector's *declared* profile, not the
    # rows that happened to arrive. Measuring it over the observed rows only
    # answers "of what I saw, how much did I trust it", which is 100% even when
    # a single factor out of eight showed up.
    declared_weight = sum(
        _factor_weight(factor_id) * abs(beta) for factor_id, beta in betas.items()
    )
    observed_weight = 0.0

    for row in factor_rows:
        factor_id = str(row.get("factor_id") or "")
        beta = betas.get(factor_id)
        if beta is None:
            continue
        score = _finite(row.get("score"))
        if score is None:
            continue
        quality = _finite(row.get("confidence"))
        quality = 1.0 if quality is None else max(0.0, min(1.0, quality))
        weight = _factor_weight(factor_id)
        if weight <= 0.0 or quality <= 0.0:
            continue
        z = (score - 50.0) / 50.0
        share = weight * quality
        numerator += share * beta * z
        denominator += share * abs(beta)
        observed_weight += share * abs(beta)
        contributions.append((share * beta * z, factor_id))

    # Confidence is the share of this sector's declared exposure we could
    # actually observe, weighted by how much each factor matters.
    confidence = (
        max(0.0, min(1.0, observed_weight / declared_weight))
        if declared_weight > 0.0
        else 0.0
    )
    if denominator <= 0.0 or confidence < MIN_EXPOSURE_COVERAGE:
        # Not enough of this sector's exposure profile is observable. Reporting
        # 50 here would be a claim about the environment; None is the truth.
        return MacroFit(
            score=None,
            confidence=round(confidence, 4),
            version=EXPOSURE_VERSION,
            tailwind=None,
            supporting=(),
            opposing=(),
            effective_weight=round(denominator, 6),
        )

    raw = 50.0 + 50.0 * (numerator / denominator)
    raw = max(0.0, min(100.0, raw))
    # Shrink toward neutral by confidence: a fit built on half the profile
    # should not read as strongly as one built on all of it.
    shrunk = 50.0 + (raw - 50.0) * confidence

    contributions.sort(key=lambda item: item[0], reverse=True)
    supporting = tuple(name for value, name in contributions if value > 0)[:max_drivers]
    opposing = tuple(
        name for value, name in sorted(contributions, key=lambda item: item[0])
        if value < 0
    )[:max_drivers]

    return MacroFit(
        score=round(shrunk, 1),
        confidence=confidence,
        version=EXPOSURE_VERSION,
        tailwind=tailwind_label(shrunk),
        supporting=supporting,
        opposing=opposing,
        effective_weight=round(denominator, 6),
    )


def shadow_ranking_adjustment(fit: MacroFit) -> float:
    """Points a macro fit would move a ranking score, capped at +/-3.

    Shadow only. The cap exists so macro can never overrule a stock's own trend
    and volume evidence -- it is a tilt, not a vote.
    """

    if fit.score is None:
        return 0.0
    raw = (fit.score - 50.0) / 50.0 * STRENGTH_SHADOW_CAP
    return round(max(-STRENGTH_SHADOW_CAP, min(STRENGTH_SHADOW_CAP, raw)), 3)


def shadow_alert_priority_adjustment(fit: MacroFit) -> float:
    """Points a macro fit would move a breakout alert priority, capped at +/-4."""

    if fit.score is None:
        return 0.0
    raw = (fit.score - 50.0) / 50.0 * BREAKOUT_PRIORITY_SHADOW_CAP
    return round(
        max(-BREAKOUT_PRIORITY_SHADOW_CAP, min(BREAKOUT_PRIORITY_SHADOW_CAP, raw)),
        3,
    )


def macro_technical_gap(
    technical_market_fit: Optional[float],
    structural_macro_score: Optional[float],
) -> Optional[float]:
    """Technical minus structural macro, as a two-dimensional read.

    A single blended number hides the interesting cases. A large positive gap
    says price has run ahead of the environment; a large negative one says the
    environment improved first and price has not followed.
    """

    technical = _finite(technical_market_fit)
    macro = _finite(structural_macro_score)
    if technical is None or macro is None:
        return None
    return round(technical - macro, 1)


#: Structural macro is liquidity/funding/treasury/rates. Credit and risk are
#: priced off the same instruments the technical market regime already reads
#: (HYG, LQD, KRE, VIX, SPY/TLT, IWM/SPY), so counting them again would weight
#: one signal twice under two names.
STRUCTURAL_MODULES = ("liquidity", "funding", "treasury", "rates")
CONFIRMING_MODULES = ("credit", "risk")


def structural_macro_score(module_rows: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Weighted mean of the structural modules only."""

    total = 0.0
    weight = 0.0
    for row in module_rows:
        module_id = str(row.get("module_id") or "")
        if module_id not in STRUCTURAL_MODULES:
            continue
        score = _finite(row.get("score"))
        if score is None:
            continue
        module = MODULES_BY_ID.get(module_id)
        module_weight = _finite(getattr(module, "weight", None)) or 1.0
        total += score * module_weight
        weight += module_weight
    if weight <= 0.0:
        return None
    return round(total / weight, 1)


__all__ = [
    "BREAKOUT_PRIORITY_SHADOW_CAP",
    "CONFIRMING_MODULES",
    "MIN_EXPOSURE_COVERAGE",
    "STRENGTH_SHADOW_CAP",
    "STRUCTURAL_MODULES",
    "MacroFit",
    "UNAVAILABLE",
    "compute_macro_fit",
    "macro_technical_gap",
    "shadow_alert_priority_adjustment",
    "shadow_ranking_adjustment",
    "structural_macro_score",
    "tailwind_label",
]
