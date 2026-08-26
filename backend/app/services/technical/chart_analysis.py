"""Pure-data ChartAnalysisBundle assembler.

Algorithms never return an Apache ECharts option. Strength Scanner is not
imported — family scores here are summaries of already-computed per-stock
series, not Radar ranks or market-fit.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.strength.features import _feature_row as build_feature_row
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
# 指纹口径的名字：换算法/换字段就要改这个版本，两端各钉一个字面 digest 向量。
FINGERPRINT_ALGORITHM = "sha256-bar-ohlcv-v1"
_NY = ZoneInfo("America/New_York")
_INTRADAY_RANGES = {"5m", "15m", "1h"}


def _fmt6(value: Any) -> str:
    scaled = int(float(value) * 1_000_000 + (0.5 if float(value) >= 0 else -0.5))
    return f"{scaled / 1_000_000:.6f}"


def canonical_bar_payload(series: Mapping[str, list]) -> str:
    """One line per analysis bar: timestamp|open|high|low|close|volume|ext|quote_only."""

    closes = list(series.get("closes") or [])
    n = len(closes)
    times = list(series.get("times") or [0] * n)
    opens = list(series.get("opens") or closes)
    highs = list(series.get("highs") or closes)
    lows = list(series.get("lows") or closes)
    volumes = list(series.get("volumes") or [0.0] * n)
    exts = list(series.get("ext") or [False] * n)
    quotes = list(series.get("quote_only") or [False] * n)
    lines: list[str] = []
    for i in range(n):
        lines.append(
            f"{int(times[i] if i < len(times) else 0)}|"
            f"{_fmt6(opens[i] if i < len(opens) else closes[i])}|"
            f"{_fmt6(highs[i] if i < len(highs) else closes[i])}|"
            f"{_fmt6(lows[i] if i < len(lows) else closes[i])}|"
            f"{_fmt6(closes[i])}|"
            f"{_fmt6(volumes[i] if i < len(volumes) else 0.0)}|"
            f"{1 if i < len(exts) and exts[i] else 0}|"
            f"{1 if i < len(quotes) and quotes[i] else 0}"
        )
    return "\n".join(lines)


def bar_fingerprint(series: Mapping[str, list]) -> str:
    """SHA-256 of every analysis bar's OHLCV and flags. Frontend must match before painting."""

    return hashlib.sha256(canonical_bar_payload(series).encode("utf-8")).hexdigest()


