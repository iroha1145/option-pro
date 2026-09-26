"""Six-month weekly trend for watchlist cards.

The seven-point ``spark`` remains the short day-to-day chart. ``trend_6m`` adds
the last close of each ISO week over roughly six months, with the row's latest
quote as the final point, so a card can show where the price sits within its
half-year range.

Completed weeks change at most once a day, so each symbol's weekly closes are
cached for hours and only stale symbols are fetched again, a bounded number per
watchlist build. Prices are split-adjusted but not dividend-adjusted: Massive
``adjusted=true`` bars and Yahoo's unadjusted OHLC share that basis, and the
live quote is on the same post-split share count.
"""

from __future__ import annotations

import math
import threading
import time
from datetime import date, datetime, tzinfo
from typing import Any, Callable, Iterable, Mapping

TREND_RANGE = "6mo"
TREND_INTERVAL = "1wk"
TREND_ADJUSTMENT = "split"
TREND_WEEKS = 26
TREND_MAX_POINTS = TREND_WEEKS + 1  # completed weeks plus the current one
HISTORY_TTL_SECONDS = 6 * 60 * 60
MISSING_TTL_SECONDS = 30 * 60
FETCH_BUDGET_PER_BUILD = 256  # starter symbols plus the owner's saved list (<=50) in one build
CACHE_MAX_ENTRIES = 1024

WeeklyCloses = tuple[tuple[date, float], ...]
DailyFetcher = Callable[[list[str]], Mapping[str, Iterable[tuple[date, float]]]]

_lock = threading.Lock()
_cache: dict[str, tuple[float, WeeklyCloses | None]] = {}


