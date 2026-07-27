from __future__ import annotations

import math
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import pandas as pd
import yfinance as yf

from app.services import massive

_MASSIVE_PERIOD_DAYS = {
    "1y": 405,
    "2y": 770,
    "6mo": 200,
    "3mo": 105,
    "1mo": 40,
    # Long windows exist for the macro-conditions ETF proxies, which need a
    # multi-year backfill. Existing callers keep their previous periods.
    "5y": 1_890,
    "10y": 3_720,
}
_YAHOO_TIMEOUT_SECONDS = 10

_cache: OrderedDict[str, tuple[datetime, Any]] = OrderedDict()
_cache_lock = threading.RLock()
_key_locks: dict[str, threading.Lock] = {}
_key_lock_users: dict[str, int] = {}
_CACHE_MAX_ENTRIES = 512

SECTOR_ETFS = ["XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB"]
_MIN_SECTOR_BREADTH_COVERAGE = 0.60


def _acquire_key_lock(key: str) -> threading.Lock:
    with _cache_lock:
        key_lock = _key_locks.get(key)
        if key_lock is None:
            key_lock = threading.Lock()
            _key_locks[key] = key_lock
        _key_lock_users[key] = _key_lock_users.get(key, 0) + 1
    key_lock.acquire()
    return key_lock


def _release_key_lock(key: str, key_lock: threading.Lock) -> None:
    key_lock.release()
    with _cache_lock:
        remaining = _key_lock_users.get(key, 1) - 1
        if remaining > 0:
            _key_lock_users[key] = remaining
            return
        _key_lock_users.pop(key, None)
        if key not in _cache and _key_locks.get(key) is key_lock:
            _key_locks.pop(key, None)


def _fresh_cache_hit(key: str, now: datetime) -> tuple[datetime, Any] | None:
    """Read and promote one entry while holding the cache lock."""
    hit = _cache.get(key)
    if not hit:
        return None
    if hit[0] <= now:
        _cache.pop(key, None)
        if _key_lock_users.get(key, 0) == 0:
            _key_locks.pop(key, None)
        return None
    _cache.move_to_end(key)
    return hit


def _cached_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {**value, "_cached": True}
    return value


def _read_cached(key: str) -> Any | None:
    """Read one fresh process-local signal result without invoking a loader."""

    with _cache_lock:
        hit = _fresh_cache_hit(key, datetime.now(timezone.utc))
    return _cached_value(hit[1]) if hit is not None else None


def cached_market_signals() -> dict[str, Any] | None:
    value = _read_cached("market_signals")
    return value if isinstance(value, dict) else None


def cached_stock_signals(ticker: str) -> dict[str, Any] | None:
    value = _read_cached(f"stock_signals:{ticker.upper().strip()}")
    return value if isinstance(value, dict) else None


def _cached(key: str, ttl_seconds: int, loader: Callable[[], Any]) -> Any:
    with _cache_lock:
        hit = _fresh_cache_hit(key, datetime.now(timezone.utc))
    if hit:
        return _cached_value(hit[1])

    key_lock = _acquire_key_lock(key)
    try:
        with _cache_lock:
            hit = _fresh_cache_hit(key, datetime.now(timezone.utc))
        if hit:
            return _cached_value(hit[1])

        value = loader()
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
        with _cache_lock:
            current = datetime.now(timezone.utc)
            for stale_key in [
                stale_key
                for stale_key, (stale_at, _) in _cache.items()
                if stale_at <= current
            ]:
                _cache.pop(stale_key, None)
                if _key_lock_users.get(stale_key, 0) == 0:
                    _key_locks.pop(stale_key, None)
            while len(_cache) >= _CACHE_MAX_ENTRIES:
                evicted_key, _ = _cache.popitem(last=False)
                if _key_lock_users.get(evicted_key, 0) == 0:
                    _key_locks.pop(evicted_key, None)
            _cache[key] = (expires_at, value)
            _cache.move_to_end(key)
        return value
    finally:
        _release_key_lock(key, key_lock)