def fingerprint_meta(series: Mapping[str, list]) -> dict[str, Any]:
    """描述「到底哈了哪些 bar」，让不匹配可修复而不是整张图默默消失。

    后端会丢掉 OHLC 自相矛盾/非有限的坏 bar，前端只镜像 ext/quote_only 过滤：
    一根坏 bar 就让指纹永久对不上。带上根数与首尾 barKey，客户端能把自己的
    窗口对齐回来重算，对不上也知道该报什么。
    """

    dates = list(series.get("dates") or [])
    return {
        "fingerprintAlgorithm": FINGERPRINT_ALGORITHM,
        "barFingerprint": bar_fingerprint(series),
        "barCount": len(series.get("closes") or []),
        "firstBarDate": dates[0] if dates else None,
        "lastBarDate": dates[-1] if dates else None,
    }


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
        # 「触碰 n 次」这枚 chip 读的是 evidence.touches，而 touches 原本只在
        # 顶层 pattern 行上——不带进来这枚 chip 永远渲染不出。
        if row.get("touches") is not None:
            evidence["touches"] = int(row["touches"])
        overlays.append(
            _overlay(
                overlay_id=str(row["id"]),
                source_id="auto_patterns",
                algorithm_version=str(row.get("algorithmVersion") or AUTO_PATTERNS_VERSION),
                group="price",
                kind=str(row["kind"]),
                geometry={
                    "type": "rails",
                    "anchors": list(row.get("fitAnchors") or row.get("anchors") or []),
                    "fitAnchors": list(row.get("fitAnchors") or row.get("anchors") or []),
                    "touchAnchors": list(row.get("touchAnchors") or []),
                    "supportRail": row.get("supportRail"),
                    "resistanceRail": row.get("resistanceRail"),
                    "slope": row.get("slope"),
                    "intercept": row.get("intercept"),
                    "supportSlope": row.get("supportSlope"),
                    "supportIntercept": row.get("supportIntercept"),
                    "resistanceSlope": row.get("resistanceSlope"),
                    "resistanceIntercept": row.get("resistanceIntercept"),
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
    # 这里只算注册表里真有图层的两条线。dollarVolume 曾经也在下发，但既不在
    # layer_registry 也不在前端注册表里，前端 VALID_IDS 一律丢掉——要上必须
    # 连同注册表条目和 i18n 文案一起有意加。
    closes = list(series.get("closes") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    volumes = list(series.get("volumes") or [0.0] * len(closes))
    n = len(closes)
    obv: list[float | None] = [None] * n
    clv: list[float | None] = [None] * n
    running = 0.0
    for i in range(n):
        vol = float(volumes[i] if i < len(volumes) else 0.0)
        if i > 0:
            if closes[i] > closes[i - 1]:
                running += vol
            elif closes[i] < closes[i - 1]:
                running -= vol
        obv[i] = round(running, 4)
        span = highs[i] - lows[i] if i < len(highs) and i < len(lows) else 0.0
        if span > 0:
            clv[i] = round((2 * closes[i] - highs[i] - lows[i]) / span, 4)
    return {"obv": obv, "clv": clv}


def _warmup_len(values: Sequence[float | None]) -> int:
    """Leading Nones — the warmup a series legitimately cannot fill."""

    count = 0
    for value in values:
        if value is not None:
            break
        count += 1
    return count


def _offset_series(values: Sequence[float | None]) -> tuple[int, list[float | None]]:
    """暖机段的 None 换成一个索引偏移；日期整包只发一份，不随每条线复制。"""

    start = _warmup_len(values)
    if start >= len(values):
        return 0, []
    return start, list(values[start:])


def _pane(
    pane_id: str,
    label: str,
    kind: str,
    values: Mapping[str, Sequence[float | None]],
) -> dict[str, Any]:
    arrays = {key: list(series) for key, series in values.items()}
    length = max((len(series) for series in arrays.values()), default=0)
    # 同一副图里几条线共用一个 startIndex，取最短的暖机长度，索引才对得齐。
    start = min((_warmup_len(series) for series in arrays.values()), default=0)
    if start >= length:
        start = 0
    return {
        "id": pane_id,
        "label": label,
        "kind": kind,
        "startIndex": start,
        "values": {key: series[start:] for key, series in arrays.items()},
    }


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
        base_stock: float | None = None
        base_spy: float | None = None
        for i, (price, spy) in enumerate(zip(closes, aligned)):
            if price is None or spy is None or price <= 0 or spy <= 0:
                continue
            if base_stock is None or base_spy is None:
                base_stock = float(price)
                base_spy = float(spy)
                rs[i] = 100.0
                continue
            rs[i] = round(
                100.0 * (float(price) / base_stock) / (float(spy) / base_spy),
                6,
            )
    panes = [
        _pane("rsi", "RSI", "rsi", {"rsi": rsi_series(closes)}),
        _pane("macd", "MACD", "macd", macd),
        _pane("obv", "OBV", "obv", {"obv": vol["obv"]}),
        _pane("clv", "CLV", "clv", {"clv": vol["clv"]}),
        _pane(
            "range_persistence",
            "60日区间位置",
            "range",
            {"position": range_position_series(closes, highs, lows)},
        ),
    ]
    if any(value is not None for value in rs):
        panes.append(_pane("spy_rs", "SPY Relative Strength", "rs", {"rs": rs}))
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
        start, values = _offset_series(sma_series(closes, window))
        overlays.append(
            _overlay(
                overlay_id=layer_id,
                source_id="indicators",
                algorithm_version=TECHNICALS_VERSION,
                group="price",
                kind="ma",
                geometry={
                    "type": "series",
                    "window": window,
                    "values": values,
                    "startIndex": start,
                    "styleHint": "auto-pale",
                },
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


def _intrinsic_row(
    *,
    series: Mapping[str, list],
    hist: pd.DataFrame,
    ticker: str,
    spy_closes: Sequence[float | None] | Mapping[str, float] | None,
) -> dict[str, Any] | None:
    """雷达与详情图共用的那一份特征行（strength/features），不是各抄一份。

    抄过一次就漂了：follow_through 用 closes[-3] 而雷达用最近三根的最小值，
    atr_pct 与 avg_dollar_volume_20d 干脆没给——score_intrinsic 会读它们，
    缺了就被静默重归一；补齐字段的第二份抄写连雷达对每个因子的定点舍入都
    跟不上，同一支票同一天两个界面仍差几分。features 是中立模块（不含全市场
    扫描/排名/market-fit），个股图路径 import 它不违反「不跑 Strength Scanner」。
    缺 SPY 就给空表，与扫描器缺基准时同一条路径。
    """

    aligned = _align_spy_closes(list(series.get("dates") or []), spy_closes)
    spy = (
        pd.DataFrame({"Close": aligned}, index=hist.index)
        if aligned is not None and len(aligned) == len(hist.index)
        else pd.DataFrame()
    )
    return build_feature_row(ticker or "", hist, spy, {})


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
    series: Mapping[str, list],
    data_through: str,
    ticker: str = "",
    spy_closes: Sequence[float | None] | Mapping[str, float] | None = None,
    hist: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Family scores as context only. Final Strength Score never enters shapeQuality.

    price_action / vol_price 不再从外面传：共用的特征行从同一份 hist 现算，
    外面那两份副本反而是漂移源。
    """

    rsi = technicals.get("rsi_score")
    macd = (technicals.get("macd") or {}).get("direction_pct")
    trend_eff = technicals.get("trend_efficiency_63d")
    ma_slope = technicals.get("ma50_slope_pct_21d")
    frame = hist if hist is not None else _hist_frame(series)
    row = (
        _intrinsic_row(series=series, hist=frame, ticker=ticker, spy_closes=spy_closes)
        if not frame.empty
        else None
    )
    # 样本不足 63 根时雷达同样构造不出特征行：families 全 None，不编。
    intrinsic = score_intrinsic(row, frame, range_mode="disabled") if row is not None else {}
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
    # 1h 没有 5m 序列就不能发明 opening_range：一根 1h K 盖不住前 30 分钟。
    if chart_range == "1h":
        expected_bars = 0
        opening_indexes = []
    opening_complete = bool(expected_bars and len(opening_indexes) >= expected_bars)
    opening_high = max(highs[i] for i in opening_indexes) if opening_indexes and opening_complete else None
    opening_low = min(lows[i] for i in opening_indexes) if opening_indexes and opening_complete else None
    session_start_key = dates[session_start] if dates and 0 <= session_start < len(dates) else None
    session_end_key = dates[-1] if dates else None
    last_vwap = next((value for value in reversed(vwap_values) if value is not None), None)
    hold_vwap = 0
    for i in range(n - 1, session_start - 1, -1):
        vwap_i = vwap_values[i]
        if vwap_i is None or closes[i] < vwap_i:
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
    tod_rvol = None
    from app.services.breakouts.feature_engine import compute_time_of_day_rvol
    from app.services.breakouts.models import MarketSession, TemporalCutoff

    index = pd.DatetimeIndex([datetime.fromtimestamp(int(t), tz=timezone.utc) for t in times])
    frame = pd.DataFrame(
        {
            "Open": opens if len(opens) == n else closes,
            "High": highs,
            "Low": lows,
            "Close": closes,
            "Volume": volumes if len(volumes) == n else [0.0] * n,
        },
        index=index,
    )
    event_at = index[-1].to_pydatetime()
    if event_at.tzinfo is None:
        event_at = event_at.replace(tzinfo=timezone.utc)
    cutoff = TemporalCutoff(
        event_at=event_at,
        include_current_bar=True,
        session=MarketSession.REGULAR,
    )
    rvol = compute_time_of_day_rvol(frame, cutoff)
    raw_rvol = rvol.get("rvol_time_of_day")
    if raw_rvol is not None:
        tod_rvol = round(float(raw_rvol), 4)
    start = dates[session_start] if dates else data_through
    overlays = [
        _overlay(
            overlay_id="vwap",
            source_id="breakouts",
            algorithm_version="intraday-vwap",
            group="price",
            kind="vwap",
            geometry={
                "type": "series",
                "values": vwap_values,
                "startIndex": 0,
                "styleHint": "auto-pale",
            },
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
                    "sessionStartBarKey": session_start_key,
                    "sessionEndBarKey": session_end_key,
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
        # 指纹 + 「到底哈了哪一段」的元数据（根数/首末 bar），见 fingerprint_meta。
        **fingerprint_meta(series),
        "lastClose": round(float(closes[-1]), 6) if closes else None,
        "seriesBreakAt": series_break_at,
        # 日期整包只发这一份；overlay 几何与副图各自带 startIndex 索引进来，
        # 而不是每条线复制一遍这 ~500 个日期。
        "dates": dates,
        "overlays": overlays,
        "indicatorPanes": [] if chart_range in _INTRADAY_RANGES else _indicator_panes(series, spy_closes=spy_closes, dates=dates),
        "strengthContext": None
        if chart_range in _INTRADAY_RANGES
        else _strength_context(
            technicals=technicals,
            series=series,
            data_through=data_through,
            ticker=ticker,
            spy_closes=spy_closes,
            hist=hist,
        ),
        # 形态只以 overlays 一种形态下发；autoPatterns 曾在这里再发一份，无人读。
    }


__all__ = [
    "BUNDLE_VERSION",
    "FINGERPRINT_ALGORITHM",
    "assemble_chart_analysis",
    "assemble_intraday_analysis",
    "bar_fingerprint",
    "canonical_bar_payload",
    "consecutive_swing_labels",
    "fingerprint_meta",
    "series_from_chart_bars",
]
