"""Deterministic chart projection from the saved stock evidence pack.

No provider calls, model prose parsing, account fields, or inferred currencies.
The route owns result visibility; this module only projects immutable input.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

_VERSION = "stock-report-visuals-v1"
_LABELS = {
    "market_context": "大盘环境", "macro_conditions": "宏观条件",
    "options_chain": "期权链", "recent_news": "近期新闻",
    "upcoming_earnings": "财报日历", "company_profile": "公司资料",
    "price_history": "历史行情", "financials": "财务报表",
    "analyst_consensus": "分析师预期", "earnings_history": "历史盈利",
}
_STATUSES = {"ok", "partial", "unavailable", "timeout", "not_applicable", "omitted_size", "stale"}


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except (OverflowError, ValueError):
        return None


def _date(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None
    return value


def _bar_date(value: Any) -> str | None:
    stamp = _number(value)
    if stamp is None:
        return None
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _usd(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "USD"


def _caption(block: dict, *, through: str | None = None, detail: str = "") -> str:
    source = block.get("source")
    source = source[:80] if isinstance(source, str) and source else "保存的分析输入"
    source = {
        "Yahoo/yfinance": "雅虎财经（Yahoo Finance）",
        "public_stock_data": "已保存的市场数据",
        "manual_pull": "手动更新的市场数据",
        "saved_stock_resource": "已保存的市场数据",
    }.get(source, source)
    date = through or _date(block.get("data_through")) or _date(block.get("as_of"))
    parts = [f"来源：{source}", f"资料截至：{date}" if date else "资料日期未提供"]
    observed = _date(block.get("as_of"))
    if observed and observed != date:
        parts.append(f"保存观察时间：{observed}")
    if block.get("stale") is True:
        parts.append("来源标记为旧数据")
    if detail:
        parts.append(detail)
    return "；".join(parts)


def stock_report_visuals(row: dict) -> dict | None:
    """Return bounded, source-backed chart rows, or None for invalid input."""
    if not isinstance(row, dict):
        return None
    raw = row.get("payload_json")
    if isinstance(raw, str):
        if len(raw) > 1024 * 1024:
            return None
        try:
            payload = json.loads(raw)
        except (ValueError, TypeError, RecursionError):
            return None
    else:
        payload = raw
    if not isinstance(payload, dict):
        return None
    as_of = _date(payload.get("evidence_as_of")) or _date(payload.get("as_of")) or _date(row.get("created_at"))
    if as_of is None:
        return None

    nested = _dict(payload.get("signal_context"))
    blocks = _dict(nested.get("blocks"))
    def block(key: str) -> dict:
        return _dict(payload.get(key)) or _dict(blocks.get(key))
    statuses = {**_dict(nested.get("status")), **_dict(payload.get("context_status"))}
    coverage = []
    for key in dict.fromkeys([*_LABELS, *statuses]):
        if not isinstance(key, str) or len(key) > 96:
            continue
        item = block(key)
        raw_status = statuses.get(key, item.get("status", "ok" if item else "unavailable"))
        status = raw_status if isinstance(raw_status, str) and raw_status in _STATUSES else "unavailable"
        covered = {"key": key, "label": _LABELS.get(key, "其他资料"), "status": status}
        date = _date(item.get("as_of"))
        if date:
            covered["as_of"] = date
        coverage.append(covered)

    charts: list[dict] = []
    notes: list[str] = []
    def usable(key: str) -> dict:
        candidate = block(key)
        status = statuses.get(key, candidate.get("status", "ok" if candidate else "unavailable"))
        return candidate if isinstance(status, str) and status in {"ok", "partial", "stale"} else {}
    def add(id: str, title: str, kind: str, unit: str, rows: list[dict], caption: str):
        if rows and len(charts) < 6:
            charts.append({"id": id, "title": title, "caption": caption, "kind": kind, "unit": unit, "rows": rows[-(250 if kind == "line" else 16):]})

    prices = usable("price_history")
    profile = usable("company_profile")
    # The profile currency is the same stock's quote currency, never its
    # financialCurrency. An explicit history currency always takes precedence.
    price_currency = prices.get("currency") or profile.get("currency")
    price_rows, volume_rows = [], []
    seen: set[str] = set()
    saved_bars = _list(prices.get("bars"))
    for index, bar in enumerate(saved_bars[-250:], start=max(0, len(saved_bars) - 250)):
        if not isinstance(bar, dict):
            continue
        label = _bar_date(bar.get("t"))
        if label is None or label in seen:
            continue
        seen.add(label)
        evidence_id = f"price_history.bars.{index}"
        close, volume = _number(bar.get("c")), _number(bar.get("v"))
        if close is not None and close > 0:
            price_rows.append({"label": label, "value": close, "evidence_id": evidence_id + ".c"})
        if volume is not None and volume >= 0:
            volume_rows.append({"label": label, "value": volume, "evidence_id": evidence_id + ".v"})
    price_rows.sort(key=lambda item: item["label"])
    volume_rows.sort(key=lambda item: item["label"])
    if _usd(price_currency):
        add("price_history", "历史收盘价", "line", "usd", price_rows, _caption(prices, through=price_rows[-1]["label"] if price_rows else None, detail="保存的逐日价格；不含模型推算"))
    elif price_rows:
        notes.append("历史行情的币种未确认美元，金额图表未显示。")

    financials = usable("financials")
    revenue_rows = []
    for index, item in enumerate(_list(financials.get("quarterly"))[:16]):
        if not isinstance(item, dict):
            continue
        period, revenue = _date(item.get("period_end")), _number(item.get("revenue"))
        if period and revenue is not None:
            revenue_rows.append({"label": period[:10], "value": revenue, "evidence_id": f"financials.quarterly.{index}.revenue"})
    revenue_rows.sort(key=lambda item: item["label"])
    income_meta = {**financials, "as_of": _dict(financials.get("component_as_of")).get("quarterly_income") or financials.get("as_of")}
    if _usd(financials.get("currency")):
        add("quarterly_revenue", "季度收入", "bar", "usd", revenue_rows, _caption(income_meta, through=revenue_rows[-1]["label"] if revenue_rows else None, detail="财报期末日期；金额为美元原值"))
    elif revenue_rows:
        notes.append("财务报表的币种未确认美元，季度收入图表未显示。")

    scores = _dict(payload.get("scores"))
    score_rows = []
    for key, label in (("top_score", "顶部结构分数"), ("bottom_score", "底部结构分数"), ("dip_buy_quality", "回调质量"), ("data_quality", "资料质量")):
        value = _number(scores.get(key))
        # The persisted scoring contract is a bounded 0–100 scale.
        if value is not None and 0 <= value <= 100:
            score_rows.append({"label": label, "value": value, "evidence_id": f"scores.{key}"})
    add("structure_scores", "技术结构评分", "bar", "score", score_rows, _caption({"source": payload.get("evidence_source"), "as_of": as_of, "stale": payload.get("evidence_stale")}, detail="站内规则评分，范围0—100"))
    add("daily_volume", "日成交量", "line", "shares", volume_rows, _caption(prices, through=volume_rows[-1]["label"] if volume_rows else None, detail="单位为股；零成交量保留"))

    analyst = usable("analyst_consensus")
    targets = _dict(analyst.get("targets"))
    target_rows = []
    for key, label in (("low", "最低目标价"), ("mean", "平均目标价"), ("median", "中位目标价"), ("high", "最高目标价")):
        value = _number(targets.get(key))
        if value is not None and value >= 0:
            target_rows.append({"label": label, "value": value, "evidence_id": f"analyst_consensus.targets.{key}"})
    if _usd(analyst.get("currency")):
        add("analyst_targets", "分析师目标价", "bar", "usd", target_rows, _caption(analyst, detail="供应商汇总的目标价，不是模型预测"))
    elif target_rows:
        notes.append("分析师目标价的币种未确认美元，金额图表未显示。")

    earnings = usable("earnings_history")
    surprise_rows = []
    # Only this exact source-unit contract confirms surprisePercent's ratio.
    if _dict(earnings.get("units")).get("surprise_pct") == "provider fraction":
        for index, item in enumerate(_list(earnings.get("items"))[:16]):
            if not isinstance(item, dict):
                continue
            period, surprise = _date(item.get("period_end")), _number(item.get("surprise_pct"))
            if period and surprise is not None:
                surprise_rows.append({"label": period[:10], "value": surprise, "evidence_id": f"earnings_history.items.{index}.surprise_pct"})
    surprise_rows.sort(key=lambda item: item["label"])
    add("earnings_surprise", "盈利超预期比例", "bar", "ratio", surprise_rows, _caption(earnings, through=surprise_rows[-1]["label"] if surprise_rows else None, detail="保留供应商原始比例，0.1代表10%；不进行百分数换算"))
    if not charts:
        notes.append("保存的资料不足以生成图表，请参阅资料覆盖情况。")
    return {"version": _VERSION, "as_of": as_of, "charts": charts, "coverage": coverage, "notes": notes}
