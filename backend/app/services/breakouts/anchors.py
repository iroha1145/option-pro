"""Stable opening-range levels, including read-time recovery of saved events.

The daily base is useful background, but never supplies an ORB trigger or stop.
Legacy event JSON already contains the opening high/low and the identity's high.
Recover only consistent, same-session evidence; missing evidence stays missing.
"""

from __future__ import annotations

from datetime import date, datetime
import math
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.services.breakouts.models import BreakoutEventAnchor


_NEW_YORK = ZoneInfo("America/New_York")


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _enum(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _session_date(value: Any) -> date | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return stamp.astimezone(_NEW_YORK).date() if stamp.tzinfo is not None else None


def opening_range_event(event: Mapping[str, Any]) -> bool:
    features = event.get("features") or {}
    origin = _enum(event.get("origin_setup_type") or features.get("origin_setup_type"))
    if origin and origin not in {"RETEST_BREAKOUT", "RECOVERY_BREAKOUT"}:
        return origin == "OPENING_RANGE_BREAKOUT"
    return _enum(event.get("setup_type")) == "OPENING_RANGE_BREAKOUT" or str(event.get("pivot_id") or "").startswith("orb-")


def resolve_event_anchor(event: Mapping[str, Any]) -> BreakoutEventAnchor | None:
    """Resolve one immutable ORB anchor without network or database writes."""

    if not opening_range_event(event):
        return None
    raw_date = event.get("trading_date")
    try:
        trading_date = raw_date if isinstance(raw_date, date) and not isinstance(raw_date, datetime) else date.fromisoformat(str(raw_date))
    except (TypeError, ValueError):
        trading_date = _session_date(event.get("first_seen_at") or event.get("event_at"))
    if trading_date is None:
        return None

    ticker = str(event.get("ticker") or "").strip().upper()
    identity_prefix = f"orb-{ticker}-{trading_date.isoformat()}-"
    pivot_id = str(event.get("pivot_id") or "")
    identity_matches = not pivot_id.startswith("orb-") or pivot_id.startswith(identity_prefix)
    identity_high = _positive(pivot_id[len(identity_prefix):]) if pivot_id.startswith(identity_prefix) else None
    saved = event.get("event_anchor")
    saved_anchor = None
    if saved is not None:
        try:
            anchor = saved if isinstance(saved, BreakoutEventAnchor) else BreakoutEventAnchor.model_validate(saved)
        except (TypeError, ValueError):
            anchor = None
        if anchor is not None and identity_matches and anchor.trading_date == trading_date and (
            identity_high is None or anchor.pivot_price is None
            or math.isclose(anchor.pivot_price, identity_high, rel_tol=0.0, abs_tol=0.000001)
        ):
            if anchor.status == "active":
                return anchor
            saved_anchor = anchor
            # A partial recovered identity can gain its missing low later,
            # but only from matching evidence for the original session.
            if anchor.pivot_price is not None:
                identity_high = anchor.pivot_price

    features = event.get("features") or {}
    feature_date = _session_date(
        features.get("feature_cutoff_at") or features.get("calculation_cutoff_at")
        or event.get("last_seen_at") or event.get("first_seen_at") or event.get("event_at")
    )
    feature_high = _positive(features.get("opening_range_high"))
    feature_low = _positive(features.get("opening_range_low"))
    consistent_range = bool(
        features.get("opening_range_complete") is True
        and identity_matches
        and feature_date == trading_date and feature_high is not None
        and (identity_high is None or math.isclose(feature_high, identity_high, rel_tol=0.0, abs_tol=0.000001))
    )
    if consistent_range:
        low = feature_low if feature_low is not None and feature_low <= feature_high else None
        return BreakoutEventAnchor(
            trading_date=trading_date, pivot_price=feature_high, invalidation_price=low,
            status="active" if low is not None else "partial", source="legacy_opening_range",
        )
    if saved_anchor is not None:
        return saved_anchor
    return BreakoutEventAnchor(
        trading_date=trading_date, pivot_price=identity_high, invalidation_price=None,
        status="partial" if identity_high is not None else "unavailable",
        source="legacy_pivot_id" if identity_high is not None else "unavailable",
    )


def anchor_levels(anchor: BreakoutEventAnchor) -> dict[str, Any]:
    """Existing public level fields retain their meaning for each event type."""

    pivot, low = anchor.pivot_price, anchor.invalidation_price
    return {
        "pivot_price": pivot,
        "resistance_price": pivot,
        "resistance_zone": {"low": pivot, "high": pivot} if pivot is not None else None,
        "support_zone": {"low": low, "high": low} if low is not None else None,
        "invalidation_price": low,
    }