def clamp(value: float | int | None, lo: float = 0, hi: float = 100) -> float:
    if value is None:
        return 0
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return 0
        return max(lo, min(hi, f))
    except Exception:
        return 0


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        f = float(value)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, ndigits)
    except Exception:
        return None


def _clean_frame(df: pd.DataFrame) -> pd.DataFrame:
    # Drop rows with NaN close (yfinance sometimes returns trailing NaN)
    if not df.empty and "Close" in df.columns:
        df = df.dropna(subset=["Close"])
    return df


def _massive_daily(symbol: str, period: str) -> pd.DataFrame:
    """Massive 主源单票日线(复权)→ yfinance 形状单层 frame;不支持/失败返回空。"""
    if not massive.configured():
        return pd.DataFrame()
    mapped = massive.to_symbol(symbol)
    if mapped is None or mapped.startswith("I:"):
        return pd.DataFrame()
    end = date.today()
    start = end - timedelta(days=_MASSIVE_PERIOD_DAYS.get(str(period).lower(), 405))
    try:
        bars = massive.ticker_range(mapped, 1, "day", start.isoformat(), end.isoformat(), adjusted=True)
    except massive.MassiveError:
        return pd.DataFrame()
    rows = [bar for bar in bars if isinstance(bar.get("t"), (int, float))]
    if not rows:
        return pd.DataFrame()
    index = pd.DatetimeIndex(
        [
            pd.Timestamp(bar["t"], unit="ms", tz="UTC").tz_convert("America/New_York").normalize().tz_localize(None)
            for bar in rows
        ]
    )
    frame = pd.DataFrame(
        {
            "Open": [bar.get("o") for bar in rows],
            "High": [bar.get("h") for bar in rows],
            "Low": [bar.get("l") for bar in rows],
            "Close": [bar.get("c") for bar in rows],
            "Volume": [bar.get("v") for bar in rows],
        },
        index=index,
    )
    cleaned = _clean_frame(frame.sort_index())
    cleaned.attrs["price_provider"] = "Massive"
    return cleaned


def _yahoo_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    try:
        frame = _clean_frame(
            yf.Ticker(symbol).history(
                period=period,
                auto_adjust=True,
                timeout=_YAHOO_TIMEOUT_SECONDS,
            )
        )
        frame.attrs["price_provider"] = "Yahoo/yfinance"
        return frame
    except Exception:
        return pd.DataFrame()


def _history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Single-symbol daily history with a short TTL cache.

    The cache matters: compute_stock_signals re-fetches SPY for every ticker,
    which used to mean one redundant network download per stock request.
    """
    def load() -> pd.DataFrame:
        frame = _massive_daily(symbol, period)
        if not frame.empty:
            return frame
        return _yahoo_history(symbol, period)

    return _cached(f"hist:{symbol}:{period}", 300, load)


def daily_adjusted_history(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Public entry point for the Massive-first, Yahoo-fallback daily chain.

    Both providers return split/dividend-adjusted closes, and the frame carries
    the provider that actually served it in ``attrs["price_provider"]``. Callers
    outside this module use this instead of building a second price chain.
    """

    return _history(symbol, period)


