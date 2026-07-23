"""Massive (formerly Polygon.io) price-data provider.

主源定位:自选批量报价、K 线图、突破雷达价格帧优先走 Massive 聚合行情;
任一子调用失败(未配密钥/超配额/计划不含该端点/网络错误)即如实抛出,
调用方回落到既有 Yahoo 链。期权路径不经此模块。

密钥:MASSIVE_API_KEY(secrets.env,经 ./personal.sh secrets set 注入)。
认证走 Authorization: Bearer 头,绝不进 URL(与 MarketData 同纪律)。
"""

from __future__ import annotations

import math
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import get_settings

_NEW_YORK = ZoneInfo("America/New_York")

# 指数映射:Yahoo 脱字符代码 → Massive I: 前缀(需指数计划;403/404 时回落 Yahoo)
_INDEX_SYMBOLS = {
    "^GSPC": "I:SPX",
    "^SPX": "I:SPX",
    "^NDX": "I:NDX",
    "^IXIC": "I:COMP",
    "^DJI": "I:DJI",
    "^RUT": "I:RUT",
    "^VIX": "I:VIX",
    "^SOX": "I:SOX",
    "^TNX": "I:TNX",
}

_CONNECT_TIMEOUT = 3.0
_READ_TIMEOUT = 10.0
_MAX_CONCURRENT_REQUESTS = 4
_request_gate = threading.BoundedSemaphore(_MAX_CONCURRENT_REQUESTS)

_client_lock = threading.Lock()
_client: httpx.Client | None = None


