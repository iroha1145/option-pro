"""Executable-path returns and MAE/MFE. These never feed ranking."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Mapping

from app.services.research.calendar import nth_trading_day
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import parse_session_date, split_for_date


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def executable_open_return(
    dataset: OfflineOHLCV,
    ticker: str,
    signal_date: date | str,
    *,
    entry_lag_sessions: int,
    hold_sessions: int = 20,
    allow_sealed: bool = False,
    lock_split: bool = True,
) -> dict[str, Any]:
    """Fill at the open ``entry_lag_sessions`` after the signal close.

    Exit is the open of the hold-th subsequent session. Missing opens stay
    unavailable and are not rolled to a later print.
    """

    session = parse_session_date(signal_date)
    entry_date = nth_trading_day(session, int(entry_lag_sessions), after=True)
    if entry_date is None:
        return {
            "status": "unavailable",
            "reason": "entry_calendar_unresolved",
            "forward_return": None,
        }
    exit_date = nth_trading_day(entry_date, int(hold_sessions), after=True)
    if exit_date is None:
        return {
            "status": "unavailable",
            "reason": "exit_calendar_unresolved",
            "entry_date": entry_date.isoformat(),
            "forward_return": None,
        }
    if not allow_sealed and (
        split_for_date(entry_date) == "sealed" or split_for_date(exit_date) == "sealed"
    ):
        return {
            "status": "purged",
            "reason": "path_enters_sealed",
            "entry_date": entry_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "forward_return": None,
        }
    if lock_split:
        start_split = split_for_date(session)
        if start_split and (
            split_for_date(entry_date) != start_split or split_for_date(exit_date) != start_split
        ):
            return {
                "status": "purged",
                "reason": "path_enters_next_split",
                "entry_date": entry_date.isoformat(),
                "exit_date": exit_date.isoformat(),
                "forward_return": None,
            }
    entry_bar = dataset.bar(ticker, entry_date)
    exit_bar = dataset.bar(ticker, exit_date)
    entry = None if entry_bar is None else _finite(entry_bar.get("adj_open"))
    exit_px = None if exit_bar is None else _finite(exit_bar.get("adj_open"))
    if entry is None or entry <= 0:
        return {
            "status": "unavailable",
            "reason": "missing_entry_open",
            "entry_date": entry_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "forward_return": None,
        }
    if exit_px is None or exit_px <= 0:
        return {
            "status": "unavailable",
            "reason": "missing_exit_open",
            "entry_date": entry_date.isoformat(),
            "exit_date": exit_date.isoformat(),
            "entry_open": entry,
            "forward_return": None,
        }
    return {
        "status": "active",
        "reason": None,
        "entry_date": entry_date.isoformat(),
        "exit_date": exit_date.isoformat(),
        "entry_open": entry,
        "exit_open": exit_px,
        "forward_return": exit_px / entry - 1.0,
        "entry_is_fill": True,
        "exit_is_fill": True,
    }


def mae_mfe_from_entry(
    dataset: OfflineOHLCV,
    ticker: str,
    entry_date: date | str,
    exit_date: date | str,
    *,
    entry_price: float | None = None,
) -> dict[str, Any]:
    """Adverse/favorable excursion during the hold.

    Entry day uses that session's high/low versus the entry open. The exit
    session contributes only its open, not later high/low.
    """

    start = parse_session_date(entry_date)
    end = parse_session_date(exit_date)
    start_bar = dataset.bar(ticker, start)
    if start_bar is None:
        return {"status": "unavailable", "reason": "missing_entry_bar", "mae": None, "mfe": None}
    entry = entry_price if entry_price is not None else _finite(start_bar.get("adj_open"))
    if entry is None or entry <= 0:
        return {"status": "unavailable", "reason": "missing_entry_price", "mae": None, "mfe": None}

    worst = 0.0
    best = 0.0
    cursor = start
    sessions = 0
    incomplete = False
    while cursor <= end:
        bar = dataset.bar(ticker, cursor)
        if bar is None:
            incomplete = True
            break
        if cursor == end:
            mark = _finite(bar.get("adj_open"))
            if mark is None:
                incomplete = True
                break
            excursion = mark / entry - 1.0
            worst = min(worst, excursion)
            best = max(best, excursion)
        else:
            low = _finite(bar.get("adj_low"))
            high = _finite(bar.get("adj_high"))
            if low is None or high is None:
                incomplete = True
                break
            worst = min(worst, low / entry - 1.0)
            best = max(best, high / entry - 1.0)
        sessions += 1
        nxt = nth_trading_day(cursor, 1, after=True)
        if nxt is None:
            if cursor != end:
                incomplete = True
            break
        cursor = nxt
    return {
        "status": "incomplete" if incomplete else "active",
        "reason": "missing_path_bar" if incomplete else None,
        "mae": worst,
        "mfe": best,
        "entry_price": entry,
        "sessions_seen": sessions,
        "note": "Exit-day high/low are excluded after the exit open.",
    }


def trigger_to_entry_change(
    dataset: OfflineOHLCV,
    ticker: str,
    signal_date: date | str,
    entry_date: date | str,
) -> dict[str, Any]:
    session = parse_session_date(signal_date)
    entry = parse_session_date(entry_date)
    signal_bar = dataset.bar(ticker, session)
    entry_bar = dataset.bar(ticker, entry)
    close = None if signal_bar is None else _finite(signal_bar.get("adj_close"))
    open_ = None if entry_bar is None else _finite(entry_bar.get("adj_open"))
    if close is None or close <= 0 or open_ is None or open_ <= 0:
        return {"status": "unavailable", "price_change": None}
    return {
        "status": "active",
        "trigger_close": close,
        "entry_open": open_,
        "price_change": open_ / close - 1.0,
    }
