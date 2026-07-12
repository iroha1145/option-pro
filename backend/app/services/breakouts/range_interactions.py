"""Auditable Breakout Radar interactions for Range Persistence."""

from __future__ import annotations

import math
from typing import Any, Mapping


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _bounded(value: float, cap: float) -> float:
    return round(max(-cap, min(cap, value)), 6)


def range_persistence_interactions(
    feature: Mapping[str, Any],
    detection: Mapping[str, Any],
    *,
    cap: float = 4.0,
    ratio_threshold: float = 60.0,
) -> dict[str, Any]:
    """Return separate confirmation and chase adjustments.

    This background indicator never creates a breakout trigger. Confirmation
    requires a positive slope and a supported recent ratio. A crowded,
    extended move increases chase risk separately. Falling fast persistence
    near the top of the range is exposed as a warning.
    """

    limit = max(0.0, min(4.0, float(cap)))
    if feature.get("status") != "active" or limit == 0:
        return {
            "status": "inactive",
            "confirmation_adjustment": 0.0,
            "confirmation_reason": "interaction_disabled_or_feature_inactive",
            "chase_adjustment": 0.0,
            "chase_reason": "interaction_disabled_or_feature_inactive",
            "fading_near_high": False,
            "warnings": [],
        }

    slope = _finite(feature.get("range_persistence_slope_5d"))
    ratio = _finite(feature.get("range_persistence_ratio_10d"))
    self_percentile = _finite(
        feature.get("range_persistence_self_percentile")
    )
    position = _finite(feature.get("range_position"))
    fast = _finite(feature.get("range_persistence_fast"))
    slow = _finite(feature.get("range_persistence_slow"))
    distance = _finite(detection.get("breakout_distance_atr"))
    if distance is None:
        distance = _finite(detection.get("opening_range_distance_atr"))

    confirmation = 0.0
    confirmation_reason = "no_supported_confirmation_interaction"
    if slope is not None and slope < 0:
        confirmation = _bounded(slope * 0.25, limit)
        confirmation_reason = "negative_persistence_slope"
    elif (
        slope is not None
        and slope > 0
        and ratio is not None
        and ratio >= ratio_threshold
    ):
        ratio_support = min(
            1.0,
            (ratio - ratio_threshold) / max(100.0 - ratio_threshold, 1.0),
        )
        confirmation = _bounded(slope * 0.25 + ratio_support, limit)
        confirmation_reason = "rising_persistence_with_supported_ratio"

    chase = 0.0
    chase_reason = "extension_threshold_not_met"
    if (
        self_percentile is not None
        and self_percentile >= 90.0
        and distance is not None
        and distance >= 2.0
    ):
        chase = min(
            limit,
            1.0
            + (self_percentile - 90.0) * 0.10
            + (distance - 2.0) * 1.50,
        )
        chase_reason = "extreme_self_percentile_and_two_atr_extension"

    fading = bool(
        slope is not None
        and slope < 0
        and position is not None
        and position >= 80.0
        and fast is not None
        and slow is not None
        and fast < slow
    )
    return {
        "status": "active",
        "confirmation_adjustment": round(confirmation, 6),
        "confirmation_reason": confirmation_reason,
        "chase_adjustment": round(chase, 6),
        "chase_reason": chase_reason,
        "fading_near_high": fading,
        "warnings": ["range_persistence_fading_near_high"] if fading else [],
        "evidence": {
            "slope_5d": slope,
            "ratio_10d": ratio,
            "self_percentile": self_percentile,
            "range_position": position,
            "fast": fast,
            "slow": slow,
            "breakout_distance_atr": distance,
        },
    }
