"""signal_analysis 证据包的可选上下文块（大盘/宏观/期权链/新闻/财报日期）。

个股 AI 分析的输入原本只有该股的技术信号与评分，结果契约里的
options_flow_read、event_risks 等字段模型只能诚实留空。这里把站内已有的
确定性数据源汇成紧凑块喂给付费任务，边界与 Market Focus 的宏观块一致：

- 每块独立失败：任何一个数据源故障、超时或未配置都不能阻塞手动分析入队，
  缺失的块以 context_status="unavailable" 如实告知模型；
- 每块有硬条数/字数上限：全部块加核心信号必须稳定落在付费请求
  60KB(_MAX_UNTRUSTED_JSON_BYTES) 之内，最终兜底由 API 层按序丢块；
- 只搬运事实读数与本地已生成的中文摘要，不在这里做任何新的判断。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.personal_config import get_personal_config
from app.public_home_snapshot import (
    public_home_resource_parameters,
    read_owner_public_home_entry_async,
)
from app.services import yahoo
from app.services.catalysts.local_intelligence import macro_conditions_context
from app.services.scoring import compute_market_scores
from app.services.signals import compute_market_signals

logger = logging.getLogger(__name__)

#: 全部上下文块的总组装预算。手动分析是显式付费动作，UI 有创建中状态，
#: 多等几秒可以接受；超时块按 unavailable 降级，绝不阻塞入队。
CONTEXT_TIMEOUT_SECONDS = 10.0

#: 载荷里上下文块的固定丢弃顺序（兜底裁剪时先丢文本最多的）。
CONTEXT_BLOCK_KEYS = (
    "recent_news",
    "options_chain",
    "market_context",
    "macro_conditions",
    "upcoming_earnings",
)

_NEWS_WINDOW_HOURS = 168
_NEWS_FETCH_LIMIT = 12
_NEWS_MAX_ITEMS = 10
_NEWS_TITLE_MAX_CHARS = 160
_NEWS_SUMMARY_MAX_CHARS = 240
_NEWS_REASON_MAX_CHARS = 160
_NEWS_TICKERS_PER_ITEM = 6
_CHAIN_MAX_EXPIRATIONS = 2
_CHAIN_TOP_CONTRACTS = 4
_CHAIN_MAX_ALERTS = 5
_CHAIN_ALERT_REASONS = 3
CONTEXT_TICKER_LIMIT = 24
#: 大盘快照过陈旧时改走现算（现算失败仍回退陈旧快照并如实标注 stale）。
_MARKET_SNAPSHOT_MAX_AGE_SECONDS = 24 * 3600.0


def _clip(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > limit:
        return text[: limit - 1] + "…"
    return text


def _finite_number(value: Any) -> float | int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return value


async def _market_block() -> dict[str, Any] | None:
    """大盘信号：优先 Worker 快照，过期先试现算，失败回退陈旧快照。"""

    config = get_personal_config()
    now = time.time()
    entry = await read_owner_public_home_entry_async(
        "market_signals",
        parameters=public_home_resource_parameters("market_signals", now=now),
        fresh_for_seconds=float(config.public_home.signals_seconds),
        now=now,
    )
    stale_fallback: dict[str, Any] | None = None
    if entry is not None and isinstance(entry.get("payload"), dict):
        payload = dict(entry["payload"])
        snapshot = {
            "source": "worker_snapshot",
            "stale": not bool(entry.get("fresh")),
            "as_of": payload.get("as_of"),
            "signals": payload.get("signals"),
            "scores": payload.get("scores"),
        }
        age = now - float(entry.get("saved_at") or 0.0)
        if bool(entry.get("fresh")) or age <= _MARKET_SNAPSHOT_MAX_AGE_SECONDS:
            return snapshot
        stale_fallback = snapshot
    try:
        signals = await asyncio.to_thread(compute_market_signals)
        if not isinstance(signals, dict):
            raise RuntimeError("market signals unavailable")
        cleaned = dict(signals)
        cleaned.pop("_cached", None)
        return {
            "source": "live",
            "stale": False,
            "as_of": datetime.now(timezone.utc).isoformat(),
            "signals": cleaned,
            "scores": compute_market_scores(cleaned),
        }
    except Exception:
        if stale_fallback is not None:
            return stale_fallback
        raise


def _summarize_chain(chain: dict[str, Any]) -> dict[str, Any] | None:
    calls = [c for c in chain.get("calls") or [] if isinstance(c, dict)]
    puts = [p for p in chain.get("puts") or [] if isinstance(p, dict)]
    if not calls and not puts:
        return None
    spot = _finite_number(chain.get("underlying_price"))

    def _volume(rows: list[dict[str, Any]]) -> int:
        return sum(int(row.get("volume") or 0) for row in rows)

    def _open_interest(rows: list[dict[str, Any]]) -> int:
        return sum(int(row.get("open_interest") or 0) for row in rows)

    def _ratio(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 3) if denominator > 0 else None

    def _project(row: dict[str, Any], side: str) -> dict[str, Any]:
        return {
            "type": side,
            "strike": _finite_number(row.get("strike")),
            "volume": int(row.get("volume") or 0),
            "open_interest": int(row.get("open_interest") or 0),
            "implied_volatility": _finite_number(row.get("implied_volatility")),
            "last_price": _finite_number(row.get("last_price")),
        }

    def _tops(metric: str) -> list[dict[str, Any]]:
        ranked = sorted(
            [(row, "call") for row in calls] + [(row, "put") for row in puts],
            key=lambda pair: int(pair[0].get(metric) or 0),
            reverse=True,
        )
        return [
            _project(row, side)
            for row, side in ranked[:_CHAIN_TOP_CONTRACTS]
            if int(row.get(metric) or 0) > 0
        ]

    def _atm(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
        if spot is None or not rows:
            return None
        candidates = [
            row for row in rows if _finite_number(row.get("strike")) is not None
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda row: abs(float(row["strike"]) - spot))

    atm_call = _atm(calls)
    atm_put = _atm(puts)
    expected_move_pct = None
    if spot and atm_call is not None and atm_put is not None:
        call_mid = _finite_number(atm_call.get("mid")) or _finite_number(
            atm_call.get("last_price")
        )
        put_mid = _finite_number(atm_put.get("mid")) or _finite_number(
            atm_put.get("last_price")
        )
        if call_mid and put_mid:
            expected_move_pct = round((call_mid + put_mid) / spot * 100, 2)

    alerts = []
    for alert in (chain.get("alerts") or [])[:_CHAIN_MAX_ALERTS]:
        if not isinstance(alert, dict):
            continue
        alerts.append(
            {
                "type": alert.get("type"),
                "strike": _finite_number(alert.get("strike")),
                "volume": int(alert.get("volume") or 0),
                "open_interest": int(alert.get("open_interest") or 0),
                "vol_oi_ratio": _finite_number(alert.get("vol_oi_ratio")),
                "premium_flow": _finite_number(alert.get("premium_flow")),
                "moneyness": alert.get("moneyness"),
                "reasons": [
                    reason
                    for reason in (alert.get("reasons") or [])[:_CHAIN_ALERT_REASONS]
                    if isinstance(reason, str)
                ],
            }
        )

    call_volume = _volume(calls)
    put_volume = _volume(puts)
    call_oi = _open_interest(calls)
    put_oi = _open_interest(puts)
    return {
        "expiration": chain.get("expiration"),
        "dte": _finite_number(chain.get("dte")),
        "call_volume": call_volume,
        "put_volume": put_volume,
        "call_open_interest": call_oi,
        "put_open_interest": put_oi,
        "volume_put_call_ratio": _ratio(put_volume, call_volume),
        "open_interest_put_call_ratio": _ratio(put_oi, call_oi),
        "atm_strike": (
            _finite_number(atm_call.get("strike")) if atm_call is not None else None
        ),
        "atm_call_iv": (
            _finite_number(atm_call.get("implied_volatility"))
            if atm_call is not None
            else None
        ),
        "atm_put_iv": (
            _finite_number(atm_put.get("implied_volatility"))
            if atm_put is not None
            else None
        ),
        "expected_move_pct": expected_move_pct,
        "top_open_interest": _tops("open_interest"),
        "top_volume": _tops("volume"),
        "unusual_alerts": alerts,
    }


def _options_block(symbol: str) -> dict[str, Any] | None:
    """近端两个到期日的期权链摘要；无期权的标的返回 None。"""

    snapshot = yahoo.get_expirations_snapshot(symbol)
    expirations = [
        value
        for value in snapshot.get("expirations") or []
        if isinstance(value, str) and value
    ][:_CHAIN_MAX_EXPIRATIONS]
    if not expirations:
        return None
    summaries: list[dict[str, Any]] = []
    underlying_price: float | int | None = None
    for expiration in expirations:
        try:
            chain = yahoo.get_option_chain(symbol, expiration)
        except Exception:
            continue
        summary = _summarize_chain(chain)
        if summary is None:
            continue
        underlying_price = (
            _finite_number(chain.get("underlying_price")) or underlying_price
        )
        summaries.append(summary)
    if not summaries:
        return None
    try:
        atm_iv = yahoo.get_stock_iv(symbol)
    except Exception:
        atm_iv = None
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "underlying_price": underlying_price,
        "atm_iv_20_60d": _finite_number(atm_iv),
        "note": "无成交主动方向数据",
        "expirations": summaries,
    }


def _news_block(symbol: str) -> tuple[dict[str, Any], list[str]] | None:
    """本地催化流按票摘要（中文标题/结论优先，未分析条目保留原文标题）。"""

    from app.services.catalysts.personal_service import PersonalCatalystService

    service = PersonalCatalystService(get_settings())
    payload = service.ticker(
        symbol,
        as_of=datetime.now(timezone.utc),
        window_hours=_NEWS_WINDOW_HOURS,
        limit=_NEWS_FETCH_LIMIT,
        min_confidence=0,
        include_unanalyzed=True,
        include_neutral=True,
    )
    status = str(payload.get("status") or "")
    if status in {"disabled", "unavailable"}:
        return None
    items: list[dict[str, Any]] = []
    tickers: list[str] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        analysis = item.get("analysis")
        impact_score = None
        impact_reason = None
        impact_horizon = None
        if isinstance(analysis, dict):
            for stock in analysis.get("affected_stocks") or []:
                if isinstance(stock, dict) and stock.get("ticker") == symbol:
                    impact_score = _finite_number(stock.get("impact_score"))
                    impact_reason = _clip(
                        stock.get("reason"), _NEWS_REASON_MAX_CHARS
                    )
                    impact_horizon = stock.get("horizon")
                    break
        source_tickers = [
            ticker
            for ticker in (item.get("source_tickers") or [])
            if isinstance(ticker, str) and ticker
        ][:_NEWS_TICKERS_PER_ITEM]
        for ticker in source_tickers:
            if ticker not in tickers:
                tickers.append(ticker)
        items.append(
            {
                "published_at": item.get("published_at"),
                "source": item.get("source"),
                "title": _clip(item.get("title"), _NEWS_TITLE_MAX_CHARS),
                "summary": _clip(item.get("summary"), _NEWS_SUMMARY_MAX_CHARS),
                "classification": item.get("classification"),
                "confidence": _finite_number(item.get("confidence")),
                "ticker_impact_score": impact_score,
                "ticker_impact_reason": impact_reason,
                "ticker_impact_horizon": impact_horizon,
                "tickers": source_tickers,
            }
        )
        if len(items) >= _NEWS_MAX_ITEMS:
            break
    block = {
        "window_hours": _NEWS_WINDOW_HOURS,
        "as_of": payload.get("as_of"),
        "data_through": payload.get("data_through"),
        "items": items,
    }
    return block, tickers


async def _earnings_block(symbol: str) -> dict[str, Any] | None:
    """自选池财报日历里该票的下一份（或最近一份）财报行。"""

    config = get_personal_config()
    now = time.time()
    entry = await read_owner_public_home_entry_async(
        "earnings",
        parameters=public_home_resource_parameters("earnings", now=now),
        fresh_for_seconds=float(config.public_home.earnings_seconds),
        now=now,
    )
    if entry is None or not isinstance(entry.get("payload"), dict):
        return None
    payload = entry["payload"]
    for row in payload.get("earnings") or []:
        if not isinstance(row, dict) or row.get("ticker") != symbol:
            continue
        return {
            "earnings_date": row.get("earnings_date"),
            "days_until": _finite_number(row.get("days_until")),
            "timing": row.get("timing"),
            "release_status": row.get("release_status"),
            "eps_estimate": _finite_number(row.get("eps_estimate")),
            "revenue_estimate": _finite_number(row.get("revenue_estimate")),
            "eps_actual": _finite_number(row.get("eps_actual")),
            "revenue_actual": _finite_number(row.get("revenue_actual")),
            "expected_move_pct": _finite_number(row.get("expected_move_pct")),
            "source_as_of": payload.get("snapshot_saved_at"),
        }
    return None


async def _isolated(name: str, awaitable: Any) -> Any:
    try:
        return await awaitable
    except Exception as error:
        logger.warning(
            "signal context block %s unavailable (%s)",
            name,
            type(error).__name__,
        )
        return None


async def build_signal_context(symbol: str) -> dict[str, Any]:
    """组装全部上下文块；永不抛异常，缺失块在 status 里如实标注。"""

    tasks: dict[str, asyncio.Task[Any]] = {
        "market_context": asyncio.create_task(
            _isolated("market_context", _market_block())
        ),
        "macro_conditions": asyncio.create_task(
            _isolated(
                "macro_conditions", asyncio.to_thread(macro_conditions_context)
            )
        ),
        "options_chain": asyncio.create_task(
            _isolated("options_chain", asyncio.to_thread(_options_block, symbol))
        ),
        "recent_news": asyncio.create_task(
            _isolated("recent_news", asyncio.to_thread(_news_block, symbol))
        ),
        "upcoming_earnings": asyncio.create_task(
            _isolated("upcoming_earnings", _earnings_block(symbol))
        ),
    }
    await asyncio.wait(tasks.values(), timeout=CONTEXT_TIMEOUT_SECONDS)
    blocks: dict[str, Any] = {}
    status: dict[str, str] = {}
    context_tickers: list[str] = []
    for key, task in tasks.items():
        value = task.result() if task.done() else None
        if key == "recent_news" and value is not None:
            value, tickers = value
            for ticker in tickers:
                if (
                    ticker != symbol
                    and ticker not in context_tickers
                    and len(context_tickers) < CONTEXT_TICKER_LIMIT
                ):
                    context_tickers.append(ticker)
        if value is None:
            status[key] = "unavailable"
        else:
            blocks[key] = value
            status[key] = "ok"
    if any(not task.done() for task in tasks.values()):
        logger.warning(
            "signal context assembly timed out after %.1fs (%s)",
            CONTEXT_TIMEOUT_SECONDS,
            ",".join(sorted(key for key, task in tasks.items() if not task.done())),
        )
    return {
        "blocks": blocks,
        "status": status,
        "context_tickers": sorted(context_tickers),
    }
