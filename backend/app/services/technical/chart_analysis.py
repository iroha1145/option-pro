"""Pure-data ChartAnalysisBundle assembler.

Algorithms never return an Apache ECharts option. Strength Scanner is not
imported — family scores here are summaries of already-computed per-stock
series, not Radar ranks or market-fit.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.strength.scoring import score_intrinsic
from app.services.technical.auto_patterns import (
    ALGORITHM_VERSION as AUTO_PATTERNS_VERSION,
    compute_display_priority,
    detect_auto_patterns,
)
from app.services.technical.base_structure import DETECTOR_VERSION as BASE_VERSION
from app.services.technical.indicators import (
    TECHNICALS_VERSION,
    macd_series,
    range_position_series,
    rsi_series,
    sma_series,
)
from app.services.technical.layer_registry import LAYER_REGISTRY_VERSION

BUNDLE_VERSION = "optix-chart-analysis-v1"
_NY = ZoneInfo("America/New_York")
_INTRADAY_RANGES = {"5m", "15m", "1h"}


def fingerprint_raw(
    dates: Sequence[str],
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> str:
    """Shared payload the frontend hashes with the same FNV-1a 64-bit digest."""

    n = len(closes)
    acc = 0
    for close in closes:
        acc = (acc * 1_000_003 + int(round(float(close) * 10_000))) % (2**64)
    first = dates[0] if dates else ""
    last = dates[-1] if dates else ""
    last_close = f"{closes[-1]:.6f}" if n else "0"
    last_high = f"{highs[-1]:.6f}" if highs and n else "0"
    last_low = f"{lows[-1]:.6f}" if lows and n else "0"
    return f"{n}|{first}|{last}|{last_close}|{last_high}|{last_low}|{acc:x}"


def fnv1a64_hex16(raw: str) -> str:
    """FNV-1a 64-bit, hex16 — identical to the frontend `barFingerprint` digest."""

    h = 0xCBF29CE484222325
    for char in raw:
        h ^= ord(char)
        h = (h * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return f"{h:016x}"


def bar_fingerprint(
    dates: Sequence[str],
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> str:
    """Stable id for the analysis series. Frontend must match before painting."""

    return fnv1a64_hex16(fingerprint_raw(dates, closes, highs, lows))


def _anchor_from_index(
    times: Sequence[int],
    dates: Sequence[str],
    index: int,
    price: float,
) -> dict[str, Any]:
    day = dates[index]
    return {
        "time": f"{day}T00:00:00+00:00",
        "barKey": day,
        "price": round(float(price), 4),
    }


def _overlay(
    *,
    overlay_id: str,
    source_id: str,
    algorithm_version: str,
    group: str,
    kind: str,
    geometry: Mapping[str, Any],
    status: str,
    direction: str,
    shape_quality: float,
    display_priority: float,
    evidence: Mapping[str, Any],
    formation_start: str,
    formation_end: str,
    data_through: str,
    label: str,
    detail: str,
) -> dict[str, Any]:
    return {
        "id": overlay_id,
        "sourceId": source_id,
        "algorithmVersion": algorithm_version,
        "group": group,
        "kind": kind,
        "geometry": dict(geometry),
        "status": status,
        "direction": direction,
        "shapeQuality": round(float(shape_quality), 4),
        "displayPriority": round(float(display_priority), 4),
        "evidence": dict(evidence),
        "formationStart": formation_start,
        "formationEnd": formation_end,
        "dataThrough": data_through,
        "label": label,
        "detail": detail,
    }


def _pattern_overlays(patterns: Sequence[Mapping[str, Any]], data_through: str) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    for row in patterns:
        if row.get("kind") == "box":
            continue
        evidence = dict(row.get("evidence") or {})
        overlays.append(
            _overlay(
                overlay_id=str(row["id"]),
                source_id="auto_patterns",
                algorithm_version=str(row.get("algorithmVersion") or AUTO_PATTERNS_VERSION),
                group="price",
                kind=str(row["kind"]),
                geometry={
                    "type": "rails",
                    "anchors": list(row.get("anchors") or []),
                    "subtype": row.get("subtype"),
                    "styleHint": "auto-pale",
                },
                status=str(row.get("status") or "forming"),
                direction=str(row.get("direction") or "neutral"),
                shape_quality=float(row.get("shapeQuality") or 0.0),
                display_priority=float(row.get("displayPriority") or 0.0),
                evidence=evidence,
                formation_start=str(row.get("formationStart") or data_through),
                formation_end=str(row.get("formationEnd") or data_through),
                data_through=data_through,
                label=f"{row.get('kind')}:{row.get('subtype') or ''}".rstrip(":"),
                detail="shapeQuality is geometry, not a probability",
            )
        )
    return overlays


def _base_overlays(base: Mapping[str, Any] | None, base_state: Mapping[str, Any] | None, data_through: str) -> list[dict[str, Any]]:
    if not base:
        return []
    start = str(base.get("base_start") or data_through)
    end = str(base.get("base_end") or data_through)
    status_map = {
        "breakout": "broken_up",
        "failed": "broken_down",
        "at_resistance": "testing",
        "below_support": "testing",
        "in_base": "forming",
    }
    live = (base_state or {}).get("status") or "in_base"
    status = status_map.get(str(live), "forming")
    quality = float(base.get("quality") or 0.0)
    consensus = min(1.0, float(base.get("window_agreement") or 1) / max(float(base.get("windows_scanned") or 7), 1.0))
    evidence = {
        "shapeQuality": quality,
        "volumeConfirmation": 0.5,
        "trendAlignment": 0.5,
        "recency": 0.6,
        "consensus": round(consensus, 4),
        "sources": ["base_structure"],
        "windowAgreement": base.get("window_agreement"),
    }
    geometry = {
        "type": "band",
        "resistanceHigh": base.get("resistance_high"),
        "resistanceLow": base.get("resistance_low"),
        "supportLow": base.get("support_low"),
        "supportHigh": base.get("support_high"),
        "pivot": base.get("pivot_price"),
        "invalidation": base.get("invalidation_price"),
        "breakBuffer": base.get("break_buffer"),
        "styleHint": "auto-pale",
    }
    overlays = [
        _overlay(
            overlay_id=f"base:{base.get('pivot_id') or start}",
            source_id="base_structure",
            algorithm_version=str(base.get("detector_version") or BASE_VERSION),
            group="price",
            kind="box",
            geometry=geometry,
            status=status,
            direction="neutral",
            shape_quality=quality,
            display_priority=compute_display_priority(quality, 0.5, 0.5, 0.6, consensus),
            evidence=evidence,
            formation_start=start,
            formation_end=end,
            data_through=data_through,
            label="整理区",
            detail="box overlays come only from base_structure",
        )
    ]
    overlays.append(
        _overlay(
            overlay_id=f"pivot:{base.get('pivot_id') or start}",
            source_id="base_structure",
            algorithm_version=str(base.get("detector_version") or BASE_VERSION),
            group="price",
            kind="pivot",
            geometry={
                "type": "levels",
                "pivot": base.get("pivot_price"),
                "invalidation": base.get("invalidation_price"),
                "styleHint": "auto-pale",
            },
            status=status,
            direction="neutral",
            shape_quality=quality,
            display_priority=compute_display_priority(quality, 0.5, 0.5, 0.6, consensus),
            evidence=evidence,
            formation_start=start,
            formation_end=end,
            data_through=data_through,
            label="pivot/invalidation",
            detail="not a probability",
        )
    )
    return overlays


def consecutive_swing_labels(points: Sequence[Mapping[str, Any]], *, role: str) -> list[str]:
    """Label each confirmed swing from the previous same-side swing.

    First high/low in the pack is ``H`` / ``L``; later highs are HH/LH and
    later lows are HL/LL. Equal prices keep the continuation label.
    """

    indexed = list(enumerate(points))
    indexed.sort(key=lambda item: (str(item[1].get("trade_date") or ""), float(item[1].get("price") or 0), item[0]))
    labels = [""] * len(points)
    prev: float | None = None
    for original_index, point in indexed:
        try:
            price = float(point.get("price"))
        except (TypeError, ValueError):
            labels[original_index] = "H" if role == "high" else "L"
            continue
        if prev is None:
            labels[original_index] = "H" if role == "high" else "L"
        elif role == "high":
            labels[original_index] = "HH" if price >= prev else "LH"
        else:
            labels[original_index] = "HL" if price >= prev else "LL"
        prev = price
    return labels


def _price_action_overlays(
    price_action: Mapping[str, Any],
    series: Mapping[str, list],
    data_through: str,
) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    dates: list[str] = list(series.get("dates") or [])
    highs = list(price_action.get("swing_highs") or [])
    lows = list(price_action.get("swing_lows") or [])
    high_labels = consecutive_swing_labels(highs, role="high")
    low_labels = consecutive_swing_labels(lows, role="low")
    for point, label in zip(highs, high_labels):
        day = point.get("trade_date") or data_through
        overlays.append(
            _overlay(
                overlay_id=f"swing-h:{day}:{point.get('price')}",
                source_id="price_action",
                algorithm_version="price-action-swings",
                group="price",
                kind="swing",
                geometry={
                    "type": "point",
                    "anchors": [{"time": f"{day}T00:00:00+00:00", "barKey": day, "price": point.get("price")}],
                    "role": "high",
                    "styleHint": "auto-pale",
                },
                status="forming",
                direction="bearish",
                shape_quality=0.6,
                display_priority=0.4,
                evidence={"sources": ["price_action"], "shapeQuality": 0.6, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.7, "consensus": 1.0},
                formation_start=day,
                formation_end=day,
                data_through=data_through,
                label=label,
                detail="confirmed fractal swing",
            )
        )
    for point, label in zip(lows, low_labels):
        day = point.get("trade_date") or data_through
        overlays.append(
            _overlay(
                overlay_id=f"swing-l:{day}:{point.get('price')}",
                source_id="price_action",
                algorithm_version="price-action-swings",
                group="price",
                kind="swing",
                geometry={
                    "type": "point",
                    "anchors": [{"time": f"{day}T00:00:00+00:00", "barKey": day, "price": point.get("price")}],
                    "role": "low",
                    "styleHint": "auto-pale",
                },
                status="forming",
                direction="bullish",
                shape_quality=0.6,
                display_priority=0.4,
                evidence={"sources": ["price_action"], "shapeQuality": 0.6, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.7, "consensus": 1.0},
                formation_start=day,
                formation_end=day,
                data_through=data_through,
                label=label,
                detail="confirmed fractal swing",
            )
        )
    if price_action.get("resistance") is not None:
        overlays.append(
            _overlay(
                overlay_id="sr:resistance",
                source_id="price_action",
                algorithm_version="price-action-sr",
                group="price",
                kind="level",
                geometry={"type": "level", "price": price_action.get("resistance"), "role": "resistance", "styleHint": "auto-pale"},
                status="forming",
                direction="bearish",
                shape_quality=0.55,
                display_priority=0.35,
                evidence={"sources": ["price_action"], "shapeQuality": 0.55, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.6, "consensus": 1.0},
                formation_start=data_through,
                formation_end=data_through,
                data_through=data_through,
                label="最近阻力",
                detail="nearest confirmed swing high",
            )
        )
    if price_action.get("support") is not None:
        overlays.append(
            _overlay(
                overlay_id="sr:support",
                source_id="price_action",
                algorithm_version="price-action-sr",
                group="price",
                kind="level",
                geometry={"type": "level", "price": price_action.get("support"), "role": "support", "styleHint": "auto-pale"},
                status="forming",
                direction="bullish",
                shape_quality=0.55,
                display_priority=0.35,
                evidence={"sources": ["price_action"], "shapeQuality": 0.55, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.6, "consensus": 1.0},
                formation_start=data_through,
                formation_end=data_through,
                data_through=data_through,
                label="最近支撑",
                detail="nearest confirmed swing low",
            )
        )
    for event in price_action.get("pattern_events") or []:
        name = str(event.get("pattern") or event.get("name") or "")
        day = str(event.get("trade_date") or data_through)
        bars_ago = event.get("bars_ago")
        price = event.get("price")
        if price is None and isinstance(bars_ago, int) and bars_ago >= 0:
            idx = len(dates) - 1 - bars_ago
            if 0 <= idx < len(series.get("closes") or []):
                price = series["closes"][idx]
                day = dates[idx] if idx < len(dates) else day
        if not name or price is None:
            continue
        overlays.append(
            _overlay(
                overlay_id=f"candle:{name}:{day}",
                source_id="price_action",
                algorithm_version="price-action-candles",
                group="event",
                kind="candle",
                geometry={
                    "type": "point",
                    "anchors": [{"time": f"{day}T00:00:00+00:00", "barKey": day, "price": round(float(price), 4)}],
                    "pattern": name,
                    "barKey": day,
                    "styleHint": "event",
                },
                status="forming",
                direction="bullish" if "bull" in name or name in {"hammer"} else ("bearish" if "bear" in name or name == "shooting_star" else "neutral"),
                shape_quality=0.5,
                display_priority=0.45,
                evidence={"sources": ["price_action"], "shapeQuality": 0.5, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.9, "consensus": 1.0},
                formation_start=day,
                formation_end=day,
                data_through=data_through,
                label=name,
                detail="exact barKey event",
            )
        )
    for trap_name, flag_key, date_key, level_key in (
        ("spring", "spring", "spring_trade_date", "spring_level"),
        ("upthrust", "upthrust", "upthrust_trade_date", "upthrust_level"),
    ):
        if not price_action.get(flag_key):
            continue
        day = str(price_action.get(date_key) or data_through)
        price = price_action.get(level_key)
        if price is None:
            continue
        overlays.append(
            _overlay(
                overlay_id=f"trap:{trap_name}:{day}",
                source_id="price_action",
                algorithm_version="price-action-traps",
                group="event",
                kind="trap",
                geometry={
                    "type": "point",
                    "anchors": [{"time": f"{day}T00:00:00+00:00", "barKey": day, "price": round(float(price), 4)}],
                    "pattern": trap_name,
                    "styleHint": "event",
                },
                status="testing",
                direction="bullish" if trap_name == "spring" else "bearish",
                shape_quality=0.6,
                display_priority=0.5,
                evidence={"sources": ["price_action"], "shapeQuality": 0.6, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 0.85, "consensus": 1.0},
                formation_start=day,
                formation_end=day,
                data_through=data_through,
                label=trap_name,
                detail="failed break against a prior confirmed swing",
            )
        )
    return overlays


def _vol_price_overlays(vol_price: Mapping[str, Any], data_through: str) -> list[dict[str, Any]]:
    if not vol_price or vol_price.get("status") != "active":
        return []
    setup = str(vol_price.get("setup_type") or "unknown")
    label = str(vol_price.get("setup_label") or setup)
    quality = 0.55
    return [
        _overlay(
            overlay_id=f"volprice:{setup}:{data_through}",
            source_id="vol_price_match",
            algorithm_version="vol-price-match",
            group="event",
            kind="volume_setup",
            geometry={"type": "summary", "window": 10, "styleHint": "summary"},
            status="forming",
            direction="bullish" if "bull" in setup else ("bearish" if "bear" in setup or setup == "vacuum" else "neutral"),
            shape_quality=quality,
            display_priority=0.4,
            evidence={
                "shapeQuality": quality,
                "volumeConfirmation": 0.7,
                "trendAlignment": 0.5,
                "recency": 1.0,
                "consensus": 1.0,
                "sources": ["vol_price_match"],
                "effort": vol_price.get("effort"),
                "result": vol_price.get("result"),
                "obvSlope": vol_price.get("obv_slope"),
                "clvMean": vol_price.get("clv_mean"),
                "upDownVolumeRatio": vol_price.get("up_down_volume_ratio"),
                "breakoutQualityAdjustment": vol_price.get("breakout_quality_adjustment"),
                "falseBreakoutRisk": vol_price.get("false_breakout_risk"),
            },
            formation_start=data_through,
            formation_end=data_through,
            data_through=data_through,
            label=label,
            detail="last 10-day volume/price window; not a win rate",
        )
    ]


def _breakout_overlays(
    base: Mapping[str, Any] | None,
    base_state: Mapping[str, Any] | None,
    data_through: str,
    *,
    chart_range: str,
) -> list[dict[str, Any]]:
    if not base or not base_state:
        return []
    live = str(base_state.get("status") or "")
    status_map = {
        "breakout": "triggered",
        "failed": "failed",
        "at_resistance": "testing",
        "in_base": "forming",
        "below_support": "failed",
    }
    status = status_map.get(live, "forming")
    return [
        _overlay(
            overlay_id=f"breakout:{base.get('pivot_id') or data_through}",
            source_id="breakouts",
            algorithm_version="daily-base-breakout",
            group="event",
            kind="breakout",
            geometry={
                "type": "levels",
                "pivot": base.get("pivot_price"),
                "breakBuffer": base.get("break_buffer"),
                "invalidation": base.get("invalidation_price"),
                "styleHint": "emphasis" if status in {"triggered", "confirmed"} else "auto-pale",
            },
            status=status,
            direction="bullish" if status in {"triggered", "confirmed", "retest"} else ("bearish" if status == "failed" else "neutral"),
            shape_quality=float(base.get("quality") or 0.5),
            display_priority=0.5,
            evidence={
                "shapeQuality": float(base.get("quality") or 0.5),
                "volumeConfirmation": 0.5,
                "trendAlignment": 0.5,
                "recency": 0.7,
                "consensus": 1.0,
                "sources": ["base_structure"],
                "intraday": chart_range in {"5m", "15m", "1h"},
            },
            formation_start=str(base.get("base_start") or data_through),
            formation_end=data_through,
            data_through=data_through,
            label=f"breakout:{status}",
            detail="daily pivot trigger / buffer / failed — minute VWAP loaded on demand",
        )
    ]


def _volume_series(series: Mapping[str, list]) -> dict[str, list[float | None]]:
    closes = list(series.get("closes") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    volumes = list(series.get("volumes") or [0.0] * len(closes))
    n = len(closes)
    obv: list[float | None] = [None] * n
    clv: list[float | None] = [None] * n
    dollar: list[float | None] = [None] * n
    running = 0.0
    for i in range(n):
        vol = float(volumes[i] if i < len(volumes) else 0.0)
        dollar[i] = round(closes[i] * vol, 4) if vol > 0 else None
        if i > 0:
            if closes[i] > closes[i - 1]:
                running += vol
            elif closes[i] < closes[i - 1]:
                running -= vol
        obv[i] = round(running, 4)
        span = highs[i] - lows[i] if i < len(highs) and i < len(lows) else 0.0
        if span > 0:
            clv[i] = round((2 * closes[i] - highs[i] - lows[i]) / span, 4)
    return {"obv": obv, "clv": clv, "dollarVolume": dollar}


def _indicator_panes(
    series: Mapping[str, list],
    *,
    spy_closes: Sequence[float] | None,
    dates: Sequence[str],
) -> list[dict[str, Any]]:
    closes = list(series.get("closes") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    vol = _volume_series(series)
    macd = macd_series(closes)
    aligned = _align_spy_closes(dates, spy_closes)
    rs: list[float | None] = [None] * len(closes)
    if aligned is not None:
        for i, (price, spy) in enumerate(zip(closes, aligned)):
            if spy is not None and spy > 0 and price is not None:
                rs[i] = round(float(price) / float(spy), 6)
    panes = [
        {"id": "rsi", "label": "RSI", "kind": "rsi", "values": {"rsi": rsi_series(closes)}, "dates": list(dates)},
        {"id": "macd", "label": "MACD", "kind": "macd", "values": macd, "dates": list(dates)},
        {"id": "obv", "label": "OBV", "kind": "obv", "values": {"obv": vol["obv"]}, "dates": list(dates)},
        {"id": "clv", "label": "CLV", "kind": "clv", "values": {"clv": vol["clv"]}, "dates": list(dates)},
        {
            "id": "range_persistence",
            "label": "Range Persistence",
            "kind": "range",
            "values": {"position": range_position_series(closes, highs, lows)},
            "dates": list(dates),
        },
        {
            "id": "dollar_volume",
            "label": "Dollar volume",
            "kind": "dollar",
            "values": {"dollarVolume": vol["dollarVolume"]},
            "dates": list(dates),
        },
    ]
    if any(value is not None for value in rs):
        panes.insert(
            5,
            {"id": "spy_rs", "label": "SPY Relative Strength", "kind": "rs", "values": {"rs": rs}, "dates": list(dates)},
        )
    return panes


def _align_spy_closes(
    dates: Sequence[str],
    spy_closes: Sequence[float | None] | Mapping[str, float] | None,
) -> list[float | None] | None:
    if spy_closes is None:
        return None
    if isinstance(spy_closes, Mapping):
        aligned = [spy_closes.get(day) for day in dates]
    else:
        values = list(spy_closes)
        if len(values) != len(dates):
            return None
        aligned = []
        for value in values:
            try:
                number = float(value) if value is not None else None
            except (TypeError, ValueError):
                number = None
            aligned.append(number if number is not None and number > 0 else None)
        return aligned
    out: list[float | None] = []
    for value in aligned:
        try:
            number = float(value) if value is not None else None
        except (TypeError, ValueError):
            number = None
        out.append(number if number is not None and number > 0 else None)
    return out


def _ma_overlays(series: Mapping[str, list], data_through: str) -> list[dict[str, Any]]:
    closes = list(series.get("closes") or [])
    dates = list(series.get("dates") or [])
    overlays = []
    for window, layer_id in ((20, "ma20"), (50, "ma50"), (200, "ma200")):
        values = sma_series(closes, window)
        overlays.append(
            _overlay(
                overlay_id=layer_id,
                source_id="indicators",
                algorithm_version=TECHNICALS_VERSION,
                group="price",
                kind="ma",
                geometry={"type": "series", "window": window, "values": values, "dates": dates, "styleHint": "auto-pale"},
                status="forming",
                direction="neutral",
                shape_quality=1.0,
                display_priority=0.2,
                evidence={"sources": ["indicators"], "shapeQuality": 1.0, "volumeConfirmation": 0.5, "trendAlignment": 0.5, "recency": 1.0, "consensus": 1.0},
                formation_start=dates[0] if dates else data_through,
                formation_end=data_through,
                data_through=data_through,
                label=f"MA{window}",
                detail="same-series moving average",
            )
        )
    return overlays


def _pct_return(closes: Sequence[float], bars: int) -> float | None:
    if len(closes) <= bars or closes[-1 - bars] <= 0:
        return None
    return float(closes[-1] / closes[-1 - bars] - 1.0)


def _sma(closes: Sequence[float], window: int) -> float | None:
    if len(closes) < window or window <= 0:
        return None
    return sum(closes[-window:]) / window


def _intrinsic_row(
    *,
    series: Mapping[str, list],
    technicals: Mapping[str, Any],
    price_action: Mapping[str, Any],
    vol_price: Mapping[str, Any],
    spy_closes: Sequence[float | None] | Mapping[str, float] | None,
) -> dict[str, Any]:
    closes = list(series.get("closes") or [])
    volumes = list(series.get("volumes") or [])
    price = closes[-1] if closes else None
    sma20 = _sma(closes, 20)
    sma50 = _sma(closes, 50)
    sma200 = _sma(closes, 200)
    avg_vol20 = _sma(volumes, 20) if volumes else None
    rel_volume = None
    if volumes and avg_vol20 and avg_vol20 > 0:
        rel_volume = volumes[-1] / avg_vol20
    high_52w = max(closes[-252:]) if len(closes) >= 240 else None
    high_3m = max(closes[-63:]) if len(closes) >= 63 else None
    aligned = _align_spy_closes(list(series.get("dates") or []), spy_closes)
    spy_ret_63 = None
    if aligned is not None and len(aligned) > 63:
        spy_now = aligned[-1]
        spy_then = aligned[-1 - 63]
        if spy_now and spy_then and spy_then > 0:
            spy_ret_63 = float(spy_now / spy_then - 1.0)
    stock_ret_63 = _pct_return(closes, 63)
    ma_states = [price > average for average in (sma20, sma50, sma200) if price and average]
    macd = technicals.get("macd") if isinstance(technicals.get("macd"), Mapping) else {}
    return {
        "return_5d": _pct_return(closes, 5),
        "return_20d": _pct_return(closes, 20),
        "return_63d": stock_ret_63,
        "return_126d": _pct_return(closes, 126),
        "return_252d": _pct_return(closes, 252),
        "rs_spy_63d": (stock_ret_63 - spy_ret_63) if stock_ret_63 is not None and spy_ret_63 is not None else None,
        "dist_sma20": (price / sma20 - 1.0) if price and sma20 else None,
        "dist_sma50": (price / sma50 - 1.0) if price and sma50 else None,
        "dist_sma200": (price / sma200 - 1.0) if price and sma200 else None,
        "ma_alignment": (sum(1 for state in ma_states if state) / len(ma_states) * 100.0) if ma_states else None,
        "rsi14": technicals.get("rsi14"),
        "macd_direction": macd.get("direction_pct"),
        "rel_volume": rel_volume,
        "ath_proximity": (price / high_52w * 100.0) if price and high_52w else None,
        "price_action": price_action,
        "vol_price_match": vol_price,
        "follow_through": bool(len(closes) >= 5 and closes[-3] >= sum(closes[-20:]) / 20.0) if len(closes) >= 20 else False,
        "breakout_confirmed": bool(high_3m and price and price >= high_3m * 0.995 and (rel_volume or 0) >= 1.15),
    }


def _hist_frame(series: Mapping[str, list]) -> pd.DataFrame:
    times = list(series.get("times") or [])
    if not times:
        return pd.DataFrame()
    index = pd.DatetimeIndex([datetime.fromtimestamp(int(t), tz=timezone.utc) for t in times])
    return pd.DataFrame(
        {
            "Open": list(series.get("opens") or []),
            "High": list(series.get("highs") or []),
            "Low": list(series.get("lows") or []),
            "Close": list(series.get("closes") or []),
            "Volume": list(series.get("volumes") or [0.0] * len(times)),
        },
        index=index,
    )


def _family_from_intrinsic(name: str, score: float | None, detail: Mapping[str, Any] | None) -> dict[str, Any]:
    detail = detail or {}
    return {
        "id": name,
        "score": None if score is None else round(float(score), 2),
        "activeWeights": dict(detail.get("effective_weights") or {}),
        "contributions": dict(detail.get("contributions") or {}),
    }


def _strength_context(
    *,
    technicals: Mapping[str, Any],
    price_action: Mapping[str, Any],
    vol_price: Mapping[str, Any],
    series: Mapping[str, list],
    data_through: str,
    spy_closes: Sequence[float | None] | Mapping[str, float] | None = None,
    hist: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Family scores as context only. Final Strength Score never enters shapeQuality."""

    rsi = technicals.get("rsi_score")
    macd = (technicals.get("macd") or {}).get("direction_pct")
    trend_eff = technicals.get("trend_efficiency_63d")
    ma_slope = technicals.get("ma50_slope_pct_21d")
    row = _intrinsic_row(
        series=series,
        technicals=technicals,
        price_action=price_action,
        vol_price=vol_price,
        spy_closes=spy_closes,
    )
    frame = hist if hist is not None else _hist_frame(series)
    intrinsic = score_intrinsic(row, frame if not frame.empty else None, range_mode="disabled")
    families_raw = (intrinsic.get("factor_breakdown") or {}).get("factor_families") or {}
    details = (intrinsic.get("factor_breakdown") or {}).get("family_details") or {}
    note = "Family scores are context, not a win probability, and never enter shapeQuality."
    return {
        "snapshotDate": data_through,
        "note": note,
        "scoreVersion": intrinsic.get("score_version"),
        "finalScore": None,
        "globalPercentile": None,
        "sectorPercentile": None,
        "families": {
            "short": _family_from_intrinsic("short", families_raw.get("short"), details.get("short")),
            "mid": _family_from_intrinsic("mid", families_raw.get("mid"), details.get("mid")),
            "long": _family_from_intrinsic("long", families_raw.get("long"), details.get("long")),
            "trend": _family_from_intrinsic("trend", families_raw.get("trend"), details.get("trend")),
            "breakout": _family_from_intrinsic(
                "breakout",
                families_raw.get("breakout"),
                {"effective_weights": {}, "contributions": {"breakout": families_raw.get("breakout")}},
            ),
            "price_action": _family_from_intrinsic(
                "price_action",
                families_raw.get("price_action"),
                {"effective_weights": {}, "contributions": {"price_action": families_raw.get("price_action")}},
            ),
        },
        "trendAlignmentInputs": {
            "rsi14": technicals.get("rsi14"),
            "macd": technicals.get("macd"),
            "ma50Slope": ma_slope,
            "trendEfficiency": trend_eff,
            "rsiScore": rsi,
            "macdDirection": macd,
        },
    }


