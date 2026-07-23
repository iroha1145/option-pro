from __future__ import annotations

import io
import hashlib
import math
from bisect import bisect_left, bisect_right
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yfinance as yf

from app.config import get_settings
from app.services import massive
from app.services import yahoo
from app.services.cache import cache
from app.services.sectors import SECTORS
from app.services.strength.finnhub import (
    OPTION_DATA_SOURCE_CANDIDATES,
    enrich_rows_with_finnhub,
    finnhub_is_enabled,
)
from app.services.strength.marketdata import (
    enrich_rows_with_marketdata_options,
    marketdata_is_enabled,
)
from app.services.strength.market_regime import MARKET_BENCHMARKS, compute_market_regime
from app.services.strength.market_shape import MARKET_SHAPE_VERSION
from app.services.strength.price_action import compute_price_action
from app.services.strength.scoring import (
    FEATURE_VERSION as STRENGTH_FEATURE_VERSION,
    NORMALIZATION_VERSION as STRENGTH_NORMALIZATION_VERSION,
    SCORE_VERSION as STRENGTH_SCORE_VERSION,
    score_intrinsic,
    score_market_fit,
    score_profile_fit,
    score_ranking,
)
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.strength.yahoo_options import (
    enrich_rows_with_yahoo_options,
    yahoo_options_is_enabled,
)
from app.services.yfinance_batch import download_in_bounded_batches
from app.services.technical.range_persistence import (
    RANGE_PERSISTENCE_VERSION,
    compute_range_persistence,
)
from app.services.zh_names import get_zh_name

TIMEFRAMES = ("short", "mid", "long", "all")
PROFILES = ("conservative", "balanced", "aggressive")
UNIVERSES = ("themes",)
BENCHMARKS = MARKET_BENCHMARKS
SECTOR_PERIOD_DAYS = {"1mo": 20, "3mo": 63, "6mo": 126}
STRENGTH_CACHE_TTL_SECONDS = 900
MARKET_STRENGTH_CACHE_TTL_SECONDS = 900
STRENGTH_HISTORY_PERIOD = "2y"
_YAHOO_HISTORY_DOWNLOAD_ATTEMPTS = 2
INTRINSIC_STRENGTH_VERSION = STRENGTH_SCORE_VERSION
_NEW_YORK = ZoneInfo("America/New_York")

_FALLBACK_MAX_WORKERS = 8
_FALLBACK_TOTAL_BUDGET_SECONDS = 20.0
_FALLBACK_FAILURE_LIMIT = 8

