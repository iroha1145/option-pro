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
    start_split = split_for_date(signal_date)
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
    if start_split is not None and end_split is not None and end_split != start_split:
        return {
            "status": "purged",
            "reason": "label_enters_next_split",
            "horizon": horizon,
            "start_date": signal_date.isoformat(),
            "end_date": end.isoformat(),
            "forward_return": None,
            "start_split": start_split,
            "end_split": end_split,
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
    start_price = _finite(start_bar["adj_close" if adjusted else "close"])
    end_price = _finite(end_bar["adj_close" if adjusted else "close"])
    if start_price is None or end_price is None or start_price <= 0 or end_price <= 0:
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


def universe_forward_mean(
    dataset: OfflineOHLCV,
    session: date,
    horizon: int,
    tickers: Sequence[str],
    *,
    allow_sealed: bool = False,
) -> dict[str, Any]:
    values: list[float] = []
    missing = 0
    purged = 0
    for ticker in tickers:
        item = forward_close_return(
            dataset,
            ticker,
            session,
            horizon,
            allow_sealed_prices=allow_sealed,
        )
        value = _finite(item.get("forward_return"))
        if value is not None:
            values.append(value)
        elif item.get("status") == "purged":
            purged += 1
        else:
            missing += 1
    return {
        "status": "active" if values else "unavailable",
        "mean": (sum(values) / len(values)) if values else None,
        "n": len(values),
        "missing": missing,
        "purged": purged,
        "horizon": horizon,
        "signal_date": session.isoformat(),
    }


def attach_event_labels(
    events: Iterable[Mapping[str, Any]],
    dataset: OfflineOHLCV,
    *,
    horizon: int = 20,
    universe_tickers: Sequence[str] | None = None,
    allow_sealed: bool = False,
    benchmark: str = "SPY",
) -> list[dict[str, Any]]:
    """Attach close/close labels plus same-day universe and SPY excess."""

    symbols = list(dict.fromkeys(universe_tickers or dataset.tickers()))
    universe_cache: dict[date, dict[str, Any]] = {}
    labeled: list[dict[str, Any]] = []
    for event in events:
        session = parse_session_date(str(event.get("trading_date") or event.get("signal_date")))
        raw = forward_close_return(
            dataset,
            str(event.get("ticker") or ""),
            session,
            horizon,
            allow_sealed_prices=allow_sealed,
        )
        spy = forward_close_return(
            dataset,
            benchmark,
            session,
            horizon,
            allow_sealed_prices=allow_sealed,
        )
        if session not in universe_cache:
            universe_cache[session] = universe_forward_mean(
                dataset,
                session,
                horizon,
                symbols,
                allow_sealed=allow_sealed,
            )
        universe = universe_cache[session]
        raw_ret = _finite(raw.get("forward_return"))
        spy_ret = _finite(spy.get("forward_return"))
        uni_ret = _finite(universe.get("mean"))
        item = dict(event)
        item[f"label_{horizon}d"] = raw
        item[f"spy_{horizon}d"] = spy
        item[f"universe_{horizon}d"] = universe
        item[f"excess_vs_spy_{horizon}d"] = (
            None if raw_ret is None or spy_ret is None else raw_ret - spy_ret
        )
        item[f"excess_vs_universe_{horizon}d"] = (
            None if raw_ret is None or uni_ret is None else raw_ret - uni_ret
        )
        labeled.append(item)
    return labeled


def outcome_crosses_split(row: Mapping[str, Any], *, horizon: str | int) -> bool:
    label = ((row.get("labels") or {}).get(str(horizon)) or {})
    start = label.get("start_date") or row.get("signal_date")
    end = label.get("end_date")
    if not start or not end:
        return False
    start_split = split_for_date(str(start))
    end_split = split_for_date(str(end))
    return bool(start_split and end_split and start_split != end_split)