def _session_minute(epoch: int) -> tuple[str, int]:
    local = datetime.fromtimestamp(int(epoch), tz=timezone.utc).astimezone(_NY)
    return local.date().isoformat(), local.hour * 60 + local.minute


def _intraday_overlays(series: Mapping[str, list], data_through: str, chart_range: str) -> list[dict[str, Any]]:
    times = [int(t) for t in series.get("times") or []]
    dates = list(series.get("dates") or [])
    opens = list(series.get("opens") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    closes = list(series.get("closes") or [])
    volumes = list(series.get("volumes") or [0.0] * len(closes))
    n = len(closes)
    if n == 0 or chart_range not in _INTRADAY_RANGES:
        return []
    sessions: list[str] = []
    minutes: list[int] = []
    for epoch in times:
        day, minute = _session_minute(epoch)
        sessions.append(day)
        minutes.append(minute)
    last_day = sessions[-1]
    vwap_values: list[float | None] = [None] * n
    dollar_acc = 0.0
    volume_acc = 0.0
    session_start = 0
    for i in range(n):
        if i > 0 and sessions[i] != sessions[i - 1]:
            dollar_acc = 0.0
            volume_acc = 0.0
            session_start = i
        typical = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = float(volumes[i] if i < len(volumes) else 0.0)
        if vol > 0:
            dollar_acc += typical * vol
            volume_acc += vol
        if volume_acc > 0:
            vwap_values[i] = round(dollar_acc / volume_acc, 6)
    opening_start = 9 * 60 + 30
    opening_end = opening_start + 30
    opening_indexes = [
        i
        for i in range(n)
        if sessions[i] == last_day and opening_start <= minutes[i] < opening_end
    ]
    expected_bars = 6 if chart_range == "5m" else (2 if chart_range == "15m" else 0)
    opening_complete = bool(expected_bars and len(opening_indexes) >= expected_bars)
    opening_high = max(highs[i] for i in opening_indexes) if opening_indexes and opening_complete else None
    opening_low = min(lows[i] for i in opening_indexes) if opening_indexes and opening_complete else None
    last_vwap = next((value for value in reversed(vwap_values) if value is not None), None)
    hold_vwap = 0
    for i in range(n - 1, session_start - 1, -1):
        if last_vwap is None or closes[i] < last_vwap:
            break
        hold_vwap += 1
    hold_or = 0
    if opening_high is not None:
        for i in range(n - 1, session_start - 1, -1):
            if closes[i] < opening_high:
                break
            hold_or += 1
    last_span = highs[-1] - lows[-1] if n else 0.0
    last_clv = ((2 * closes[-1] - highs[-1] - lows[-1]) / last_span) if last_span > 0 else None
    prior_days = []
    seen = []
    for day in sessions:
        if day not in seen:
            seen.append(day)
    for day in seen[:-1]:
        prior_days.append(sum(float(volumes[i]) for i, session in enumerate(sessions) if session == day))
    today_volume = sum(float(volumes[i]) for i, session in enumerate(sessions) if session == last_day)
    tod_rvol = None
    if prior_days:
        median = sorted(prior_days)[len(prior_days) // 2]
        if median > 0:
            tod_rvol = round(today_volume / median, 4)
    start = dates[session_start] if dates else data_through
    overlays = [
        _overlay(
            overlay_id="vwap",
            source_id="breakouts",
            algorithm_version="intraday-vwap",
            group="price",
            kind="vwap",
            geometry={"type": "series", "values": vwap_values, "dates": dates, "styleHint": "auto-pale"},
            status="forming",
            direction="neutral",
            shape_quality=1.0,
            display_priority=0.45,
            evidence={
                "shapeQuality": 1.0,
                "volumeConfirmation": 0.5,
                "trendAlignment": 0.5,
                "recency": 1.0,
                "consensus": 1.0,
                "sources": ["intraday"],
                "holdBarsAboveVwap": hold_vwap,
                "rvolTimeOfDay": tod_rvol,
                "clv": None if last_clv is None else round(last_clv, 4),
            },
            formation_start=start,
            formation_end=data_through,
            data_through=data_through,
            label="VWAP",
            detail="session VWAP from typical price × volume",
        )
    ]
    if opening_high is not None and opening_low is not None:
        overlays.append(
            _overlay(
                overlay_id="opening-range",
                source_id="breakouts",
                algorithm_version="intraday-opening-range",
                group="event",
                kind="opening_range",
                geometry={
                    "type": "band",
                    "high": opening_high,
                    "low": opening_low,
                    "complete": opening_complete,
                    "styleHint": "auto-pale",
                },
                status="confirmed" if opening_complete else "forming",
                direction="neutral",
                shape_quality=0.7 if opening_complete else 0.4,
                display_priority=0.5,
                evidence={
                    "shapeQuality": 0.7 if opening_complete else 0.4,
                    "volumeConfirmation": 0.5,
                    "trendAlignment": 0.5,
                    "recency": 1.0,
                    "consensus": 1.0,
                    "sources": ["intraday"],
                    "holdBarsAboveOpeningRange": hold_or,
                    "rvolTimeOfDay": tod_rvol,
                    "clv": None if last_clv is None else round(last_clv, 4),
                },
                formation_start=start,
                formation_end=data_through,
                data_through=data_through,
                label="opening range",
                detail="first 30 minutes of the regular session",
            )
        )
    overlays.append(
        _overlay(
            overlay_id=f"intraday-hold:{data_through}",
            source_id="breakouts",
            algorithm_version="intraday-hold",
            group="event",
            kind="breakout",
            geometry={
                "type": "summary",
                "vwap": last_vwap,
                "openingRangeHigh": opening_high,
                "openingRangeLow": opening_low,
                "styleHint": "summary",
            },
            status="forming",
            direction="bullish" if hold_vwap >= 3 else "neutral",
            shape_quality=0.5,
            display_priority=0.4,
            evidence={
                "shapeQuality": 0.5,
                "volumeConfirmation": 0.5 if tod_rvol is None else min(1.0, max(0.0, (tod_rvol or 0) / 2.0)),
                "trendAlignment": 0.5,
                "recency": 1.0,
                "consensus": 1.0,
                "sources": ["intraday"],
                "holdBarsAboveVwap": hold_vwap,
                "holdBarsAboveOpeningRange": hold_or,
                "rvolTimeOfDay": tod_rvol,
                "clv": None if last_clv is None else round(last_clv, 4),
            },
            formation_start=start,
            formation_end=data_through,
            data_through=data_through,
            label="intraday hold",
            detail="VWAP / opening-range hold bars, time-of-day RVOL, last-bar CLV",
        )
    )
    return overlays


def series_from_chart_bars(
    bars: Sequence[Mapping[str, Any]],
    chart_range: str,
) -> dict[str, list] | None:
    times: list[int] = []
    dates: list[str] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    for bar in bars:
        if bar.get("ext") is True or bar.get("quote_only") is True:
            continue
        try:
            raw_t = bar["t"]
            if isinstance(raw_t, str):
                parsed = datetime.fromisoformat(raw_t.replace("Z", "+00:00"))
                t = int(parsed.timestamp())
            else:
                t = int(raw_t)
                if t > 100_000_000_000:
                    t //= 1000
            close = float(bar["c"])
            open_ = float(bar.get("o") or close)
            high = float(bar.get("h") or close)
            low = float(bar.get("l") or close)
            volume = max(0.0, float(bar.get("v") or 0))
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0:
            continue
        times.append(t)
        if chart_range in _INTRADAY_RANGES:
            dates.append(str(t))
        else:
            dates.append(datetime.fromtimestamp(t, tz=timezone.utc).astimezone(_NY).date().isoformat())
        opens.append(open_)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        volumes.append(volume)
    if not closes:
        return None
    return {
        "times": times,
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
    }


def assemble_intraday_analysis(
    bars: Sequence[Mapping[str, Any]],
    *,
    ticker: str = "",
    chart_range: str,
    adjustment: str = "raw",
) -> dict[str, Any] | None:
    """Minute VWAP / opening range / RVOL / CLV / hold bars, on demand."""

    if chart_range not in _INTRADAY_RANGES:
        return None
    series = series_from_chart_bars(bars, chart_range)
    if series is None:
        return None
    data_through = series["dates"][-1]
    return assemble_chart_analysis(
        series=series,
        data_through=data_through,
        ticker=ticker,
        chart_range=chart_range,
        adjustment=adjustment,
        auto_patterns=[],
    )


def assemble_chart_analysis(
    *,
    series: Mapping[str, list],
    data_through: str,
    ticker: str = "",
    chart_range: str = "1d",
    adjustment: str = "raw",
    price_action: Mapping[str, Any] | None = None,
    vol_price: Mapping[str, Any] | None = None,
    base: Mapping[str, Any] | None = None,
    base_state: Mapping[str, Any] | None = None,
    technicals: Mapping[str, Any] | None = None,
    auto_patterns: Sequence[Mapping[str, Any]] | None = None,
    spy_closes: Sequence[float | None] | Mapping[str, float] | None = None,
    series_break_at: str | None = None,
    hist: pd.DataFrame | None = None,
) -> dict[str, Any]:
    dates = list(series.get("dates") or [])
    closes = list(series.get("closes") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    fingerprint = bar_fingerprint(dates, closes, highs, lows)
    price_action = price_action or {}
    vol_price = vol_price or {}
    technicals = technicals or {}
    if auto_patterns is None:
        auto_patterns = detect_auto_patterns(series, data_through=data_through)
    overlays: list[dict[str, Any]] = []
    if chart_range in _INTRADAY_RANGES:
        overlays.extend(_intraday_overlays(series, data_through, chart_range))
    else:
        overlays.extend(_ma_overlays(series, data_through))
        overlays.extend(_price_action_overlays(price_action, series, data_through))
        overlays.extend(_base_overlays(base, base_state, data_through))
        overlays.extend(_pattern_overlays(auto_patterns, data_through))
        overlays.extend(_vol_price_overlays(vol_price, data_through))
        overlays.extend(_breakout_overlays(base, base_state, data_through, chart_range=chart_range))
    return {
        "version": BUNDLE_VERSION,
        "registryVersion": LAYER_REGISTRY_VERSION,
        "ticker": ticker,
        "range": chart_range,
        "adjustment": adjustment,
        "dataThrough": data_through,
        "barFingerprint": fingerprint,
        "barCount": len(closes),
        "lastClose": round(float(closes[-1]), 6) if closes else None,
        "seriesBreakAt": series_break_at,
        "overlays": overlays,
        "indicatorPanes": [] if chart_range in _INTRADAY_RANGES else _indicator_panes(series, spy_closes=spy_closes, dates=dates),
        "strengthContext": None
        if chart_range in _INTRADAY_RANGES
        else _strength_context(
            technicals=technicals,
            price_action=price_action,
            vol_price=vol_price,
            series=series,
            data_through=data_through,
            spy_closes=spy_closes,
            hist=hist,
        ),
        "autoPatterns": list(auto_patterns),
    }


__all__ = [
    "BUNDLE_VERSION",
    "assemble_chart_analysis",
    "assemble_intraday_analysis",
    "bar_fingerprint",
    "consecutive_swing_labels",
    "fingerprint_raw",
    "fnv1a64_hex16",
    "series_from_chart_bars",
]
