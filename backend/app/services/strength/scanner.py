from __future__ import annotations

import io
import math
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pandas as pd
import yfinance as yf

from app.config import get_settings
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
from app.services.strength.price_action import compute_price_action
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.strength.yahoo_options import (
    enrich_rows_with_yahoo_options,
    yahoo_options_is_enabled,
)
from app.services.zh_names import get_zh_name

TIMEFRAMES = ("short", "mid", "long", "all")
PROFILES = ("conservative", "balanced", "aggressive")
UNIVERSES = ("themes",)
BENCHMARKS = MARKET_BENCHMARKS

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


def _clamp(value: float | int | None, lo: float = 0.0, hi: float = 100.0, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _score_signed_pct(value: float | None, scale: float, neutral: float = 50.0) -> float:
    if value is None:
        return neutral
    return _clamp(neutral + (value * 100.0 * scale))


def _score_rsi(value: float | None) -> float:
    if value is None:
        return 50.0
    # Strong-but-not-exhausted RSI gets the best score.
    if 50 <= value <= 68:
        return _clamp(58 + (value - 50) * 1.7)
    if 68 < value <= 78:
        return _clamp(88 - (value - 68) * 2.2)
    if value < 50:
        return _clamp(42 + (value - 35) * 1.1)
    return _clamp(50 - (value - 78) * 1.5)


def _pct_rank(items: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = sorted((row[key] for row in items if row.get(key) is not None))
    if not values:
        return {}
    if len(values) == 1:
        return {row["ticker"]: 50.0 for row in items if row.get(key) is not None}
    denom = max(len(values) - 1, 1)
    ranks: dict[str, float] = {}
    for row in items:
      value = row.get(key)
      if value is None:
          continue
      below = sum(1 for candidate in values if candidate <= value)
      ranks[row["ticker"]] = round((below - 1) / denom * 100, 1)
    return ranks


def _theme_universe(sector_id: str | None = None) -> tuple[list[str], dict[str, dict[str, str]]]:
    sector_meta: dict[str, dict[str, str]] = {}
    tickers: list[str] = []
    for sid, sector in SECTORS.items():
        if sector_id and sid != sector_id:
            continue
        for ticker in sector["tickers"]:
            symbol = ticker.upper().strip()
            if not symbol or "." in symbol:
                # Keep the MVP US-focused and avoid mixed exchange suffixes.
                continue
            tickers.append(symbol)
            sector_meta.setdefault(symbol, {"sector_id": sid, "sector_name": sector["name"]})
    return list(dict.fromkeys(tickers)), sector_meta


def _period_to_days(period: str) -> int:
    period = (period or "1y").strip().lower()
    if period.endswith("y"):
        return max(365, int(float(period[:-1] or 1) * 365))
    if period.endswith("mo"):
        return max(31, int(float(period[:-2] or 1) * 31))
    if period.endswith("d"):
        return max(1, int(float(period[:-1] or 1)))
    return 365


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
    token = (settings.marketdata_token or settings.marketdata_api_token or "").strip()
    if not token or not settings.marketdata_stock_candle_fallback_enabled:
        return pd.DataFrame(), [], tickers

    limit = max(0, int(settings.marketdata_stock_candle_fallback_limit or 0))
    if limit <= 0:
        return pd.DataFrame(), [], tickers

    base_url = str(settings.marketdata_base_url).rstrip("/")
    end_date = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=_period_to_days(period) + 10)
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:limit]
    timeout = min(float(settings.request_timeout or 20.0), 8.0)
    frames: list[pd.DataFrame] = []
    loaded: list[str] = []
    missing: list[str] = []

    try:
        with httpx.Client(timeout=timeout) as client:
            for symbol in symbols:
                try:
                    response = client.get(
                        f"{base_url}/v1/stocks/candles/D/{symbol}/",
                        params={
                            "from": start_date.isoformat(),
                            "to": end_date.isoformat(),
                            "token": token,
                        },
                    )
                    response.raise_for_status()
                    frame = _finnhub_candle_frame(symbol, response.json())
                    if frame.empty:
                        missing.append(symbol)
                        continue
                    frames.append(frame)
                    loaded.append(symbol)
                except Exception:
                    missing.append(symbol)
    except Exception:
        return pd.DataFrame(), loaded, tickers

    for symbol in tickers:
        if symbol.startswith("^") or symbol not in symbols:
            missing.append(symbol)

    if not frames:
        return pd.DataFrame(), loaded, list(dict.fromkeys(missing))
    return pd.concat(frames, axis=1).sort_index(), loaded, list(dict.fromkeys(missing))


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
    frames: list[pd.DataFrame] = []
    loaded: list[str] = []
    missing: list[str] = []

    try:
        with httpx.Client(timeout=timeout) as client:
            for symbol in symbols:
                stooq_symbol = _stooq_symbol(symbol)
                if not stooq_symbol:
                    continue
                try:
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
                    frame = _stooq_candle_frame(symbol, response.text)
                    if frame.empty:
                        missing.append(symbol)
                        continue
                    frames.append(frame)
                    loaded.append(symbol)
                except Exception:
                    missing.append(symbol)
    except Exception:
        return pd.DataFrame(), loaded, tickers

    for symbol in tickers:
        if symbol not in symbols:
            missing.append(symbol)

    if not frames:
        return pd.DataFrame(), loaded, list(dict.fromkeys(missing))
    return pd.concat(frames, axis=1).sort_index(), loaded, list(dict.fromkeys(missing))


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
    frames: list[pd.DataFrame] = []
    loaded: list[str] = []
    missing: list[str] = []
    symbols = [symbol for symbol in tickers if symbol and not symbol.startswith("^")][:limit]
    timeout = min(float(settings.request_timeout or 20.0), 8.0)

    try:
        with httpx.Client(timeout=timeout, headers={"X-Finnhub-Token": token}) as client:
            for symbol in symbols:
                try:
                    response = client.get(
                        f"{base_url}/stock/candle",
                        params={
                            "symbol": symbol,
                            "resolution": "D",
                            "from": start_ts,
                            "to": end_ts,
                            "token": token,
                        },
                    )
                    response.raise_for_status()
                    frame = _finnhub_candle_frame(symbol, response.json())
                    if frame.empty:
                        missing.append(symbol)
                        continue
                    frames.append(frame)
                    loaded.append(symbol)
                except Exception:
                    missing.append(symbol)
    except Exception:
        return pd.DataFrame(), loaded, tickers

    for symbol in tickers:
        if symbol.startswith("^") or symbol not in symbols:
            missing.append(symbol)

    if not frames:
        return pd.DataFrame(), loaded, list(dict.fromkeys(missing))
    return pd.concat(frames, axis=1).sort_index(), loaded, list(dict.fromkeys(missing))


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