PROFILE_TILT = {
    "conservative": {"trend": 1.12, "risk": 1.22, "volume": .88, "breakout": .90},
    "balanced": {"trend": 1.0, "risk": 1.0, "volume": 1.0, "breakout": 1.0},
    "aggressive": {"trend": .92, "risk": .82, "volume": 1.15, "breakout": 1.18},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _clamp(
    value: float | int | None,
    lo: float = 0.0,
    hi: float = 100.0,
    default: float | None = None,
) -> float | None:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _pct_rank(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = sorted((row[key] for row in items if row.get(key) is not None))
    if not values:
        return {}
    if len(values) == 1:
        # A single observation has no defensible cross-sectional percentile.
        return {}
    denom = max(len(values) - 1, 1)
    ranks: dict[str, float] = {}
    for row in items:
        value = row.get(key)
        if value is None:
            continue
        below = bisect_left(values, value)
        tied = bisect_right(values, value) - below
        midrank = below + (tied - 1) / 2
        ranks[row["ticker"]] = round(midrank / denom * 100, 1)
    return ranks


def _theme_universe(sector_id: str | None = None) -> tuple[list[str], dict[str, dict[str, Any]]]:
    sector_meta: dict[str, dict[str, Any]] = {}
    tickers: list[str] = []
    for sid, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            symbol = ticker.upper().strip()
            if not symbol or "." in symbol:
                # Keep the MVP US-focused and avoid mixed exchange suffixes.
                continue
            tickers.append(symbol)
            metadata = sector_meta.setdefault(
                symbol,
                {
                    "sector_id": sid,
                    "sector_name": sector["name"],
                    "primary_sector_id": sid,
                    "primary_sector_name": sector["name"],
                    "theme_ids": [],
                    "theme_names": [],
                },
            )
            metadata["theme_ids"].append(sid)
            metadata["theme_names"].append(sector["name"])
    canonical = list(dict.fromkeys(tickers))
    if not sector_id:
        return canonical, sector_meta
    selected = [
        ticker
        for ticker in canonical
        if sector_id in set(sector_meta.get(ticker, {}).get("theme_ids") or [])
    ]
    return selected, {ticker: sector_meta[ticker] for ticker in selected}


def _canonical_universe_version(
    tickers: list[str],
    metadata: Mapping[str, Mapping[str, Any]],
) -> str:
    members = []
    for ticker in sorted(tickers):
        item = metadata.get(ticker, {})
        themes = ",".join(sorted(str(value) for value in item.get("theme_ids", [])))
        members.append(
            f"{ticker}:{item.get('primary_sector_id') or item.get('sector_id') or ''}:{themes}"
        )
    digest = hashlib.sha256("|".join(members).encode("utf-8")).hexdigest()[:16]
    return f"themes-{digest}"


def _attach_canonical_ranks(rows: list[dict[str, Any]]) -> None:
    global_ranks = _pct_rank(rows, "intrinsic_score")
    by_sector: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sector = str(row.get("primary_sector_id") or row.get("sector_id") or "")
        if sector:
            by_sector.setdefault(sector, []).append(row)
    sector_ranks: dict[str, float] = {}
    for members in by_sector.values():
        sector_ranks.update(_pct_rank(members, "intrinsic_score"))

    for row in rows:
        ticker = str(row.get("ticker") or "")
        row["global_rank_percentile"] = global_ranks.get(ticker)
        row["sector_rank_percentile"] = sector_ranks.get(ticker)
        row["sector_score"] = sector_ranks.get(ticker)
        row["cross_section_status"] = {
            "global": (
                "active" if ticker in global_ranks else "cross_section_unavailable"
            ),
            "sector": (
                "active" if ticker in sector_ranks else "cross_section_unavailable"
            ),
        }


def _period_to_days(period: str) -> int:
    period = (period or "1y").strip().lower()
    if period.endswith("y"):
        return max(365, int(float(period[:-1] or 1) * 365))
    if period.endswith("mo"):
        return max(31, int(float(period[:-2] or 1) * 31))
    if period.endswith("d"):
        return max(1, int(float(period[:-1] or 1)))
    return 365


def _bounded_history_fetch(
    symbols: list[str],
    fetch_one: Callable[[str], pd.DataFrame],
    *,
    max_workers: int = _FALLBACK_MAX_WORKERS,
    total_budget_seconds: float = _FALLBACK_TOTAL_BUDGET_SECONDS,
    request_timeout_seconds: float = 6.0,
    failure_limit: int = _FALLBACK_FAILURE_LIMIT,
) -> dict[str, pd.DataFrame]:
    """Fetch fallback candles with bounded parallelism and a circuit breaker.

    New work is only scheduled while enough total budget remains for one
    request. A run of provider failures stops scheduling the rest of a large
    universe, preventing the old ``N * timeout`` worst case.
    """
    ordered = list(dict.fromkeys(symbol for symbol in symbols if symbol))
    if not ordered:
        return {}

    workers = max(1, min(int(max_workers), len(ordered)))
    failure_limit = max(1, int(failure_limit))
    budget = max(float(total_budget_seconds), 0.01)
    request_window = max(min(float(request_timeout_seconds), budget), 0.01)
    deadline = monotonic() + budget
    iterator = iter(ordered)
    futures: dict[Any, str] = {}
    results: dict[str, pd.DataFrame] = {}
    consecutive_failures = 0
    stopped = False

    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="price-fallback")

    def submit_one() -> bool:
        nonlocal stopped
        if stopped or deadline - monotonic() < request_window:
            return False
        try:
            symbol = next(iterator)
        except StopIteration:
            stopped = True
            return False
        futures[executor.submit(fetch_one, symbol)] = symbol
        return True

    for _ in range(workers):
        if not submit_one():
            break

    try:
        while futures and monotonic() < deadline:
            remaining = max(0.0, deadline - monotonic())
            done, _ = wait(futures, timeout=min(0.25, remaining), return_when=FIRST_COMPLETED)
            if not done:
                continue
            for future in done:
                symbol = futures.pop(future)
                try:
                    frame = future.result()
                except Exception:
                    frame = pd.DataFrame()
                if isinstance(frame, pd.DataFrame) and not frame.empty:
                    results[symbol] = frame
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= failure_limit:
                        stopped = True
                if not stopped:
                    submit_one()
    finally:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
    return results


def _history_status(
    *,
    provider: str,
    status: str,
    message: str,
    fallback_symbols: list[str] | None = None,
    missing_symbols: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "status": status,
        "message": message,
        "fallback_symbols": fallback_symbols or [],
        "missing_symbols": missing_symbols or [],
    }


def _attach_history_status(df: pd.DataFrame, status: dict[str, Any]) -> pd.DataFrame:
    df.attrs["price_source"] = status
    return df


def _empty_history_with_status(status: dict[str, Any]) -> pd.DataFrame:
    return _attach_history_status(pd.DataFrame(), status)


def _finnhub_candle_frame(symbol: str, payload: dict[str, Any]) -> pd.DataFrame:
    if payload.get("s") != "ok":
        return pd.DataFrame()
    times = payload.get("t") or []
    closes = payload.get("c") or []
    opens = payload.get("o") or []
    highs = payload.get("h") or []
    lows = payload.get("l") or []
    volumes = payload.get("v") or []
    size = min(len(times), len(opens), len(highs), len(lows), len(closes), len(volumes))
    if size <= 0:
        return pd.DataFrame()

    index = pd.to_datetime(times[:size], unit="s", utc=True).tz_convert(None)
    frame = pd.DataFrame(
        {
            "Open": opens[:size],
            "High": highs[:size],
            "Low": lows[:size],
            "Close": closes[:size],
            "Volume": volumes[:size],
        },
        index=index,
    )
    frame = frame.apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    if frame.empty:
        return pd.DataFrame()
    frame.columns = pd.MultiIndex.from_product([[symbol], frame.columns])
    return frame


def _download_marketdata_history(tickers: list[str], period: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    settings = get_settings()
    token = settings.marketdata_token.strip()
    if not token or not settings.marketdata_stock_candle_fallback_enabled:
        return pd.DataFrame(), [], tickers

    limit = max(0, int(settings.marketdata_stock_candle_fallback_limit or 0))
    if limit <= 0:
        return pd.DataFrame(), [], tickers

    base_url = str(settings.marketdata_base_url).rstrip("/")
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_period_to_days(period) + 10)
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:limit]
    timeout = min(float(settings.request_timeout or 20.0), 6.0)

    try:
        with httpx.Client(
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            def fetch_one(symbol: str) -> pd.DataFrame:
                response = client.get(
                    f"{base_url}/v1/stocks/candles/D/{symbol}/",
                    params={
                        "from": start_date.isoformat(),
                        "to": end_date.isoformat(),
                    },
                )
                response.raise_for_status()
                return _finnhub_candle_frame(symbol, response.json())

            fetched = _bounded_history_fetch(symbols, fetch_one, request_timeout_seconds=timeout)
    except Exception:
        fetched = {}

    loaded = [symbol for symbol in symbols if symbol in fetched]
    frames = [fetched[symbol] for symbol in loaded]
    missing = [symbol for symbol in tickers if symbol not in fetched]
    if not frames:
        return pd.DataFrame(), loaded, missing
    return pd.concat(frames, axis=1).sort_index(), loaded, missing


def _stooq_symbol(symbol: str) -> str | None:
    symbol = (symbol or "").strip().lower()
    if not symbol or symbol.startswith("^"):
        return None
    return f"{symbol}.us"


def _stooq_candle_frame(symbol: str, csv_text: str) -> pd.DataFrame:
    if "Date,Open,High,Low,Close,Volume" not in csv_text[:80]:
        return pd.DataFrame()
    try:
        frame = pd.read_csv(io.StringIO(csv_text))
    except Exception:
        return pd.DataFrame()
    if frame.empty or "Date" not in frame.columns or "Close" not in frame.columns:
        return pd.DataFrame()

    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame = frame.dropna(subset=["Date", "Close"]).set_index("Date")
    columns = [column for column in ("Open", "High", "Low", "Close", "Volume") if column in frame.columns]
    if "Close" not in columns:
        return pd.DataFrame()
    frame = frame[columns].apply(pd.to_numeric, errors="coerce").dropna(subset=["Close"])
    if frame.empty:
        return pd.DataFrame()
    frame.columns = pd.MultiIndex.from_product([[symbol], frame.columns])
    return frame


def _download_stooq_history(tickers: list[str], period: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    settings = get_settings()
    if not settings.stooq_price_fallback_enabled:
        return pd.DataFrame(), [], tickers

    limit = max(0, int(settings.stooq_price_fallback_limit or 0))
    if limit <= 0:
        return pd.DataFrame(), [], tickers

    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_period_to_days(period) + 10)
    symbols = [symbol for symbol in tickers if _stooq_symbol(symbol)][:limit]
    timeout = min(float(settings.request_timeout or 20.0), 6.0)

    try:
        with httpx.Client(timeout=timeout) as client:
            def fetch_one(symbol: str) -> pd.DataFrame:
                stooq_symbol = _stooq_symbol(symbol)
                if not stooq_symbol:
                    return pd.DataFrame()
                response = client.get(
                    "https://stooq.com/q/d/l/",
                    params={
                        "s": stooq_symbol,
                        "i": "d",
                        "d1": start_date.strftime("%Y%m%d"),
                        "d2": end_date.strftime("%Y%m%d"),
                    },
                )
                response.raise_for_status()
                return _stooq_candle_frame(symbol, response.text)

            fetched = _bounded_history_fetch(symbols, fetch_one, request_timeout_seconds=timeout)
    except Exception:
        fetched = {}

    loaded = [symbol for symbol in symbols if symbol in fetched]
    frames = [fetched[symbol] for symbol in loaded]
    missing = [symbol for symbol in tickers if symbol not in fetched]
    if not frames:
        return pd.DataFrame(), loaded, missing
    return pd.concat(frames, axis=1).sort_index(), loaded, missing


def _download_finnhub_history(tickers: list[str], period: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    settings = get_settings()
    token = (settings.finnhub_api_key or "").strip()
    if not token or not settings.finnhub_candle_fallback_enabled:
        return pd.DataFrame(), [], tickers

    limit = max(0, int(settings.finnhub_candle_fallback_limit or 0))
    if limit <= 0:
        return pd.DataFrame(), [], tickers

    base_url = str(settings.finnhub_base_url).rstrip("/")
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (_period_to_days(period) + 10) * 24 * 60 * 60
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:limit]
    timeout = min(float(settings.request_timeout or 20.0), 6.0)

    try:
        with httpx.Client(timeout=timeout, headers={"X-Finnhub-Token": token}) as client:
            def fetch_one(symbol: str) -> pd.DataFrame:
                response = client.get(
                    f"{base_url}/stock/candle",
                    params={
                        "symbol": symbol,
                        "resolution": "D",
                        "from": start_ts,
                        "to": end_ts,
                    },
                )
                response.raise_for_status()
                return _finnhub_candle_frame(symbol, response.json())

            fetched = _bounded_history_fetch(symbols, fetch_one, request_timeout_seconds=timeout)
    except Exception:
        fetched = {}

    loaded = [symbol for symbol in symbols if symbol in fetched]
    frames = [fetched[symbol] for symbol in loaded]
    missing = [symbol for symbol in tickers if symbol not in fetched]
    if not frames:
        return pd.DataFrame(), loaded, missing
    return pd.concat(frames, axis=1).sort_index(), loaded, missing


def _merge_history(primary: pd.DataFrame, fallback: pd.DataFrame) -> pd.DataFrame:
    if primary.empty:
        return fallback.copy()
    if fallback.empty:
        return primary
    primary_frame = primary
    if isinstance(primary.columns, pd.MultiIndex) and isinstance(fallback.columns, pd.MultiIndex):
        fallback_symbols = set(str(symbol) for symbol in fallback.columns.get_level_values(0))
        primary_frame = primary.loc[:, [column for column in primary.columns if str(column[0]) not in fallback_symbols]]
    merged = pd.concat([primary_frame, fallback], axis=1).sort_index()
    return merged.loc[:, ~merged.columns.duplicated()]


def _has_usable_history(df: pd.DataFrame, tickers: list[str] | tuple[str, ...]) -> bool:
    if not isinstance(df, pd.DataFrame) or df.empty:
        return False
    return any(not _slice_ticker(df, ticker).empty for ticker in tickers)


def _period_calendar_days(period: str) -> int:
    """yfinance 周期串 → 日历天数(多留缓冲覆盖节假日),解析失败按 1y。"""
    text = str(period or "1y").strip().lower()
    try:
        if text.endswith("mo"):
            return max(20, int(float(text[:-2]) * 31) + 10)
        if text.endswith("y"):
            return max(60, int(float(text[:-1]) * 365) + 40)
        if text.endswith("d"):
            return max(5, int(float(text[:-1])) + 5)
    except ValueError:
        pass
    return 405


def _massive_history_is_complete(
    rows: list[dict[str, Any]],
    *,
    period: str,
    end: date,
) -> bool:
    """Reject short, stale or structurally incomplete Massive histories."""

    minimum_rows = {
        "2y": 380,
        "1y": 190,
        "6mo": 95,
        "3mo": 45,
        "1mo": 15,
    }.get(str(period).lower(), 190)
    usable = [
        row
        for row in rows
        if isinstance(row.get("t"), (int, float))
        and isinstance(row.get("c"), (int, float))
        and math.isfinite(float(row["c"]))
        and float(row["c"]) > 0
        and isinstance(row.get("v"), (int, float))
        and math.isfinite(float(row["v"]))
        and float(row["v"]) >= 0
    ]
    if len(usable) < minimum_rows:
        return False
    latest = pd.Timestamp(max(float(row["t"]) for row in usable), unit="ms", tz="UTC").date()
    return (end - latest).days <= 7


def _download_massive_history(
    tickers: list[str],
    period: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Massive 主源日线(复权,正股专用)。

    返回 (MultiIndex frame, 未覆盖代码);指数/期货等不支持形态直接进
    未覆盖名单,由既有 Yahoo → 公开源链兜底。未配置密钥时整体跳过。
    """
    if not massive.configured():
        return pd.DataFrame(), list(tickers)
    end = date.today()
    start_s = (end - timedelta(days=_period_calendar_days(period))).isoformat()
    end_s = end.isoformat()

    def _one(ticker: str) -> tuple[str, list[dict[str, Any]] | None]:
        symbol = massive.to_symbol(ticker)
        if symbol is None or symbol.startswith("I:"):
            return ticker, None
        try:
            bars = massive.ticker_range(symbol, 1, "day", start_s, end_s, adjusted=True)
        except massive.MassiveError:
            return ticker, None
        return ticker, bars or None

    frames: dict[tuple[str, str], pd.Series] = {}
    missing: list[str] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        for ticker, bars in pool.map(_one, tickers):
            rows = [bar for bar in (bars or []) if isinstance(bar.get("t"), (int, float))]
            if not rows or not _massive_history_is_complete(rows, period=period, end=end):
                # 过短、过旧或缺成交量时必须交给 Yahoo/公开源补齐，不能把残缺主源标成成功。
                missing.append(ticker)
                continue
            index = pd.DatetimeIndex(
                [
                    pd.Timestamp(bar["t"], unit="ms", tz="UTC")
                    .tz_convert("America/New_York")
                    .normalize()
                    .tz_localize(None)
                    for bar in rows
                ]
            )
            for field, key in (
                ("Open", "o"),
                ("High", "h"),
                ("Low", "l"),
                ("Close", "c"),
                ("Volume", "v"),
            ):
                frames[(ticker, field)] = pd.Series([bar.get(key) for bar in rows], index=index)
    if not frames:
        return pd.DataFrame(), missing
    frame = pd.DataFrame(frames)
    frame.columns = pd.MultiIndex.from_tuples(frame.columns)
    return frame.sort_index(), missing


def _download_history(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    # Massive 为主源;未覆盖(未配置/指数/单票无数据)的余量走 Yahoo 链
    try:
        massive_frame, massive_missing = _download_massive_history(tickers, period)
    except Exception:
        massive_frame, massive_missing = pd.DataFrame(), list(tickers)
    massive_used = not massive_frame.empty
    yahoo_targets = massive_missing if massive_used else list(tickers)

    session = getattr(yahoo, "_yf_session", None)
    kwargs: dict[str, Any] = {
        "period": period,
        "interval": "1d",
        "group_by": "ticker",
        "progress": False,
        "auto_adjust": True,
    }
    if session is not None:
        kwargs["session"] = session
    primary = pd.DataFrame()
    # A fresh yfinance session can occasionally return an immediate empty
    # frame while its cookie/crumb state is being initialized. Retry that
    # transient shape once before falling back or reporting unavailability.
    if yahoo_targets:
        for _attempt in range(_YAHOO_HISTORY_DOWNLOAD_ATTEMPTS):
            try:
                candidate = download_in_bounded_batches(
                    yf.download,
                    tickers=yahoo_targets,
                    **kwargs,
                )
            except Exception:
                candidate = pd.DataFrame()
            if isinstance(candidate, pd.DataFrame) and _has_usable_history(candidate, yahoo_targets):
                primary = candidate
                break

    yahoo_used = not primary.empty
    if massive_used:
        primary = _merge_history(massive_frame, primary) if yahoo_used else massive_frame
    base_label = " + ".join(
        label
        for label, used in (("Massive", massive_used), ("Yahoo/yfinance", yahoo_used))
        if used
    ) or "Yahoo/yfinance"

    missing = [ticker for ticker in tickers if _slice_ticker(primary, ticker).empty]
    if not primary.empty and not missing:
        return _attach_history_status(
            primary,
            _history_status(
                provider=base_label,
                status="active",
                message=f"{base_label} 日线价格、成交量与技术指标输入",
            ),
        )

    merged = primary
    providers: list[str] = []
    fallback_symbols: list[str] = []
    remaining = missing or tickers

    marketdata_fallback, marketdata_symbols, marketdata_missing = _download_marketdata_history(remaining, period)
    if not marketdata_fallback.empty:
        merged = _merge_history(merged, marketdata_fallback)
        providers.append("MarketData.app")
        fallback_symbols.extend(marketdata_symbols)
        remaining = [ticker for ticker in tickers if _slice_ticker(merged, ticker).empty]
    else:
        remaining = marketdata_missing or remaining

    stooq_fallback, stooq_symbols, stooq_missing = _download_stooq_history(remaining, period)
    if not stooq_fallback.empty:
        merged = _merge_history(merged, stooq_fallback)
        providers.append("Stooq")
        fallback_symbols.extend(stooq_symbols)
        remaining = [ticker for ticker in tickers if _slice_ticker(merged, ticker).empty]
    else:
        remaining = stooq_missing or remaining

    finnhub_fallback, finnhub_symbols, finnhub_missing = _download_finnhub_history(remaining, period)
    if not finnhub_fallback.empty:
        merged = _merge_history(merged, finnhub_fallback)
        providers.append("Finnhub")
        fallback_symbols.extend(finnhub_symbols)

    if not merged.empty and providers:
        still_missing = [ticker for ticker in tickers if _slice_ticker(merged, ticker).empty]
        provider = f"{base_label} + " + " + ".join(providers)
        status = "active" if not still_missing else "degraded"
        source_label = " + ".join(providers)
        message = (
            f"{base_label} 部分或全部数据不可用，已启用 {source_label} 日线兜底"
            if primary.empty
            else f"{base_label} 缺少部分标的，已用 {source_label} 日线补齐"
        )
        return _attach_history_status(
            merged,
            _history_status(
                provider=provider,
                status=status,
                message=message,
                fallback_symbols=list(dict.fromkeys(fallback_symbols)),
                missing_symbols=still_missing,
            ),
        )

    if primary.empty:
        fallback_missing = list(dict.fromkeys([*marketdata_missing, *stooq_missing, *finnhub_missing]))
        return _empty_history_with_status(
            _history_status(
                provider=base_label,
                status="degraded",
                message=f"{base_label} 数据不可用，公开日线兜底源也未拿到可用数据",
                missing_symbols=fallback_missing or tickers,
            )
        )

    return _attach_history_status(
        primary,
        _history_status(
            provider=base_label,
            status="degraded",
            message=f"{base_label} 缺少部分标的，公开日线兜底源未拿到可用数据",
            missing_symbols=missing,
        ),
    )


def _slice_ticker(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        if ticker in df.columns.get_level_values(0):
            out = df[ticker].copy()
        else:
            return pd.DataFrame()
    else:
        out = df.copy()
    if "Close" not in out.columns:
        return pd.DataFrame()
    out = out.dropna(subset=["Close"])
    return out


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    avg_gain = _safe_float(gain.dropna().iloc[-1], 8) if not gain.dropna().empty else None
    avg_loss = _safe_float(loss.dropna().iloc[-1], 8) if not loss.dropna().empty else None
    if avg_gain is None or avg_loss is None:
        return None
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return _safe_float(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def _macd_direction(close: pd.Series) -> float | None:
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    if len(hist.dropna()) < 4 or not close.iloc[-1]:
        return None
    return _safe_float((hist.iloc[-1] - hist.iloc[-4]) / close.iloc[-1] * 100, 4)


def _atr_pct(hist: pd.DataFrame) -> float | None:
    if len(hist) < 15 or not {"High", "Low", "Close"}.issubset(hist.columns):
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().dropna()
    if atr.empty or close.iloc[-1] <= 0:
        return None
    return _safe_float(atr.iloc[-1] / close.iloc[-1] * 100, 2)


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    base = close.iloc[-(days + 1)]
    if not base or base <= 0:
        return None
    return _safe_float(close.iloc[-1] / base - 1, 5)


def _feature_row(ticker: str, hist: pd.DataFrame, spy: pd.DataFrame, sector_meta: Mapping[str, Any]) -> dict[str, Any] | None:
    if hist.empty or len(hist) < 63:
        return None
    close = pd.to_numeric(hist["Close"], errors="coerce").replace([math.inf, -math.inf], pd.NA).dropna()
    volume = (
        pd.to_numeric(hist["Volume"], errors="coerce").replace([math.inf, -math.inf], pd.NA)
        if "Volume" in hist.columns
        else pd.Series(index=hist.index, dtype=float)
    )
    price = _safe_float(close.iloc[-1], 2)
    if price is None or price <= 0:
        return None

    def sma(period: int) -> float | None:
        if len(close) < period:
            return None
        return _safe_float(close.rolling(period).mean().iloc[-1], 4)

    sma20, sma50, sma200 = sma(20), sma(50), sma(200)
    valid_volume = volume[volume >= 0].dropna()
    avg_vol20 = (
        _safe_float(valid_volume.tail(20).mean(), 2)
        if len(valid_volume) >= 20
        else None
    )
    liquidity = pd.concat(
        [
            pd.to_numeric(hist["Close"], errors="coerce").rename("Close"),
            volume.rename("Volume"),
        ],
        axis=1,
    ).replace([math.inf, -math.inf], pd.NA)
    liquidity = liquidity[
        (liquidity["Close"] > 0) & (liquidity["Volume"] >= 0)
    ].dropna(subset=["Close", "Volume"])
    dollar_volume = liquidity["Close"] * liquidity["Volume"]
    avg_dollar_vol = (
        _safe_float(dollar_volume.tail(20).mean(), 0)
        if len(dollar_volume) >= 20
        else None
    )
    latest_volume = _safe_float(volume.reindex(close.index).iloc[-1], 4) if not close.empty else None
    rel_volume = (
        _safe_float(latest_volume / avg_vol20, 3)
        if latest_volume is not None and avg_vol20 is not None and avg_vol20 > 0
        else None
    )
    high_52w = _safe_float(close.tail(252).max() if len(close) >= 120 else close.max(), 4)
    high_3m = _safe_float(close.tail(63).max(), 4)
    vol_price_match = compute_vol_price_match(hist)
    price_action = compute_price_action(hist)

    spy_close = spy["Close"].dropna() if not spy.empty and "Close" in spy.columns else pd.Series(dtype=float)
    stock_ret_63 = _ret(close, 63)
    spy_ret_63 = _ret(spy_close, 63) if len(spy_close) else None

    moving_average_states = [
        price > average
        for average in (sma20, sma50, sma200)
        if average is not None and average > 0
    ]
    ma_alignment = (
        sum(moving_average_states) / len(moving_average_states) * 100
        if moving_average_states
        else None
    )

    return {
        "ticker": ticker,
        "name": get_zh_name(ticker) or ticker,
        "sector_id": sector_meta.get("sector_id"),
        "sector_name": sector_meta.get("sector_name"),
        "primary_sector_id": sector_meta.get("primary_sector_id") or sector_meta.get("sector_id"),
        "primary_sector_name": sector_meta.get("primary_sector_name") or sector_meta.get("sector_name"),
        "theme_ids": list(sector_meta.get("theme_ids") or ([sector_meta.get("sector_id")] if sector_meta.get("sector_id") else [])),
        "theme_names": list(sector_meta.get("theme_names") or ([sector_meta.get("sector_name")] if sector_meta.get("sector_name") else [])),
        "price": price,
        "change_pct": _safe_float((close.iloc[-1] / close.iloc[-2] - 1) * 100, 2) if len(close) > 1 and close.iloc[-2] else None,
        "return_5d": _ret(close, 5),
        "return_20d": _ret(close, 20),
        "return_63d": stock_ret_63,
        "return_126d": _ret(close, 126),
        "return_252d": _ret(close, 252),
        "rs_spy_63d": _safe_float((stock_ret_63 - spy_ret_63), 5) if stock_ret_63 is not None and spy_ret_63 is not None else None,
        "dist_sma20": _safe_float((price / sma20 - 1), 5) if sma20 else None,
        "dist_sma50": _safe_float((price / sma50 - 1), 5) if sma50 else None,
        "dist_sma200": _safe_float((price / sma200 - 1), 5) if sma200 else None,
        "above_sma20": (price > sma20) if sma20 is not None else None,
        "above_sma50": (price > sma50) if sma50 is not None else None,
        "above_sma200": (price > sma200) if sma200 is not None else None,
        "ma_alignment": _safe_float(ma_alignment, 2),
        "rsi14": _rsi(close),
        "macd_direction": _macd_direction(close),
        "atr_pct": _atr_pct(hist),
        "rel_volume": rel_volume,
        "avg_volume_20d": int(avg_vol20) if avg_vol20 is not None else None,
        "avg_dollar_volume_20d": avg_dollar_vol,
        "avg_dollar_volume_20d_calculation_method": (
            "mean_close_times_volume_20d" if avg_dollar_vol is not None else "unavailable"
        ),
        "calculation_method": {
            "average_dollar_volume_20d": (
                "mean_close_times_volume_20d" if avg_dollar_vol is not None else "unavailable"
            )
        },
        "ath_proximity": _safe_float(price / high_52w * 100, 2) if high_52w else None,
        "drawdown_3m": _safe_float((price / high_3m - 1) * 100, 2) if high_3m else None,
        "near_3m_high": bool(high_3m and price >= high_3m * 0.985),
        "breakout_confirmed": bool(high_3m and price >= high_3m * 0.995 and (rel_volume or 0) >= 1.15),
        "follow_through": bool(len(close) >= 5 and close.tail(3).min() >= close.tail(20).mean()),
        "vol_price_match": vol_price_match,
        "volume_truth": vol_price_match,
        "price_action": price_action,
        "history_days": len(close),
    }


def _complete_daily_frame(
    hist: pd.DataFrame,
    as_of: datetime,
) -> tuple[pd.DataFrame, datetime]:
    """Trim a daily frame to the latest completed US regular session."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    from app.services.market_calendar import early_close_minutes, is_trading_day

    local = as_of.astimezone(_NEW_YORK)
    completed = local.date()
    close_minutes = early_close_minutes(completed) or 16 * 60
    if not is_trading_day(completed) or local.hour * 60 + local.minute < close_minutes:
        completed -= timedelta(days=1)
        while not is_trading_day(completed):
            completed -= timedelta(days=1)
    completed_close_minutes = early_close_minutes(completed) or 16 * 60
    cutoff = datetime(
        completed.year,
        completed.month,
        completed.day,
        completed_close_minutes // 60,
        completed_close_minutes % 60,
        tzinfo=_NEW_YORK,
    )
    bounded = hist.copy()
    if isinstance(bounded.index, pd.DatetimeIndex):
        bounded = bounded[pd.Index(bounded.index.date) <= completed]
    return bounded, cutoff


def _actual_daily_data_through(hist: pd.DataFrame) -> datetime | None:
    """Return the regular-session close represented by the last real daily bar."""

    if hist.empty or not isinstance(hist.index, pd.DatetimeIndex):
        return None
    from app.services.market_calendar import early_close_minutes, is_trading_day

    session_days: list[date] = []
    for raw in hist.index:
        timestamp = pd.Timestamp(raw)
        if pd.isna(timestamp):
            continue
        if timestamp.tzinfo is not None:
            session_day = timestamp.tz_convert(_NEW_YORK).date()
        else:
            session_day = timestamp.date()
        if is_trading_day(session_day):
            session_days.append(session_day)
    if not session_days:
        return None
    session_day = max(session_days)
    close_minutes = early_close_minutes(session_day) or 16 * 60
    return datetime(
        session_day.year,
        session_day.month,
        session_day.day,
        close_minutes // 60,
        close_minutes % 60,
        tzinfo=_NEW_YORK,
    ).astimezone(timezone.utc)


def _completed_daily_key(as_of: datetime) -> str:
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    from app.services.market_calendar import early_close_minutes, is_trading_day

    local = as_of.astimezone(_NEW_YORK)
    completed = local.date()
    close_minutes = early_close_minutes(completed) or 16 * 60
    if not is_trading_day(completed) or local.hour * 60 + local.minute < close_minutes:
        completed -= timedelta(days=1)
        while not is_trading_day(completed):
            completed -= timedelta(days=1)
    return completed.isoformat()


def _intrinsic_row(
    row: dict[str, Any],
    hist: pd.DataFrame | None,
    *,
    range_feature: dict[str, Any],
    range_mode: str,
    range_trend_weight: float = 0.15,
    range_final_cap: float = 0.04,
) -> dict[str, Any]:
    """Compatibility wrapper around the one canonical intrinsic engine."""

    result = score_intrinsic(
        row,
        hist,
        range_feature=range_feature,
        range_mode=range_mode,
        range_trend_weight=range_trend_weight,
        range_final_cap=range_final_cap,
    )
    intrinsic_score = result.get("score")
    if intrinsic_score is None:
        classification = "数据不足"
    elif intrinsic_score >= 78:
        classification = "质量趋势"
    elif intrinsic_score >= 68:
        classification = "相对强势"
    elif intrinsic_score >= 58:
        classification = "观察"
    else:
        classification = "偏弱"
    factor_breakdown = dict(result.get("factor_breakdown") or {})
    return {
        **row,
        "score": intrinsic_score,
        "intrinsic_score": intrinsic_score,
        "market_fit_score": None,
        "profile_fit_score": None,
        "ranking_score": None,
        "score_scope": "intrinsic",
        "score_status": result.get("status"),
        "score_version": result.get("score_version") or STRENGTH_SCORE_VERSION,
        "feature_version": result.get("feature_version") or STRENGTH_FEATURE_VERSION,
        "normalization_version": (
            result.get("normalization_version") or STRENGTH_NORMALIZATION_VERSION
        ),
        "confidence": result.get("confidence", 0.0),
        "coverage": dict(result.get("coverage") or {}),
        "configured_weights": dict(result.get("configured_weights") or {}),
        "effective_weights": dict(result.get("effective_weights") or {}),
        "contributions": dict(result.get("contributions") or {}),
        "missing_components": list(result.get("missing_components") or []),
        "included_features": list(result.get("included_features") or []),
        "factor_breakdown": factor_breakdown,
        "final_score": intrinsic_score,
        "strength_score": intrinsic_score,
        "score_short": _safe_float(result.get("score_short"), 1),
        "score_mid": _safe_float(result.get("score_mid"), 1),
        "score_long": _safe_float(result.get("score_long"), 1),
        "breakout_quality_score": _safe_float(
            result.get("breakout_quality_score"), 1
        ),
        "price_action_score": _safe_float(result.get("price_action_score"), 1),
        "classification": classification,
        "label": classification,
        "breakdown": factor_breakdown,
        "data_quality": round(float(result.get("confidence") or 0.0) * 100),
        "range_persistence": result.get("range_persistence"),
        "range_persistence_shadow": result.get("range_persistence_shadow"),
        "option_heat_score": None,
        "option_score_weight": 0.0,
        "option_activity": None,
        "option_risk": None,
        "option_direction": None,
        "option_context": {
            "status": "skipped",
            "source_status": "skipped",
            "reason": "options do not enter intrinsic strength",
        },
        "market_regime_score": None,
        "sector_score": None,
    }


def _risk_penalty(row: dict[str, Any], min_avg_dollar_volume: float, profile: str) -> tuple[float, list[str], list[str]]:
    tilt = PROFILE_TILT.get(profile, PROFILE_TILT["balanced"])
    penalty = 0.0
    flags: list[str] = []
    warnings: list[str] = []

    atr = row.get("atr_pct")
    if atr is not None and atr > 7:
        penalty += 12
        flags.append("高波动")
        warnings.append(f"ATR约{atr:.1f}%，波动风险高")
    elif atr is not None and atr > 5:
        penalty += 7
        flags.append("波动偏高")

    if row.get("above_sma200") is False:
        penalty += 8
        flags.append("低于200日线")
        warnings.append("长期趋势仍未修复")

    avg_dollar = _safe_float(row.get("avg_dollar_volume_20d"), 2)
    if avg_dollar is not None and avg_dollar < min_avg_dollar_volume * 1.4:
        penalty += 4
        flags.append("流动性边缘")

    drawdown = row.get("drawdown_3m")
    if drawdown is not None and drawdown < -22:
        penalty += 7
        flags.append("回撤较深")

    vol_price = row.get("vol_price_match") if isinstance(row.get("vol_price_match"), dict) else {}
    setup_type = str(vol_price.get("setup_type") or "")
    vol_adjustment = _safe_float(vol_price.get("risk_penalty_adjustment"), 1) or 0.0
    if vol_adjustment:
        penalty += vol_adjustment
    if setup_type == "vacuum":
        flags.append("真空型")
        warnings.append("真空型收缩，假突破风险偏高")
    elif setup_type == "absorption_bearish":
        flags.append("空头吸收")
        warnings.append("空头吸收结构，向上突破需要更强确认")
    elif setup_type == "absorption_bullish":
        flags.append("多头吸收")

    return round(penalty * tilt["risk"], 1), flags, warnings


def _classify(row: dict[str, Any], final_score: float | None, risk_penalty: float) -> str:
    if final_score is None:
        return "数据不足"
    ma_alignment = _safe_float(row.get("ma_alignment"), 2)
    if final_score >= 78 and ma_alignment is not None and ma_alignment >= 66:
        return "质量趋势"
    if final_score >= 70 and (row.get("rel_volume") or 0) >= 1.5 and (row.get("ath_proximity") or 0) >= 88:
        return "放量突破"
    if final_score >= 64 and (row.get("rs_spy_63d") or 0) > 0:
        return "相对强势"
    rsi = _safe_float(row.get("rsi14"), 2)
    if final_score >= 58 and rsi is not None and rsi < 52:
        return "回暖候选"
    if risk_penalty >= 16:
        return "高风险题材"
    return "观察"


def _score_rows(
    rows: list[dict[str, Any]],
    market: dict[str, Any],
    profile: str,
    min_avg_dollar_volume: float,
) -> list[dict[str, Any]]:
    """Add market/profile/ranking layers without changing intrinsic scores.

    ``min_avg_dollar_volume`` remains in the public signature for compatibility,
    but page filter thresholds are deliberately excluded from every score.
    """

    del min_avg_dollar_volume
    market_fit = score_market_fit(market)
    scored: list[dict[str, Any]] = []
    for source_row in rows:
        row = source_row
        if "intrinsic_score" not in row:
            row = _intrinsic_row(
                source_row,
                None,
                range_feature={"status": "disabled", "version": RANGE_PERSISTENCE_VERSION},
                range_mode="disabled",
            )
        intrinsic = {
            "score": row.get("intrinsic_score"),
            "status": row.get("score_status"),
            "confidence": row.get("confidence", 0.0),
        }
        profile_fit = score_profile_fit(row, profile)
        ranking = score_ranking(intrinsic, market_fit, profile_fit)
        ranking_score = _safe_float(ranking.get("score"), 1)
        intrinsic_score = _safe_float(row.get("intrinsic_score"), 1)
        risk_penalty, risk_flags, warnings = _risk_penalty(
            row,
            10_000_000,
            profile,
        )
        classification = _classify(row, ranking_score, risk_penalty)

        no_market = score_ranking(
            intrinsic,
            {
                "score": None,
                "status": "insufficient_data",
                "confidence": 0.0,
            },
            profile_fit,
        )
        no_market_score = _safe_float(no_market.get("score"), 4)
        market_adjustment = (
            round(float(ranking.get("score")) - no_market_score, 2)
            if ranking.get("score") is not None and no_market_score is not None
            else None
        )

        tags: list[str] = []
        reasons: list[str] = []
        if row.get("rs_spy_63d") is not None and row["rs_spy_63d"] > 0:
            tags.append("相对SPY强")
            reasons.append("近3个月跑赢SPY")
        if row.get("ath_proximity") is not None and row["ath_proximity"] >= 90:
            tags.append("接近52周高位")
            reasons.append("价格接近一年高点区域")
        if row.get("rel_volume") is not None and row["rel_volume"] >= 1.5:
            tags.append("放量")
            reasons.append(f"成交量约为20日均量{row['rel_volume']:.1f}倍")
        price_action = row.get("price_action") if isinstance(row.get("price_action"), dict) else {}
        volume_truth = row.get("vol_price_match") if isinstance(row.get("vol_price_match"), dict) else {}
        for tag in volume_truth.get("tags", [])[:2]:
            if tag not in {"未明显收缩", "量价样本不足"}:
                tags.append(str(tag))
        for tag in price_action.get("tags", [])[:2]:
            if tag not in {"K线数据不足", "K线样本不足", "区间震荡"}:
                tags.append(str(tag))
        if price_action.get("structure") == "uptrend":
            reasons.append("HH/HL 上升结构完好")
        elif price_action.get("spring"):
            reasons.append("Spring 假跌破后回收，结构偏多")
        elif price_action.get("structure") == "downtrend":
            warnings.append("LH/LL 下降结构未破坏")
        if price_action.get("upthrust"):
            warnings.append("前高假突破（Upthrust），追高需谨慎")
        if (row.get("ma_alignment") or 0) >= 66:
            tags.append("均线多头")
            reasons.append("价格位于关键均线上方")
        market_score = _safe_float(market_fit.get("score"), 1)
        if market_score is not None and market_score >= 64:
            tags.append("市场顺风")
        elif market_score is not None and market_score < 40:
            tags.append("弱市降权")
        elif market_score is None:
            warnings.append("市场行情不足，市场维度暂不计入评分")
        tags.extend(risk_flags[:2])
        if not reasons:
            reasons.append(
                "可用价格证据已完成评分"
                if intrinsic_score is not None
                else "价格证据不足，暂不生成强势结论"
            )

        factor_breakdown = dict(row.get("factor_breakdown") or {})
        legacy_breakdown = {
            "relative_strength": (
                factor_breakdown.get("factor_families", {}).get("mid")
                if isinstance(factor_breakdown.get("factor_families"), dict)
                else None
            ),
            "trend": (
                factor_breakdown.get("factor_families", {}).get("trend")
                if isinstance(factor_breakdown.get("factor_families"), dict)
                else None
            ),
            "volume": None,
            "breakout": row.get("breakout_quality_score"),
            "base_breakout": (
                factor_breakdown.get("family_details", {}).get("breakout", {}).get("score")
                if isinstance(factor_breakdown.get("family_details"), dict)
                else None
            ),
            "price_action": row.get("price_action_score"),
            "price_action_detail": {
                "structure": price_action.get("structure"),
                "structure_label": price_action.get("structure_label"),
                "patterns": price_action.get("pattern_labels") or [],
                "spring": bool(price_action.get("spring")),
                "upthrust": bool(price_action.get("upthrust")),
                "support": price_action.get("support"),
                "resistance": price_action.get("resistance"),
                "support_dist_pct": price_action.get("support_dist_pct"),
                "resistance_dist_pct": price_action.get("resistance_dist_pct"),
            },
            "technical": row.get("score_short"),
            "sector": row.get("sector_rank_percentile"),
            "option_heat": row.get("option_heat_score"),
            "risk_penalty": risk_penalty,
            "market_regime": market_score,
            "market_regime_scoring_value": market_score,
            "risk_on_spread": _safe_float(market.get("risk_on_spread_score"), 1),
            "volume_truth": {
                "setup_type": volume_truth.get("setup_type"),
                "setup_label": volume_truth.get("setup_label"),
                "breakout_quality_adjustment": volume_truth.get("breakout_quality_adjustment"),
                "false_breakout_risk": volume_truth.get("false_breakout_risk"),
            },
            "market_adjustment": market_adjustment,
            "market_rules": dict(ranking.get("effective_weights") or {}),
            "intrinsic": factor_breakdown,
            "profile_fit": profile_fit,
            "market_fit": market_fit,
            "ranking": ranking,
        }
        existing_range = (
            row.get("breakdown", {}).get("range_persistence")
            if isinstance(row.get("breakdown"), dict)
            else None
        )
        if existing_range is not None:
            legacy_breakdown["range_persistence"] = existing_range

        scored.append({
            **row,
            "intrinsic_score": intrinsic_score,
            "market_fit_score": market_score,
            "profile_fit_score": _safe_float(profile_fit.get("score"), 1),
            "ranking_score": ranking_score,
            "score_scope": "ranking",
            "score_status": ranking.get("status"),
            "score_version": STRENGTH_SCORE_VERSION,
            "feature_version": STRENGTH_FEATURE_VERSION,
            "normalization_version": STRENGTH_NORMALIZATION_VERSION,
            "confidence": ranking.get("confidence", 0.0),
            "intrinsic_confidence": intrinsic.get("confidence", 0.0),
            "coverage": {
                "ranking": {
                    "status": ranking.get("status"),
                    "ratio": ranking.get("confidence", 0.0),
                    "active_weight": ranking.get("active_weight"),
                },
                "intrinsic": dict(row.get("coverage") or {}),
                "profile_fit": {
                    "status": profile_fit.get("status"),
                    "ratio": profile_fit.get("confidence", 0.0),
                },
                "market_fit": {
                    "status": market_fit.get("status"),
                    "ratio": market_fit.get("confidence", 0.0),
                },
            },
            "configured_weights": dict(ranking.get("configured_weights") or {}),
            "effective_weights": dict(ranking.get("effective_weights") or {}),
            "contributions": dict(ranking.get("contributions") or {}),
            "missing_components": list(
                dict.fromkeys(
                    [
                        *list(row.get("missing_components") or []),
                        *list(ranking.get("missing_components") or []),
                    ]
                )
            ),
            "factor_breakdown": factor_breakdown,
            "final_score": ranking_score,
            "strength_score": ranking_score,
            "market_regime_score": market_score,
            "risk_on_spread_score": market.get("risk_on_spread_score"),
            "risk_penalty": risk_penalty,
            "classification": classification,
            "label": classification,
            "tags": list(dict.fromkeys(tags))[:6],
            "reasons": reasons[:4],
            "warnings": list(dict.fromkeys(warnings))[:5],
            "breakdown": legacy_breakdown,
            "data_quality": round(float(ranking.get("confidence") or 0.0) * 100),
            "option_heat_score": row.get("option_heat_score"),
            "option_score_weight": 0.0,
            "option_activity": row.get("option_activity"),
            "option_risk": row.get("option_risk"),
            "option_direction": row.get("option_direction"),
            "option_context": row.get("option_context") or {
                "status": "unavailable",
                "source_status": "unavailable",
                "reason": "option data is not part of strength ranking",
            },
            "data_sources": {
                "prices": "Yahoo/yfinance",
                "technicals": "Yahoo/yfinance",
                "fundamentals": "not_configured",
                "options": "not_used_for_scoring",
            },
        })

    scored.sort(
        key=lambda item: (
            item.get("ranking_score") is not None,
            item.get("ranking_score") if item.get("ranking_score") is not None else -1,
            item.get("ticker") or "",
        ),
        reverse=True,
    )
    return scored


def _refresh_classifications(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        final_score = _safe_float(row.get("final_score"), 1)
        if final_score is None:
            continue
        classification = _classify(row, final_score, _safe_float(row.get("risk_penalty"), 1) or 0.0)
        row["classification"] = classification
        row["label"] = classification


def _sort_scored(rows: list[dict[str, Any]], timeframe: str) -> None:
    if timeframe in {"short", "mid", "long"}:
        key = f"score_{timeframe}"
        rows.sort(
            key=lambda item: (
                item.get(key) is not None,
                (_safe_float(item.get(key), 4) or 0.0) * .94
                + (_safe_float(item.get("ranking_score"), 4) or 0.0) * .06,
                item.get("ticker") or "",
            ),
            reverse=True,
        )
        return
    rows.sort(
        key=lambda item: (
            item.get("ranking_score") is not None,
            _safe_float(item.get("ranking_score"), 4) or 0.0,
            item.get("ticker") or "",
        ),
        reverse=True,
    )


def _sector_strength(rows: list[dict[str, Any]], period: str = "3mo") -> list[dict[str, Any]]:
    if period not in SECTOR_PERIOD_DAYS:
        raise ValueError(f"Unsupported sector period: {period}")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sid = row.get("sector_id")
        if sid:
            grouped.setdefault(sid, []).append(row)
    sectors = []
    for sid, items in grouped.items():
        period_averages: dict[str, float | None] = {}
        for period_name, days in SECTOR_PERIOD_DAYS.items():
            values = [x.get(f"return_{days}d") for x in items if x.get(f"return_{days}d") is not None]
            period_averages[period_name] = round(sum(values) / len(values) * 100, 2) if values else None
        final = [x.get("final_score") for x in items if x.get("final_score") is not None]
        leaders = sorted(items, key=lambda x: x.get("final_score") or 0, reverse=True)[:4]
        selected_return = period_averages[period]
        sectors.append({
            "sector_id": sid,
            "id": sid,
            "name": SECTORS.get(sid, {}).get("name", sid),
            "count": len(items),
            "period": period,
            "period_days": SECTOR_PERIOD_DAYS[period],
            "avg_return": selected_return,
            "avg_return_period": selected_return,
            "avg_return_1mo": period_averages["1mo"],
            "avg_return_3mo": period_averages["3mo"],
            "avg_return_6mo": period_averages["6mo"],
            # Backward-compatible alias; it always remains a true 63-day value.
            "avg_return_3m": period_averages["3mo"],
            "avg_strength": round(sum(final) / len(final), 1) if final else None,
            "leaders": [{"ticker": x["ticker"], "score": x["final_score"]} for x in leaders],
        })
    sectors.sort(
        key=lambda sector: (
            sector.get("avg_return") is not None,
            sector.get("avg_return") if sector.get("avg_return") is not None else float("-inf"),
            sector.get("avg_strength") or 0,
        ),
        reverse=True,
    )
    return sectors


def _combined_options_status(yahoo_status: dict[str, Any], marketdata_status: dict[str, Any]) -> dict[str, Any]:
    yahoo_active = yahoo_status.get("status") == "active"
    marketdata_active = marketdata_status.get("status") == "active"
    if yahoo_active and marketdata_active:
        provider = "Yahoo/yfinance + MarketData.app"
    elif marketdata_active:
        provider = "MarketData.app"
    elif yahoo_active:
        provider = "Yahoo/yfinance"
    else:
        provider = "Yahoo/yfinance / MarketData.app"

    if yahoo_active or marketdata_active:
        status = "active"
    elif yahoo_status.get("status") == "degraded" or marketdata_status.get("status") == "degraded":
        status = "degraded"
    elif yahoo_status.get("status") == "disabled" and marketdata_status.get("status") == "disabled":
        status = "disabled"
    else:
        status = marketdata_status.get("status") or yahoo_status.get("status") or "unavailable"

    messages = []
    if yahoo_status.get("message"):
        messages.append(str(yahoo_status["message"]))
    if marketdata_status.get("message"):
        messages.append(str(marketdata_status["message"]))

    return {
        "provider": provider,
        "status": status,
        "configured": bool(yahoo_status.get("configured") or marketdata_status.get("configured")),
        "enriched": int(yahoo_status.get("enriched") or 0) + int(marketdata_status.get("enriched") or 0),
        "failed": int(yahoo_status.get("failed") or 0) + int(marketdata_status.get("failed") or 0),
        "broad": yahoo_status,
        "refinement": marketdata_status,
        "candidate_pool": yahoo_status.get("candidate_pool"),
        "coverage": yahoo_status.get("coverage"),
        "message": "；".join(messages),
    }


def _scan_sync(
    *,
    universe: str,
    timeframe: str,
    profile: str,
    top: int,
    sector_id: str | None,
    min_price: float,
    min_avg_dollar_volume: float,
    include_options: bool = True,
    raw_history: pd.DataFrame | None = None,
) -> dict[str, Any]:
    from app.services.breakouts.config import get_breakout_settings

    breakout_settings = get_breakout_settings()
    observed_at = datetime.now(timezone.utc)
    if universe != "themes":
        raise ValueError(f"Unsupported universe: {universe}")
    if sector_id and sector_id not in SECTORS:
        raise ValueError(f"Unknown sector: {sector_id}")
    # Always score the complete canonical universe. View filters are applied
    # only after features, intrinsic scores and canonical ranks are frozen.
    tickers, sector_meta = _theme_universe()
    if not tickers:
        raise ValueError("No tickers in selected universe")
    universe_version = _canonical_universe_version(tickers, sector_meta)
    universe_as_of = observed_at.isoformat()

    all_symbols = list(dict.fromkeys(tickers + list(BENCHMARKS)))
    # Keep every production strength entry on the same feature horizon.  The
    # 252-session factors need more than a nominal one-year download once
    # holidays and incomplete sessions are removed.
    raw = (
        raw_history
        if raw_history is not None
        else _download_history(all_symbols, period=STRENGTH_HISTORY_PERIOD)
    )
    price_source = raw.attrs.get("price_source") or _history_status(
        provider="Yahoo/yfinance",
        status="active",
        message="日线价格、成交量与技术指标输入",
    )
    index_data = {
        symbol: _complete_daily_frame(_slice_ticker(raw, symbol), observed_at)[0]
        for symbol in BENCHMARKS
    }
    market = compute_market_regime(index_data, as_of=observed_at)
    spy = index_data.get("SPY", pd.DataFrame())

    range_context: dict[str, dict[str, Any]] = {}
    skipped = {
        "insufficient_history": 0,
        "low_price": 0,
        "low_liquidity": 0,
        "data_error": 0,
        "range_persistence_error": 0,
    }
    for ticker in tickers:
        try:
            hist, completed_cutoff = _complete_daily_frame(
                _slice_ticker(raw, ticker),
                observed_at,
            )
            row = _feature_row(ticker, hist, spy, sector_meta.get(ticker, {}))
            if not row:
                skipped["insufficient_history"] += 1
                continue
            try:
                range_feature = (
                    {
                        "status": "disabled",
                        "version": breakout_settings.range_persistence_version,
                    }
                    if breakout_settings.range_persistence_mode == "disabled"
                    else compute_range_persistence(
                        hist,
                        cutoff=completed_cutoff,
                        length=breakout_settings.range_persistence_length,
                        fast_length=breakout_settings.range_persistence_fast_length,
                        slope_lookback=breakout_settings.range_persistence_slope_days,
                        ratio_window=breakout_settings.range_persistence_ratio_window,
                        ratio_threshold=breakout_settings.range_persistence_ratio_threshold,
                        min_history_multiplier=(
                            breakout_settings.range_persistence_min_history_multiplier
                        ),
                        version=breakout_settings.range_persistence_version,
                    )
                )
                range_context[ticker] = _intrinsic_row(
                    row,
                    hist,
                    range_feature=range_feature,
                    range_mode=breakout_settings.range_persistence_mode,
                    range_trend_weight=(
                        breakout_settings.range_persistence_trend_family_weight
                    ),
                    range_final_cap=(
                        breakout_settings.range_persistence_final_weight_cap
                    ),
                )
            except Exception:
                skipped["range_persistence_error"] += 1
                unavailable_range = {
                    "status": "unavailable",
                    "version": breakout_settings.range_persistence_version,
                    "warnings": ["range_persistence_calculation_failed"],
                }
                range_context[ticker] = _intrinsic_row(
                    row,
                    hist,
                    range_feature=unavailable_range,
                    range_mode=breakout_settings.range_persistence_mode,
                    range_trend_weight=(
                        breakout_settings.range_persistence_trend_family_weight
                    ),
                    range_final_cap=(
                        breakout_settings.range_persistence_final_weight_cap
                    ),
                )
            actual_data_through = _actual_daily_data_through(hist)
            range_context[ticker]["daily_data_through"] = (
                actual_data_through.isoformat()
                if actual_data_through is not None
                else None
            )
        except Exception:
            skipped["data_error"] += 1

    intrinsic_rows = [
        range_context[ticker]
        for ticker in tickers
        if ticker in range_context and "intrinsic_score" in range_context[ticker]
    ]
    for item in intrinsic_rows:
        item["universe_version"] = universe_version
        item["universe_as_of"] = universe_as_of
        item["universe_member"] = True
    _attach_canonical_ranks(intrinsic_rows)
    scored = _score_rows(intrinsic_rows, market, profile, min_avg_dollar_volume)
    for item in scored:
        shadow = item.get("range_persistence_shadow") or {}
        item["range_persistence_mode"] = breakout_settings.range_persistence_mode
        production = _safe_float(shadow.get("production_score"), 4)
        hypothetical = _safe_float(shadow.get("hypothetical_score"), 4)
        score_delta = (
            round(hypothetical - production, 4)
            if production is not None and hypothetical is not None
            else None
        )
        item["range_persistence_score_delta"] = score_delta
        item.setdefault("breakdown", {})["range_persistence"] = {
            "mode": breakout_settings.range_persistence_mode,
            "feature": item.get("range_persistence"),
            "shadow": shadow,
            "legacy_score_delta": score_delta,
        }

    view_rows: list[dict[str, Any]] = []
    for item in scored:
        if sector_id and sector_id not in set(item.get("theme_ids") or []):
            continue
        price = _safe_float(item.get("price"), 4)
        if price is None or price < min_price:
            skipped["low_price"] += 1
            continue
        average_dollar_volume = _safe_float(
            item.get("avg_dollar_volume_20d"),
            2,
        )
        if (
            average_dollar_volume is None
            or average_dollar_volume < min_avg_dollar_volume
        ):
            skipped["low_liquidity"] += 1
            continue
        view_rows.append(item)

    if include_options:
        yahoo_options_status = enrich_rows_with_yahoo_options(view_rows, display_top=top)
    else:
        # Single-stock lookups don't need the expensive option-chain pass.
        yahoo_options_status = {
            "provider": "Yahoo/yfinance",
            "status": "skipped",
            "configured": True,
            "enriched": 0,
            "message": "单标的查询跳过期权粗筛（性能优化）",
        }
    _refresh_classifications(view_rows)
    _sort_scored(view_rows, timeframe)
    for selected_rank, item in enumerate(view_rows, start=1):
        item["selected_view_rank"] = selected_rank
    limited = view_rows[:top]
    finnhub_status = enrich_rows_with_finnhub(limited)
    if include_options:
        marketdata_status = enrich_rows_with_marketdata_options(limited)
    else:
        marketdata_status = {"provider": "MarketData.app", "status": "skipped", "configured": False, "enriched": 0, "message": "单标的查询跳过期权增强"}
    _refresh_classifications(limited)
    _sort_scored(limited, timeframe)
    options_status = _combined_options_status(yahoo_options_status, marketdata_status)
    return {
        "as_of": _now_iso(),
        "params": {
            "universe": universe,
            "timeframe": timeframe,
            "profile": profile,
            "top": top,
            "sector_id": sector_id,
            "min_price": min_price,
            "min_avg_dollar_volume": min_avg_dollar_volume,
            "range_persistence_mode": breakout_settings.range_persistence_mode,
            "range_persistence_version": breakout_settings.range_persistence_version,
        },
        "market_regime": market,
        "market_context": market.get("market_context", {}),
        "spread_matrix": market.get("spread_matrix", {}),
        "range_persistence_mode": breakout_settings.range_persistence_mode,
        "range_persistence_version": breakout_settings.range_persistence_version,
        "score_version": STRENGTH_SCORE_VERSION,
        "feature_version": STRENGTH_FEATURE_VERSION,
        "normalization_version": STRENGTH_NORMALIZATION_VERSION,
        "universe_version": universe_version,
        "universe_as_of": universe_as_of,
        "count": len(limited),
        "universe_count": len(tickers),
        "screened_count": len(view_rows),
        "skipped": skipped,
        "results": limited,
        "rows": limited,
        "sectors": _sector_strength(scored),
        "data_sources": {
            "prices": {
                "provider": price_source.get("provider") or "Yahoo/yfinance",
                "status": price_source.get("status") or "active",
                "message": price_source.get("message") or "日线价格、成交量与技术指标输入",
                "fallback_symbols": price_source.get("fallback_symbols") or [],
                "missing_symbols": price_source.get("missing_symbols") or [],
            },
            "fundamentals": finnhub_status,
            "options": {
                **options_status,
                "candidates": OPTION_DATA_SOURCE_CANDIDATES,
            },
            "range_persistence": {
                "status": breakout_settings.range_persistence_mode,
                "version": breakout_settings.range_persistence_version,
                "message": (
                    "影子计算，不改变生产排序"
                    if breakout_settings.range_persistence_mode == "shadow"
                    else "已进入生产排序"
                    if breakout_settings.range_persistence_mode == "enabled"
                    else "已停用"
                ),
            },
        },
    }


async def scan_strength(
    *,
    universe: str = "themes",
    timeframe: str = "all",
    profile: str = "balanced",
    top: int = 20,
    sector_id: str | None = None,
    min_price: float = 5.0,
    min_avg_dollar_volume: float = 10_000_000,
    include_options: bool = True,
    force_refresh: bool = False,
) -> dict[str, Any]:
    settings = get_settings()
    from app.services.breakouts.config import get_breakout_settings

    breakout_settings = get_breakout_settings()
    key = (
        f"strength:{universe}:{timeframe}:{profile}:{top}:{sector_id}:{min_price}:{min_avg_dollar_volume}"
        f":fh:{int(finnhub_is_enabled(settings))}:md:{int(marketdata_is_enabled(settings))}"
        f":yo:{int(yahoo_options_is_enabled(settings) and include_options)}:{settings.yahoo_options_enrich_limit}"
        f":ydte:{settings.yahoo_option_target_dte}:ywin:{settings.yahoo_option_strike_window_pct}"
        f":opt:{int(include_options)}"
        f":rp:{breakout_settings.range_persistence_mode}:{breakout_settings.range_persistence_version}"
        f":rpl:{breakout_settings.range_persistence_length}:{breakout_settings.range_persistence_fast_length}"
        f":rps:{breakout_settings.range_persistence_slope_days}:{breakout_settings.range_persistence_ratio_window}"
        f":rpt:{breakout_settings.range_persistence_ratio_threshold}:{breakout_settings.range_persistence_min_history_multiplier}"
        f":rpw:{breakout_settings.range_persistence_trend_family_weight}:{breakout_settings.range_persistence_final_weight_cap}"
        f":score:{STRENGTH_SCORE_VERSION}:{STRENGTH_FEATURE_VERSION}:{STRENGTH_NORMALIZATION_VERSION}"
        f":canonical-universe:themes:{MARKET_SHAPE_VERSION}"
    )

    async def produce() -> dict[str, Any]:
        import asyncio

        raw_history: pd.DataFrame | None = None
        if universe == "themes":
            tickers, _metadata = _theme_universe()
            all_symbols = list(dict.fromkeys([*tickers, *BENCHMARKS]))
            symbol_hash = hashlib.sha256(
                ",".join(all_symbols).encode("ascii")
            ).hexdigest()[:16]
            history_key = (
                f"strength-history:{STRENGTH_HISTORY_PERIOD}:"
                f"{_completed_daily_key(datetime.now(timezone.utc))}:{symbol_hash}"
                f":fh:{int(bool(settings.finnhub_candle_fallback_enabled))}:"
                f"{settings.finnhub_candle_fallback_limit}"
                f":md:{int(bool(settings.marketdata_stock_candle_fallback_enabled))}:"
                f"{settings.marketdata_stock_candle_fallback_limit}"
                f":stooq:{int(bool(settings.stooq_price_fallback_enabled))}:"
                f"{settings.stooq_price_fallback_limit}"
            )

            async def load_history() -> pd.DataFrame:
                history = await asyncio.to_thread(
                    _download_history,
                    all_symbols,
                    period=STRENGTH_HISTORY_PERIOD,
                )
                # Frames without a usable theme ticker are transient provider
                # failures, not valid 15-minute scan snapshots. Raising keeps
                # both cache layers from retaining a false empty result.
                if not _has_usable_history(history, tickers):
                    raise RuntimeError("strength_price_history_unavailable")
                return history

            if force_refresh:
                raw_history = await load_history()
                cache.set(history_key, raw_history, STRENGTH_CACHE_TTL_SECONDS)
            else:
                raw_history, _, _ = await cache.get_or_set_with_meta(
                    history_key,
                    STRENGTH_CACHE_TTL_SECONDS,
                    load_history,
                )

        return await asyncio.to_thread(
            _scan_sync,
            universe=universe,
            timeframe=timeframe,
            profile=profile,
            top=top,
            sector_id=sector_id,
            min_price=min_price,
            min_avg_dollar_volume=min_avg_dollar_volume,
            include_options=include_options,
            raw_history=raw_history,
        )

    if force_refresh:
        payload = await produce()
        cache.set(key, payload, STRENGTH_CACHE_TTL_SECONDS)
        cached = cache.get_with_expiry(key)
        if cached is None:  # pragma: no cover - set() guarantees this branch
            raise RuntimeError("strength_cache_write_failed")
        expires_at = cached[0]
        was_cached = False
    else:
        payload, was_cached, expires_at = await cache.get_or_set_with_meta(
            key,
            STRENGTH_CACHE_TTL_SECONDS,
            produce,
        )
    return {
        **payload,
        "_cached": was_cached,
        "cache_ttl_seconds": STRENGTH_CACHE_TTL_SECONDS,
        "cache_expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


def _score_ticker_frames_sync(
    tickers: list[str],
    *,
    frames: Mapping[str, pd.DataFrame],
    as_of: datetime,
    range_mode: str,
    range_version: str = RANGE_PERSISTENCE_VERSION,
    range_length: int = 35,
    range_fast_length: int = 3,
    range_slope_days: int = 5,
    range_ratio_window: int = 10,
    range_ratio_threshold: float = 60.0,
    range_min_history_multiplier: int = 5,
    range_trend_weight: float = 0.15,
    range_final_cap: float = 0.04,
    price_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from app.services.breakouts.models import normalize_ticker

    symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
    if not symbols:
        raise ValueError("ticker set must not be empty")
    if len(symbols) > 150:
        raise ValueError("ticker set exceeds 150 symbols")
    source = dict(price_source or {})
    spy, _ = _complete_daily_frame(frames.get("SPY", pd.DataFrame()), as_of)
    theme_tickers, theme_meta = _theme_universe()
    theme_members = set(theme_tickers)
    universe_version = _canonical_universe_version(theme_tickers, theme_meta)
    rows: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}
    for symbol in symbols:
        hist, completed_cutoff = _complete_daily_frame(
            frames.get(symbol, pd.DataFrame()),
            as_of,
        )
        row = _feature_row(symbol, hist, spy, theme_meta.get(symbol, {}))
        if row is None:
            skipped[symbol] = "insufficient_history"
            continue
        if range_mode == "disabled":
            range_feature = {"status": "disabled", "version": range_version}
        else:
            try:
                range_feature = compute_range_persistence(
                    hist,
                    cutoff=completed_cutoff,
                    length=range_length,
                    fast_length=range_fast_length,
                    slope_lookback=range_slope_days,
                    ratio_window=range_ratio_window,
                    ratio_threshold=range_ratio_threshold,
                    min_history_multiplier=range_min_history_multiplier,
                    version=range_version,
                )
            except Exception:
                range_feature = {
                    "status": "unavailable",
                    "version": range_version,
                    "warnings": ["range_persistence_calculation_failed"],
                }
        scored = _intrinsic_row(
            row,
            hist,
            range_feature=range_feature,
            range_mode=range_mode,
            range_trend_weight=range_trend_weight,
            range_final_cap=range_final_cap,
        )
        scored["as_of"] = as_of.astimezone(timezone.utc).isoformat()
        scored["universe_version"] = universe_version
        scored["universe_as_of"] = as_of.astimezone(timezone.utc).isoformat()
        scored["universe_member"] = symbol in theme_members
        scored["global_rank_percentile"] = None
        scored["sector_rank_percentile"] = None
        scored["selected_view_rank"] = None
        scored["cross_section_status"] = {
            "global": "cross_section_unavailable",
            "sector": "cross_section_unavailable",
        }
        rows.append(scored)
    rows.sort(
        key=lambda item: (
            item.get("final_score") is not None,
            item.get("final_score") if item.get("final_score") is not None else -1,
            item["ticker"],
        ),
        reverse=True,
    )
    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "score_scope": "intrinsic",
        "score_version": INTRINSIC_STRENGTH_VERSION,
        "feature_version": STRENGTH_FEATURE_VERSION,
        "normalization_version": STRENGTH_NORMALIZATION_VERSION,
        "universe_version": universe_version,
        "universe_as_of": as_of.astimezone(timezone.utc).isoformat(),
        "range_persistence_mode": range_mode,
        "ticker_set_hash": hashlib.sha256(
            ",".join(sorted(symbols)).encode("ascii")
        ).hexdigest(),
        "count": len(rows),
        "requested_count": len(symbols),
        "rows": rows,
        "results": rows,
        "skipped": skipped,
        "data_sources": {
            "prices": {
                "provider": source.get("provider") or "Yahoo/yfinance",
                "status": source.get("status") or "active",
                "message": source.get("message") or "explicit ticker-set daily prices",
            },
            "options": {
                "status": "skipped",
                "message": "intrinsic scoring excludes options",
            },
            "market_shape": {
                "status": "not_used",
                "message": "market shape never changes intrinsic score",
            },
        },
    }


def _score_ticker_set_sync(
    tickers: list[str],
    *,
    as_of: datetime,
    range_mode: str,
    range_version: str = RANGE_PERSISTENCE_VERSION,
    range_length: int = 35,
    range_fast_length: int = 3,
    range_slope_days: int = 5,
    range_ratio_window: int = 10,
    range_ratio_threshold: float = 60.0,
    range_min_history_multiplier: int = 5,
    range_trend_weight: float = 0.15,
    range_final_cap: float = 0.04,
) -> dict[str, Any]:
    from app.services.breakouts.models import normalize_ticker

    symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
    all_symbols = list(dict.fromkeys([*symbols, "SPY"]))
    raw = _download_history(all_symbols, period=STRENGTH_HISTORY_PERIOD)
    if not _has_usable_history(raw, symbols):
        # SPY alone cannot produce a valid requested-ticker score. Raising here
        # prevents the outer 15-minute cache from retaining a false empty set.
        raise RuntimeError("strength_ticker_set_history_unavailable")
    source = raw.attrs.get("price_source") or _history_status(
        provider="Yahoo/yfinance",
        status="active",
        message="explicit ticker-set daily prices",
    )
    frames = {symbol: _slice_ticker(raw, symbol) for symbol in all_symbols}
    return _score_ticker_frames_sync(
        symbols,
        frames=frames,
        as_of=as_of,
        range_mode=range_mode,
        range_version=range_version,
        range_length=range_length,
        range_fast_length=range_fast_length,
        range_slope_days=range_slope_days,
        range_ratio_window=range_ratio_window,
        range_ratio_threshold=range_ratio_threshold,
        range_min_history_multiplier=range_min_history_multiplier,
        range_trend_weight=range_trend_weight,
        range_final_cap=range_final_cap,
        price_source=source,
    )


async def score_ticker_set(
    tickers: list[str],
    *,
    as_of: datetime | None = None,
    profile: str = "balanced",
    include_options: bool = False,
    range_mode: str | None = None,
    range_version: str | None = None,
    range_length: int | None = None,
    range_fast_length: int | None = None,
    range_slope_days: int | None = None,
    range_ratio_window: int | None = None,
    range_ratio_threshold: float | None = None,
    range_min_history_multiplier: int | None = None,
    range_trend_weight: float | None = None,
    range_final_cap: float | None = None,
) -> dict[str, Any]:
    """Score an explicit ticker set without market, sector, option, or Top-N effects."""

    if profile not in PROFILES:
        raise ValueError(f"Unsupported profile: {profile}")
    if include_options:
        raise ValueError("intrinsic ticker-set scoring does not accept option enrichment")
    observed_at = as_of or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    from app.services.breakouts.config import get_breakout_settings

    breakout_settings = get_breakout_settings()
    if range_mode is None:
        range_mode = breakout_settings.range_persistence_mode
    if range_mode not in {"disabled", "shadow", "enabled"}:
        raise ValueError("invalid range persistence mode")
    range_version = range_version or breakout_settings.range_persistence_version
    range_length = (
        breakout_settings.range_persistence_length
        if range_length is None
        else int(range_length)
    )
    range_fast_length = (
        breakout_settings.range_persistence_fast_length
        if range_fast_length is None
        else int(range_fast_length)
    )
    range_slope_days = (
        breakout_settings.range_persistence_slope_days
        if range_slope_days is None
        else int(range_slope_days)
    )
    range_ratio_window = (
        breakout_settings.range_persistence_ratio_window
        if range_ratio_window is None
        else int(range_ratio_window)
    )
    range_ratio_threshold = (
        breakout_settings.range_persistence_ratio_threshold
        if range_ratio_threshold is None
        else float(range_ratio_threshold)
    )
    range_min_history_multiplier = (
        breakout_settings.range_persistence_min_history_multiplier
        if range_min_history_multiplier is None
        else int(range_min_history_multiplier)
    )
    range_trend_weight = (
        breakout_settings.range_persistence_trend_family_weight
        if range_trend_weight is None
        else float(range_trend_weight)
    )
    range_final_cap = (
        breakout_settings.range_persistence_final_weight_cap
        if range_final_cap is None
        else float(range_final_cap)
    )
    from app.services.breakouts.models import normalize_ticker

    symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
    ticker_hash = hashlib.sha256(
        ",".join(sorted(symbols)).encode("ascii")
    ).hexdigest()
    key = ":".join(
        [
            "strength-intrinsic",
            INTRINSIC_STRENGTH_VERSION,
            range_version,
            str(range_mode),
            str(range_length),
            str(range_fast_length),
            str(range_slope_days),
            str(range_ratio_window),
            str(range_ratio_threshold),
            str(range_min_history_multiplier),
            str(range_trend_weight),
            str(range_final_cap),
            _completed_daily_key(observed_at),
            ticker_hash,
        ]
    )

    async def produce() -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(
            _score_ticker_set_sync,
            symbols,
            as_of=observed_at,
            range_mode=str(range_mode),
            range_version=range_version,
            range_length=range_length,
            range_fast_length=range_fast_length,
            range_slope_days=range_slope_days,
            range_ratio_window=range_ratio_window,
            range_ratio_threshold=range_ratio_threshold,
            range_min_history_multiplier=range_min_history_multiplier,
            range_trend_weight=range_trend_weight,
            range_final_cap=range_final_cap,
        )

    payload, was_cached, expires_at = await cache.get_or_set_with_meta(
        key,
        STRENGTH_CACHE_TTL_SECONDS,
        produce,
    )
    current_as_of = observed_at.astimezone(timezone.utc).isoformat()
    rows = [{**row, "as_of": current_as_of} for row in payload.get("rows", [])]
    return {
        **payload,
        "as_of": current_as_of,
        "rows": rows,
        "results": rows,
        "_cached": was_cached,
        "cache_ttl_seconds": STRENGTH_CACHE_TTL_SECONDS,
        "cache_expires_at": datetime.fromtimestamp(
            expires_at,
            timezone.utc,
        ).isoformat(),
    }


async def score_ticker_frames(
    tickers: list[str],
    *,
    frames: Mapping[str, pd.DataFrame],
    as_of: datetime,
    range_mode: str,
    range_version: str = RANGE_PERSISTENCE_VERSION,
    range_length: int = 35,
    range_fast_length: int = 3,
    range_slope_days: int = 5,
    range_ratio_window: int = 10,
    range_ratio_threshold: float = 60.0,
    range_min_history_multiplier: int = 5,
    range_trend_weight: float = 0.15,
    range_final_cap: float = 0.04,
    price_source: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Score already-fetched complete daily frames without another download."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    if range_mode not in {"disabled", "shadow", "enabled"}:
        raise ValueError("invalid range persistence mode")
    import asyncio

    return await asyncio.to_thread(
        _score_ticker_frames_sync,
        list(tickers),
        frames=frames,
        as_of=as_of,
        range_mode=range_mode,
        range_version=range_version,
        range_length=range_length,
        range_fast_length=range_fast_length,
        range_slope_days=range_slope_days,
        range_ratio_window=range_ratio_window,
        range_ratio_threshold=range_ratio_threshold,
        range_min_history_multiplier=range_min_history_multiplier,
        range_trend_weight=range_trend_weight,
        range_final_cap=range_final_cap,
        price_source=price_source,
    )


async def sector_strength(period: str = "3mo") -> dict[str, Any]:
    if period not in SECTOR_PERIOD_DAYS:
        raise ValueError(f"Unsupported sector period: {period}")
    payload = await scan_strength(timeframe="all", profile="balanced", top=120)
    selected_key = f"avg_return_{period}"
    sectors = []
    for item in payload.get("sectors", []):
        selected_return = item.get(selected_key)
        sectors.append({
            **item,
            "period": period,
            "period_days": SECTOR_PERIOD_DAYS[period],
            "avg_return": selected_return,
            "avg_return_period": selected_return,
        })
    sectors.sort(
        key=lambda sector: (
            sector.get("avg_return") is not None,
            sector.get("avg_return") if sector.get("avg_return") is not None else float("-inf"),
            sector.get("avg_strength") or 0,
        ),
        reverse=True,
    )
    return {
        "as_of": payload["as_of"],
        "period": period,
        "period_days": SECTOR_PERIOD_DAYS[period],
        "sectors": sectors,
        "count": len(sectors),
        "_cached": payload.get("_cached", False),
        "cache_ttl_seconds": payload.get("cache_ttl_seconds"),
        "cache_expires_at": payload.get("cache_expires_at"),
    }


def _market_strength_sync(as_of: datetime) -> dict[str, Any]:
    """Load only market benchmarks; never trigger the full stock-universe scan."""

    raw = _download_history(list(MARKET_BENCHMARKS), period="2y")
    if not _has_usable_history(raw, MARKET_BENCHMARKS):
        raise RuntimeError("market_price_history_unavailable")
    price_source = raw.attrs.get("price_source") or _history_status(
        provider="Yahoo/yfinance",
        status="active",
        message="大盘形态日线输入",
    )
    index_data: dict[str, pd.DataFrame] = {}
    for symbol in MARKET_BENCHMARKS:
        bounded, _ = _complete_daily_frame(_slice_ticker(raw, symbol), as_of)
        index_data[symbol] = bounded
    market = compute_market_regime(index_data, as_of=as_of)
    return {
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "market_regime": market,
        "data_sources": {
            "prices": {
                "provider": price_source.get("provider") or "Yahoo/yfinance",
                "status": price_source.get("status") or "active",
                "message": price_source.get("message") or "大盘形态日线输入",
                "fallback_symbols": price_source.get("fallback_symbols") or [],
                "missing_symbols": price_source.get("missing_symbols") or [],
            }
        },
    }


async def market_strength(*, as_of: datetime | None = None) -> dict[str, Any]:
    """Return cached market regime and six-state shape without scanning stocks."""

    observed_at = as_of or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    settings = get_settings()
    key = (
        f"market-strength:{MARKET_SHAPE_VERSION}:{_completed_daily_key(observed_at)}"
        f":fh:{int(bool(settings.finnhub_candle_fallback_enabled))}"
        f":md:{int(bool(settings.marketdata_stock_candle_fallback_enabled))}"
        f":stooq:{int(bool(settings.stooq_price_fallback_enabled))}"
    )

    async def produce() -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(_market_strength_sync, observed_at)

    payload, was_cached, expires_at = await cache.get_or_set_with_meta(
        key,
        MARKET_STRENGTH_CACHE_TTL_SECONDS,
        produce,
    )
    return {
        **payload,
        "_cached": was_cached,
        "cache_ttl_seconds": MARKET_STRENGTH_CACHE_TTL_SECONDS,
        "cache_expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


async def stock_strength(ticker: str, profile: str = "balanced") -> dict[str, Any]:
    symbol = ticker.upper().strip()
    # Preserve the public endpoint's historical profile, market-regime and
    # classification semantics. Breakout Radar uses score_ticker_set directly.
    payload = await scan_strength(
        timeframe="all",
        profile=profile,
        top=250,
        min_price=0,
        min_avg_dollar_volume=0,
        include_options=False,
    )
    for row in payload.get("rows", []):
        if row.get("ticker") == symbol:
            return {
                "as_of": payload["as_of"],
                "ticker": symbol,
                "row": row,
                "market_regime": payload["market_regime"],
            }
    raise KeyError(symbol)


def profiles() -> dict[str, Any]:
    return {
        "profiles": list(PROFILES),
        "timeframes": list(TIMEFRAMES),
        "universes": list(UNIVERSES),
        "sectors": [{"id": sid, "name": sector["name"]} for sid, sector in SECTORS.items()],
    }
