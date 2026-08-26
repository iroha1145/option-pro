"""Pure-data ChartAnalysisBundle assembler.

Algorithms never return an Apache ECharts option. Strength Scanner is not
imported — family scores here are summaries of already-computed per-stock
series, not Radar ranks or market-fit.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping, Sequence

from app.services.technical.auto_patterns import (
    ALGORITHM_VERSION as AUTO_PATTERNS_VERSION,
    apply_volume_confirmation,
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
    trend_efficiency,
)
from app.services.technical.layer_registry import LAYER_REGISTRY_VERSION

BUNDLE_VERSION = "optix-chart-analysis-v1"


def bar_fingerprint(
    dates: Sequence[str],
    closes: Sequence[float],
    highs: Sequence[float] | None = None,
    lows: Sequence[float] | None = None,
) -> str:
    """Stable id for the analysis series. Frontend must match before painting."""

    n = len(closes)
    acc = 0
    for close in closes:
        acc = (acc * 1_000_003 + int(round(float(close) * 10_000))) % (2**64)
    first = dates[0] if dates else ""
    last = dates[-1] if dates else ""
    last_close = f"{closes[-1]:.6f}" if n else "0"
    last_high = f"{highs[-1]:.6f}" if highs and n else "0"
    last_low = f"{lows[-1]:.6f}" if lows and n else "0"
    raw = f"{n}|{first}|{last}|{last_close}|{last_high}|{last_low}|{acc:x}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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


def _price_action_overlays(
    price_action: Mapping[str, Any],
    series: Mapping[str, list],
    data_through: str,
) -> list[dict[str, Any]]:
    overlays: list[dict[str, Any]] = []
    dates: list[str] = list(series.get("dates") or [])
    times: list[int] = list(series.get("times") or [])
    for point in price_action.get("swing_highs") or []:
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
                label="HH" if (price_action.get("structure") or "").startswith("up") else "LH",
                detail="confirmed fractal swing",
            )
        )
    for point in price_action.get("swing_lows") or []:
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
                label="HL" if "hl" in str(price_action.get("structure") or "") else "LL",
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
    rs: list[float | None] = [None] * len(closes)
    if spy_closes and len(spy_closes) == len(closes):
        for i, (price, spy) in enumerate(zip(closes, spy_closes)):
            if spy and spy > 0:
                rs[i] = round(price / spy, 6)
    return [
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
        {"id": "spy_rs", "label": "SPY Relative Strength", "kind": "rs", "values": {"rs": rs}, "dates": list(dates)},
        {
            "id": "dollar_volume",
            "label": "Dollar volume",
            "kind": "dollar",
            "values": {"dollarVolume": vol["dollarVolume"]},
            "dates": list(dates),
        },
    ]


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


def _strength_context(
    *,
    technicals: Mapping[str, Any],
    price_action: Mapping[str, Any],
    vol_price: Mapping[str, Any],
    series: Mapping[str, list],
    data_through: str,
) -> dict[str, Any]:
    """Family scores as context only. Final Strength Score never enters shapeQuality."""

    rsi = technicals.get("rsi_score")
    macd = (technicals.get("macd") or {}).get("direction_pct")
    trend_eff = technicals.get("trend_efficiency_63d")
    ma_slope = technicals.get("ma50_slope_pct_21d")
    pa_score = price_action.get("score")
    breakout_adj = vol_price.get("breakout_quality_adjustment") if vol_price.get("status") == "active" else None

    def family(name: str, score: float | None, contributions: dict[str, float | None]) -> dict[str, Any]:
        active = {key: value for key, value in contributions.items() if value is not None}
        weight = round(1.0 / len(active), 4) if active else None
        return {
            "id": name,
            "score": None if score is None else round(float(score), 2),
            "activeWeights": {key: weight for key in active} if weight is not None else {},
            "contributions": {key: None if value is None else round(float(value), 2) for key, value in contributions.items()},
        }

    closes = list(series.get("closes") or [])
    efficiency = trend_efficiency(closes)
    return {
        "snapshotDate": data_through,
        "note": "Family scores are context, not a win probability, and never enter shapeQuality.",
        "finalScore": None,
        "globalPercentile": None,
        "sectorPercentile": None,
        "families": {
            "short": family("short", rsi, {"rsi14": rsi}),
            "mid": family("mid", None if macd is None else max(0.0, min(100.0, 50.0 + float(macd) * 8)), {"macd_direction": macd}),
            "long": family("long", None, {"ma50_slope": ma_slope}),
            "trend": family(
                "trend",
                None if efficiency is None else round(float(efficiency) * 100.0, 2),
                {"trend_efficiency": None if efficiency is None else round(float(efficiency) * 100.0, 2), "ma50_slope": ma_slope},
            ),
            "breakout": family("breakout", None if breakout_adj is None else max(0.0, min(100.0, 50.0 + float(breakout_adj))), {"breakout_quality_adjustment": breakout_adj}),
            "price_action": family("price_action", pa_score, {"price_action": pa_score}),
        },
        "trendAlignmentInputs": {
            "rsi14": technicals.get("rsi14"),
            "macd": technicals.get("macd"),
            "ma50Slope": ma_slope,
            "trendEfficiency": trend_eff,
        },
    }


def _volume_confirmation_from_setup(vol_price: Mapping[str, Any]) -> float:
    if vol_price.get("status") != "active":
        return 0.5
    setup = str(vol_price.get("setup_type") or "")
    if setup == "absorption_bullish":
        return 0.82
    if setup == "absorption_bearish":
        return 0.28
    if setup == "balanced_compression":
        return 0.62
    if setup == "vacuum":
        return 0.30
    return 0.5


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
    spy_closes: Sequence[float] | None = None,
    series_break_at: str | None = None,
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
    vol_conf = _volume_confirmation_from_setup(vol_price)
    scored_patterns = [apply_volume_confirmation(row, vol_conf) for row in auto_patterns]
    overlays: list[dict[str, Any]] = []
    overlays.extend(_ma_overlays(series, data_through))
    overlays.extend(_price_action_overlays(price_action, series, data_through))
    overlays.extend(_base_overlays(base, base_state, data_through))
    overlays.extend(_pattern_overlays(scored_patterns, data_through))
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
        "indicatorPanes": _indicator_panes(series, spy_closes=spy_closes, dates=dates),
        "strengthContext": _strength_context(
            technicals=technicals,
            price_action=price_action,
            vol_price=vol_price,
            series=series,
            data_through=data_through,
        ),
        "autoPatterns": scored_patterns,
    }


__all__ = [
    "BUNDLE_VERSION",
    "assemble_chart_analysis",
    "bar_fingerprint",
]
