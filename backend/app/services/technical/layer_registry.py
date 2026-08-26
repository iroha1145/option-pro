"""Data-only layer catalog. The menu is generated from this; algorithms do not
return ECharts options. Frontend mirrors the ids in chart-drawings/analysis/registry.ts.
"""

from __future__ import annotations

from typing import Any

LAYER_REGISTRY_VERSION = "optix-layer-registry-v1"

# group -> layers the 算法与图层 menu lists
LAYERS: list[dict[str, Any]] = [
    {"id": "ma20", "group": "price", "kind": "ma", "label": "MA20"},
    {"id": "ma50", "group": "price", "kind": "ma", "label": "MA50"},
    {"id": "ma200", "group": "price", "kind": "ma", "label": "MA200"},
    {"id": "swings", "group": "price", "kind": "swing", "label": "摆动点"},
    {"id": "support_resistance", "group": "price", "kind": "level", "label": "支撑阻力"},
    {"id": "bases", "group": "price", "kind": "box", "label": "整理区"},
    {"id": "pivots", "group": "price", "kind": "pivot", "label": "pivot/invalidation"},
    {"id": "auto_patterns", "group": "price", "kind": "pattern", "label": "自动趋势线/通道/三角形/楔形"},
    {"id": "candles", "group": "event", "kind": "candle", "label": "K线形态"},
    {"id": "traps", "group": "event", "kind": "trap", "label": "Spring/Upthrust"},
    {"id": "breakouts", "group": "event", "kind": "breakout", "label": "突破触发/确认/回踩/失败"},
    {"id": "rsi", "group": "pane", "kind": "rsi", "label": "RSI"},
    {"id": "macd", "group": "pane", "kind": "macd", "label": "MACD"},
    {"id": "obv", "group": "pane", "kind": "obv", "label": "OBV"},
    {"id": "clv", "group": "pane", "kind": "clv", "label": "CLV"},
    {"id": "range_persistence", "group": "pane", "kind": "range", "label": "Range Persistence"},
    {"id": "spy_rs", "group": "pane", "kind": "rs", "label": "SPY Relative Strength"},
    {"id": "strength_short", "group": "strength", "kind": "score", "label": "short"},
    {"id": "strength_mid", "group": "strength", "kind": "score", "label": "mid"},
    {"id": "strength_long", "group": "strength", "kind": "score", "label": "long"},
    {"id": "strength_trend", "group": "strength", "kind": "score", "label": "trend"},
    {"id": "strength_breakout", "group": "strength", "kind": "score", "label": "breakout"},
    {"id": "strength_price_action", "group": "strength", "kind": "score", "label": "price_action"},
    {"id": "strength_percentiles", "group": "strength", "kind": "score", "label": "global/sector percentile"},
    {"id": "strength_contributions", "group": "strength", "kind": "score", "label": "factor contributions"},
]

PRESETS: dict[str, dict[str, Any]] = {
    "minimal": {
        "label": "极简",
        "enabled": ["ma20", "auto_patterns"],
        "maxPatterns": 3,
        "maxLabels": 6,
        "minShapeQuality": 0.70,
        "onlyActive": True,
        "showInvalidated": False,
        "labelDensity": 0.4,
    },
    "structure": {
        "label": "结构分析",
        "enabled": [
            "swings",
            "support_resistance",
            "bases",
            "pivots",
            "auto_patterns",
            "candles",
            "traps",
        ],
        "maxPatterns": 8,
        "maxLabels": 10,
        "minShapeQuality": 0.55,
        "onlyActive": False,
        "showInvalidated": False,
        "labelDensity": 0.7,
    },
    "breakout": {
        "label": "突破交易",
        "enabled": ["bases", "pivots", "breakouts", "auto_patterns", "obv", "clv"],
        "maxPatterns": 6,
        "maxLabels": 8,
        "minShapeQuality": 0.55,
        "onlyActive": True,
        "showInvalidated": True,
        "labelDensity": 0.6,
    },
    "momentum": {
        "label": "动量",
        "enabled": ["ma20", "ma50", "ma200", "rsi", "macd", "spy_rs", "strength_trend"],
        "maxPatterns": 0,
        "maxLabels": 4,
        "minShapeQuality": 0.70,
        "onlyActive": True,
        "showInvalidated": False,
        "labelDensity": 0.4,
    },
    "volume": {
        "label": "量价",
        "enabled": ["obv", "clv", "range_persistence", "traps", "breakouts"],
        "maxPatterns": 4,
        "maxLabels": 6,
        "minShapeQuality": 0.55,
        "onlyActive": False,
        "showInvalidated": False,
        "labelDensity": 0.5,
    },
    "all": {
        "label": "全部",
        "enabled": [layer["id"] for layer in LAYERS],
        "maxPatterns": 12,
        "maxLabels": 16,
        "minShapeQuality": 0.50,
        "onlyActive": False,
        "showInvalidated": True,
        "labelDensity": 1.0,
    },
}

ADVANCED_DEFAULTS = {
    "minShapeQuality": 0.70,
    "onlyActive": True,
    "showInvalidated": False,
    "maxPatterns": 3,
    "labelDensity": 0.4,
}


def layer_by_id(layer_id: str) -> dict[str, Any] | None:
    for layer in LAYERS:
        if layer["id"] == layer_id:
            return layer
    return None


def preset_enabled(preset: str) -> set[str]:
    row = PRESETS.get(preset) or PRESETS["minimal"]
    return set(row["enabled"])


__all__ = [
    "ADVANCED_DEFAULTS",
    "LAYERS",
    "LAYER_REGISTRY_VERSION",
    "PRESETS",
    "layer_by_id",
    "preset_enabled",
]
