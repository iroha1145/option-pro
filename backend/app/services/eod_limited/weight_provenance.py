"""Describe and verify stored weights without applying the theme prior again."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Mapping

from app.services.research_eod_v1.capability import D_MARKET_RESIDUAL_DIAGNOSTIC, PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.registry_scoring import FACTORS, normalized

PRIOR_SOURCE = "base_times_prior_normalized_v1"
BASE_SOURCE = "base_only_v1"
WEIGHT_TOLERANCE = 1e-9  # Stored theme weights have ten decimal places.


def _source(registry: Mapping[str, Any], theme_id: str, family: str) -> tuple[str, float]:
    sector = registry["sectors"][theme_id]
    base = registry["base_algorithm_weights"][family]
    stored = sector["candidates"][family]["weights"]
    if theme_id == "all_market_stocks":
        source = BASE_SOURCE
        expected = dict(base)
    else:
        source = PRIOR_SOURCE
        prior = sector["factor_prior"]
        expected = normalized({factor: base[factor] * prior[factor] for factor in FACTORS})
    # Validate stored values as well as their correspondence to the source.
    normalized(stored)
    error = max(abs(stored[factor] - expected[factor]) for factor in FACTORS)
    if not math.isclose(sum(stored.values()), 1.0, abs_tol=WEIGHT_TOLERANCE, rel_tol=0):
        raise ValueError(f"{theme_id}/{family}: stored weights do not sum to one")
    if error > WEIGHT_TOLERANCE:
        raise ValueError(f"{theme_id}/{family}: stored weights disagree with {source}")
    return source, error


def validate_weight_sources(registry: Mapping[str, Any]) -> dict[str, Any]:
    """Check all 24 sealed themes, plus the optional live generic stock context."""
    sectors = registry["sectors"]
    themes = [theme for theme in sectors if theme != "all_market_stocks"]
    families = registry["base_algorithm_weights"]
    if len(themes) != 24 or len(families) != 4:
        raise ValueError("expected 24 sealed themes and four families")
    max_error = 0.0
    counts = {PRIOR_SOURCE: 0, BASE_SOURCE: 0}
    for theme_id, sector in sectors.items():
        if set(sector["candidates"]) != set(families):
            raise ValueError(f"{theme_id}: incomplete family coverage")
        for family in families:
            source, error = _source(registry, theme_id, family)
            counts[source] += 1
            max_error = max(max_error, error)
    return {"checked_by_source": counts, "maximum_absolute_error": max_error}


def weight_provenance(
    registry: Mapping[str, Any], *, theme_id: str, family: str,
    profile: str, horizon: str, track: str,
) -> dict[str, Any]:
    """One provenance record per family payload; rows carry only its identifier.

    The formula is verified numerically. This does not claim that a historical
    generation script was retained. The digest covers the supplied registry,
    including any explicitly added live context.
    """
    source, error = _source(registry, theme_id, family)
    registry_sha256 = hashlib.sha256(
        json.dumps(registry, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    record = {
        "source": source,
        "verification": "stored_weights_match_formula",
        "maximum_absolute_error": error,
        "registry_sha256": registry_sha256,
        "registry_schema_version": registry.get("schema_version"),
        "theme_id": theme_id,
        "family": family,
        "profile": profile,
        "horizon": horizon,
        "track": track,
        "disabled_factors": ["G"] if track in {PRICE_ONLY_DIAGNOSTIC, D_MARKET_RESIDUAL_DIAGNOSTIC} else [],
        "profile_factor_tilt": list(registry["profiles"][profile]["factor_tilt"]),
        "horizon_factor_tilt": list(registry["horizons"][horizon]["factor_tilt"]),
        "factor_order": list(FACTORS),
        "prior_application_count": 0 if source == BASE_SOURCE else 1,
    }
    record["id"] = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return record