def _download_history(tickers: list[str], period: str = "1y") -> pd.DataFrame:
    session = getattr(yahoo, "_yf_session", None)
    kwargs: dict[str, Any] = {
        "tickers": " ".join(tickers),
        "period": period,
        "interval": "1d",
        "group_by": "ticker",
        "threads": True,
        "progress": False,
        "auto_adjust": True,
    }
    if session is not None:
        kwargs["session"] = session
    try:
        primary = yf.download(**kwargs)
    except Exception:
        primary = pd.DataFrame()

    missing = [ticker for ticker in tickers if _slice_ticker(primary, ticker).empty]
    if not primary.empty and not missing:
        return _attach_history_status(
            primary,
            _history_status(
                provider="Yahoo/yfinance",
                status="active",
                message="Yahoo/yfinance 日线价格、成交量与技术指标输入",
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
        provider = "Yahoo/yfinance + " + " + ".join(providers)
        status = "active" if not still_missing else "degraded"
        source_label = " + ".join(providers)
        message = (
            f"Yahoo/yfinance 部分或全部数据不可用，已启用 {source_label} 日线兜底"
            if primary.empty
            else f"Yahoo/yfinance 缺少部分标的，已用 {source_label} 日线补齐"
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
                provider="Yahoo/yfinance",
                status="degraded",
                message="Yahoo/yfinance 数据不可用，公开日线兜底源也未拿到可用数据",
                missing_symbols=fallback_missing or tickers,
            )
        )

    return _attach_history_status(
        primary,
        _history_status(
            provider="Yahoo/yfinance",
            status="degraded",
            message="Yahoo/yfinance 缺少部分标的，公开日线兜底源未拿到可用数据",
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
    rs = gain / loss.replace(0, math.nan)
    rsi = 100 - (100 / (1 + rs))
    return _safe_float(rsi.dropna().iloc[-1], 2) if not rsi.dropna().empty else None


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
    base = close.iloc[-days]
    if not base or base <= 0:
        return None
    return _safe_float(close.iloc[-1] / base - 1, 5)


def _feature_row(ticker: str, hist: pd.DataFrame, spy: pd.DataFrame, sector_meta: dict[str, str]) -> dict[str, Any] | None:
    if hist.empty or len(hist) < 63:
        return None
    close = hist["Close"].dropna()
    volume = hist["Volume"].fillna(0) if "Volume" in hist.columns else pd.Series([0] * len(hist), index=hist.index)
    price = _safe_float(close.iloc[-1], 2)
    if price is None or price <= 0:
        return None

    def sma(period: int) -> float | None:
        if len(close) < period:
            return None
        return _safe_float(close.rolling(period).mean().iloc[-1], 4)

    sma20, sma50, sma200 = sma(20), sma(50), sma(200)
    avg_vol20 = _safe_float(volume.tail(20).mean(), 2) or 0
    avg_dollar_vol = price * avg_vol20
    rel_volume = _safe_float((volume.iloc[-1] / avg_vol20), 3) if avg_vol20 > 0 else None
    high_52w = _safe_float(close.tail(252).max() if len(close) >= 120 else close.max(), 4)
    high_3m = _safe_float(close.tail(63).max(), 4)
    vol_price_match = compute_vol_price_match(hist)
    price_action = compute_price_action(hist)

    spy_close = spy["Close"].dropna() if not spy.empty and "Close" in spy.columns else pd.Series(dtype=float)
    stock_ret_63 = _ret(close, 63)
    spy_ret_63 = _ret(spy_close, 63) if len(spy_close) else None

    return {
        "ticker": ticker,
        "name": get_zh_name(ticker) or ticker,
        "sector_id": sector_meta.get("sector_id"),
        "sector_name": sector_meta.get("sector_name"),
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
        "above_sma20": bool(sma20 and price > sma20),
        "above_sma50": bool(sma50 and price > sma50),
        "above_sma200": bool(sma200 and price > sma200),
        "ma_alignment": sum(bool(x) for x in (sma20 and price > sma20, sma50 and price > sma50, sma200 and price > sma200)) / 3 * 100,
        "rsi14": _rsi(close),
        "macd_direction": _macd_direction(close),
        "atr_pct": _atr_pct(hist),
        "rel_volume": rel_volume,
        "avg_volume_20d": int(avg_vol20),
        "avg_dollar_volume_20d": _safe_float(avg_dollar_vol, 0),
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


def _sector_scores(rows: list[dict[str, Any]]) -> dict[str, float]:
    sector_returns: dict[str, list[float]] = {}
    for row in rows:
        sector_id = row.get("sector_id") or "unknown"
        value = row.get("return_63d")
        if value is not None:
            sector_returns.setdefault(sector_id, []).append(value)
    medians = [
        {"ticker": sid, "value": sorted(vals)[len(vals) // 2]}
        for sid, vals in sector_returns.items() if vals
    ]
    ranks = _pct_rank(medians, "value")
    return {sid: _clamp(score) for sid, score in ranks.items()}


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

    if not row.get("above_sma200"):
        penalty += 8
        flags.append("低于200日线")
        warnings.append("长期趋势仍未修复")

    avg_dollar = row.get("avg_dollar_volume_20d") or 0
    if avg_dollar < min_avg_dollar_volume * 1.4:
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


def _classify(row: dict[str, Any], final_score: float, risk_penalty: float) -> str:
    if final_score >= 78 and row.get("ma_alignment", 0) >= 66:
        return "质量趋势"
    if final_score >= 70 and (row.get("rel_volume") or 0) >= 1.5 and (row.get("ath_proximity") or 0) >= 88:
        return "放量突破"
    if final_score >= 64 and (row.get("rs_spy_63d") or 0) > 0:
        return "相对强势"
    if final_score >= 58 and (row.get("rsi14") or 50) < 52:
        return "回暖候选"
    if risk_penalty >= 16:
        return "高风险题材"
    return "观察"


def _score_rows(rows: list[dict[str, Any]], market: dict[str, Any], profile: str, min_avg_dollar_volume: float) -> list[dict[str, Any]]:
    percentile_keys = ["return_5d", "return_20d", "return_63d", "return_126d", "return_252d", "rs_spy_63d", "rel_volume"]
    ranks = {key: _pct_rank(rows, key) for key in percentile_keys}
    sector_score_by_id = _sector_scores(rows)
    tilt = PROFILE_TILT.get(profile, PROFILE_TILT["balanced"])
    rules = market.get("rules") if isinstance(market.get("rules"), dict) else {}
    momentum_mult = float(rules.get("momentum_weight_multiplier", 1.0) or 1.0)
    relative_mult = float(rules.get("relative_strength_weight_multiplier", 1.0) or 1.0)
    long_mult = float(rules.get("long_trend_weight_multiplier", 1.0) or 1.0)
    breakout_mult = float(rules.get("breakout_weight_multiplier", 1.0) or 1.0)
    sector_mult = float(rules.get("sector_strength_weight_multiplier", 1.0) or 1.0)
    option_mult = float(rules.get("option_heat_weight_multiplier", 1.0) or 1.0)
    risk_mult = float(rules.get("risk_penalty_multiplier", 1.0) or 1.0)
    weights = {
        "short": .16 * momentum_mult,
        "mid": .24 * relative_mult,
        "long": .14 * long_mult,
        "breakout": .12 * breakout_mult,
        # Pure price-action structure (HH/HL, candle patterns, spring/upthrust).
        # Not tied to a market-regime multiplier: structure reads the same in
        # any regime — the regime already scales momentum/breakout weights.
        "pa": .10,
        "sector": .09 * sector_mult,
        "option": .07 * option_mult,
        "market": .08,
    }
    weight_total = sum(weights.values()) or 1.0
    effective_weights = {key: round(value / weight_total, 4) for key, value in weights.items()}
    scored: list[dict[str, Any]] = []

    for row in rows:
        ticker = row["ticker"]
        ret5 = ranks["return_5d"].get(ticker, 50)
        ret20 = ranks["return_20d"].get(ticker, 50)
        ret63 = ranks["return_63d"].get(ticker, 50)
        ret126 = ranks["return_126d"].get(ticker, 50)
        ret252 = ranks["return_252d"].get(ticker, 50)
        rs63 = ranks["rs_spy_63d"].get(ticker, 50)
        rv_rank = ranks["rel_volume"].get(ticker, 50)

        short_score = (
            ret5 * .28 +
            ret20 * .26 +
            rv_rank * .18 * tilt["volume"] +
            _score_signed_pct(row.get("dist_sma20"), 420) * .16 +
            _score_rsi(row.get("rsi14")) * .12
        ) / (1 + max(0, tilt["volume"] - 1) * .18)

        mid_score = (
            ret63 * .28 +
            rs63 * .26 +
            row.get("ma_alignment", 50) * .22 * tilt["trend"] +
            _score_signed_pct(row.get("macd_direction"), 40) * .12 +
            rv_rank * .12
        ) / (1 + max(0, tilt["trend"] - 1) * .22)

        long_trend = _score_signed_pct(row.get("dist_sma200"), 260)
        long_score = (
            ret126 * .26 +
            ret252 * .22 +
            long_trend * .24 * tilt["trend"] +
            _clamp(row.get("ath_proximity")) * .18 * tilt["breakout"] +
            row.get("ma_alignment", 50) * .10
        ) / (1 + max(0, tilt["trend"] - 1) * .24 + max(0, tilt["breakout"] - 1) * .18)

        sector_score = sector_score_by_id.get(row.get("sector_id") or "", 50)
        option_heat_score = 50.0
        risk_penalty, risk_flags, warnings = _risk_penalty(row, min_avg_dollar_volume, profile)
        vol_price = row.get("vol_price_match") if isinstance(row.get("vol_price_match"), dict) else {}
        price_action = row.get("price_action") if isinstance(row.get("price_action"), dict) else {}
        pa_score = _clamp(price_action.get("score"), default=50.0)
        base_breakout_score = (_clamp(row.get("ath_proximity")) + ret20) / 2
        breakout_quality_score = _clamp(
            base_breakout_score +
            (_safe_float(vol_price.get("breakout_quality_adjustment"), 1) or 0.0) -
            max(_safe_float(vol_price.get("false_breakout_risk"), 1) or 0.0, 0.0)
        )
        if vol_price.get("setup_type") == "vacuum" and not row.get("follow_through"):
            breakout_quality_score = min(breakout_quality_score, 65.0)
        if vol_price.get("setup_type") == "absorption_bearish" and not row.get("breakout_confirmed"):
            breakout_quality_score = min(breakout_quality_score, 55.0)
        raw_final = (
            short_score * weights["short"] +
            mid_score * weights["mid"] +
            long_score * weights["long"] +
            breakout_quality_score * weights["breakout"] +
            pa_score * weights["pa"] +
            sector_score * weights["sector"] +
            option_heat_score * weights["option"] +
            market["score"] * weights["market"]
        ) / weight_total - risk_penalty * risk_mult
        market_adjustment = _safe_float(
            raw_final - (
                short_score * .16 +
                mid_score * .24 +
                long_score * .14 +
                base_breakout_score * .12 +
                pa_score * .10 +
                sector_score * .09 +
                option_heat_score * .07 +
                market["score"] * .08 -
                risk_penalty
            ),
            2,
        )
        final_score = round(_clamp(raw_final), 1)
        classification = _classify(row, final_score, risk_penalty)

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
        for tag in vol_price.get("tags", [])[:2]:
            if tag not in {"未明显收缩", "量价样本不足"}:
                tags.append(str(tag))
        # Price-action structure tags/reasons (pure K线).
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
        if row.get("ma_alignment", 0) >= 66:
            tags.append("均线多头")
            reasons.append("价格位于关键均线上方")
        if market["score"] >= 64:
            tags.append("市场顺风")
        elif market["score"] < 40:
            tags.append("弱市降权")
        tags.extend(risk_flags[:2])
        if not reasons:
            reasons.append("综合强度处于股票池前列")
        if option_heat_score == 50:
            warnings.append("期权热度待接入")

        quality_inputs = [
            row.get("return_20d"), row.get("return_63d"), row.get("return_126d"),
            row.get("rs_spy_63d"), row.get("dist_sma50"), row.get("rsi14"),
            row.get("rel_volume"), row.get("atr_pct"), row.get("ath_proximity"),
        ]
        data_quality = round(sum(v is not None for v in quality_inputs) / len(quality_inputs) * 100)
        breakdown = {
            "relative_strength": round(rs63, 1),
            "trend": round((row.get("ma_alignment", 50) + _score_signed_pct(row.get("dist_sma50"), 320)) / 2, 1),
            "volume": round(rv_rank, 1),
            "breakout": round(breakout_quality_score, 1),
            "base_breakout": round(base_breakout_score, 1),
            "price_action": round(pa_score, 1),
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
            "technical": round((_score_rsi(row.get("rsi14")) + _score_signed_pct(row.get("macd_direction"), 40)) / 2, 1),
            "sector": round(sector_score, 1),
            "option_heat": round(option_heat_score, 1),
            "risk_penalty": round(risk_penalty, 1),
            "market_regime": round(market.get("score") or 50, 1),
            "risk_on_spread": round(market.get("risk_on_spread_score") or 50, 1),
            "volume_truth": {
                "setup_type": vol_price.get("setup_type"),
                "setup_label": vol_price.get("setup_label"),
                "breakout_quality_adjustment": vol_price.get("breakout_quality_adjustment"),
                "false_breakout_risk": vol_price.get("false_breakout_risk"),
            },
            "market_adjustment": market_adjustment,
            "market_rules": effective_weights,
        }
        scored.append({
            **row,
            "score_short": round(_clamp(short_score), 1),
            "score_mid": round(_clamp(mid_score), 1),
            "score_long": round(_clamp(long_score), 1),
            "sector_score": round(sector_score, 1),
            "price_action_score": round(pa_score, 1),
            "breakout_quality_score": round(breakout_quality_score, 1),
            "option_heat_score": round(option_heat_score, 1),
            "option_score_weight": effective_weights["option"],
            "market_regime_score": market["score"],
            "risk_on_spread_score": market.get("risk_on_spread_score"),
            "risk_penalty": risk_penalty,
            "final_score": final_score,
            "strength_score": final_score,
            "classification": classification,
            "label": classification,
            "tags": list(dict.fromkeys(tags))[:6],
            "reasons": reasons[:4],
            "warnings": list(dict.fromkeys(warnings))[:4],
            "breakdown": breakdown,
            "data_quality": data_quality,
            "option_context": {
                "option_heat_score": round(option_heat_score, 1),
                "iv_rank": None,
                "iv_label": "待接入",
                "source_status": "placeholder",
                "warning": "当前为中性占位，待接入真实期权流/IV历史",
            },
            "data_sources": {
                "prices": "Yahoo/yfinance",
                "technicals": "Yahoo/yfinance",
                "fundamentals": "not_configured",
                "options": "placeholder",
            },
        })

    scored.sort(key=lambda item: item["final_score"], reverse=True)
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
                (item.get(key) or 0) * .88
                + (item.get("option_heat_score") or 50) * .06
                + (item.get("final_score") or 0) * .06
            ),
            reverse=True,
        )
        return
    rows.sort(key=lambda item: item.get("final_score") or 0, reverse=True)


def _sector_strength(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sid = row.get("sector_id")
        if sid:
            grouped.setdefault(sid, []).append(row)
    sectors = []
    for sid, items in grouped.items():
        ret63 = [x.get("return_63d") for x in items if x.get("return_63d") is not None]
        final = [x.get("final_score") for x in items if x.get("final_score") is not None]
        leaders = sorted(items, key=lambda x: x.get("final_score") or 0, reverse=True)[:4]
        sectors.append({
            "sector_id": sid,
            "id": sid,
            "name": SECTORS.get(sid, {}).get("name", sid),
            "count": len(items),
            "avg_return_3m": round(sum(ret63) / len(ret63) * 100, 2) if ret63 else None,
            "avg_strength": round(sum(final) / len(final), 1) if final else None,
            "leaders": [{"ticker": x["ticker"], "score": x["final_score"]} for x in leaders],
        })
    sectors.sort(key=lambda s: s.get("avg_strength") or 0, reverse=True)
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
        status = marketdata_status.get("status") or yahoo_status.get("status") or "placeholder"

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
) -> dict[str, Any]:
    tickers, sector_meta = _theme_universe(sector_id)
    if universe != "themes":
        raise ValueError(f"Unsupported universe: {universe}")
    if not tickers:
        raise ValueError("No tickers in selected universe")

    all_symbols = list(dict.fromkeys(tickers + list(BENCHMARKS)))
    raw = _download_history(all_symbols)
    price_source = raw.attrs.get("price_source") or _history_status(
        provider="Yahoo/yfinance",
        status="active",
        message="日线价格、成交量与技术指标输入",
    )
    index_data = {symbol: _slice_ticker(raw, symbol) for symbol in BENCHMARKS}
    market = compute_market_regime(index_data)
    spy = index_data.get("SPY", pd.DataFrame())

    rows: list[dict[str, Any]] = []
    skipped = {"insufficient_history": 0, "low_price": 0, "low_liquidity": 0, "data_error": 0}
    for ticker in tickers:
        try:
            hist = _slice_ticker(raw, ticker)
            row = _feature_row(ticker, hist, spy, sector_meta.get(ticker, {}))
            if not row:
                skipped["insufficient_history"] += 1
                continue
            if row["price"] < min_price:
                skipped["low_price"] += 1
                continue
            if (row.get("avg_dollar_volume_20d") or 0) < min_avg_dollar_volume:
                skipped["low_liquidity"] += 1
                continue
            rows.append(row)
        except Exception:
            skipped["data_error"] += 1

    scored = _score_rows(rows, market, profile, min_avg_dollar_volume)
    if include_options:
        yahoo_options_status = enrich_rows_with_yahoo_options(scored, display_top=top)
    else:
        # Single-stock lookups don't need the (very expensive) option-chain
        # enrichment pass over up to 90 tickers; keep the neutral placeholder.
        yahoo_options_status = {
            "provider": "Yahoo/yfinance",
            "status": "skipped",
            "configured": True,
            "enriched": 0,
            "message": "单标的查询跳过期权粗筛（性能优化）",
        }
    _refresh_classifications(scored)
    _sort_scored(scored, timeframe)
    limited = scored[:top]
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
        },
        "market_regime": market,
        "market_context": market.get("market_context", {}),
        "spread_matrix": market.get("spread_matrix", {}),
        "count": len(limited),
        "universe_count": len(tickers),
        "screened_count": len(rows),
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
        },
    }


async def scan_strength(
    *,
    universe: str = "themes",
    timeframe: str = "all",
    profile: str = "balanced",
    top: int = 30,
    sector_id: str | None = None,
    min_price: float = 5.0,
    min_avg_dollar_volume: float = 10_000_000,
    ttl: int = 600,
    include_options: bool = True,
) -> dict[str, Any]:
    settings = get_settings()
    key = (
        f"strength:{universe}:{timeframe}:{profile}:{top}:{sector_id}:{min_price}:{min_avg_dollar_volume}"
        f":fh:{int(finnhub_is_enabled(settings))}:md:{int(marketdata_is_enabled(settings))}"
        f":yo:{int(yahoo_options_is_enabled(settings) and include_options)}:{settings.yahoo_options_enrich_limit}"
        f":ydte:{settings.yahoo_option_target_dte}:ywin:{settings.yahoo_option_strike_window_pct}"
        f":opt:{int(include_options)}"
        ":mr:v3:spread:voltruth:pa1"
    )

    async def produce() -> dict[str, Any]:
        import asyncio
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
        )

    payload, was_cached, expires_at = await cache.get_or_set_with_meta(key, ttl, produce)
    return {
        **payload,
        "_cached": was_cached,
        "cache_ttl_seconds": ttl,
        "cache_expires_at": datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
    }