class MassiveError(RuntimeError):
    """Massive 调用失败;code 便于调用方分类(rate_limited/plan/…)。"""

    def __init__(self, message: str, *, code: str = "massive_error", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


def configured() -> bool:
    return bool(get_settings().massive_api_key)


def to_symbol(ticker: str) -> str | None:
    """Yahoo 风格代码 → Massive 代码;不支持的形态返回 None(回落 Yahoo)。"""

    symbol = (ticker or "").strip().upper()
    if not symbol:
        return None
    if symbol.startswith("^"):
        return _INDEX_SYMBOLS.get(symbol)
    # 期货(ES=F)/外汇对等形态不在本集成范围
    if "=" in symbol:
        return None
    # 交易所后缀(RMS.PA 等非美市场)不支持
    if "." in symbol:
        return None
    # Yahoo 的 B 类股连字符写法 → Massive 点写法(BRK-B → BRK.B)
    if "-" in symbol:
        head, _, tail = symbol.partition("-")
        if head and tail and len(tail) <= 2 and head.isalpha() and tail.isalpha():
            return f"{head}.{tail}"
        return None
    return symbol


def _http() -> httpx.Client:
    global _client
    with _client_lock:
        if _client is None:
            settings = get_settings()
            _client = httpx.Client(
                base_url=settings.massive_base_url.rstrip("/"),
                timeout=httpx.Timeout(_READ_TIMEOUT, connect=_CONNECT_TIMEOUT),
                headers={"Accept": "application/json"},
                follow_redirects=False,
            )
        return _client


def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = get_settings()
    key = settings.massive_api_key
    if not key:
        raise MassiveError("MASSIVE_API_KEY is not configured", code="not_configured")
    with _request_gate:
        try:
            response = _http().get(
                path,
                params=params,
                headers={"Authorization": f"Bearer {key}"},
            )
        except httpx.HTTPError as exc:
            raise MassiveError(f"transport failure: {type(exc).__name__}", code="transport") from exc
    if response.status_code == 429:
        raise MassiveError("rate limited", code="rate_limited", status=429)
    if response.status_code in {401, 403}:
        raise MassiveError("unauthorized or plan-restricted", code="plan", status=response.status_code)
    if response.status_code == 404:
        raise MassiveError("not found", code="not_found", status=404)
    if response.status_code >= 400:
        raise MassiveError(f"http {response.status_code}", code="http", status=response.status_code)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MassiveError("non-JSON response", code="protocol") from exc
    if not isinstance(payload, dict):
        raise MassiveError("unexpected payload shape", code="protocol")
    return payload


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _epoch_iso(value: Any) -> str | None:
    """Massive 的毫秒/微秒/纳秒时间戳 → UTC ISO-8601。"""

    try:
        stamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(stamp) or stamp <= 0:
        return None
    if stamp >= 1e17:
        stamp /= 1_000_000_000
    elif stamp >= 1e14:
        stamp /= 1_000_000
    elif stamp >= 1e11:
        stamp /= 1_000
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def grouped_daily(day: str) -> dict[str, dict[str, Any]]:
    """{SYM: {t,o,h,l,c,v}} — 单日全市场股票/ETF 日线;休市日返回空。"""

    payload = _get(
        f"/v2/aggs/grouped/locale/us/market/stocks/{day}",
        {"adjusted": "false", "include_otc": "false"},
    )
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("results") or []:
        symbol = str(row.get("T") or "").upper()
        close = _finite(row.get("c"))
        if not symbol or close is None:
            continue
        out[symbol] = {
            "t": row.get("t"),
            "o": _finite(row.get("o")),
            "h": _finite(row.get("h")),
            "l": _finite(row.get("l")),
            "c": close,
            "v": row.get("v"),
        }
    return out


def ticker_range(
    symbol: str,
    multiplier: int,
    timespan: str,
    start: str,
    end: str,
    *,
    adjusted: bool = False,
    limit: int = 50_000,
) -> list[dict[str, Any]]:
    """按时间升序返回聚合条;字段 {t(ms),o,h,l,c,v}。"""

    payload = _get(
        f"/v2/aggs/ticker/{symbol}/range/{multiplier}/{timespan}/{start}/{end}",
        {"adjusted": "true" if adjusted else "false", "sort": "asc", "limit": limit},
    )
    bars: list[dict[str, Any]] = []
    for row in payload.get("results") or []:
        close = _finite(row.get("c"))
        if close is None:
            continue
        bars.append(
            {
                "t": row.get("t"),
                "o": _finite(row.get("o")),
                "h": _finite(row.get("h")),
                "l": _finite(row.get("l")),
                "c": close,
                "v": row.get("v"),
            }
        )
    return bars


def recent_session_days(sessions: int, *, today: datetime | None = None) -> list[str]:
    """最近 N 个交易日的 YYYY-MM-DD(按美东日历回溯,跳过周末;节假日由空结果自然跳过)。"""

    anchor = (today or datetime.now(timezone.utc)).astimezone(_NEW_YORK).date()
    days: list[str] = []
    probe = anchor
    while len(days) < sessions + 4 and (anchor - probe) < timedelta(days=sessions * 2 + 10):
        if probe.weekday() < 5:
            days.append(probe.isoformat())
        probe -= timedelta(days=1)
    return days


def watchlist_daily_closes(
    tickers: list[str],
    *,
    sessions: int = 7,
) -> tuple[dict[str, list[tuple[int, float]]], list[str]]:
    """自选日线收盘序列(升序 [(ms,close)…] 取近 N 个交易日)。

    返回 (covered, missing):grouped 端点天然只含股票/ETF;指数、期货、
    外市代码与当日无数据者进 missing,由调用方回落 Yahoo。
    """

    symbol_map: dict[str, str] = {}
    missing: list[str] = []
    for ticker in tickers:
        symbol = to_symbol(ticker)
        if symbol is None or symbol.startswith("I:"):
            missing.append(ticker)
        else:
            symbol_map[ticker] = symbol

    if not symbol_map:
        return {}, missing

    series: dict[str, list[tuple[int, float]]] = {t: [] for t in symbol_map}
    found_sessions = 0
    for day in recent_session_days(sessions):
        if found_sessions >= sessions:
            break
        try:
            rows = grouped_daily(day)
        except MassiveError as exc:
            if exc.code in {"rate_limited", "plan", "not_configured", "transport"}:
                raise
            continue
        if not rows:
            continue
        found_sessions += 1
        for ticker, symbol in symbol_map.items():
            row = rows.get(symbol)
            if row is None:
                continue
            stamp = row.get("t")
            if not isinstance(stamp, (int, float)):
                continue
            series[ticker].append((int(stamp), row["c"]))

    covered: dict[str, list[tuple[int, float]]] = {}
    for ticker, points in series.items():
        if points:
            covered[ticker] = sorted(points)[-sessions:]
        else:
            missing.append(ticker)
    return covered, missing


def snapshot_batch(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """批量最新快照 {SYM: {minute:{t,c,o,h,l,v}, day:{...}, prev_close}}。

    100 只一批;计划不含 snapshot 时抛 plan 错误由调用方整体回落。
    """

    out: dict[str, dict[str, Any]] = {}
    for offset in range(0, len(symbols), 100):
        batch = symbols[offset : offset + 100]
        payload = _get(
            "/v2/snapshot/locale/us/markets/stocks/tickers",
            {"tickers": ",".join(batch)},
        )
        for row in payload.get("tickers") or []:
            symbol = str(row.get("ticker") or "").upper()
            if not symbol:
                continue
            minute = row.get("min") or {}
            day = row.get("day") or {}
            prev = row.get("prevDay") or {}
            close_price = _finite(minute.get("c")) or _finite(day.get("c"))
            if close_price is None:
                continue
            as_of = (
                _epoch_iso(minute.get("t"))
                or _epoch_iso(day.get("t"))
                or _epoch_iso(row.get("updated"))
            )
            out[symbol] = {
                "minute": {
                    "t": minute.get("t"),
                    "c": _finite(minute.get("c")),
                    "o": _finite(minute.get("o")),
                    "h": _finite(minute.get("h")),
                    "l": _finite(minute.get("l")),
                    "v": minute.get("v"),
                },
                "day": {
                    "t": day.get("t"),
                    "c": _finite(day.get("c")),
                    "o": _finite(day.get("o")),
                    "h": _finite(day.get("h")),
                    "l": _finite(day.get("l")),
                    "v": day.get("v"),
                },
                "day_close": _finite(day.get("c")),
                "prev_close": _finite(prev.get("c")),
                "updated": row.get("updated"),
                "as_of": as_of,
            }
    return out


def close() -> None:
    global _client
    with _client_lock:
        if _client is not None:
            _client.close()
            _client = None
