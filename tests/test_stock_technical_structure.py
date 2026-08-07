"""Technical-structure service for the stock detail page.

Synthetic daily bars are built with a known shape (a flat resistance shelf
touched several times, a rising floor, then a drift toward the shelf) so
each assertion targets a property the UI actually renders: the base band,
the invalidation line below support, dated swing markers that address real
bars, and indicator values inside their documented ranges.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.technical.base_structure import detect_base_structure
from app.services.technical.structure import (
    clean_series,
    compute_technical_structure,
    series_excluding_last,
)

_DAY = 24 * 60 * 60


def _bar(epoch: int, o: float, h: float, l: float, c: float, v: int) -> dict:
    return {"t": epoch, "o": o, "h": h, "l": l, "c": c, "v": v}


def _synthetic_bars(n: int = 220) -> list[dict]:
    """Uptrend into a 60-bar consolidation under a ~100 shelf."""

    start = int(datetime(2026, 1, 5, 21, 0, tzinfo=timezone.utc).timestamp())
    bars: list[dict] = []
    price = 60.0
    for i in range(n):
        epoch = start + i * _DAY
        if i < n - 60:
            # steady uptrend with mild oscillation
            price += 0.25 if i % 5 else -0.2
            o, c = price - 0.3, price
            h, l = price + 0.6, price - 0.8
            v = 900_000 + (i % 7) * 40_000
        else:
            # consolidation: highs press a flat shelf near 100, lows rise
            j = i - (n - 60)
            shelf = 100.0
            touch = j % 12 == 6  # periodic taps on the shelf
            base_low = 92.0 + j * 0.05
            c = base_low + 2.5 + (0.8 if touch else (j % 4) * 0.3)
            o = c - 0.4
            h = shelf + (0.15 if touch else -1.2 + (j % 3) * 0.2)
            l = base_low
            v = 700_000 - j * 4_000  # volume dries up into the base
            price = c
        bars.append(_bar(epoch, round(o, 2), round(h, 2), round(l, 2), round(c, 2), v))
    return bars


def test_clean_series_drops_malformed_bars_and_keeps_ny_dates() -> None:
    bars = _synthetic_bars(60)
    bars[10] = {"t": bars[10]["t"], "o": 10, "h": 9, "l": 11, "c": 10, "v": 1}  # high < low
    bars[11] = {"t": bars[11]["t"], "o": None, "h": None, "l": None, "c": None, "v": None}
    series = clean_series(bars)
    assert series is not None
    assert len(series["closes"]) == 58
    # Epoch 21:00 UTC = 16:00/17:00 New York — the date must be the NY session
    # date, not the UTC date and not the viewer's locale.
    first = datetime.fromtimestamp(bars[0]["t"], tz=timezone.utc) - timedelta(hours=5)
    assert series["dates"][0] == first.date().isoformat()
    # turnover is dollar volume
    assert series["turnover"][0] == series["closes"][0] * series["volumes"][0]


def test_clean_series_requires_minimum_bars() -> None:
    assert clean_series(_synthetic_bars(20)) is None


def test_detect_base_structure_finds_the_shelf() -> None:
    series = clean_series(_synthetic_bars())
    assert series is not None
    prior = series_excluding_last(series)
    assert prior is not None
    base = detect_base_structure(prior)
    assert base is not None
    # The shelf sits at 100; the band must bracket it tightly.
    assert 97.0 <= base["resistance_low"] <= 100.5
    assert 99.5 <= base["resistance_high"] <= 103.0
    assert base["resistance_touches"] >= 2
    # Invalidation sits strictly below the support floor.
    assert base["invalidation_price"] < base["support_low"]
    assert 0.0 <= base["quality"] <= 1.0
    # Point-in-time: the base window must end before the evaluation day.
    assert base["base_end"] <= prior["dates"][-1]


def test_compute_technical_structure_full_payload() -> None:
    bars = _synthetic_bars()
    result = compute_technical_structure(bars)
    assert result is not None

    pa = result["price_action"]
    assert pa["status"] == "active"
    assert pa["structure_label"]
    assert isinstance(pa["swing_highs"], list) and isinstance(pa["swing_lows"], list)
    assert pa["swing_highs"], "consolidation must yield confirmed swing highs"
    bar_times = {bar["t"] for bar in bars}
    for point in pa["swing_highs"] + pa["swing_lows"]:
        # Every marker addresses a real chart bar by identity, so the frontend
        # can match candles by t without date arithmetic.
        assert point["t"] in bar_times
        assert point["price"] > 0
        assert len(point["trade_date"]) == 10

    tech = result["technicals"]
    assert tech["rsi14"] is None or 0.0 <= tech["rsi14"] <= 100.0
    assert tech["range_position_60d"] is None or 0.0 <= tech["range_position_60d"] <= 1.0
    assert "direction_pct" in tech["macd"]

    vp = result["vol_price"]
    assert "setup_label" in vp and "false_breakout_risk" in vp

    overlays = result["chart_overlays"]
    assert overlays["swing_highs"] == pa["swing_highs"]
    if result["base"] is not None:
        assert overlays["resistance_high"] == result["base"]["resistance_high"]
        assert overlays["invalidation_price"] == result["base"]["invalidation_price"]

    assert result["basis" if "basis" in result else "version"]
    assert result["data_through"] == clean_series(bars)["dates"][-1]


def test_structure_returns_none_without_enough_bars() -> None:
    assert compute_technical_structure(_synthetic_bars(10)) is None
    assert compute_technical_structure([]) is None


def test_swing_values_match_price_action_module() -> None:
    """The dated swing list is derived with the same fractal parameters as the
    canonical price-action analysis; the last swing must equal its
    resistance/support values."""

    result = compute_technical_structure(_synthetic_bars())
    assert result is not None
    pa = result["price_action"]
    if pa["swing_highs"] and pa["resistance"] is not None:
        assert abs(pa["swing_highs"][-1]["price"] - pa["resistance"]) < 1e-6
    if pa["swing_lows"] and pa["support"] is not None:
        assert abs(pa["swing_lows"][-1]["price"] - pa["support"]) < 1e-6
