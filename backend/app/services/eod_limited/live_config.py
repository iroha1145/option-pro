"""Production switch board for the v1.7 candidates.

``LiveConfig()`` with no arguments is the v1.6 behaviour (every switch off);
``LIVE_CONFIG`` is what production runs. The historical replay
(research/option_pro_us_eod_v1/return_pack/full_market_v1_7) turned the switches
on one at a time through the same production functions; its ``live`` variant
reads ``LIVE_CONFIG`` so the adopted configuration can be replayed and compared
with the evaluated candidate lists.

v1.7 adopts stage-1 set S1 (2026-09-28, 176 replay dates 2023-03 to 2026-09):

* ``conservative_policy=v1.7``: the conservative list's 63-day top-20 excess over
  SPY went from -2.90 to +0.54 points per signal (paired +3.44, Newey-West t 4.0,
  better in both periods and all four years);
* ``fund_scope=benchmarks``: the scored pool keeps only SPY, QQQ and the sealed
  ``etfs`` theme funds; stock rows were identical to the full pool on every one
  of the 176 dates and nine views. Per replay date and worker the precompute
  took 264 s instead of 461 s and scoring 75 s instead of 128 s (about two
  fifths less). Fund scores become a within-twelve ranking, so the fund theme's
  strength is withheld (``diagnostics.build_theme_statistics``).

The industry switches stay off: every G candidate (g3, g3x2, g4, full3, full4)
was worse than v1.6 on the balanced and aggressive stock lists, worst in P1.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .full_market_tuning import DEFAULT_POLICY, TuningPolicy, conservative_v17_policy
from .industry import DEFAULT_LEVEL, DEFAULT_LOOKUP_BUDGET, IndustryTag
from .options import DEFAULT_G_PROFILES, INDUSTRY_MODES, INDUSTRY_OFF, ScoringOptions
from .universe import FUND_SCOPES, FUND_SCOPE_ALL, FUND_SCOPE_BENCHMARKS

CONSERVATIVE_V14 = "v1.4"
CONSERVATIVE_V17 = "v1.7"
# The v1.6 balanced/aggressive lean (T x0.5, M x2.0) applied to conservative too, and
# its R tilt brought back from 1.5 to 1.0: with R neutralised the tilt only shifts
# every score by a constant against the fixed 78-point floor.
CONSERVATIVE_V17_TILT_MULTIPLIERS = {"T": 0.5, "M": 2.0, "R": 1.0 / 1.5}


@dataclass(frozen=True)
class LiveConfig:
    industry_mode: str = INDUSTRY_OFF
    sic_level: int = DEFAULT_LEVEL
    g_tilt: float | None = None
    conservative_policy: str = CONSERVATIVE_V14
    conservative_atr_multiplier: float = 2.0
    fund_scope: str = FUND_SCOPE_ALL
    industry_lookup_budget: int = DEFAULT_LOOKUP_BUDGET

    def __post_init__(self) -> None:
        if self.industry_mode not in INDUSTRY_MODES:
            raise ValueError(f"industry_mode must be one of {INDUSTRY_MODES}")
        if self.conservative_policy not in {CONSERVATIVE_V14, CONSERVATIVE_V17}:
            raise ValueError("conservative_policy must be v1.4 or v1.7")
        if self.fund_scope not in FUND_SCOPES:
            raise ValueError(f"fund_scope must be one of {FUND_SCOPES}")
        if self.g_tilt is not None and not self.g_tilt > 0:
            raise ValueError("g_tilt must be a positive multiplier")

    @property
    def wants_industry(self) -> bool:
        return self.industry_mode != INDUSTRY_OFF

    def tilt_multipliers(self) -> dict[str, dict[str, float]] | None:
        """Extra registry tilts on top of v1.6; None when nothing changes."""
        tilts: dict[str, dict[str, float]] = {}
        if self.g_tilt is not None:
            for profile in sorted(DEFAULT_G_PROFILES):
                tilts[profile] = {"G": float(self.g_tilt)}
        if self.conservative_policy == CONSERVATIVE_V17:
            tilts["conservative"] = dict(CONSERVATIVE_V17_TILT_MULTIPLIERS)
        return tilts or None

    def tuning_policy(self) -> TuningPolicy:
        if self.conservative_policy == CONSERVATIVE_V17:
            return conservative_v17_policy(self.conservative_atr_multiplier)
        return DEFAULT_POLICY

    def scoring_options(self, industry: Mapping[str, IndustryTag] | None = None) -> ScoringOptions | None:
        """None means the v1.6 scoring path.

        An industry mode without a single tag (no table yet, provider down) falls
        back to scoring without industries rather than blocking publication; the
        worker records that in the batch manifest.
        """
        policy = self.tuning_policy()
        mode = self.industry_mode if industry else INDUSTRY_OFF
        if mode == INDUSTRY_OFF and policy is DEFAULT_POLICY:
            return None
        label = self.label() if mode == self.industry_mode else f"{self.label()} industry=unavailable"
        return ScoringOptions(industry_mode=mode, industry=dict(industry or {}), tuning=policy, label=label)

    def label(self) -> str:
        parts = ["v1.7"]
        if self.wants_industry:
            parts.append(f"industry={self.industry_mode}:sic{self.sic_level}")
        if self.g_tilt is not None:
            parts.append(f"g_tilt={self.g_tilt:g}")
        if self.conservative_policy == CONSERVATIVE_V17:
            parts.append(f"conservative=v1.7:atr{self.conservative_atr_multiplier:g}")
        if self.fund_scope != FUND_SCOPE_ALL:
            parts.append(f"funds={self.fund_scope}")
        return " ".join(parts) if len(parts) > 1 else "v1.6"

    def describe(self) -> dict[str, Any]:
        return {"label": self.label(), **{k: v for k, v in self.__dict__.items()}}


# The v1.6 production behaviour: the control every replay baseline (``v16``) scores with.
V16_CONFIG = LiveConfig()
# What production runs (v1.7 = S1 of the stage-1 replay, see the module docstring).
LIVE_CONFIG = LiveConfig(conservative_policy=CONSERVATIVE_V17, fund_scope=FUND_SCOPE_BENCHMARKS)

__all__ = [
    "CONSERVATIVE_V14",
    "CONSERVATIVE_V17",
    "CONSERVATIVE_V17_TILT_MULTIPLIERS",
    "LIVE_CONFIG",
    "LiveConfig",
    "V16_CONFIG",
]