def _positive_finite(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def weekly_closes(daily: Iterable[tuple[date, float]]) -> WeeklyCloses:
    """Last usable close of each ISO week, oldest first, at most 27 weeks."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for day, close in sorted(daily, key=lambda point: point[0]):
        if not _positive_finite(close):
            continue
        year, week, _ = day.isocalendar()
        by_week[(year, week)] = (day, float(close))
    ordered = [by_week[key] for key in sorted(by_week)]
    return tuple(ordered[-TREND_MAX_POINTS:])


def compose_trend(
    weekly: WeeklyCloses | None,
    *,
    price: Any,
    quote_date: date,
) -> dict[str, Any] | None:
    """Weekly history with the latest quote standing in for the current week."""
    if not weekly or not _positive_finite(price):
        return None
    points = list(weekly)
    last_day = points[-1][0]
    if last_day.isocalendar()[:2] == quote_date.isocalendar()[:2]:
        points[-1] = (max(last_day, quote_date), float(price))
    elif quote_date > last_day:
        points.append((quote_date, float(price)))
    points = points[-TREND_MAX_POINTS:]
    if len(points) < 2:
        return None
    return {
        "range": TREND_RANGE,
        "interval": TREND_INTERVAL,
        "adjustment": TREND_ADJUSTMENT,
        "points": [
            {"date": day.isoformat(), "close": round(close, 2)}
            for day, close in points
        ],
    }


def valid_trend(value: Any) -> bool:
    """Structural check used before a persisted snapshot is served again."""
    if not isinstance(value, dict):
        return False
    if (
        value.get("range") != TREND_RANGE
        or value.get("interval") != TREND_INTERVAL
        or value.get("adjustment") != TREND_ADJUSTMENT
    ):
        return False
    points = value.get("points")
    if not isinstance(points, list) or not 2 <= len(points) <= TREND_MAX_POINTS:
        return False
    previous: str | None = None
    for point in points:
        if not isinstance(point, dict):
            return False
        day, close = point.get("date"), point.get("close")
        if not isinstance(day, str) or not _positive_finite(close):
            return False
        try:
            if date.fromisoformat(day).isoformat() != day:
                return False
        except ValueError:
            return False
        if previous is not None and day <= previous:
            return False
        previous = day
    return True


def cached_weekly_history(
    tickers: Iterable[str],
    fetch: DailyFetcher,
    *,
    now: float | None = None,
    budget: int = FETCH_BUDGET_PER_BUILD,
) -> dict[str, WeeklyCloses]:
    """Weekly closes for ``tickers``, fetching at most ``budget`` stale symbols.

    Symbols past the budget keep their previous value (or none) until a later
    build; a failed or empty fetch is remembered briefly so one unsupported
    symbol is not requested on every build.
    """
    current = time.monotonic() if now is None else now
    wanted = list(dict.fromkeys(tickers))
    history: dict[str, WeeklyCloses] = {}
    stale: list[str] = []
    with _lock:
        for ticker in wanted:
            cached = _cache.get(ticker)
            if cached is not None and cached[1]:
                history[ticker] = cached[1]
            if cached is None or cached[0] <= current:
                stale.append(ticker)
    to_fetch = stale[: max(0, budget)]
    if not to_fetch:
        return history
    try:
        fetched = fetch(to_fetch)
    except Exception:
        fetched = {}
    with _lock:
        for ticker in to_fetch:
            weekly = weekly_closes(fetched.get(ticker) or ())
            if weekly:
                _cache[ticker] = (current + HISTORY_TTL_SECONDS, weekly)
                history[ticker] = weekly
            else:
                previous = _cache.get(ticker)
                _cache[ticker] = (current + MISSING_TTL_SECONDS, previous[1] if previous else None)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.pop(next(iter(_cache)))
    return history


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _close_series(frame: Any, ticker: str, *, single: bool) -> Any:
    if frame is None or getattr(frame, "empty", True):
        return None
    columns = frame.columns
    if getattr(columns, "nlevels", 1) > 1:
        if ticker in columns.get_level_values(0):
            frame = frame[ticker]
        elif ticker in columns.get_level_values(1):
            frame = frame.xs(ticker, axis=1, level=1)
        else:
            return None
    elif not single:
        return None
    for column in ("Close", "Adj Close"):
        if column in frame.columns:
            return frame[column]
    return None


def _session_closes(series: Any, market_timezone: tzinfo) -> list[tuple[date, float]]:
    """(session date, close) pairs; aware stamps are read in the symbol's market.

    Batched Yahoo frames are normalised to UTC before they are merged, which
    moves an Asian session's midnight into the previous UTC day. Naive daily
    stamps already name the local session.
    """
    if series is None:
        return []
    points: list[tuple[date, float]] = []
    for index, value in series.items():
        try:
            close = float(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if not math.isfinite(close) or close <= 0:
            continue
        when = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
        if isinstance(when, datetime):
            if when.tzinfo is not None:
                when = when.astimezone(market_timezone)
            points.append((when.date(), close))
        elif isinstance(when, date):
            points.append((when, close))
    return points


def fetch_six_month_daily(
    tickers: list[str],
    *,
    download: Callable[..., Any],
    market_timezone: Callable[[str], tzinfo],
    session: Any = None,
) -> dict[str, list[tuple[date, float]]]:
    """Massive split-adjusted daily bars first; Yahoo for symbols it cannot cover.

    Only these two providers are used: the trend is decoration for a card, not
    a reason to spend the MarketData/Stooq/Finnhub budgets the screener relies on.
    """
    from app.services.strength.scanner import _download_massive_history
    from app.services.yfinance_batch import download_in_bounded_batches

    closes: dict[str, list[tuple[date, float]]] = {}
    try:
        massive_frame, _missing = _download_massive_history(tickers, TREND_RANGE)
    except Exception:
        massive_frame = None
    for ticker in tickers:
        points = _session_closes(
            _close_series(massive_frame, ticker, single=False),
            market_timezone(ticker),
        )
        if points:
            closes[ticker] = points
    remaining = [ticker for ticker in tickers if ticker not in closes]
    if remaining:
        kwargs: dict[str, Any] = {
            "tickers": remaining,
            "period": TREND_RANGE,
            "interval": "1d",
            "group_by": "ticker",
            "progress": False,
            "auto_adjust": False,
        }
        if session is not None:
            kwargs["session"] = session
        try:
            yahoo_frame = download_in_bounded_batches(download, **kwargs)
        except Exception:
            yahoo_frame = None
        for ticker in remaining:
            points = _session_closes(
                _close_series(yahoo_frame, ticker, single=len(remaining) == 1),
                market_timezone(ticker),
            )
            if points:
                closes[ticker] = points
    return closes
