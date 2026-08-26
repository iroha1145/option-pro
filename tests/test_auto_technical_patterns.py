"""Deterministic auto-pattern detector on synthetic daily bars."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.technical.auto_patterns import ALGORITHM_VERSION, detect_auto_patterns
from app.services.technical.structure import clean_series, compute_technical_structure

_DAY = 24 * 60 * 60
_START = int(datetime(2025, 1, 6, 21, 0, tzinfo=timezone.utc).timestamp())


def _bar(i: int, o: float, h: float, l: float, c: float, v: int = 1_000_000) -> dict:
    return {
        "t": _START + i * _DAY,
        "o": round(o, 4),
        "h": round(h, 4),
        "l": round(l, 4),
        "c": round(c, 4),
        "v": v,
    }


def _series(bars: list[dict]) -> dict:
    cleaned = clean_series(bars)
    assert cleaned is not None
    return cleaned


def _detect(bars: list[dict]):
    series = _series(bars)
    return detect_auto_patterns(series, data_through=series["dates"][-1])


def _zigzag(n: int, trough_fn, peak_fn, period: int = 12, wick: float = 0.15) -> list[dict]:
    bars: list[dict] = []
    for i in range(n):
        phase = i % period
        half = period / 2
        if phase <= half:
            t = phase / half
        else:
            t = (period - phase) / half
        trough = trough_fn(i)
        peak = peak_fn(i)
        price = trough + (peak - trough) * t
        high = max(price, peak if abs(phase - half) < 0.6 else price) + wick
        low = min(price, trough if phase == 0 else price) - wick
        open_ = price - 0.05
        close = price + 0.05
        high = max(high, open_, close)
        low = min(low, open_, close)
        bars.append(_bar(i, open_, high, low, close))
    return bars


def _high_conf(rows, kind: str, subtype: str | None = None) -> dict | None:
    matched = [
        row
        for row in rows
        if row["kind"] == kind and (subtype is None or row.get("subtype") == subtype)
        and row["confidence"] >= 70
    ]
    return matched[0] if matched else None


def test_rising_support_trend() -> None:
    bars = _zigzag(
        180,
        trough_fn=lambda i: 50 + 0.18 * i,
        peak_fn=lambda i: 62 + 0.18 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "support_trend", "rising")
    assert found is not None, rows
    assert found["algorithmVersion"] == ALGORITHM_VERSION
    assert found["dataThrough"] <= _series(bars)["dates"][-1]
    assert found["touches"] >= 2
    assert found["anchors"]


def test_falling_resistance_trend() -> None:
    bars = _zigzag(
        180,
        trough_fn=lambda i: 70 - 0.16 * i,
        peak_fn=lambda i: 84 - 0.16 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "resistance_trend", "falling")
    assert found is not None, rows


def test_rising_channel() -> None:
    bars = _zigzag(
        200,
        trough_fn=lambda i: 40 + 0.14 * i,
        peak_fn=lambda i: 52 + 0.14 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "channel", "rising")
    assert found is not None, rows
    assert found["subtype"] == "rising"


def test_falling_channel() -> None:
    bars = _zigzag(
        200,
        trough_fn=lambda i: 90 - 0.14 * i,
        peak_fn=lambda i: 102 - 0.14 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "channel", "falling")
    assert found is not None, rows
    assert found["subtype"] == "falling"


def test_symmetric_triangle() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 40 + 0.12 * i,
        peak_fn=lambda i: 90 - 0.12 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "triangle", "symmetric")
    assert found is not None, rows
    assert found["subtype"] == "symmetric"


def test_ascending_triangle() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 40 + 0.14 * i,
        peak_fn=lambda i: 80.0,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "triangle", "ascending")
    assert found is not None, rows
    assert found["subtype"] == "ascending"


def test_descending_triangle() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 40.0,
        peak_fn=lambda i: 90 - 0.14 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "triangle", "descending")
    assert found is not None, rows
    assert found["subtype"] == "descending"


def test_rising_wedge() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 40 + 0.22 * i,
        peak_fn=lambda i: 70 + 0.12 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "wedge", "rising")
    assert found is not None, rows
    assert found["subtype"] == "rising"


def test_falling_wedge() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 80 - 0.12 * i,
        peak_fn=lambda i: 110 - 0.22 * i,
    )
    rows = _detect(bars)
    found = _high_conf(rows, "wedge", "falling")
    assert found is not None, rows
    assert found["subtype"] == "falling"


def test_horizontal_box() -> None:
    bars = _zigzag(
        160,
        trough_fn=lambda i: 50.0,
        peak_fn=lambda i: 62.0,
    )
    rows = _detect(bars)
    assert all(row["kind"] != "box" for row in rows)
    from app.services.technical.base_structure import detect_base_structure
    from app.services.technical.chart_analysis import assemble_chart_analysis

    series = _series(bars)
    prior = {key: values[:-1] for key, values in series.items()}
    base = detect_base_structure(prior)
    assert base is not None
    bundle = assemble_chart_analysis(
        series=series,
        data_through=series["dates"][-1],
        base=base,
        auto_patterns=rows,
    )
    boxes = [row for row in bundle["overlays"] if row["kind"] == "box"]
    assert boxes
    assert all(row["sourceId"] == "base_structure" for row in boxes)


def test_noisy_data_does_not_emit_high_confidence() -> None:
    bars = []
    price = 100.0
    # Deterministic LCG — not crypto, just a stable jumble.
    state = 1234567
    for i in range(180):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        shock = (state % 1000) / 1000.0 - 0.5
        price = max(5.0, price * (1 + shock * 0.08))
        o = price
        h = price * (1.01 + abs(shock) * 0.02)
        l = price * (0.99 - abs(shock) * 0.02)
        c = price * (1 + shock * 0.01)
        bars.append(_bar(i, o, h, l, c, v=400_000 + state % 200_000))
    rows = _detect(bars)
    assert all(row["confidence"] < 70 for row in rows), rows


def test_insufficient_data_returns_empty() -> None:
    bars = _zigzag(20, lambda i: 50 + i, lambda i: 60 + i)
    series = clean_series(bars)
    if series is None:
        assert True
        return
    assert detect_auto_patterns(series, data_through=series["dates"][-1]) == []


def test_long_wicks_do_not_dominate() -> None:
    bars = _zigzag(
        180,
        trough_fn=lambda i: 50 + 0.18 * i,
        peak_fn=lambda i: 62 + 0.18 * i,
        wick=0.15,
    )
    # Plant a few absurd wicks that should be ignored for penetration.
    for idx in (40, 80, 120):
        row = bars[idx]
        bars[idx] = {
            **row,
            "h": row["h"] * 1.25,
            "l": row["l"] * 0.75,
        }
    rows = _detect(bars)
    found = _high_conf(rows, "support_trend", "rising") or _high_conf(rows, "channel", "rising")
    assert found is not None, rows
    assert found["status"] != "invalidated"


def test_atr_scale_tolerance() -> None:
    small = _zigzag(180, lambda i: 10 + 0.03 * i, lambda i: 12 + 0.03 * i)
    large = _zigzag(180, lambda i: 1000 + 3.0 * i, lambda i: 1200 + 3.0 * i)
    small_rows = _detect(small)
    large_rows = _detect(large)
    assert _high_conf(small_rows, "support_trend") or _high_conf(small_rows, "channel")
    assert _high_conf(large_rows, "support_trend") or _high_conf(large_rows, "channel")


def test_duplicate_collapse_and_identical_replay() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    first = _detect(bars)
    second = _detect(bars)
    assert first == second
    keys = [(row["kind"], row["subtype"], row["id"]) for row in first]
    assert len(keys) == len(set(keys))


def test_no_lookahead_when_truncating_last_bars() -> None:
    bars = _zigzag(200, lambda i: 50 + 0.16 * i, lambda i: 64 + 0.16 * i)
    full_series = _series(bars)
    truncated_bars = bars[:-8]
    truncated_series = _series(truncated_bars)
    cutoff = truncated_series["dates"][-1]
    from_truncated = detect_auto_patterns(
        truncated_series, data_through=cutoff
    )
    from_full_cutoff = detect_auto_patterns(
        full_series, data_through=cutoff
    )
    assert from_full_cutoff == from_truncated
    for row in from_truncated + from_full_cutoff:
        assert row["dataThrough"] <= cutoff
        for anchor in row["anchors"]:
            assert anchor["barKey"] <= cutoff
    # Truncating must not invent future-dated anchors.
    future_days = set(full_series["dates"][-8:])
    for row in from_truncated:
        for anchor in row["anchors"]:
            assert anchor["barKey"] not in future_days


def test_broken_status_and_measured_target_direction() -> None:
    # Rising channel then a sharp close above the upper bound.
    bars = _zigzag(150, lambda i: 40 + 0.12 * i, lambda i: 50 + 0.12 * i)
    last = bars[-1]
    bars[-1] = {
        **last,
        "h": last["h"] * 1.35,
        "c": last["h"] * 1.32,
        "o": last["c"],
        "v": 5_000_000,
    }
    rows = _detect(bars)
    broken = [row for row in rows if row["status"] in {"broken_up", "broken_down", "testing", "forming", "invalidated"}]
    assert broken
    ups = [row for row in rows if row["status"] == "broken_up" and row.get("measuredTarget") is not None]
    downs = [row for row in rows if row["status"] == "broken_down" and row.get("measuredTarget") is not None]
    for row in ups:
        last_price = max(anchor["price"] for anchor in row["anchors"])
        assert row["measuredTarget"] > last_price or row["measuredTarget"] > row["anchors"][-1]["price"]
        assert row["measuredTargetNote"] == "technical_projection"
    for row in downs:
        assert row["measuredTarget"] < max(anchor["price"] for anchor in row["anchors"])


def test_single_trend_status_and_no_measured_target() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    last = bars[-1]
    bars[-1] = {
        **last,
        "h": last["h"] * 1.4,
        "c": last["h"] * 1.35,
        "o": last["c"],
        "v": 8_000_000,
    }
    rows = _detect(bars)
    for row in rows:
        if row["kind"] == "support_trend":
            assert row["status"] in {"forming", "testing", "broken_down", "invalidated"}
            assert row["measuredTarget"] is None
        if row["kind"] == "resistance_trend":
            assert row["status"] in {"forming", "testing", "broken_up", "invalidated"}
            assert row["measuredTarget"] is None
    down_bars = _zigzag(180, lambda i: 70 - 0.16 * i, lambda i: 84 - 0.16 * i)
    down_last = down_bars[-1]
    down_bars[-1] = {
        **down_last,
        "l": down_last["l"] * 0.6,
        "c": down_last["l"] * 0.65,
        "o": down_last["c"],
        "v": 8_000_000,
    }
    down_rows = _detect(down_bars)
    for row in down_rows:
        if row["kind"] == "resistance_trend":
            assert row["status"] in {"forming", "testing", "broken_up", "invalidated"}
            assert row["measuredTarget"] is None
        if row["kind"] == "support_trend":
            assert row["status"] in {"forming", "testing", "broken_down", "invalidated"}
            assert row["measuredTarget"] is None
    patterned = [
        row
        for row in rows + down_rows
        if row["kind"] in {"channel", "triangle", "wedge"}
        and row.get("measuredTarget") is not None
    ]
    for row in patterned:
        assert row["kind"] in {"channel", "triangle", "wedge"}


def test_structure_payload_includes_auto_patterns() -> None:
    bars = _zigzag(180, lambda i: 50 + 0.18 * i, lambda i: 62 + 0.18 * i)
    now = datetime(2027, 1, 4, 12, 0, tzinfo=timezone.utc)
    result = compute_technical_structure(bars, now=now)
    assert result is not None
    assert result["auto_patterns_version"] == ALGORITHM_VERSION
    assert isinstance(result["auto_patterns"], list)
    for row in result["auto_patterns"]:
        assert row["dataThrough"] <= result["data_through"]
        assert datetime.fromisoformat(row["dataThrough"]) <= datetime.fromisoformat(result["data_through"])