def _bulk_history(symbols: list[str], period: str = "1y") -> dict[str, pd.DataFrame]:
    """Download many symbols in ONE yfinance batch call instead of N sequential
    requests. Falls back to per-symbol fetch for anything missing."""
    out: dict[str, pd.DataFrame] = {}
    remaining = list(dict.fromkeys(symbols))
    # Massive 主源批量(并发 4);未覆盖的余量继续走 yfinance 批下载
    if remaining and massive.configured():
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                for symbol, frame in zip(remaining, pool.map(lambda s: _massive_daily(s, period), remaining)):
                    if not frame.empty:
                        out[symbol] = frame
        except Exception:
            pass
        remaining = [symbol for symbol in remaining if symbol not in out]
        if not remaining:
            return out
    try:
        from app.services.yahoo import _yf_session
        kwargs: dict[str, Any] = {
            "tickers": " ".join(remaining),
            "period": period,
            "interval": "1d",
            "group_by": "ticker",
            "threads": False,
            "progress": False,
            "auto_adjust": True,
            "timeout": _YAHOO_TIMEOUT_SECONDS,
        }
        if _yf_session is not None:
            kwargs["session"] = _yf_session
        df = yf.download(**kwargs)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                available = set(df.columns.get_level_values(0))
                for symbol in remaining:
                    if symbol in available:
                        frame = _clean_frame(df[symbol].copy())
                        if not frame.empty:
                            out[symbol] = frame
            elif len(remaining) == 1:
                frame = _clean_frame(df.copy())
                if not frame.empty:
                    out[remaining[0]] = frame
    except Exception:
        pass
    missing = [symbol for symbol in remaining if symbol not in out]
    if missing:
        try:
            with ThreadPoolExecutor(max_workers=min(4, len(missing))) as pool:
                for symbol, frame in zip(
                    missing,
                    pool.map(lambda s: _yahoo_history(s, period), missing),
                ):
                    out[symbol] = frame
        except Exception:
            for symbol in missing:
                out.setdefault(symbol, pd.DataFrame())
    return out


def _last(series: pd.Series, default: float | None = None) -> float | None:
    try:
        s = series.dropna()
        return _safe_float(s.iloc[-1]) if not s.empty else default
    except Exception:
        return default


def compute_period_return(close: pd.Series, days: int) -> float | None:
    """Return the price change across exactly ``days`` trading intervals."""
    if close is None or days <= 0:
        return None
    clean = close.dropna()
    if len(clean) <= days:
        return None
    base = _safe_float(clean.iloc[-(days + 1)])
    current = _safe_float(clean.iloc[-1])
    if base is None or current is None or base <= 0:
        return None
    return _safe_float(current / base - 1, 6)


def compute_rsi(close: pd.Series, period: int = 14) -> float | None:
    if close is None or len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    avg_gain = _last(gain)
    avg_loss = _last(loss)
    if avg_gain is None or avg_loss is None:
        return None
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    rs = avg_gain / avg_loss
    return _safe_float(100 - (100 / (1 + rs)), 2)


def compute_atr(hist: pd.DataFrame, period: int = 14) -> pd.Series:
    if hist.empty:
        return pd.Series(dtype=float)
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def compute_obv_divergence(close: pd.Series, volume: pd.Series, lookback: int = 20) -> float | None:
    """Return bounded price-versus-signed-volume divergence for one window."""
    if close is None or volume is None or lookback < 1:
        return None
    aligned = pd.concat(
        [
            pd.to_numeric(close, errors="coerce").rename("close"),
            pd.to_numeric(volume, errors="coerce").rename("volume"),
        ],
        axis=1,
        join="inner",
    )
    if len(aligned) < lookback + 1:
        return None

    # Select the requested calendar window before validating it.  Dropping a
    # bad latest row first would silently backfill an older bar and publish a
    # stale value as though the current window were complete.
    window = aligned.tail(lookback + 1).replace([math.inf, -math.inf], pd.NA)
    if (
        window[["close", "volume"]].isna().any().any()
        or (window["close"] <= 0).any()
        or (window["volume"] < 0).any()
    ):
        return None
    # The first bar establishes the starting level and has no price direction,
    # so its volume must not dilute the signed-volume denominator.
    total_volume = _safe_float(window["volume"].iloc[1:].sum())
    start = _safe_float(window["close"].iloc[0])
    end = _safe_float(window["close"].iloc[-1])
    if total_volume is None or total_volume <= 0 or start is None or end is None:
        return None

    direction = window["close"].diff().map(
        lambda change: 1.0 if change > 0 else (-1.0 if change < 0 else 0.0)
    )
    signed_volume_ratio = _safe_float(
        (direction.iloc[1:] * window["volume"].iloc[1:]).sum() / total_volume
    )
    if signed_volume_ratio is None:
        return None
    price_return = end / start - 1.0
    divergence = 100.0 * (price_return - signed_volume_ratio)
    return round(max(-100.0, min(100.0, divergence)), 2)


