"""Shared number conversions with distinct missing-value contracts."""
from __future__ import annotations

import math
from typing import Any


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rounded_number(value: Any, ndigits: int = 4) -> float | None:
    # Provider metrics historically treat conversion/rounding failures as missing.
    # Keep that boundary separate from finite_number, which exposes other errors.
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def clamp_number(
    value: float | int | None,
    lo: float = 0.0,
    hi: float = 100.0,
    default: float | None = 50.0,
) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def positive_sum(values: list[Any]) -> float:
    total = 0.0
    for value in values:
        number = rounded_number(value, 4)
        if number is not None and number > 0:
            total += number
    return total


def positive_weighted_average(values: list[Any], weights: list[Any]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in zip(values, weights):
        number = rounded_number(value, 6)
        w = rounded_number(weight, 4) or 0.0
        if number is None or number <= 0 or w <= 0:
            continue
        numerator += number * w
        denominator += w
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)