async def sector_strength(period: str = "3mo") -> dict[str, Any]:
    # Period is reserved for the next data provider; P0 ranks by 3-month theme strength.
    payload = await scan_strength(timeframe="all", profile="balanced", top=120)
    return {
        "as_of": payload["as_of"],
        "period": period,
        "sectors": payload.get("sectors", []),
        "count": len(payload.get("sectors", [])),
    }


async def market_strength() -> dict[str, Any]:
    # Only market_regime is returned — option enrichment would be wasted work.
    payload = await scan_strength(timeframe="all", profile="balanced", top=5, include_options=False)
    return {"as_of": payload["as_of"], "market_regime": payload["market_regime"]}


async def stock_strength(ticker: str, profile: str = "balanced") -> dict[str, Any]:
    symbol = ticker.upper().strip()
    # include_options=False: a single-stock lookup used to trigger a full
    # 250-row scan INCLUDING the option-chain enrichment of up to 90 tickers
    # (minutes of sequential Yahoo calls on a cold cache). Price/trend/sector
    # scoring is unaffected; option heat stays at its neutral placeholder.
    payload = await scan_strength(
        timeframe="all", profile=profile, top=250, min_avg_dollar_volume=0, include_options=False
    )
    for row in payload.get("rows", []):
        if row.get("ticker") == symbol:
            return {"as_of": payload["as_of"], "ticker": symbol, "row": row, "market_regime": payload["market_regime"]}
    raise KeyError(symbol)


def profiles() -> dict[str, Any]:
    return {
        "profiles": list(PROFILES),
        "timeframes": list(TIMEFRAMES),
        "universes": list(UNIVERSES),
        "sectors": [{"id": sid, "name": sector["name"]} for sid, sector in SECTORS.items()],
    }