def compute_macd_histogram(close: pd.Series) -> float | None:
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    # normalized 3-day slope as pct of price
    slope = (hist.iloc[-1] - hist.iloc[-4]) / close.iloc[-1] * 100 if len(hist) >= 4 and close.iloc[-1] else hist.iloc[-1]
    return _safe_float(slope, 4)


def _percentile_rank(
    series: pd.Series,
    value: float | None,
    *,
    window: int = 252,
    min_samples: int = 60,
) -> float | None:
    """Percentile of ``value`` within the trailing ``window`` observations.

    调用点的标签写的是「1年分位」——分布就必须取最近约一年（252 根），
    且样本太少（不足一个季度）时宁可缺失，也不在七八个观测里排名。
    """

    if value is None or series is None:
        return None
    clean = series.dropna()
    if window and window > 0:
        clean = clean.tail(window)
    if len(clean) < max(int(min_samples), 1):
        return None
    return _safe_float((clean <= value).mean() * 100, 1)


def _is_above_sma_frame(hist: pd.DataFrame, period: int = 50) -> bool:
    if hist is None or hist.empty or len(hist) < period or "Close" not in hist.columns:
        return False
    close = hist["Close"]
    sma = close.rolling(period).mean().iloc[-1]
    return bool(close.iloc[-1] > sma)


def _with_score(
    key: str,
    value: Any,
    label: str,
    scorer: Callable[[str, Any], tuple[int | None, int | None]],
) -> dict:
    normalized = _safe_float(value, 4) if isinstance(value, (int, float)) else value
    if normalized is None:
        top = bottom = None
    else:
        top, bottom = scorer(key, normalized)
    return {
        "value": normalized,
        "label": label,
        "top_score": top,
        "bottom_score": bottom,
    }


