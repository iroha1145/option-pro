from __future__ import annotations

import io
import hashlib
import math
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime, timedelta, timezone
from time import monotonic
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yfinance as yf

from app.config import get_settings
from app.failure_diagnostics import record_fallback_failure
from app.services import massive
from app.services import yahoo
from app.services.cache import cache
from app.services.finnhub_budget import mark_finnhub_rate_limited, reserve_finnhub_request
from app.services.sectors import SECTORS
from app.services.strength.features import (
    _feature_row,
    _safe_float,
)
from app.services.strength.market_regime import MARKET_BENCHMARKS, compute_market_regime
from app.services.strength.market_shape import MARKET_SHAPE_VERSION
from app.services.strength.scoring import (
    FEATURE_VERSION as STRENGTH_FEATURE_VERSION,
    NORMALIZATION_VERSION as STRENGTH_NORMALIZATION_VERSION,
    SCORE_VERSION as STRENGTH_SCORE_VERSION,
    score_intrinsic,
)
from app.services.yfinance_batch import download_in_bounded_batches
from app.services.technical.range_persistence import (
    RANGE_PERSISTENCE_VERSION,
    compute_range_persistence,
)

TIMEFRAMES = ("short", "mid", "long", "all")
PROFILES = ("conservative", "balanced", "aggressive")
UNIVERSES = ("themes",)
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


