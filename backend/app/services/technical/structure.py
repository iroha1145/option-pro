"""Chart-ready technical structure for the stock detail page.

Assembles four existing analyses over the same raw daily bars the chart
endpoint serves, so every overlay lands exactly on the candles the user sees:

- price action (strength/price_action.compute_price_action — canonical)
- effort-vs-result volume/price match (strength/vol_price_match)
- completed-base detection (technical/base_structure — pivot clustering)
- classic indicators (technical/indicators)

Swing points are re-derived here with the same fractal parameters as the
price-action module (span=3, lookback=120) purely to attach bar timestamps
for chart markers; values are bit-identical to the analysis by construction.

Input bars are the chart contract shape: {t: epoch-seconds, o, h, l, c, v}.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.strength.price_action import (
    _find_swings,
    compute_price_action,
)
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.technical.base_structure import detect_base_structure
from app.services.technical.indicators import compute_technicals

STRUCTURE_VERSION = "us-structure-v1"

_NEW_YORK_TZ = ZoneInfo("America/New_York")
_SWING_SPAN = 3
_SWING_LOOKBACK = 120
_MIN_BARS = 30


def clean_series(bars: Sequence[Mapping[str, Any]]) -> dict[str, list] | None:
    """Chart bars → parallel columns. Malformed bars are dropped, not repaired.

    ``turnover`` is dollar volume (close × volume) — share counts are not
    comparable across price levels, same discipline as vol_price_match.
    """

    times: list[int] = []
    dates: list[str] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    turnover: list[float | None] = []
    for bar in bars:
        try:
            t = int(bar["t"])
            close = float(bar["c"])
            open_ = float(bar.get("o") or close)
            high = float(bar.get("h") or close)
            low = float(bar.get("l") or close)
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0 or high < low or close > high * 1.0001 or close < low * 0.9999:
            continue
        try:
            volume = max(0.0, float(bar.get("v") or 0))
        except (TypeError, ValueError):
            volume = 0.0
        times.append(t)
        # Daily bars stamp the New York session; render dates in that zone so
        # a UTC+8 viewer's overlay matches the axis label, not the next day.
        dates.append(
            datetime.fromtimestamp(t, tz=timezone.utc).astimezone(_NEW_YORK_TZ).date().isoformat()
        )
        opens.append(open_)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        volumes.append(volume)
        turnover.append(close * volume if volume > 0 else None)
    if len(closes) < _MIN_BARS:
        return None
    return {
        "times": times,
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
        "turnover": turnover,
    }


def series_excluding_last(series: dict[str, list]) -> dict[str, list] | None:
    if len(series["closes"]) < 2:
        return None
    return {key: values[:-1] for key, values in series.items()}


def _frame(series: dict[str, list]) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime.fromtimestamp(t, tz=timezone.utc) for t in series["times"]]
    )
    return pd.DataFrame(
        {
            "Open": series["opens"],
            "High": series["highs"],
            "Low": series["lows"],
            "Close": series["closes"],
            "Volume": series["volumes"],
        },
        index=index,
    )


def _dated_swings(series: dict[str, list]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Confirmed fractal swings over the price-action window, with the exact
    bar `t` so the frontend can address the matching candle by identity."""

    highs = series["highs"][-_SWING_LOOKBACK:]
    lows = series["lows"][-_SWING_LOOKBACK:]
    times = series["times"][-_SWING_LOOKBACK:]
    dates = series["dates"][-_SWING_LOOKBACK:]
    swing_highs, swing_lows = _find_swings(highs, lows, _SWING_SPAN)

    def pack(points: list[tuple[int, float]]) -> list[dict[str, Any]]:
        return [
            {"t": times[index], "trade_date": dates[index], "price": round(price, 4)}
            for index, price in points[-4:]
        ]

    return pack(swing_highs), pack(swing_lows)


def compute_technical_structure(bars: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """Full detail-page structure payload, or ``None`` when bars are unusable."""

    series = clean_series(bars)
    if series is None:
        return None
    frame = _frame(series)

    price_action = compute_price_action(frame, swing_span=_SWING_SPAN, lookback=_SWING_LOOKBACK)
    swing_highs, swing_lows = _dated_swings(series)
    price_action = {**price_action, "swing_highs": swing_highs, "swing_lows": swing_lows}

    vol_price = compute_vol_price_match(frame)

    prior = series_excluding_last(series)
    base = detect_base_structure(prior) if prior else None

    technicals = compute_technicals(series)

    overlays: dict[str, Any] = {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
    }
    if base:
        overlays["resistance_high"] = base.get("resistance_high")
        overlays["resistance_low"] = base.get("resistance_low")
        overlays["support_low"] = base.get("support_low")
        overlays["invalidation_price"] = base.get("invalidation_price")
        overlays["pivot_price"] = base.get("pivot_price")
        overlays["base_start"] = base.get("base_start")
        overlays["base_end"] = base.get("base_end")

    return {
        "version": STRUCTURE_VERSION,
        "base": base,
        "price_action": price_action,
        "vol_price": vol_price,
        "technicals": technicals,
        "chart_overlays": overlays,
        "bar_count": len(series["closes"]),
        "data_through": series["dates"][-1],
    }


__all__ = ["STRUCTURE_VERSION", "clean_series", "compute_technical_structure", "series_excluding_last"]
