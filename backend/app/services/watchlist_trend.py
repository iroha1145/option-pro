"""Bounded projection of existing raw daily bars; no provider access."""
from datetime import datetime
import math
from typing import Any
from zoneinfo import ZoneInfo


def daily_trend(payload: Any, *, market_timezone: ZoneInfo) -> dict | None:
    if not isinstance(payload, dict) or payload.get("range", "1d") != "1d":
        return None
    if payload.get("price_adjustment", payload.get("adjustment", "raw")) != "raw":
        return None
    bars = payload.get("bars")
    if not isinstance(bars, list) or len(bars) > 2_000:
        return None
    points: dict[str, float] = {}
    for bar in bars:
        if not isinstance(bar, dict):
            return None
        if bar.get("ext") is True or bar.get("quote_only") is True:
            continue
        close, stamp = bar.get("c"), bar.get("t")
        if isinstance(close, bool) or not isinstance(close, (float, int)):
            return None
        try:
            if not math.isfinite(close) or close <= 0:
                return None
            if isinstance(stamp, (int, float)) and not isinstance(stamp, bool):
                when = datetime.fromtimestamp(stamp / 1000 if stamp >= 100_000_000_000 else stamp, market_timezone)
            elif isinstance(stamp, str):
                when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
                if when.tzinfo is not None:
                    when = when.astimezone(market_timezone)
            else:
                return None
            day = when.date().isoformat()
        except (OverflowError, ValueError, OSError):
            return None
        # Ambiguous duplicate sessions are not silently joined into a curve.
        if day in points:
            return None
        points[day] = float(close)
    visible = sorted(points.items())[-30:]
    if len(visible) < 2:
        return None
    return {
        "interval": "1d",
        "adjustment": "raw",
        "points": [{"date": day, "close": close} for day, close in visible],
    }
