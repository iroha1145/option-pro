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
    {"id": "breakouts", "group": "event", "kind": "breakout", "label": "突破触发/测试/失败"},
    {"id": "rsi", "group": "pane", "kind": "rsi", "label": "RSI"},
    {"id": "macd", "group": "pane", "kind": "macd", "label": "MACD"},
    {"id": "obv", "group": "pane", "kind": "obv", "label": "OBV"},
    {"id": "clv", "group": "pane", "kind": "clv", "label": "CLV"},
    {"id": "range_persistence", "group": "pane", "kind": "range", "label": "60日区间位置"},
    {"id": "spy_rs", "group": "pane", "kind": "rs", "label": "SPY Relative Strength"},
    # strength_* 图层曾列在这里，但既没有后端 pane 也没有绘制路径，勾了什么都不会发生，
    # 已随前端 registry 一起删除。要恢复请连同 pane/绘制一起加，parity 测试会盯住两边。
]

# 各预设的质量门槛统一到 0.45（= 检测器闸门）：高于它等于把后端已放行的形态
# 再滤一遍，实测会滤成 0 条。「极简 vs 全部」的差异交给 maxPatterns 的条数上限，
# 不靠一条会把所有形态一起滤光的质量线。
PRESETS: dict[str, dict[str, Any]] = {
    "minimal": {
        "label": "极简",
        "enabled": ["ma20", "auto_patterns"],
        "maxPatterns": 3,
        "maxLabels": 6,
        "minShapeQuality": 0.45,
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
        "minShapeQuality": 0.45,
        "onlyActive": False,
        "showInvalidated": False,
        "labelDensity": 0.7,
    },
    "breakout": {
        "label": "突破交易",
        "enabled": ["bases", "pivots", "breakouts", "auto_patterns", "obv", "clv"],
        "maxPatterns": 6,
        "maxLabels": 8,
        "minShapeQuality": 0.45,
        "onlyActive": True,
        "showInvalidated": True,
        "labelDensity": 0.6,
    },
    "momentum": {
        "label": "动量",
        "enabled": ["ma20", "ma50", "ma200", "rsi", "macd", "spy_rs"],
        "maxPatterns": 0,
        "maxLabels": 4,
        "minShapeQuality": 0.45,
        "onlyActive": True,
        "showInvalidated": False,
        "labelDensity": 0.4,
    },
    "volume": {
        "label": "量价",
        "enabled": ["obv", "clv", "range_persistence", "traps", "breakouts"],
        "maxPatterns": 4,
        "maxLabels": 6,
        "minShapeQuality": 0.45,
        "onlyActive": False,
        "showInvalidated": False,
        "labelDensity": 0.5,
    },
    "all": {
        "label": "全部",
        "enabled": [layer["id"] for layer in LAYERS],
        "maxPatterns": 12,
        "maxLabels": 16,
        "minShapeQuality": 0.45,
        "onlyActive": False,
        "showInvalidated": True,
        "labelDensity": 1.0,
    },
}

ADVANCED_DEFAULTS = {
    "minShapeQuality": 0.45,
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