def _score_market_signal(key: str, value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    v = float(value)
    top = bottom = 0.0
    if key in ("sma20_distance", "sma50_distance", "sma200_distance"):
        mult = 8 if key == "sma20_distance" else (5 if key == "sma50_distance" else 2.5)
        top, bottom = clamp(v * mult), clamp(-v * mult)
    elif key == "rsi14":
        top, bottom = clamp((v - 50) * 3), clamp((50 - v) * 3)
    elif key == "return_20d":
        top, bottom = clamp(v * 4), clamp(-v * 4)
    elif key in ("rsp_spy_5d", "iwm_spy_5d"):
        top, bottom = clamp(-v * 15), clamp(v * 15)
    elif key == "qqq_spy_5d":
        top, bottom = clamp(v * 8), clamp(-v * 8)
    elif key == "sectors_above_50dma":
        top = clamp((45 - v) * 2) + clamp((v - 85) * 1.5)
        bottom = clamp((55 - v) * 1.5)
    elif key == "vix":
        top, bottom = clamp((15 - v) * 5), clamp((v - 20) * 4)
    elif key == "vix_percentile":
        top, bottom = clamp((100 - v) * 0.8), clamp(v * 0.8)
    elif key == "vix_5d_change":
        top = clamp(v * 3) if v > 0 else 0
        bottom = clamp(-v * 3) if v < 0 else 0
    elif key == "credit_risk":
        top, bottom = clamp(-v * 8), clamp(v * 5)  # HYG/TLT falling is risk; rising is stable/risk-on
    elif key in ("yield_10y", "yield_10y_20d_change"):
        top, bottom = (clamp((v - 4) * 25), clamp((4 - v) * 10)) if key == "yield_10y" else (clamp(v * 80), clamp(-v * 80))
    return round(clamp(top)), round(clamp(bottom))


def _score_stock_signal(key: str, value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    known_keys = {
        "sma20_dist",
        "sma50_dist",
        "sma200_dist",
        "rsi14",
        "return_20d",
        "atr_percentile",
        "volume_zscore",
        "obv_divergence",
        "relative_strength_spy",
        "close_position",
        "macd_hist",
    }
    if key not in known_keys:
        return None, None
    v = float(value)
    top = bottom = 0.0
    if key in ("sma20_dist", "sma50_dist", "sma200_dist"):
        mult = {"sma20_dist": 8, "sma50_dist": 5, "sma200_dist": 2.5}[key]
        top, bottom = clamp(v * mult), clamp(-v * mult)
    elif key == "rsi14":
        top, bottom = clamp((v - 50) * 3), clamp((50 - v) * 3)
    elif key == "return_20d":
        top, bottom = clamp(v * 3.5), clamp(-v * 3.5)
    elif key == "atr_percentile":
        top, bottom = clamp((v - 60) * 1.2), clamp((v - 70) * 1.0)
    elif key == "volume_zscore":
        top, bottom = clamp(v * 20), clamp(v * 10)
    elif key == "obv_divergence":
        top, bottom = clamp(v * 3), clamp(-v * 3)
    elif key == "relative_strength_spy":
        top, bottom = clamp(v * 6), clamp(-v * 6)
    elif key == "close_position":
        top, bottom = clamp((35 - v) * 2), clamp((v - 65) * 1.2)
    elif key == "macd_hist":
        top, bottom = clamp(-v * 150), clamp(v * 150)
    return round(clamp(top)), round(clamp(bottom))


def compute_market_signals() -> dict:
    def load() -> dict:
        # ONE batched download for all 19 symbols (8 benchmarks + 11 sector
        # ETFs) instead of 19 sequential network round-trips.
        benchmarks = ["SPY", "QQQ", "IWM", "RSP", "^VIX", "HYG", "TLT", "^TNX"]
        frames = _bulk_history(benchmarks + SECTOR_ETFS)
        spy = frames.get("SPY", pd.DataFrame()); qqq = frames.get("QQQ", pd.DataFrame())
        iwm = frames.get("IWM", pd.DataFrame()); rsp = frames.get("RSP", pd.DataFrame())
        vix = frames.get("^VIX", pd.DataFrame()); hyg = frames.get("HYG", pd.DataFrame())
        tlt = frames.get("TLT", pd.DataFrame()); tnx = frames.get("^TNX", pd.DataFrame())
        if spy.empty or len(spy) < 60:
            raise RuntimeError("Insufficient SPY data")
        close = spy["Close"]
        signals: dict[str, dict] = {}
        add = lambda k, val, lab: signals.__setitem__(k, _with_score(k, val, lab, _score_market_signal))
        add("sma20_distance", (close.iloc[-1] / close.rolling(20).mean().iloc[-1] - 1) * 100, "SPY距20日线偏离%")
        add("sma50_distance", (close.iloc[-1] / close.rolling(50).mean().iloc[-1] - 1) * 100, "SPY距50日线偏离%")
        add("sma200_distance", (close.iloc[-1] / close.rolling(200).mean().iloc[-1] - 1) * 100 if len(close) >= 200 else None, "SPY距200日线偏离%")
        add("rsi14", compute_rsi(close, 14), "SPY RSI(14)")
        spy_ret20 = compute_period_return(close, 20)
        add("return_20d", spy_ret20 * 100 if spy_ret20 is not None else None, "SPY 20日涨幅%")
        for frame, key, label in (
            (rsp, "rsp_spy_5d", "等权重/SPY 5日相对强弱%"),
            (iwm, "iwm_spy_5d", "小盘/SPY 5日相对强弱%"),
            (qqq, "qqq_spy_5d", "QQQ/SPY 5日相对强弱%"),
        ):
            left_ret = compute_period_return(frame["Close"], 5) if not frame.empty else None
            spy_ret5 = compute_period_return(close, 5)
            relative = ((1 + left_ret) / (1 + spy_ret5) - 1) * 100 if left_ret is not None and spy_ret5 is not None else None
            add(key, relative, label)
        valid_sector_frames = []
        for symbol in SECTOR_ETFS:
            frame = frames.get(symbol)
            if frame is None or frame.empty or "Close" not in frame.columns:
                continue
            if len(frame["Close"].dropna()) >= 50:
                valid_sector_frames.append(frame)
        sector_coverage = len(valid_sector_frames) / len(SECTOR_ETFS)
        breadth_value = None
        if valid_sector_frames and sector_coverage >= _MIN_SECTOR_BREADTH_COVERAGE:
            breadth_value = (
                sum(1 for frame in valid_sector_frames if _is_above_sma_frame(frame, 50))
                / len(valid_sector_frames)
                * 100
            )
        add("sectors_above_50dma", breadth_value, "板块ETF在50日线上方%")
        signals["_breadth_coverage"] = {
            "available": len(valid_sector_frames),
            "expected": len(SECTOR_ETFS),
            "ratio": round(sector_coverage, 3),
        }
        signals["_source_status"] = {
            "value": "active" if len(valid_sector_frames) == len(SECTOR_ETFS) else "degraded",
            "label": "板块广度数据完整" if len(valid_sector_frames) == len(SECTOR_ETFS) else "板块广度数据不完整",
        }
        if not vix.empty:
            v = vix["Close"].iloc[-1]
            add("vix", v, "VIX")
            add("vix_percentile", _percentile_rank(vix["Close"], v), "VIX 1年分位%")
            vix_ret5 = compute_period_return(vix["Close"], 5)
            add("vix_5d_change", vix_ret5 * 100 if vix_ret5 is not None else None, "VIX 5日变化%")
        if not hyg.empty and not tlt.empty:
            hyg_ret20 = compute_period_return(hyg["Close"], 20)
            tlt_ret20 = compute_period_return(tlt["Close"], 20)
            relative = ((1 + hyg_ret20) / (1 + tlt_ret20) - 1) * 100 if hyg_ret20 is not None and tlt_ret20 is not None else None
            add("credit_risk", relative, "信用风险(HYG/TLT) 20日变化%")
        if not tnx.empty:
            y = tnx["Close"].iloc[-1]
            add("yield_10y", y, "10年期收益率%")
            change20 = y - tnx["Close"].iloc[-21] if len(tnx) > 20 else None
            add("yield_10y_20d_change", change20, "10Y收益率20日变化")
        return signals
    return _cached("market_signals", 300, load)


def compute_stock_signals_from_history(
    ticker: str,
    history: pd.DataFrame,
    *,
    spy_history: pd.DataFrame | None = None,
    price_provider: str | None = None,
) -> dict:
    """Compute stock signals from caller-supplied, real daily OHLCV history."""

    symbol = ticker.upper().strip()
    hist = _clean_frame(history.copy())
    spy = _history("SPY") if spy_history is None else _clean_frame(spy_history.copy())
    if (
        hist.empty
        or len(hist) < 20
        or not {"High", "Low", "Close", "Volume"}.issubset(hist.columns)
    ):
        raise RuntimeError(f"Insufficient price data for {symbol}")
    close, volume = hist["Close"], hist["Volume"]
    signals: dict[str, dict] = {}
    add = lambda k, val, lab: signals.__setitem__(
        k,
        _with_score(k, val, lab, _score_stock_signal),
    )

    def safe(val):
        """Convert NaN/Inf to None."""
        if val is None:
            return None
        try:
            f = float(val)
            return round(f, 4) if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None

    # SMA distances
    for period, key in [
        (20, "sma20_dist"),
        (50, "sma50_dist"),
        (200, "sma200_dist"),
    ]:
        sma = close.rolling(period).mean().iloc[-1] if len(close) >= period else None
        val = safe((close.iloc[-1] / sma - 1) * 100) if sma and safe(sma) else None
        add(key, val, f"距{period}日线偏离%")

    add("rsi14", safe(compute_rsi(close, 14)), "RSI(14)")

    period_ret20 = compute_period_return(close, 20)
    ret20 = safe(period_ret20 * 100) if period_ret20 is not None else None
    add("return_20d", ret20, "20日涨幅%")

    atr = compute_atr(hist, 14)
    add("atr_percentile", safe(_percentile_rank(atr, _last(atr))), "ATR 1年分位%")

    vol_mean = safe(volume.rolling(20).mean().iloc[-1]) if len(volume) >= 20 else None
    vol_std = safe(volume.rolling(20).std().iloc[-1]) if len(volume) >= 20 else None
    vol_cur = safe(volume.iloc[-1])
    vol_z = (
        safe((vol_cur - vol_mean) / vol_std)
        if vol_cur is not None
        and vol_mean is not None
        and vol_std is not None
        and vol_std > 0
        else None
    )
    add("volume_zscore", vol_z, "成交量Z分数")

    signals["_volume_today"] = {
        "value": int(vol_cur) if vol_cur is not None else None,
        "label": "今日成交量",
    }
    signals["_volume_avg20"] = {
        "value": int(vol_mean) if vol_mean is not None else None,
        "label": "20日平均成交量",
    }
    signals["_volume_ratio"] = {
        "value": (
            safe(vol_cur / vol_mean)
            if vol_cur is not None and vol_mean is not None and vol_mean > 0
            else None
        ),
        "label": "成交量/均量比",
    }

    add("obv_divergence", safe(compute_obv_divergence(close, volume)), "OBV背离")

    if not spy.empty and "Close" in spy.columns:
        stock_ret = safe(compute_period_return(close, 20))
        spy_ret = safe(compute_period_return(spy["Close"], 20))
        rs = (
            safe((stock_ret - spy_ret) * 100)
            if stock_ret is not None and spy_ret is not None
            else None
        )
        add("relative_strength_spy", rs, "相对强弱(vs SPY)%")
    else:
        add("relative_strength_spy", None, "相对强弱(vs SPY)%")

    # Options data is enrichment only. A provider 402/timeout must not erase
    # otherwise valid price-derived stock signals.
    try:
        from app.services.yahoo import get_stock_iv

        iv = get_stock_iv(symbol)
    except Exception:
        iv = None
    add(
        "atm_iv_percent",
        round(iv * 100, 1) if iv is not None else None,
        "当前ATM IV%",
    )

    day_range = safe(hist["High"].iloc[-1] - hist["Low"].iloc[-1])
    close_pos = (
        safe((close.iloc[-1] - hist["Low"].iloc[-1]) / day_range * 100)
        if day_range is not None and day_range > 0
        else None
    )
    add("close_position", close_pos, "收盘位于当日区间%")

    add("macd_hist", safe(compute_macd_histogram(close)), "MACD柱状图方向")
    signals["_price_provider"] = {
        "value": price_provider,
        "label": "价格历史来源",
    }
    return signals


def compute_stock_signals(ticker: str) -> dict:
    symbol = ticker.upper().strip()

    def load() -> dict:
        hist = _history(symbol)
        provider = (
            str(hist.attrs.get("price_provider"))
            if hist.attrs.get("price_provider")
            else None
        )
        return compute_stock_signals_from_history(
            symbol,
            hist,
            price_provider=provider,
        )

    return _cached(f"stock_signals:{symbol}", 300, load)