def _finalize_history_fetch(
    tickers: list[str],
    symbols: list[str],
    fetched: Mapping[str, pd.DataFrame],
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Common tail of the three fallback history fetchers.

    ``symbols`` is the (possibly limit-truncated) subset actually requested;
    ``tickers`` is the full request, so anything outside ``symbols`` is
    reported missing too, not silently dropped.
    """

    loaded = [symbol for symbol in symbols if symbol in fetched]
    frames = [fetched[symbol] for symbol in loaded]
    missing = [symbol for symbol in tickers if symbol not in fetched]
    if not frames:
        return pd.DataFrame(), loaded, missing
    return pd.concat(frames, axis=1).sort_index(), loaded, missing


_MARKETDATA_FALLBACK_LIMIT = 260
_STOOQ_FALLBACK_LIMIT = 260
_FINNHUB_FALLBACK_LIMIT = 80
_FALLBACK_REQUEST_TIMEOUT_SECONDS = 6.0


def _download_marketdata_history(tickers: list[str], period: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    settings = get_settings()
    token = settings.marketdata_token.strip()
    if not token:
        return pd.DataFrame(), [], tickers

    base_url = str(settings.marketdata_base_url).rstrip("/")
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_period_to_days(period) + 10)
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:_MARKETDATA_FALLBACK_LIMIT]
    timeout = _FALLBACK_REQUEST_TIMEOUT_SECONDS

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
    except Exception as exc:
        # A client-level failure (auth, network, budget) means every symbol in
        # this batch is unknown, not confirmed absent -- record which and why
        # rather than silently reporting them all missing.
        record_fallback_failure("strength_marketdata_history", exc)
        fetched = {}

    return _finalize_history_fetch(tickers, symbols, fetched)


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
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_period_to_days(period) + 10)
    symbols = [symbol for symbol in tickers if _stooq_symbol(symbol)][:_STOOQ_FALLBACK_LIMIT]
    timeout = _FALLBACK_REQUEST_TIMEOUT_SECONDS

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
    except Exception as exc:
        record_fallback_failure("strength_stooq_history", exc)
        fetched = {}

    return _finalize_history_fetch(tickers, symbols, fetched)


def _download_finnhub_history(tickers: list[str], period: str) -> tuple[pd.DataFrame, list[str], list[str]]:
    settings = get_settings()
    token = (settings.finnhub_api_key or "").strip()
    if not token:
        return pd.DataFrame(), [], tickers

    base_url = str(settings.finnhub_base_url).rstrip("/")
    end_ts = int(datetime.now(timezone.utc).timestamp())
    start_ts = end_ts - (_period_to_days(period) + 10) * 24 * 60 * 60
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:_FINNHUB_FALLBACK_LIMIT]
    timeout = _FALLBACK_REQUEST_TIMEOUT_SECONDS

    try:
        with httpx.Client(timeout=timeout, headers={"X-Finnhub-Token": token}) as client:
            def fetch_one(symbol: str) -> pd.DataFrame:
                if not reserve_finnhub_request(token, timeout=0):
                    raise RuntimeError("finnhub_budget_unavailable")
                response = client.get(
                    f"{base_url}/stock/candle",
                    params={
                        "symbol": symbol,
                        "resolution": "D",
                        "from": start_ts,
                        "to": end_ts,
                    },
                )
                if getattr(response, "status_code", None) == 429:
                    mark_finnhub_rate_limited(token, retry_after=getattr(response, "headers", {}).get("Retry-After", 60))
                response.raise_for_status()
                return _finnhub_candle_frame(symbol, response.json())

            fetched = _bounded_history_fetch(symbols, fetch_one, request_timeout_seconds=timeout)
    except Exception as exc:
        record_fallback_failure("strength_finnhub_history", exc)
        fetched = {}

    return _finalize_history_fetch(tickers, symbols, fetched)


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


def _validated_massive_history(
    rows: list[dict[str, Any]],
    *,
    period: str,
    end: date,
) -> list[dict[str, float]]:
    """Return distinct, coherent OHLCV bars or an empty list when incomplete."""

    minimum_rows = {
        "2y": 380,
        "1y": 190,
        "6mo": 95,
        "3mo": 45,
        "1mo": 15,
    }.get(str(period).lower(), 190)
    by_session: dict[date, dict[str, float]] = {}
    for row in rows:
        values: dict[str, float] = {}
        valid = True
        for key in ("t", "o", "h", "l", "c", "v"):
            raw = row.get(key)
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                valid = False
                break
            value = float(raw)
            if not math.isfinite(value):
                valid = False
                break
            values[key] = value
        if not valid:
            continue
        if (
            values["o"] <= 0
            or values["h"] <= 0
            or values["l"] <= 0
            or values["c"] <= 0
            or values["v"] < 0
            or values["l"] > min(values["o"], values["c"])
            or values["h"] < max(values["o"], values["c"])
            or values["l"] > values["h"]
        ):
            continue
        try:
            session = (
                pd.Timestamp(values["t"], unit="ms", tz="UTC")
                .tz_convert("America/New_York")
                .date()
            )
        except (OverflowError, TypeError, ValueError):
            continue
        if session > end:
            continue
        # Massive 日线理论上一交易日一根；同日重复不能充当历史长度。
        by_session[session] = values
    usable = [
        by_session[session]
        for session in sorted(by_session)
    ]
    if len(usable) < minimum_rows:
        return []
    latest = max(by_session)
    age_days = (end - latest).days
    if not 0 <= age_days <= 7:
        return []
    return usable


def download_massive_history(
    tickers: list[str],
    period: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Massive 主源日线(拆股复权、分红不复权,正股专用)。

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
            rows = _validated_massive_history(
                [bar for bar in (bars or []) if isinstance(bar, dict)],
                period=period,
                end=end,
            )
            if not rows:
                # 过短、过旧、重复或 OHLCV 残缺时必须交给 Yahoo/公开源补齐。
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
        massive_frame, massive_missing = download_massive_history(tickers, period)
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
        # Yahoo's unadjusted OHLC is split-adjusted but not dividend-adjusted,
        # the same basis as Massive's adjusted=true bars. auto_adjust=True would
        # put dividend-adjusted fallback rows in the same cross-section.
        "auto_adjust": False,
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
            if isinstance(candidate, pd.DataFrame) and isinstance(candidate.columns, pd.MultiIndex):
                candidate = candidate.drop(columns="Adj Close", level=1, errors="ignore")
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


#: Tier cut-offs; must stay identical to ``tierOf`` in the screener's types.ts.


def _load_macro_reader() -> Any:
    """The published macro snapshot, or a reader that reports why there is none.

    One reader is shared by the row-level shadow fields and the sector radar so
    both describe the same snapshot. Two independent reads could straddle a
    publication and put two different macro environments on one page.
    """

    try:
        from app.services.macro_conditions.linkage_reader import (
            REASON_MODULE,
            load_macro_fit_reader,
            unavailable_reader,
        )
    except Exception:
        return None
    try:
        return load_macro_fit_reader()
    except Exception:
        # load_macro_fit_reader already swallows its own failures; this only
        # covers an import-time surprise in a dependency it reaches lazily.
        return unavailable_reader(REASON_MODULE)


def _macro_driver(factor_id: str) -> dict[str, str]:
    """Resolve a driver's display name, falling back to the bare id."""

    try:
        from app.services.macro_conditions.linkage import factor_driver
    except Exception:
        return {"factor_id": factor_id, "label": factor_id}
    return factor_driver(factor_id)


def _sector_strength(
    rows: list[dict[str, Any]],
    period: str = "3mo",
    reader: Any = None,
) -> list[dict[str, Any]]:
    """Per-sector technical strength, plus macro fit as a separate field.

    ``macro_sector_fit`` never touches ``avg_strength`` or the ordering. The two
    are different measurements and the interesting sectors are the ones where
    they disagree -- blending them into one number would hide exactly those.
    """

    if period not in SECTOR_PERIOD_DAYS:
        raise ValueError(f"Unsupported sector period: {period}")
    # 与板块「筛选」共用同一套成员口径：按完整 theme_ids 归组。主题分组
    # 本就允许成分重叠（BABA 同属 social_internet 与 china_adr），若只按
    # 首个主题归组，靠后的主题（china_adr/crypto/retail…）的均值与领涨股
    # 只剩「没被更靠前主题抢走」的残余子集，与筛选出来的成员列表对不上。
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        theme_ids = row.get("theme_ids") or (
            [row.get("sector_id")] if row.get("sector_id") else []
        )
        for sid in dict.fromkeys(theme_ids):
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
        fit = reader.fit_for(sid) if reader is not None and reader.available else None
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
            # None means "no macro read", never neutral. A sector whose exposure
            # profile is too thinly observed carries no fit rather than a 50.
            "macro_sector_fit": fit.score if fit else None,
            "macro_sector_tailwind": fit.tailwind if fit else None,
            "macro_sector_fit_confidence": (
                round(fit.confidence, 4) if fit else None
            ),
            "macro_sector_supporting_factors": (
                [_macro_driver(f) for f in fit.supporting] if fit else []
            ),
            "macro_sector_opposing_factors": (
                [_macro_driver(f) for f in fit.opposing] if fit else []
            ),
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
    key = f"market-strength:{MARKET_SHAPE_VERSION}:{_completed_daily_key(observed_at)}"

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


def profiles() -> dict[str, Any]:
    # The screener ranks stocks only (v1.6), so the fund theme would always be empty.
    return {
        "profiles": list(PROFILES),
        "timeframes": list(TIMEFRAMES),
        "universes": list(UNIVERSES),
        "sectors": [{"id": sid, "name": sector["name"]} for sid, sector in SECTORS.items() if sid != "etfs"],
    }
