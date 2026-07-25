"""Optix Macro Conditions — Optix 宏观环境.

A deterministic, auditable macro and cross-asset environment module. Scores are
rolling five-year historical percentiles of public data; they are not forecasts,
not probabilities, and not trade advice. v1 is display and research only: no
value produced here enters the official stock ranking.
"""

from __future__ import annotations

from .models import ERROR_CODES, MacroError, MacroStatus
from .registry import SCORING_VERSION


__all__ = ["ERROR_CODES", "SCORING_VERSION", "MacroError", "MacroStatus"]
