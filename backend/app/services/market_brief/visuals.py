"""首页「市场报告」的图表数据：读取时从该次运行记录的证据包里现取，不经模型、不另存。

只输出标签、数字与日期，不带证据原文、新闻或信号说明。某一块缺失或数值不合格时
那张图为 None，不补值也不猜；四张图都画不出来时整体为 None，前端显示文字版。
证据包本来就随运行记录落盘，所以加这个功能之前生成的报告同样能画。
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone
from typing import Any, Iterator, Mapping

# 只画美股指数，按这个顺序；标签用界面词典里已有的短名，前端再翻译。
US_INDEX_LABELS: Mapping[str, str] = {
    "idx:^GSPC": "标普500",
    "idx:^IXIC": "纳指综合",
    "idx:^DJI": "道琼斯",
    "idx:^RUT": "罗素2000",
}
# 合理范围之外的读数当作坏数据丢掉：指数单日涨跌幅（%）、主题一个月超额收益（百分点）。
MAX_INDEX_CHANGE_PERCENT = 30.0
MAX_THEME_EXCESS_POINTS = 300.0
MAX_THEME_ROWS = 10
MAX_MACRO_ROWS = 12
MAX_BREADTH_TOTAL = 100
_LABEL_CHARS = 40
_THEME_ID = re.compile(r"^theme:[A-Za-z0-9_.\-]{1,90}$")
_MACRO_ID = re.compile(r"^macro:[A-Za-z0-9_.\-]{1,90}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$")


def _number(value: Any, *, low: float, high: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and low <= number <= high else None


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _label(value: Any) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text[:_LABEL_CHARS] if text else None


def _day(value: Any) -> str | None:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(block: Any) -> list[Mapping[str, Any]]:
    rows = block.get("rows") if isinstance(block, Mapping) else None
    return [row for row in rows if isinstance(row, Mapping)] if isinstance(rows, list) else []


def market_chart(evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    """美股指数当日涨跌幅（%）。"""

    block = evidence.get("indices")
    values: dict[str, float] = {}
    for row in _rows(block):
        evidence_id = row.get("id")
        if evidence_id not in US_INDEX_LABELS or evidence_id in values:
            continue
        value = _number(row.get("change_percent"), low=-MAX_INDEX_CHANGE_PERCENT, high=MAX_INDEX_CHANGE_PERCENT)
        if value is not None:
            values[evidence_id] = value
    if not values:
        return None
    return {
        "as_of": _timestamp(block.get("as_of")),
        "stale": block.get("stale") is True,
        "rows": [
            {"id": evidence_id, "label": label, "value": values[evidence_id]}
            for evidence_id, label in US_INDEX_LABELS.items()
            if evidence_id in values
        ],
    }


def breadth_chart(evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    """11 只行业 ETF 里站上 50 日均线的只数（分子、分母都是证据里的原值）。"""

    internals = evidence.get("internals")
    signals = internals.get("market_signals") if isinstance(internals, Mapping) else None
    breadth = signals.get("sector_etf_breadth") if isinstance(signals, Mapping) else None
    if not isinstance(breadth, Mapping):
        return None
    above = _count(breadth.get("above_50dma"))
    total = _count(breadth.get("available"))
    expected = _count(breadth.get("expected"))
    if above is None or total is None or expected is None:
        return None
    if not (0 < total <= expected <= MAX_BREADTH_TOTAL and above <= total):
        return None
    return {
        "above": above,
        "total": total,
        "expected": expected,
        "as_of": _timestamp(signals.get("as_of")),
        "stale": signals.get("stale") is True,
    }


def _walk(value: Any) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield key, item
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def cited_theme_ids(result: Any) -> list[str]:
    """研判里引用过的主题 id，按首次出现排序；行业段的引用排在前面。"""

    ordered: list[str] = []

    def add(values: Any) -> None:
        for value in values if isinstance(values, list) else ():
            if isinstance(value, str) and _THEME_ID.fullmatch(value) and value not in ordered:
                ordered.append(value)

    if not isinstance(result, Mapping):
        return ordered
    sectors = result.get("sectors")
    for sector in sectors if isinstance(sectors, list) else ():
        if isinstance(sector, Mapping):
            add(sector.get("evidence_ids"))
    for key, value in _walk(result):
        if key == "evidence_ids":
            add(value)
        elif key == "evidence_id":
            add([value])
    return ordered


def sectors_chart(evidence: Mapping[str, Any], result: Any) -> dict[str, Any] | None:
    """报告引用的主题相对标普 500 的一个月超额收益（百分点），从高到低。"""

    block = evidence.get("themes")
    by_id: dict[str, Mapping[str, Any]] = {}
    for row in _rows(block):
        evidence_id = row.get("id")
        if isinstance(evidence_id, str):
            by_id.setdefault(evidence_id, row)
    rows: list[dict[str, Any]] = []
    for theme_id in cited_theme_ids(result):
        row = by_id.get(theme_id)
        if row is None:
            continue
        value = _number(row.get("excess_vs_spy_1mo"), low=-MAX_THEME_EXCESS_POINTS, high=MAX_THEME_EXCESS_POINTS)
        label = _label(row.get("name"))
        if value is None or label is None:
            continue
        rows.append({"id": theme_id, "label": label, "value": value})
        if len(rows) == MAX_THEME_ROWS:
            break
    if not rows:
        return None
    rows.sort(key=lambda item: -item["value"])
    return {"served_session": _day(block.get("served_session")), "rows": rows}


def macro_chart(evidence: Mapping[str, Any]) -> dict[str, Any] | None:
    """宏观各模块的分位分数（0–100）。"""

    block = evidence.get("macro")
    modules = block.get("modules") if isinstance(block, Mapping) else None
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for module in modules if isinstance(modules, list) else ():
        if not isinstance(module, Mapping):
            continue
        evidence_id = module.get("id")
        if not isinstance(evidence_id, str) or not _MACRO_ID.fullmatch(evidence_id) or evidence_id in seen:
            continue
        value = _number(module.get("score"), low=0.0, high=100.0)
        label = _label(module.get("name_zh"))
        if value is None or label is None:
            continue
        seen.add(evidence_id)
        rows.append({"id": evidence_id, "label": label, "value": value})
        if len(rows) == MAX_MACRO_ROWS:
            break
    if not rows:
        return None
    through = block.get("data_through")
    return {"data_through": _day(through) or _timestamp(through), "rows": rows}


def brief_visuals(evidence: Any, result: Any) -> dict[str, Any] | None:
    """公开响应里的 brief.visuals；一张图都画不出来时为 None。"""

    if not isinstance(evidence, Mapping):
        return None
    charts = {
        "market": market_chart(evidence),
        "breadth": breadth_chart(evidence),
        "sectors": sectors_chart(evidence, result),
        "macro": macro_chart(evidence),
    }
    return charts if any(chart is not None for chart in charts.values()) else None


__all__ = [
    "MAX_INDEX_CHANGE_PERCENT",
    "MAX_THEME_EXCESS_POINTS",
    "MAX_THEME_ROWS",
    "US_INDEX_LABELS",
    "breadth_chart",
    "brief_visuals",
    "cited_theme_ids",
    "macro_chart",
    "market_chart",
    "sectors_chart",
]
