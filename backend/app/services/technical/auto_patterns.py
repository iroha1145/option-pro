"""Deterministic auto trend/pattern detection on daily bars (v2).

No SciPy, no ML, no look-ahead. Pipeline: truncate by data_through, then
lookback, then local ATR, then multi-span swings, then two-point candidates,
then consensus-touch robust fit, then independent scores and NMS.

Boxes are not emitted here — `base_structure` is the sole box/base source.
shapeQuality describes geometry only; it is not a win rate.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any, Mapping, Sequence

from app.services.strength.price_action import _find_swings

ALGORITHM_VERSION = "optix-auto-patterns-v2"
_LOOKBACK = 252
_MIN_BARS = 40
_MIN_SPAN = 20
_TOUCH_ATR = 0.35
_PENETRATE_ATR = 0.55
# 真实行情里形态的几何质量普遍落在 0.55 以下：线上实测 NVDA 515 根日线能拟合出
# 12 个候选，0.55 一个不留、0.45 留 2 个。原值是照着合成测试数据（理想锯齿）定的，
# 单元测试一直全绿，而线上从上线起一条也没画出来过。
_KEEP_QUALITY = 0.45
_MAX_RESULTS = 12
_TOUCH_GAP = 3
_SWING_SPANS = (2, 3, 5)
# consensus = 有多少条独立候选线落在同一几何上。孤证起步 0.55，每多一条
# 被 NMS 合并进来的候选 +0.15，封顶 1.0——佐证只能抬 displayPriority，
# 不能像旧写法那样把合并后的行压到孤证之下。
_CONSENSUS_BASE = 0.55
_CONSENSUS_STEP = 0.15

# Audit-tunable mix of independent evidence. Not a probability or trade signal.
DISPLAY_PRIORITY_WEIGHTS = {
    "shapeQuality": 0.55,
    "volumeConfirmation": 0.15,
    "trendAlignment": 0.15,
    "recency": 0.10,
    "consensus": 0.05,
}


def compute_display_priority(
    shape_quality: float,
    volume_confirmation: float,
    trend_alignment: float,
    recency: float,
    consensus: float,
) -> float:
    weights = DISPLAY_PRIORITY_WEIGHTS
    value = (
        weights["shapeQuality"] * _clamp01(shape_quality)
        + weights["volumeConfirmation"] * _clamp01(volume_confirmation)
        + weights["trendAlignment"] * _clamp01(trend_alignment)
        + weights["recency"] * _clamp01(recency)
        + weights["consensus"] * _clamp01(consensus)
    )
    return round(_clamp01(value), 4)


def apply_volume_confirmation(row: Mapping[str, Any], volume_confirmation: float) -> dict[str, Any]:
    """Change displayPriority from volumeConfirmation without touching geometry."""

    return apply_display_evidence(row, volume_confirmation, float(row.get("trendAlignment") or 0.0))


def apply_display_evidence(
    row: Mapping[str, Any],
    volume_confirmation: float,
    trend_alignment: float,
) -> dict[str, Any]:
    """Change displayPriority from volume/trend evidence; geometry stays put."""

    updated = dict(row)
    evidence = dict(updated.get("evidence") or {})
    evidence["volumeConfirmation"] = round(_clamp01(volume_confirmation), 4)
    evidence["trendAlignment"] = round(_clamp01(trend_alignment), 4)
    updated["volumeConfirmation"] = evidence["volumeConfirmation"]
    updated["trendAlignment"] = evidence["trendAlignment"]
    updated["evidence"] = evidence
    updated["displayPriority"] = compute_display_priority(
        float(updated.get("shapeQuality") or 0.0),
        evidence["volumeConfirmation"],
        evidence["trendAlignment"],
        float(updated.get("recency") or 0.0),
        float(updated.get("consensus") or 0.0),
    )
    return updated


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _iso_from_epoch(times: Sequence[int], dates: Sequence[str], index: int) -> str:
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
    parts = [ALGORITHM_VERSION, kind, subtype]
    for item in anchors:
        try:
            price = float(item.get("price") or 0.0)
        except (TypeError, ValueError):
            price = 0.0
        parts.append(str(item.get("barKey") or ""))
        parts.append(f"{price:.4f}")
    raw = "|".join(parts)
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


def _robust_fit(points: Sequence[tuple[int, float]]) -> tuple[float, float] | None:
    """Theil–Sen slope + median intercept. Two points reduce to the unique line."""

    clean = [(int(x), float(y)) for x, y in points if math.isfinite(y)]
    if len(clean) < 2:
        return None
    if len(clean) == 2:
        return _line(clean[0], clean[1])
    slopes: list[float] = []
    for i, (x1, y1) in enumerate(clean):
        for x2, y2 in clean[i + 1 :]:
            if x2 == x1:
                continue
            slopes.append((y2 - y1) / (x2 - x1))
    if not slopes:
        return None
    slope = _median(slopes)
    intercept = _median([y - slope * x for x, y in clean])
    if not math.isfinite(slope) or not math.isfinite(intercept):
        return None
    return slope, intercept


def _dedupe_indexes(indexes: Sequence[int], min_gap: int = _TOUCH_GAP) -> list[int]:
    ordered = sorted({int(i) for i in indexes})
    kept: list[int] = []
    for index in ordered:
        if not kept or index - kept[-1] >= min_gap:
            kept.append(index)
    return kept


def _atr_series(
    highs: Sequence[float],
    lows: Sequence[float],
    closes: Sequence[float],
    window: int = 14,
) -> list[float]:
    n = len(closes)
    if n == 0:
        return []
    tr = [max(0.0, highs[0] - lows[0])]
    for i in range(1, n):
        tr.append(
            max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        )
    out = [tr[0]] * n
    if n <= window:
        mean = sum(tr) / n
        return [mean if mean > 0 else 1e-9] * n
    acc = sum(tr[1 : window + 1])
    atr = acc / window
    for i in range(window):
        out[i] = atr if atr > 0 else 1e-9
    out[window] = atr if atr > 0 else 1e-9
    for i in range(window + 1, n):
        atr = (atr * (window - 1) + tr[i]) / window
        out[i] = atr if atr > 0 else 1e-9
    return out


def _multi_span_swings(
    highs: Sequence[float],
    lows: Sequence[float],
    local_atr: Sequence[float],
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """span=3 is the primary fractal; span=2/5 confirm or add significant pivots."""

    h3, l3 = _find_swings(list(highs), list(lows), 3)
    h2, l2 = _find_swings(list(highs), list(lows), 2)
    h5, l5 = _find_swings(list(highs), list(lows), 5)

    def pack(
        primary: list[tuple[int, float]],
        extra: list[tuple[int, float]],
        prices: Sequence[float],
        side: str,
    ) -> list[tuple[int, float]]:
        extra_idx = {i for i, _ in extra}
        kept: list[tuple[int, float]] = []
        for index, price in primary:
            atr = local_atr[index] if index < len(local_atr) else 1.0
            left = list(prices[max(0, index - 3) : index])
            right = list(prices[index + 1 : index + 4])
            neighbors = left + right
            if side == "high":
                neigh = max(neighbors) if neighbors else price
                sig = (price - neigh) / atr if atr else 0.0
            else:
                neigh = min(neighbors) if neighbors else price
                sig = (neigh - price) / atr if atr else 0.0
            confirmed = index in extra_idx or any(abs(index - other) <= 1 for other in extra_idx)
            if confirmed or sig >= 0.20:
                kept.append((index, price))
        seen: set[int] = {i for i, _ in kept}
        for index, price in extra:
            if index in seen:
                continue
            atr = local_atr[index] if index < len(local_atr) else 1.0
            left = list(prices[max(0, index - 3) : index])
            right = list(prices[index + 1 : index + 4])
            neighbors = left + right
            if side == "high":
                neigh = max(neighbors) if neighbors else price
                sig = (price - neigh) / atr if atr else 0.0
            else:
                neigh = min(neighbors) if neighbors else price
                sig = (neigh - price) / atr if atr else 0.0
            if sig >= 0.55:
                kept.append((index, price))
                seen.add(index)
        kept.sort(key=lambda item: item[0])
        return kept

    extra_highs = h2 + h5
    extra_lows = l2 + l5
    return pack(h3, extra_highs, highs, "high"), pack(l3, extra_lows, lows, "low")


def _evaluate_line(
    *,
    slope: float,
    intercept: float,
    points: Sequence[tuple[int, float]],
    opens: Sequence[float],
    closes: Sequence[float],
    volumes: Sequence[float],
    local_atr: Sequence[float],
    side: str,
    start: int,
    end: int,
) -> dict[str, Any] | None:
    span = end - start
    if span < _MIN_SPAN:
        return None
    raw_touches: list[int] = []
    residual_sum = 0.0
    residual_n = 0
    last_touch = start
    for index, price in points:
        if index < start - 1 or index > end + 1:
            continue
        atr = local_atr[index] if index < len(local_atr) else 1.0
        expected = _y(slope, intercept, index)
        dist = (price - expected) / atr
        residual_sum += abs(dist)
        residual_n += 1
        if abs(dist) <= _TOUCH_ATR:
            raw_touches.append(index)
            last_touch = max(last_touch, index)
    touch_indexes = _dedupe_indexes(raw_touches)
    if residual_n == 0:
        return None
    residual = residual_sum / residual_n
    if len(touch_indexes) >= 2:
        touch_points = [(i, p) for i, p in points if i in set(touch_indexes)]
        refit = _robust_fit(touch_points)
        if refit is not None:
            slope, intercept = refit
            residual_sum = 0.0
            residual_n = 0
            raw_touches = []
            last_touch = start
            for index, price in points:
                if index < start - 1 or index > end + 1:
                    continue
                atr = local_atr[index] if index < len(local_atr) else 1.0
                expected = _y(slope, intercept, index)
                dist = (price - expected) / atr
                residual_sum += abs(dist)
                residual_n += 1
                if abs(dist) <= _TOUCH_ATR:
                    raw_touches.append(index)
                    last_touch = max(last_touch, index)
            touch_indexes = _dedupe_indexes(raw_touches)
            residual = residual_sum / residual_n if residual_n else residual

    touches = len(touch_indexes)
    body_hits = 0
    for i in range(start, min(end + 1, len(closes))):
        atr = local_atr[i] if i < len(local_atr) else 1.0
        expected = _y(slope, intercept, i)
        close = closes[i]
        open_ = opens[i] if i < len(opens) else close
        body_lo = min(open_, close)
        body_hi = max(open_, close)
        if side == "support" and (close < expected - _PENETRATE_ATR * atr or body_hi < expected - _PENETRATE_ATR * atr):
            body_hits += 1
        elif side == "resistance" and (close > expected + _PENETRATE_ATR * atr or body_lo > expected + _PENETRATE_ATR * atr):
            body_hits += 1
    penetrations = body_hits
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
    atr_end = local_atr[end] if end < len(local_atr) else 1.0
    height = abs(_y(slope, intercept, end) - _y(slope, intercept, start))
    height_atr = height / atr_end if atr_end else 0.0
    if 1.2 <= height_atr <= 18.0 or (abs(slope) * span < 0.45 * atr_end):
        score += 8.0
    last_close = closes[-1]
    last_expected = _y(slope, intercept, len(closes) - 1)
    last_atr = local_atr[-1] if local_atr else 1.0
    status = "forming"
    breakout_price = None
    invalidation_price = None
    measured_target = None
    direction = "neutral"
    if side == "support":
        invalidation_price = last_expected - last_atr
        direction = "bullish" if slope > 0 else "bearish"
        if last_close < last_expected - 0.5 * last_atr:
            status = "broken_down"
        elif abs(last_close - last_expected) <= 0.3 * last_atr:
            status = "testing"
    else:
        invalidation_price = last_expected + last_atr
        direction = "bearish" if slope < 0 else "bullish"
        if last_close > last_expected + 0.5 * last_atr:
            status = "broken_up"
        elif abs(last_close - last_expected) <= 0.3 * last_atr:
            status = "testing"
    if penetrations >= 3 and status == "forming":
        status = "invalidated"
        score -= 12.0
    confidence = max(0.0, min(100.0, score))
    shape_quality = _clamp01(confidence / 100.0)
    if shape_quality < _KEEP_QUALITY:
        return None
    recency = _clamp01(1.0 - last_bars_ago / 60.0)
    median_vol = _median([float(v) for v in volumes]) or 1.0
    touch_vol = _median([float(volumes[i]) for i in touch_indexes if i < len(volumes)]) if touch_indexes else median_vol
    volume_confirmation = _clamp01(0.35 + 0.4 * min(2.0, touch_vol / median_vol))
    net = closes[-1] - closes[max(0, len(closes) - 21)]
    if side == "support":
        trend_alignment = 0.7 if net >= 0 else 0.3
        if slope > 0:
            trend_alignment = min(1.0, trend_alignment + 0.15)
    else:
        trend_alignment = 0.7 if net <= 0 else 0.3
        if slope < 0:
            trend_alignment = min(1.0, trend_alignment + 0.15)
    consensus = _CONSENSUS_BASE
    return {
        "touches": touches,
        "residual": residual,
        "penetrations": penetrations,
        "span": span,
        "last_touch": last_touch,
        "touch_indexes": touch_indexes,
        "confidence": confidence,
        "shapeQuality": round(shape_quality, 4),
        "volumeConfirmation": round(volume_confirmation, 4),
        "trendAlignment": round(trend_alignment, 4),
        "recency": round(recency, 4),
        "consensus": consensus,
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
    for item in sorted(
        candidates,
        key=lambda row: (-float(row.get("displayPriority") or row.get("shapeQuality") or 0), row["kind"], row["subtype"]),
    ):
        duplicate = False
        for other in kept:
            overlap = min(item.get("end", 0), other.get("end", 0)) - max(item.get("start", 0), other.get("start", 0))
            span = max(item.get("span") or 1, other.get("span") or 1)
            same_kind = other["kind"] == item["kind"]
            mid = (item.get("start", 0) + item.get("end", 0)) / 2
            y_a = _y(item["slope"], item["intercept"], mid) if "slope" in item else None
            y_b = _y(other["slope"], other["intercept"], mid) if "slope" in other else None
            close_geom = (
                y_a is not None
                and y_b is not None
                and abs(item["slope"] - other["slope"]) * span < 0.35 * atr
                and abs(y_a - y_b) < 0.4 * atr
            )
            if same_kind and close_geom:
                sources = list(dict.fromkeys([*(other.get("sources") or []), *(item.get("sources") or [])]))
                other["sources"] = sources
                # sources 是出处（永远只有 auto_patterns），佐证数要数被合并掉的候选线。
                merged = int(other.get("mergedCount") or 1) + 1
                other["mergedCount"] = merged
                other["consensus"] = round(
                    _clamp01(_CONSENSUS_BASE + _CONSENSUS_STEP * (merged - 1)), 4
                )
                evidence = dict(other.get("evidence") or {})
                evidence["consensus"] = other["consensus"]
                evidence["sources"] = sources
                other["evidence"] = evidence
                other["displayPriority"] = compute_display_priority(
                    float(other.get("shapeQuality") or 0),
                    float(other.get("volumeConfirmation") or 0),
                    float(other.get("trendAlignment") or 0),
                    float(other.get("recency") or 0),
                    float(other["consensus"]),
                )
                duplicate = True
                break
            if (
                not same_kind
                and overlap > 0.6 * span
                and close_geom
                and {other["kind"], item["kind"]} <= {"support_trend", "channel"}
            ):
                duplicate = True
                break
        if not duplicate:
            kept.append(item)
        if len(kept) >= _MAX_RESULTS:
            break
    return kept


def _strip_anchor(item: Mapping[str, Any]) -> dict[str, Any]:
    row = {
        "time": item["time"],
        "barKey": item["barKey"],
        "price": round(float(item["price"]), 4),
    }
    if item.get("index") is not None:
        row["index"] = int(item["index"])
    return row


def _fit_rail_anchors(
    times: Sequence[int],
    dates: Sequence[str],
    start: int,
    end: int,
    slope: float,
    intercept: float,
) -> list[dict[str, Any]]:
    """Painted endpoints lie on the scored Theil–Sen line, not the candidate swings."""

    return [
        _anchor(times, dates, start, _y(slope, intercept, start)),
        _anchor(times, dates, end, _y(slope, intercept, end)),
    ]


def _touch_anchors(
    times: Sequence[int],
    dates: Sequence[str],
    points: Sequence[tuple[int, float]],
    indexes: Sequence[int],
) -> list[dict[str, Any]]:
    by_index = {int(index): float(price) for index, price in points}
    anchors: list[dict[str, Any]] = []
    for index in indexes:
        price = by_index.get(int(index))
        if price is None:
            continue
        anchors.append(_anchor(times, dates, int(index), price))
    return anchors


def _pack(
    *,
    kind: str,
    subtype: str,
    eval_row: dict[str, Any],
    anchors: list[dict[str, Any]],
    data_through: str,
    extra_rationale: Sequence[str] = (),
    sources: Sequence[str] = ("auto_patterns",),
    touch_anchors: Sequence[Mapping[str, Any]] | None = None,
    support_rail: Sequence[Mapping[str, Any]] | None = None,
    resistance_rail: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    public_anchors = [_strip_anchor(item) for item in anchors]
    id_anchors: list[dict[str, Any]] = []
    for group in (support_rail, resistance_rail, anchors, touch_anchors):
        if not group:
            continue
        id_anchors.extend(_strip_anchor(item) for item in group)
    if not id_anchors:
        id_anchors = public_anchors
    shape_quality = float(eval_row.get("shapeQuality") or (eval_row.get("confidence") or 0) / 100.0)
    volume_confirmation = float(eval_row.get("volumeConfirmation") or 0.5)
    trend_alignment = float(eval_row.get("trendAlignment") or 0.5)
    recency = float(eval_row.get("recency") or 0.5)
    consensus = float(eval_row.get("consensus") or _CONSENSUS_BASE)
    display_priority = compute_display_priority(
        shape_quality, volume_confirmation, trend_alignment, recency, consensus
    )
    rationale = [
        "touches",
        "atr_residual",
        "no_lookahead",
        "robust_fit",
        *extra_rationale,
    ]
    return {
        "id": _pattern_id(kind, subtype, id_anchors),
        "algorithmVersion": ALGORITHM_VERSION,
        "kind": kind,
        "subtype": subtype,
        "direction": eval_row["direction"],
        "anchors": public_anchors,
        "confidence": round(shape_quality * 100.0, 1),
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
        "shapeQuality": round(shape_quality, 4),
        "volumeConfirmation": round(volume_confirmation, 4),
        "trendAlignment": round(trend_alignment, 4),
        "recency": round(recency, 4),
        "consensus": round(consensus, 4),
        "displayPriority": display_priority,
        "sources": list(sources),
        "mergedCount": 1,
        "evidence": {
            "shapeQuality": round(shape_quality, 4),
            "volumeConfirmation": round(volume_confirmation, 4),
            "trendAlignment": round(trend_alignment, 4),
            "recency": round(recency, 4),
            "consensus": round(consensus, 4),
            "sources": list(sources),
        },
        "slope": eval_row.get("slope"),
        "intercept": eval_row.get("intercept"),
        "start": eval_row.get("start"),
        "end": eval_row.get("end"),
        "span": eval_row.get("span"),
        "p0": eval_row.get("p0"),
        "p1": eval_row.get("p1"),
        "touch_indexes": eval_row.get("touch_indexes"),
        "fitAnchors": [_strip_anchor(item) for item in anchors],
        "touchAnchors": [_strip_anchor(item) for item in (touch_anchors or [])],
        "supportRail": [_strip_anchor(item) for item in support_rail] if support_rail else None,
        "resistanceRail": [_strip_anchor(item) for item in resistance_rail] if resistance_rail else None,
        "supportSlope": eval_row.get("supportSlope"),
        "supportIntercept": eval_row.get("supportIntercept"),
        "resistanceSlope": eval_row.get("resistanceSlope"),
        "resistanceIntercept": eval_row.get("resistanceIntercept"),
    }


def _inside_fraction(
    *,
    support: Mapping[str, Any],
    resist: Mapping[str, Any],
    opens: Sequence[float],
    closes: Sequence[float],
    local_atr: Sequence[float],
    start: int,
    end: int,
) -> float:
    inside = 0
    total = 0
    for i in range(start, min(end + 1, len(closes))):
        atr = local_atr[i] if i < len(local_atr) else 1.0
        lo = _y(support["slope"], support["intercept"], i)
        hi = _y(resist["slope"], resist["intercept"], i)
        if hi <= lo:
            continue
        open_ = opens[i] if i < len(opens) else closes[i]
        body_lo = min(open_, closes[i])
        body_hi = max(open_, closes[i])
        total += 1
        if body_lo >= lo - 0.2 * atr and body_hi <= hi + 0.2 * atr:
            inside += 1
    return inside / total if total else 0.0


def _alternates(support_touches: Sequence[int], resist_touches: Sequence[int]) -> bool:
    support = _dedupe_indexes(support_touches)
    resist = _dedupe_indexes(resist_touches)
    if len(support) < 2 or len(resist) < 2:
        return False
    events = sorted([(i, "s") for i in support] + [(i, "r") for i in resist])
    if len(events) < 4:
        return False
    switches = 0
    for prev, cur in zip(events, events[1:]):
        if prev[1] != cur[1]:
            switches += 1
    if switches < 2:
        return False
    span = events[-1][0] - events[0][0]
    if span <= 0:
        return False
    interior_lo = events[0][0] + 0.2 * span
    interior_hi = events[-1][0] - 0.2 * span
    interior = [index for index, _side in events if interior_lo <= index <= interior_hi]
    return len(interior) >= 2


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
    opens: list[float] = list(series.get("opens") or closes)
    volumes: list[float] = list(series.get("volumes") or [])
    times: list[int] = list(series.get("times") or [])
    dates: list[str] = list(series.get("dates") or [])
    n = len(closes)
    if n < _MIN_BARS or not (len(highs) == len(lows) == len(times) == len(dates) == n):
        return []
    if data_through:
        keep_end = None
        for index, day in enumerate(dates):
            if day <= data_through:
                keep_end = index
        if keep_end is None:
            return []
        last = keep_end + 1
        if last < _MIN_BARS:
            return []
        highs = highs[:last]
        lows = lows[:last]
        closes = closes[:last]
        opens = opens[:last] if len(opens) >= last else closes[:]
        volumes = volumes[:last] if len(volumes) == n else [0.0] * last
        times = times[:last]
        dates = dates[:last]
        n = last
    start = max(0, n - lookback)
    window_highs = highs[start:]
    window_lows = lows[start:]
    window_closes = closes[start:]
    window_opens = opens[start:] if len(opens) >= n else window_closes
    window_volumes = volumes[start:] if len(volumes) == n else [0.0] * (n - start)
    window_times = times[start:]
    window_dates = dates[start:]
    local_atr = _atr_series(window_highs, window_lows, window_closes)
    atr = local_atr[-1] if local_atr else None
    if atr is None or atr <= 0:
        return []
    swing_highs, swing_lows = _multi_span_swings(window_highs, window_lows, local_atr)
    if len(swing_highs) < 2 and len(swing_lows) < 2:
        return []
    data_through = window_dates[-1]
    candidates: list[dict[str, Any]] = []

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
                    opens=window_opens,
                    closes=window_closes,
                    volumes=window_volumes,
                    local_atr=local_atr,
                    side=side,
                    start=start_i,
                    end=end_i,
                )
                if evaluated is None:
                    continue
                # raw_slope 是两点候选线的斜率（拟合前），单线趋势按它的符号取舍。
                found.append({**evaluated, "p0": points[i], "p1": points[j], "raw_slope": slope})
        return found

    # 单线趋势与通道/三角形/楔形吃的是同一份 O(k²) 摆动配对评估：算一次，
    # 单线那边按候选斜率符号挑，别把每对再评一遍。
    extra_supports = fit_all(swing_lows, "support")
    extra_resists = fit_all(swing_highs, "resistance")

    def consider_trend(
        evaluated_rows: list[dict[str, Any]],
        points: list[tuple[int, float]],
        side: str,
        want_sign: int,
    ) -> None:
        for evaluated in evaluated_rows:
            slope = float(evaluated["raw_slope"])
            if want_sign > 0 and slope <= 1e-6:
                continue
            if want_sign < 0 and slope >= -1e-6:
                continue
            kind = "support_trend" if side == "support" else "resistance_trend"
            subtype = "rising" if evaluated["slope"] > 0 else "falling"
            fit = _fit_rail_anchors(
                window_times,
                window_dates,
                int(evaluated["start"]),
                int(evaluated["end"]),
                float(evaluated["slope"]),
                float(evaluated["intercept"]),
            )
            packed = _pack(
                kind=kind,
                subtype=subtype,
                eval_row=evaluated,
                anchors=fit,
                data_through=data_through,
                extra_rationale=("swing_line",),
                touch_anchors=_touch_anchors(
                    window_times, window_dates, points, evaluated.get("touch_indexes") or []
                ),
            )
            candidates.append(packed)

    consider_trend(extra_supports, swing_lows, "support", +1)
    consider_trend(extra_resists, swing_highs, "resistance", -1)

    for support in extra_supports:
        for resist in extra_resists:
            overlap_start = max(support["start"], resist["start"])
            overlap_end = min(support["end"], resist["end"])
            span = overlap_end - overlap_start
            if span < _MIN_SPAN:
                continue
            if support["touches"] < 2 or resist["touches"] < 2:
                continue
            if not _alternates(support.get("touch_indexes") or [], resist.get("touch_indexes") or []):
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
            width_ratio = height1 / height0
            lo_flat = abs(s_lo) * span < 0.35 * atr
            hi_flat = abs(s_hi) * span < 0.35 * atr
            both_flat = lo_flat and hi_flat
            if both_flat:
                continue  # boxes come from base_structure only
            inside = _inside_fraction(
                support=support,
                resist=resist,
                opens=window_opens,
                closes=window_closes,
                local_atr=local_atr,
                start=overlap_start,
                end=overlap_end,
            )
            if inside < 0.55:
                continue
            kind = None
            subtype = None
            direction = "neutral"
            extra = []
            if parallel and not both_flat:
                if not (0.68 <= width_ratio <= 1.40):
                    continue
                kind, subtype = "channel", "rising" if (s_lo + s_hi) / 2 > 0 else "falling"
                direction = "bullish" if subtype == "rising" else "bearish"
                extra.append("parallel")
            elif hi_flat and s_lo > 1e-6:
                if width_ratio > 0.96:
                    continue
                kind, subtype = "triangle", "ascending"
                direction = "bullish"
                extra.append("convergence")
            elif lo_flat and s_hi < -1e-6:
                if width_ratio > 0.96:
                    continue
                kind, subtype = "triangle", "descending"
                direction = "bearish"
                extra.append("convergence")
            elif converging and not same_sign:
                if width_ratio > 0.96:
                    continue
                kind, subtype = "triangle", "symmetric"
                extra.append("convergence")
            elif same_sign and converging:
                if width_ratio > 0.96:
                    continue
                kind, subtype = "wedge", "rising" if s_lo > 0 else "falling"
                direction = (
                    "bullish"
                    if subtype == "falling"
                    else "bearish"
                )
                extra.append("same_direction_convergence")
            else:
                continue
            if kind in {"triangle", "wedge"} and abs(s_hi - s_lo) > 1e-9:
                apex = (support["intercept"] - resist["intercept"]) / (s_hi - s_lo)
                if apex < overlap_start - span or apex > overlap_end + 4 * span:
                    continue
            last_touch = max(support["last_touch"], resist["last_touch"])
            if (len(window_closes) - 1) - last_touch > 40:
                continue
            touches = support["touches"] + resist["touches"]
            residual = (support["residual"] + resist["residual"]) / 2
            shape_quality = _clamp01(
                0.5 * (support["shapeQuality"] + resist["shapeQuality"])
                + (0.08 if parallel else 0.0)
                + (0.06 if kind in {"triangle", "wedge"} else 0.0)
            )
            if shape_quality < _KEEP_QUALITY:
                continue
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
            recency = _clamp01(1.0 - ((len(window_closes) - 1) - last_touch) / 60.0)
            eval_row = {
                "confidence": shape_quality * 100.0,
                "shapeQuality": shape_quality,
                "volumeConfirmation": round(
                    0.5 * (support["volumeConfirmation"] + resist["volumeConfirmation"]), 4
                ),
                "trendAlignment": round(
                    0.5 * (support["trendAlignment"] + resist["trendAlignment"]), 4
                ),
                "recency": recency,
                "consensus": _CONSENSUS_BASE,
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
                "p0": support["p0"],
                "p1": support["p1"],
                "supportSlope": support["slope"],
                "supportIntercept": support["intercept"],
                "resistanceSlope": resist["slope"],
                "resistanceIntercept": resist["intercept"],
            }
            support_rail = _fit_rail_anchors(
                window_times, window_dates, overlap_start, overlap_end, support["slope"], support["intercept"]
            )
            resistance_rail = _fit_rail_anchors(
                window_times, window_dates, overlap_start, overlap_end, resist["slope"], resist["intercept"]
            )
            packed = _pack(
                kind=kind,
                subtype=subtype,
                eval_row=eval_row,
                anchors=[*support_rail, *resistance_rail],
                data_through=data_through,
                extra_rationale=extra,
                touch_anchors=_touch_anchors(
                    window_times,
                    window_dates,
                    swing_lows + swing_highs,
                    list(support.get("touch_indexes") or []) + list(resist.get("touch_indexes") or []),
                ),
                support_rail=support_rail,
                resistance_rail=resistance_rail,
            )
            packed["widthRatio"] = round(width_ratio, 4)
            candidates.append(packed)

    cleaned: list[dict[str, Any]] = []
    for row in candidates:
        if row.get("formationEnd", "") > data_through:
            continue
        if any(anchor["barKey"] > data_through for anchor in row["anchors"]):
            continue
        cleaned.append(row)
    collapsed = _collapse(cleaned, atr)
    public = []
    drop = {"p0", "p1", "touch_indexes", "widthRatio", "mergedCount"}
    for row in collapsed:
        public.append({key: value for key, value in row.items() if key not in drop})
    public.sort(
        key=lambda row: (-row["displayPriority"], row["kind"], row["subtype"], row["id"])
    )
    return public


__all__ = [
    "ALGORITHM_VERSION",
    "DISPLAY_PRIORITY_WEIGHTS",
    "apply_volume_confirmation",
    "compute_display_priority",
    "detect_auto_patterns",
]
