"""Shared option-quote checks used by IV, expected-move, and unusual-activity.

This module is intentionally small and has no service-layer imports so Yahoo
and earnings enrichment can share the same bid/ask policy without coupling.
"""

from __future__ import annotations

import math
from typing import Any

# Bid/ask mid is rejected when the spread is wider than this share of mid,
# or this absolute floor — same policy already used for earnings straddles.
MAX_SPREAD_RATIO = 0.5
MIN_ABS_SPREAD = 0.05

# Vendor IV at or below this is treated as "no real quote" (yfinance empty),
# not as a low-volatility stock. This is not a 10% realism floor.
VENDOR_IV_MIN_QUOTE = 0.005

# Accepted band for model-inverted IV. Values at the endpoints are failures.
INVERTED_IV_MIN = 0.01
INVERTED_IV_MAX = 3.0

STANDARD_CONTRACT_MULTIPLIER = 100


def finite_number(
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if minimum is not None and number < minimum:
        return None
    if maximum is not None and number > maximum:
        return None
    return number


def vendor_iv(value: Any) -> float | None:
    """Finite positive vendor IV that is not an empty-quote placeholder."""

    iv = finite_number(value)
    if iv is None or iv <= VENDOR_IV_MIN_QUOTE:
        return None
    return iv


def inverted_iv_acceptable(iv: float | None) -> bool:
    return iv is not None and INVERTED_IV_MIN < iv < INVERTED_IV_MAX


def quality_mid(
    bid: Any,
    ask: Any,
    *,
    max_spread_ratio: float = MAX_SPREAD_RATIO,
    min_abs_spread: float = MIN_ABS_SPREAD,
) -> tuple[float | None, str | None]:
    """Derive a tradable mid from bid/ask. Never trusts a `mid` field.

    Returns ``(mid, None)`` on success or ``(None, reason)`` on rejection.
    A zero bid is kept as a raw quote elsewhere; it is not a quality mid.
    """

    bid_px = finite_number(bid)
    ask_px = finite_number(ask)
    if bid_px is None or ask_px is None:
        return None, "missing_bid_ask"
    if bid_px <= 0 or ask_px <= 0:
        return None, "non_positive_quote"
    if ask_px < bid_px:
        return None, "inverted_quote"
    mid = (bid_px + ask_px) / 2.0
    if mid <= 0 or not math.isfinite(mid):
        return None, "invalid_mid"
    spread = ask_px - bid_px
    if spread > max(max_spread_ratio * mid, min_abs_spread):
        return None, "wide_spread"
    return mid, None


def option_mark(
    *,
    bid: Any = None,
    ask: Any = None,
    last: Any = None,
    allow_last_for_estimate: bool = False,
) -> dict[str, Any]:
    """Choose a mark for inversion or notional-premium estimates.

    Quality mid is preferred. Last trade is only used when explicitly allowed
    for an *estimate*, and is never labeled as a current two-sided quote.
    """

    mid, reason = quality_mid(bid, ask)
    if mid is not None:
        return {
            "value": mid,
            "basis": "quality_mid",
            "usable_for_inversion": True,
            "reason": None,
        }
    last_px = finite_number(last, minimum=0.0)
    if allow_last_for_estimate and last_px is not None and last_px > 0:
        return {
            "value": last_px,
            "basis": "last_price",
            "usable_for_inversion": False,
            "reason": reason or "last_price_estimate_only",
        }
    return {
        "value": None,
        "basis": None,
        "usable_for_inversion": False,
        "reason": reason or "no_mark",
    }


def estimated_premium(
    mark: float | None,
    volume: Any,
    *,
    multiplier: int = STANDARD_CONTRACT_MULTIPLIER,
) -> float | None:
    """``mark × volume × multiplier`` — estimated notional, not cash flow."""

    vol = finite_number(volume, minimum=0.0)
    if mark is None or vol is None or vol <= 0 or mark <= 0:
        return None
    premium = mark * vol * multiplier
    if not math.isfinite(premium) or premium < 0:
        return None
    return premium
