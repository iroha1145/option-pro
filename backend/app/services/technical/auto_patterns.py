"""Deterministic auto trend/pattern detection on daily bars.

No SciPy, no ML, no look-ahead: every candidate is enumerated from confirmed
fractal swings on the closed series the caller already truncated. Output is
stable for identical input (fixed algorithm version, no RNG).
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

from app.services.strength.price_action import _atr, _find_swings

ALGORITHM_VERSION = "optix-auto-patterns-v1"
_LOOKBACK = 252
_SWING_SPAN = 3
_MIN_BARS = 40
_MIN_SPAN = 20
_TOUCH_ATR = 0.35
_PENETRATE_ATR = 0.55
_KEEP_CONFIDENCE = 55.0
_MAX_RESULTS = 12


def _iso_from_epoch(times: Sequence[int], dates: Sequence[str], index: int) -> str:
    # Daily barKey is the New York session date; time is that date at 00:00Z.
    day = dates[index]
    return f"{day}T00:00:00+00:00"


def _anchor(times: Sequence[int], dates: Sequence[str], index: int, price: float) -> dict[str, Any]:
    day = dates[index]
    return {
        "time": _iso_from_epoch(times, dates, index),
        "barKey": day,
        "price": round(float(price), 4),
        "index": int(index),
    }


def _pattern_id(kind: str, subtype: str, anchors: Sequence[Mapping[str, Any]]) -> str:
    first = anchors[0]
    last = anchors[-1]
    raw = (
        f"{ALGORITHM_VERSION}|{kind}|{subtype}|{first.get('barKey')}|"
        f"{last.get('barKey')}|{first.get('price'):.4f}|{last.get('price'):.4f}"
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _line(p1: tuple[int, float], p2: tuple[int, float]) -> tuple[float, float] | None:
    x1, y1 = p1
    x2, y2 = p2
    if x2 == x1:
        return None
    slope = (y2 - y1) / (x2 - x1)
    intercept = y1 - slope * x1
    if not math.isfinite(slope) or not math.isfinite(intercept):
        return None
    return slope, intercept


def _y(slope: float, intercept: float, x: float) -> float:
    return slope * x + intercept


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def _evaluate_line(
    *,
    slope: float,
    intercept: float,
    points: Sequence[tuple[int, float]],
    closes: Sequence[float],
    volumes: Sequence[float],
    atr: float,
    side: str,
    start: int,
    end: int,
) -> dict[str, Any] | None:
    span = end - start
    if span < _MIN_SPAN:
        return None
    residual_sum = 0.0
    residual_n = 0
    touches = 0
    last_touch = start
    penetrations = 0
    touch_indexes: list[int] = []
    for index, price in points:
        if index < start - 1 or index > end + 1:
            continue
        expected = _y(slope, intercept, index)
        dist = (price - expected) / atr
        residual_sum += abs(dist)
        residual_n += 1
        if abs(dist) <= _TOUCH_ATR:
            touches += 1
            last_touch = max(last_touch, index)
            touch_indexes.append(index)
        elif side == "support" and dist < -_PENETRATE_ATR:
            penetrations += 1
        elif side == "resistance" and dist > _PENETRATE_ATR:
            penetrations += 1
    if residual_n == 0:
        return None
    residual = residual_sum / residual_n
    # Body-based penetration over the formation (long wicks do not dominate).
    body_hits = 0
    for i in range(start, min(end + 1, len(closes))):
        expected = _y(slope, intercept, i)
        close = closes[i]
        if side == "support" and close < expected - _PENETRATE_ATR * atr:
            body_hits += 1
        elif side == "resistance" and close > expected + _PENETRATE_ATR * atr:
            body_hits += 1
    penetrations = max(penetrations, body_hits)
    if touches < 2:
        return None
    if touches == 2 and (span < 40 or residual > 0.18):
        return None
    if touches >= 3 and residual > 0.55:
        return None
    if penetrations > max(2, span * 0.08):
        return None
    last_bars_ago = (len(closes) - 1) - last_touch
    score = 0.0
    score += min(30.0, touches * 8.0)
    score += min(15.0, span / 180.0 * 15.0)
    score += max(0.0, 20.0 - residual * 40.0)
    score -= penetrations * 10.0
    if last_bars_ago <= 8:
        score += 10.0
    elif last_bars_ago <= 20:
        score += 5.0
    height = abs(_y(slope, intercept, end) - _y(slope, intercept, start))
    height_atr = height / atr if atr else 0.0
    if 1.2 <= height_atr <= 18.0 or (abs(slope) * span < 0.45 * atr):
        score += 8.0
    last_close = closes[-1]
    last_expected = _y(slope, intercept, len(closes) - 1)
    status = "forming"
    breakout_price = None
    invalidation_price = None
    measured_target = None
    direction = "neutral"
    vol_med = _median(volumes[max(0, len(volumes) - 20) :]) or 1.0
    last_vol = volumes[-1] if volumes else 0.0
    if side == "support":
        invalidation_price = last_expected - atr
        direction = "bullish" if slope > 0 else "bearish"
        if last_close < last_expected - 0.5 * atr:
            status = "broken_down"
        elif abs(last_close - last_expected) <= 0.3 * atr:
            status = "testing"
        if last_close > last_expected + 1.2 * atr and last_vol > 1.15 * vol_med:
            status = "broken_up"
            breakout_price = last_close
            measured_target = last_close + height
            score += 5.0
    else:
        invalidation_price = last_expected + atr
        direction = "bearish" if slope < 0 else "bullish"
        if last_close > last_expected + 0.5 * atr:
            status = "broken_up"
        elif abs(last_close - last_expected) <= 0.3 * atr:
            status = "testing"
        if last_close < last_expected - 1.2 * atr and last_vol > 1.15 * vol_med:
            status = "broken_down"
            breakout_price = last_close
            measured_target = last_close - height
            score += 5.0
    if penetrations >= 3 and status == "forming":
        status = "invalidated"
        score -= 12.0
    confidence = max(0.0, min(100.0, score))
    if confidence < _KEEP_CONFIDENCE:
        return None
    return {
        "touches": touches,
        "residual": residual,
        "penetrations": penetrations,
        "span": span,
        "last_touch": last_touch,
        "touch_indexes": touch_indexes,
        "confidence": confidence,
        "status": status,
        "direction": direction,
        "breakout_price": breakout_price,
        "invalidation_price": invalidation_price,
        "measured_target": measured_target,
        "height": height,
        "slope": slope,
        "intercept": intercept,
        "start": start,
        "end": end,
    }


def _collapse(candidates: list[dict[str, Any]], atr: float) -> list[dict[str, Any]]:
    kept: list[dict[str, Any]] = []
    for item in sorted(candidates, key=lambda row: (-row["confidence"], row["kind"], row["subtype"])):
        duplicate = False
        for other in kept:
            if other["kind"] != item["kind"]:
                continue
            mid = (item["start"] + item["end"]) / 2
            y_a = _y(item["slope"], item["intercept"], mid) if "slope" in item else None
            y_b = _y(other["slope"], other["intercept"], mid) if "slope" in other else None
            if y_a is None or y_b is None:
                if other.get("formationStart") == item.get("formationStart") and other.get("subtype") == item.get("subtype"):
                    duplicate = True
                    break
                continue
            if abs(item["slope"] - other["slope"]) * max(item["span"], 1) < 0.35 * atr and abs(y_a - y_b) < 0.4 * atr:
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
        if len(kept) >= _MAX_RESULTS:
            break
    return kept


def _pack(
    *,
    kind: str,
    subtype: str,
    eval_row: dict[str, Any],
    anchors: list[dict[str, Any]],
    data_through: str,
    extra_rationale: Sequence[str] = (),
) -> dict[str, Any]:
    public_anchors = [
        {"time": item["time"], "barKey": item["barKey"], "price": item["price"]}
        for item in anchors
    ]
    rationale = [
        "touches",
        "atr_residual",
        "no_lookahead",
        *extra_rationale,
    ]
    payload = {
        "id": _pattern_id(kind, subtype, public_anchors),
        "algorithmVersion": ALGORITHM_VERSION,
        "kind": kind,
        "subtype": subtype,
        "direction": eval_row["direction"],
        "anchors": public_anchors,
        "confidence": round(eval_row["confidence"], 1),
        "touches": int(eval_row["touches"]),
        "formationStart": min(item["barKey"] for item in public_anchors),
        "formationEnd": max(item["barKey"] for item in public_anchors),
        "dataThrough": data_through,
        "status": eval_row["status"],
        "breakoutPrice": (
            round(eval_row["breakout_price"], 4) if eval_row["breakout_price"] else None
        ),
        "invalidationPrice": (
            round(eval_row["invalidation_price"], 4) if eval_row["invalidation_price"] else None
        ),
        "measuredTarget": (
            round(eval_row["measured_target"], 4) if eval_row["measured_target"] else None
        ),
        "measuredTargetNote": "technical_projection",
        "rationaleCodes": rationale,
        "slope": eval_row.get("slope"),
        "intercept": eval_row.get("intercept"),
        "start": eval_row.get("start"),
        "end": eval_row.get("end"),
        "span": eval_row.get("span"),
    }
    return payload


def detect_auto_patterns(
    series: Mapping[str, list],
    *,
    data_through: str,
    lookback: int = _LOOKBACK,
) -> list[dict[str, Any]]:
    """Return quality-gated patterns. Empty when data are insufficient."""

    highs: list[float] = list(series.get("highs") or [])
    lows: list[float] = list(series.get("lows") or [])
    closes: list[float] = list(series.get("closes") or [])
    volumes: list[float] = list(series.get("volumes") or [])
    times: list[int] = list(series.get("times") or [])
    dates: list[str] = list(series.get("dates") or [])
    n = len(closes)
    if n < _MIN_BARS or not (len(highs) == len(lows) == len(times) == len(dates) == n):
        return []
    start = max(0, n - lookback)
    window_highs = highs[start:]
    window_lows = lows[start:]
    window_closes = closes[start:]
    window_volumes = volumes[start:] if len(volumes) == n else [0.0] * (n - start)
    window_times = times[start:]
    window_dates = dates[start:]
    offset = start
    atr = _atr(window_highs, window_lows, window_closes, window=14)
    if atr is None or atr <= 0:
        return []
    swing_highs_raw, swing_lows_raw = _find_swings(window_highs, window_lows, _SWING_SPAN)
    swing_highs = [(i, p) for i, p in swing_highs_raw]
    swing_lows = [(i, p) for i, p in swing_lows_raw]
    if len(swing_highs) < 2 and len(swing_lows) < 2:
        return []

    cutoff = window_dates[-1]
    if data_through < cutoff:
        # Caller asked to evaluate only through data_through: drop later bars.
        keep = [i for i, day in enumerate(window_dates) if day <= data_through]
        if len(keep) < _MIN_BARS:
            return []
        last = keep[-1] + 1
        window_highs = window_highs[:last]
        window_lows = window_lows[:last]
        window_closes = window_closes[:last]
        window_volumes = window_volumes[:last]
        window_times = window_times[:last]
        window_dates = window_dates[:last]
        swing_highs = [(i, p) for i, p in swing_highs if i < last]
        swing_lows = [(i, p) for i, p in swing_lows if i < last]
        atr = _atr(window_highs, window_lows, window_closes, window=14) or atr
        cutoff = window_dates[-1]
    data_through = cutoff

    candidates: list[dict[str, Any]] = []

    def consider_trend(points: list[tuple[int, float]], side: str, want_sign: int) -> None:
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                fitted = _line(points[i], points[j])
                if fitted is None:
                    continue
                slope, intercept = fitted
                if want_sign > 0 and slope <= 1e-6:
                    continue
                if want_sign < 0 and slope >= -1e-6:
                    continue
                start_i = points[i][0]
                end_i = points[j][0]
                evaluated = _evaluate_line(
                    slope=slope,
                    intercept=intercept,
                    points=points,
                    closes=window_closes,
                    volumes=window_volumes,
                    atr=atr,
                    side=side,
                    start=start_i,
                    end=end_i,
                )
                if evaluated is None:
                    continue
                kind = "support_trend" if side == "support" else "resistance_trend"
                subtype = "rising" if slope > 0 else "falling"
                a0 = _anchor(window_times, window_dates, points[i][0], points[i][1])
                a1 = _anchor(window_times, window_dates, points[j][0], points[j][1])
                packed = _pack(
                    kind=kind,
                    subtype=subtype,
                    eval_row=evaluated,
                    anchors=[a0, a1],
                    data_through=data_through,
                    extra_rationale=("swing_line",),
                )
                candidates.append(packed)

    consider_trend(swing_lows, "support", +1)
    consider_trend(swing_highs, "resistance", -1)

    # Pair support/resistance lines into channels, triangles, wedges, boxes.
    support_lines = [row for row in candidates if row["kind"] == "support_trend"]
    resist_lines = [row for row in candidates if row["kind"] == "resistance_trend"]
    # Also fit falling support / rising resistance for wedges/triangles even if
    # they did not pass the single-side sign filter: re-enumerate without sign.
    extra_supports: list[dict[str, Any]] = []
    extra_resists: list[dict[str, Any]] = []

    def fit_all(points: list[tuple[int, float]], side: str) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        for i in range(len(points)):
            for j in range(i + 1, len(points)):
                fitted = _line(points[i], points[j])
                if fitted is None:
                    continue
                slope, intercept = fitted
                start_i, end_i = points[i][0], points[j][0]
                evaluated = _evaluate_line(
                    slope=slope,
                    intercept=intercept,
                    points=points,
                    closes=window_closes,
                    volumes=window_volumes,
                    atr=atr,
                    side=side,
                    start=start_i,
                    end=end_i,
                )
                if evaluated is None:
                    continue
                found.append(
                    {
                        **evaluated,
                        "p0": points[i],
                        "p1": points[j],
                    }
                )
        return found

    extra_supports = fit_all(swing_lows, "support")
    extra_resists = fit_all(swing_highs, "resistance")

    for support in extra_supports:
        for resist in extra_resists:
            overlap_start = max(support["start"], resist["start"])
            overlap_end = min(support["end"], resist["end"])
            span = overlap_end - overlap_start
            if span < _MIN_SPAN:
                continue
            s_lo, s_hi = support["slope"], resist["slope"]
            parallel = abs(s_lo - s_hi) * span <= 0.45 * atr
            same_sign = s_lo * s_hi > 0
            converging = (s_hi - s_lo) < -1e-6
            y_lo0 = _y(support["slope"], support["intercept"], overlap_start)
            y_hi0 = _y(resist["slope"], resist["intercept"], overlap_start)
            y_lo1 = _y(support["slope"], support["intercept"], overlap_end)
            y_hi1 = _y(resist["slope"], resist["intercept"], overlap_end)
            if y_hi0 <= y_lo0 or y_hi1 <= y_lo1:
                continue
            height0 = y_hi0 - y_lo0
            height1 = y_hi1 - y_lo1
            if height0 <= 0 or height1 <= 0:
                continue
            lo_flat = abs(s_lo) * span < 0.35 * atr
            hi_flat = abs(s_hi) * span < 0.35 * atr
            both_flat = lo_flat and hi_flat
            kind = None
            subtype = None
            direction = "neutral"
            extra = []
            if both_flat:
                kind, subtype = "box", "horizontal"
                extra.append("near_zero_slope")
            elif parallel and not both_flat:
                kind, subtype = "channel", "rising" if (s_lo + s_hi) / 2 > 0 else "falling"
                direction = "bullish" if subtype == "rising" else "bearish"
                extra.append("parallel")
            elif hi_flat and s_lo > 1e-6:
                kind, subtype = "triangle", "ascending"
                direction = "bullish"
                extra.append("convergence")
            elif lo_flat and s_hi < -1e-6:
                kind, subtype = "triangle", "descending"
                direction = "bearish"
                extra.append("convergence")
            elif converging and not same_sign:
                kind, subtype = "triangle", "symmetric"
                extra.append("convergence")
            elif same_sign and converging:
                kind, subtype = "wedge", "rising" if s_lo > 0 else "falling"
                direction = "bullish" if kind == "wedge" and subtype == "falling" else (
                    "bearish" if subtype == "rising" else "neutral"
                )
                extra.append("same_direction_convergence")
            else:
                continue
            # Apex of converging patterns must not be absurd.
            if kind in {"triangle", "wedge"} and abs(s_hi - s_lo) > 1e-9:
                apex = (support["intercept"] - resist["intercept"]) / (s_hi - s_lo)
                if apex < overlap_start - span or apex > overlap_end + 4 * span:
                    continue
            touches = support["touches"] + resist["touches"]
            if support["touches"] < 2 or resist["touches"] < 2:
                continue
            residual = (support["residual"] + resist["residual"]) / 2
            confidence = min(
                100.0,
                0.5 * (support["confidence"] + resist["confidence"])
                + (8.0 if parallel or both_flat else 0.0)
                + (6.0 if kind in {"triangle", "wedge"} else 0.0),
            )
            last_close = window_closes[-1]
            last_lo = _y(support["slope"], support["intercept"], len(window_closes) - 1)
            last_hi = _y(resist["slope"], resist["intercept"], len(window_closes) - 1)
            status = "forming"
            breakout_price = None
            measured_target = None
            height = (height0 + height1) / 2
            if last_close > last_hi + 0.5 * atr:
                status = "broken_up"
                breakout_price = last_close
                measured_target = last_close + height
                direction = "bullish"
            elif last_close < last_lo - 0.5 * atr:
                status = "broken_down"
                breakout_price = last_close
                measured_target = last_close - height
                direction = "bearish"
            elif min(abs(last_close - last_lo), abs(last_close - last_hi)) <= 0.3 * atr:
                status = "testing"
            eval_row = {
                "confidence": confidence,
                "touches": touches,
                "status": status,
                "direction": direction,
                "breakout_price": breakout_price,
                "invalidation_price": last_lo - atr if direction != "bearish" else last_hi + atr,
                "measured_target": measured_target,
                "slope": (s_lo + s_hi) / 2,
                "intercept": (support["intercept"] + resist["intercept"]) / 2,
                "start": overlap_start,
                "end": overlap_end,
                "span": span,
                "residual": residual,
            }
            if confidence < _KEEP_CONFIDENCE:
                continue
            anchors = [
                _anchor(window_times, window_dates, support["p0"][0], support["p0"][1]),
                _anchor(window_times, window_dates, support["p1"][0], support["p1"][1]),
                _anchor(window_times, window_dates, resist["p0"][0], resist["p0"][1]),
                _anchor(window_times, window_dates, resist["p1"][0], resist["p1"][1]),
            ]
            packed = _pack(
                kind=kind,
                subtype=subtype,
                eval_row=eval_row,
                anchors=anchors,
                data_through=data_through,
                extra_rationale=extra,
            )
            candidates.append(packed)

    # Drop internal fields used only for collapse.
    cleaned: list[dict[str, Any]] = []
    for row in candidates:
        if row.get("formationEnd", "") > data_through:
            continue
        if any(anchor["barKey"] > data_through for anchor in row["anchors"]):
            continue
        cleaned.append(row)
    collapsed = _collapse(cleaned, atr)
    public = []
    for row in collapsed:
        public.append(
            {
                key: value
                for key, value in row.items()
                if key not in {"slope", "intercept", "start", "end", "span"}
            }
        )
    public.sort(key=lambda row: (-row["confidence"], row["kind"], row["subtype"], row["id"]))
    return public


__all__ = ["ALGORITHM_VERSION", "detect_auto_patterns"]
