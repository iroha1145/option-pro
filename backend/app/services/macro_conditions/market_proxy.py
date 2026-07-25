"""Adjusted daily closes for the eight macro ETF proxies.

This module does not implement a price provider. It calls the daily chain that
already serves the screener and the breakout radar — Massive first, Yahoo as the
fallback — records which provider actually answered, and drops any bar for a
session that has not finished. There is no second provider framework, no extra
API key, and no HTTP call back into this application's own ``/api/*`` surface.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Sequence

from app.services.market_calendar import ET, early_close_minutes, is_trading_day

from .models import EtfObservation, MacroError, finite, iso_instant
from .registry import ETF_SYMBOLS


logger = logging.getLogger("optix.macro.etf")

#: yfinance/Massive period covering an eight-year backfill with slack.
BACKFILL_PERIOD = "10y"
#: Incremental refreshes only need enough tail to catch late corrections.
INCREMENTAL_PERIOD = "1y"

_REGULAR_CLOSE_MINUTES = 16 * 60


def last_completed_trading_day(now: datetime | None = None) -> date:
    """Latest session whose regular close has passed in America/New_York.

    An unfinished bar for today is never treated as a daily observation.
    """

    observed = (now or datetime.now(timezone.utc)).astimezone(ET)
    today = observed.date()
    candidate = today
    # A ten-day walk is enough for any holiday cluster in the NYSE calendar.
    for _ in range(14):
        if is_trading_day(candidate):
            if candidate < today:
                return candidate
            early = early_close_minutes(candidate)
            close_minutes = early if early is not None else _REGULAR_CLOSE_MINUTES
            if observed.hour * 60 + observed.minute >= close_minutes:
                return candidate
        candidate -= timedelta(days=1)
    return candidate


def _frame_rows(frame: object) -> list[tuple[date, float]]:
    columns = getattr(frame, "columns", None)
    if columns is None or "Close" not in list(columns):
        return []
    rows: list[tuple[date, float]] = []
    for index, value in frame["Close"].items():
        number = finite(value)
        if number is None or number <= 0:
            continue
        observation_date = getattr(index, "date", None)
        if not callable(observation_date):
            continue
        rows.append((observation_date(), number))
    return rows


class MarketProxyReader:
    """Reads the eight ETF proxies through the shared daily chain."""

    def __init__(
        self,
        *,
        history: Callable[[str, str], object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._history = history
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _load(self, symbol: str, period: str) -> object:
        if self._history is not None:
            return self._history(symbol, period)
        from app.services.signals import daily_adjusted_history

        return daily_adjusted_history(symbol, period)

    def read(
        self,
        symbols: Sequence[str] = ETF_SYMBOLS,
        *,
        period: str = BACKFILL_PERIOD,
    ) -> tuple[dict[str, tuple[EtfObservation, ...]], dict[str, str]]:
        """Return ``(observations_by_symbol, failures_by_symbol)``.

        A single missing ETF only affects the factors that require it.
        """

        observed = self._now()
        cutoff = last_completed_trading_day(observed)
        fetched_at = iso_instant(observed)
        results: dict[str, tuple[EtfObservation, ...]] = {}
        failures: dict[str, str] = {}
        for symbol in symbols:
            try:
                frame = self._load(symbol, period)
            except Exception:
                # The shared chain already swallows provider errors and returns
                # an empty frame; this guard is for unexpected local failures.
                logger.warning("macro etf history failed symbol=%s code=%s", symbol, "etf_history_unavailable")
                failures[symbol] = "etf_history_unavailable"
                continue
            provider = str(getattr(frame, "attrs", {}).get("price_provider") or "")
            rows = [
                EtfObservation(
                    symbol=symbol,
                    observation_date=observation_date,
                    adjusted_close=value,
                    provider=provider or "unknown",
                )
                for observation_date, value in _frame_rows(frame)
                if observation_date <= cutoff
            ]
            if not rows:
                failures[symbol] = "etf_history_unavailable"
                continue
            rows.sort(key=lambda item: item.observation_date)
            results[symbol] = tuple(rows)
        self.last_fetched_at = fetched_at
        self.last_cutoff = cutoff
        return results, failures


def require_symbols(symbols: Iterable[str]) -> tuple[str, ...]:
    unknown = [symbol for symbol in symbols if symbol not in ETF_SYMBOLS]
    if unknown:
        raise MacroError("etf_history_unavailable", "unregistered ETF requested")
    return tuple(symbols)


__all__ = [
    "BACKFILL_PERIOD",
    "INCREMENTAL_PERIOD",
    "MarketProxyReader",
    "last_completed_trading_day",
    "require_symbols",
]
