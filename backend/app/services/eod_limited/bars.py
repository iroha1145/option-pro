"""Bounded current-universe daily bars for worker inference. GET never calls this."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.yfinance_batch import download_in_bounded_batches

from .panel import current_universe_tickers

DOWNLOAD_PARAMS = {
    "interval": "1d",
    "group_by": "ticker",
    "progress": False,
    "auto_adjust": False,
    "repair": False,
    "prepost": False,
}


def _frame_to_bars(symbol: str, frame: pd.DataFrame) -> list[ResearchBar]:
    if frame is None or frame.empty:
        return []
    work = frame.copy()
    if isinstance(work.columns, pd.MultiIndex):
        if symbol in work.columns.get_level_values(0):
            work = work[symbol]
        else:
            work.columns = work.columns.get_level_values(-1)
    work = work.rename(columns=str.title)
    bars: list[ResearchBar] = []
    for index, row in work.iterrows():
        session = index.date() if hasattr(index, "date") else pd.Timestamp(index).date()
        close = _finite(row.get("Close"))
        adj = _finite(row.get("Adj Close"))
        volume = _finite(row.get("Volume"))
        raw_close = close
        bars.append(
            ResearchBar(
                security_id=symbol,
                session_date=session,
                open=_finite(row.get("Open")),
                high=_finite(row.get("High")),
                low=_finite(row.get("Low")),
                close=close,
                raw_open=_finite(row.get("Open")),
                raw_close=raw_close,
                volume=volume,
                dollar_volume=None if close is None or volume is None else close * volume,
                tri=adj if adj is not None else close,
                volume_scope="UNKNOWN",
            )
        )
    return bars


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def fetch_current_universe_bars(
    *,
    end: date,
    lookback_days: int = 560,
    tickers: list[str] | None = None,
) -> dict[str, list[ResearchBar]]:
    symbols = tickers or list(current_universe_tickers())
    start = end - timedelta(days=lookback_days)

    def download(**kwargs: Any) -> pd.DataFrame:
        import yfinance as yf

        return yf.download(**kwargs)

    frame = download_in_bounded_batches(
        download,
        tickers=symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        **DOWNLOAD_PARAMS,
    )
    out: dict[str, list[ResearchBar]] = {}
    if frame is None or frame.empty:
        return {symbol: [] for symbol in symbols}
    if isinstance(frame.columns, pd.MultiIndex):
        available = set(frame.columns.get_level_values(0))
        for symbol in symbols:
            out[symbol] = _frame_to_bars(symbol, frame[symbol] if symbol in available else pd.DataFrame())
        return out
    if len(symbols) == 1:
        out[symbols[0]] = _frame_to_bars(symbols[0], frame)
        return out
    return {symbol: [] for symbol in symbols}


def last_bar_session(bars: dict[str, list[ResearchBar]]) -> date | None:
    dates = [row.session_date for rows in bars.values() for row in rows]
    return max(dates) if dates else None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
