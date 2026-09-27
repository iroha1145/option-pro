"""Switchable v1.7 candidate paths. ``ScoringOptions()`` reproduces v1.6 exactly.

Every candidate is a field here so a replay can turn one on at a time and
production can adopt the winners by changing ``live_config.LIVE_CONFIG``:

* ``industry_mode="g_only"``: the G factor is computed from the SIC tags for the
  ``g_profiles`` and enters their weights; every other factor keeps its
  market-wide quantile and the D residual stays one-factor (market only).
* ``industry_mode="full"``: the tags are written onto the series, so the engine's
  original industry design is active everywhere: industry-relative quantiles
  (``cross_section.q_star``), the two-factor D residual (market + industry
  basket) and G. G still enters only the ``g_profiles`` weights.
* ``tuning``: the v1.5 hook policy per profile (see ``full_market_tuning``).

Registry-level candidates (factor tilts, including a G tilt) are expressed
through ``market_registry.load_market_registry(extra_tilt_multipliers=...)``;
the fund scope of the universe through ``universe.select_all_market_universe``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC, PRICE_SIC_INDUSTRY_DIAGNOSTIC

from .full_market_tuning import DEFAULT_POLICY, TuningPolicy
from .industry import IndustryTag

INDUSTRY_OFF = "off"
INDUSTRY_G_ONLY = "g_only"
INDUSTRY_FULL = "full"
INDUSTRY_MODES = (INDUSTRY_OFF, INDUSTRY_G_ONLY, INDUSTRY_FULL)
DEFAULT_G_PROFILES = frozenset({"balanced", "aggressive"})
G_DISABLED = "disabled_unverified_industry"
# On the industry track a security without a classification (no SIC code, or a
# fund) gets the neutral G. Leaving G missing would renormalise its score over
# the other seven factors, a systematic advantage over classified peers whose G
# sits near 50, and with a doubled G weight it would fail coverage_min outright.
G_NEUTRAL_VALUE = 50.0


@dataclass(frozen=True)
class ScoringOptions:
    industry_mode: str = INDUSTRY_OFF
    industry: Mapping[str, IndustryTag] = field(default_factory=dict)
    # Conservative keeps coverage_min 0.95: with G in its weights every stock
    # without a SIC code (about a fifth of the market, mostly ADRs) would fail
    # coverage in every family. G therefore enters only these profiles' weights.
    g_profiles: frozenset[str] = DEFAULT_G_PROFILES
    tuning: TuningPolicy = DEFAULT_POLICY
    label: str = "v1.6"
    _overlay: Mapping[str, tuple[str | None, str | None]] | None = field(default=None, init=False, repr=False,
                                                                        compare=False)

    def __post_init__(self) -> None:
        if self.industry_mode not in INDUSTRY_MODES:
            raise ValueError(f"industry_mode must be one of {INDUSTRY_MODES}")
        if self.industry_mode != INDUSTRY_OFF and not self.industry:
            raise ValueError("an industry mode needs a non-empty classification")
        object.__setattr__(self, "g_profiles", frozenset(self.g_profiles))
        object.__setattr__(self, "industry", MappingProxyType(dict(self.industry)))
        if self.industry_mode == INDUSTRY_G_ONLY:
            overlay = {sid: tag.as_pair() for sid, tag in self.industry.items()}
            object.__setattr__(self, "_overlay", MappingProxyType(overlay))

    @property
    def active(self) -> bool:
        """False only for the v1.6 production behaviour."""
        return self.industry_mode != INDUSTRY_OFF or self.tuning != DEFAULT_POLICY

    def panel_industry(self) -> Mapping[str, IndustryTag] | None:
        """Tags written onto the series (full mode); None leaves the fields cleared."""
        return self.industry if self.industry_mode == INDUSTRY_FULL else None

    def g_overlay(self) -> Mapping[str, tuple[str | None, str | None]] | None:
        """Tags used by compute_snapshot for G alone (g_only mode)."""
        return self._overlay

    def track_for(self, profile: str) -> str:
        if self.industry_mode != INDUSTRY_OFF and profile in self.g_profiles:
            return PRICE_SIC_INDUSTRY_DIAGNOSTIC
        return PRICE_ONLY_DIAGNOSTIC

    def factor_capabilities(self) -> dict[str, str]:
        g = G_DISABLED if self.industry_mode == INDUSTRY_OFF else f"sic_industry_{self.industry_mode}_missing_neutral"
        return {"R": "stability_risk_quality", "G": g}

    def describe(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "industry_mode": self.industry_mode,
            "industry_tagged": len(self.industry),
            "industry_groups": len({tag.industry_id for tag in self.industry.values()}),
            "g_profiles": sorted(self.g_profiles),
            "tuning_version": self.tuning.version,
        }


DEFAULT_OPTIONS = ScoringOptions()


def resolve_options(options: ScoringOptions | None) -> ScoringOptions:
    return DEFAULT_OPTIONS if options is None else options


__all__ = [
    "DEFAULT_G_PROFILES",
    "DEFAULT_OPTIONS",
    "G_DISABLED",
    "G_NEUTRAL_VALUE",
    "INDUSTRY_FULL",
    "INDUSTRY_G_ONLY",
    "INDUSTRY_MODES",
    "INDUSTRY_OFF",
    "ScoringOptions",
    "resolve_options",
]
