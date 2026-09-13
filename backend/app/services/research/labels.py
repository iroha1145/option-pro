"""Forward labels that never leak into scoring.

Labels live in a separate table from ranks. A missing session, halt, or
immature horizon stays unavailable. The endpoint is the exact n-th NYSE
session; it is not rolled forward to the next available print.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.services.research.calendar import nth_trading_day
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import (
    ALL_LABEL_HORIZONS,
    FROZEN_SPLITS,
    PRIMARY_HORIZONS,
    SplitName,
    assert_split_access,
    parse_session_date,
    split_for_date,
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def last_usable_signal_date(
    split: SplitName,
    horizon: int,
    *,
    embargo_sessions: int = 1,
) -> date:
    """Drop signals whose label window touches the next split (purge)."""

    window = FROZEN_SPLITS[split]
    end = window["end"]
    splits = list(FROZEN_SPLITS)
    index = splits.index(split)
    if index + 1 < len(splits):
        next_start = FROZEN_SPLITS[splits[index + 1]]["start"]
        blocked = nth_trading_day(next_start, horizon + embargo_sessions, after=False)
        if blocked is not None and blocked < end:
            end = blocked
    return end


def label_end_date(signal_date: date, horizon: int) -> date | None:
    return nth_trading_day(signal_date, horizon, after=True)


def forward_close_return(
    dataset: OfflineOHLCV,
    ticker: str,
    signal_date: date,
    horizon: int,
    *,
    adjusted: bool = True,
    allow_sealed_prices: bool = False,
) -> dict[str, Any]:
    """Point-to-point close/close label. Entry close is a mark, not a fill."""

    end = label_end_date(signal_date, horizon)
    if end is None:
        return {
            "status": "unavailable",
            "reason": "calendar_horizon_unresolved",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": None,
            "forward_return": None,
        }
    end_split = split_for_date(end)
    if end_split == "sealed" and not allow_sealed_prices:
        return {
            "status": "purged",
            "reason": "label_enters_sealed_or_unblinded_window",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": end.isoformat(),
            "forward_return": None,
        }
    start_bar = dataset.bar(ticker, signal_date)
    end_bar = dataset.bar(ticker, end)
    if start_bar is None:
        return {
            "status": "unavailable",
            "reason": "missing_signal_session",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": end.isoformat(),
            "forward_return": None,
        }
    if end_bar is None:
        return {
            "status": "unavailable",
            "reason": "missing_exit_session_not_extended",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": end.isoformat(),
            "forward_return": None,
        }
    start_price = start_bar["adj_close" if adjusted else "close"]
    end_price = end_bar["adj_close" if adjusted else "close"]
    if start_price <= 0 or end_price <= 0:
        return {
            "status": "unavailable",
            "reason": "non_positive_price",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": end.isoformat(),
            "forward_return": None,
        }
    return {
        "status": "active",
        "reason": None,
        "horizon": horizon,
        "start_date": signal_date.isoformat(),
        "end_date": end.isoformat(),
        "entry_mark": start_price,
        "exit_mark": end_price,
        "forward_return": end_price / start_price - 1.0,
        "adjustment": "split_dividend_adjusted" if adjusted else "unadjusted",
        "entry_is_fill": False,
    }


def attach_screener_labels(
    rows: Iterable[Mapping[str, Any]],
    dataset: OfflineOHLCV,
    *,
    signal_date: date | str,
    horizons: Sequence[int] = PRIMARY_HORIZONS,
    benchmark: str = "SPY",
    allow_sealed: bool = False,
) -> list[dict[str, Any]]:
    session = parse_session_date(signal_date)
    assert_split_access(session, allow_sealed=allow_sealed, purpose="screener_label")
    universe_returns: dict[int, list[float]] = {int(horizon): [] for horizon in horizons}
    prepared: list[dict[str, Any]] = []
    for row in rows:
        ticker = str(row.get("ticker") or "")
        labeled = dict(row)
        labels: dict[str, Any] = {}
        for horizon in horizons:
            item = forward_close_return(
                dataset,
                ticker,
                session,
                int(horizon),
                allow_sealed_prices=allow_sealed,
            )
            labels[str(horizon)] = item
            value = _finite(item.get("forward_return"))
            if value is not None:
                universe_returns[int(horizon)].append(value)
        labeled["labels"] = labels
        labeled["label_signal_date"] = session.isoformat()
        prepared.append(labeled)

    spy_labels = {
        str(horizon): forward_close_return(
            dataset,
            benchmark,
            session,
            int(horizon),
            allow_sealed_prices=allow_sealed,
        )
        for horizon in horizons
    }
    universe_mean = {
        str(horizon): (
            sum(values) / len(values) if values else None
        )
        for horizon, values in universe_returns.items()
    }
    for labeled in prepared:
        excess: dict[str, Any] = {}
        for horizon in horizons:
            key = str(horizon)
            raw = _finite((labeled["labels"][key] or {}).get("forward_return"))
            spy = _finite((spy_labels[key] or {}).get("forward_return"))
            mean = _finite(universe_mean[key])
            excess[key] = {
                "raw": raw,
                "excess_vs_spy": None if raw is None or spy is None else raw - spy,
                "excess_vs_universe": None if raw is None or mean is None else raw - mean,
            }
        labeled["excess"] = excess
        labeled["benchmark_labels"] = spy_labels
        labeled["universe_mean_labels"] = universe_mean
    return prepared
