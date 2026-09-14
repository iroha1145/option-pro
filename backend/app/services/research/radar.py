"""Daily reconstruction of production breakout detectors.

This is not Discovery replay. Opening-range and premarket types stay
unverifiable without intraday history.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from app.services.breakouts.base_detector import detect_base
from app.services.breakouts.breakout_detector import detect_breakout
from app.services.breakouts.config import get_breakout_settings
from app.services.breakouts.feature_engine import compute_atr
from app.services.breakouts.lifecycle import event_identity
from app.services.breakouts.models import (
    BreakoutCandidate,
    BreakoutSetupType,
    MarketSession,
    TemporalCutoff,
)
from app.services.research.calendar import previous_trading_day, session_close
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import assert_split_access, parse_session_date
from app.services.strength.scanner import _theme_universe


UNVERIFIABLE_SETUP_TYPES = {
    BreakoutSetupType.OPENING_RANGE_BREAKOUT.value,
    BreakoutSetupType.PREMARKET_GAP.value,
    BreakoutSetupType.GAP_HOLD.value,
    BreakoutSetupType.GAP_AND_GO.value,
    BreakoutSetupType.GAP_FADE.value,
}


def _clv(open_: float, high: float, low: float, close: float) -> float | None:
    span = high - low
    if span <= 0:
        return None
    return (close - low) / span


def _daily_features(
    daily: pd.DataFrame,
    *,
    structure_high: float | None,
) -> dict[str, Any]:
    if daily.empty:
        return {"event_price": None}
    bar = daily.iloc[-1]
    close = float(bar["Close"])
    high = float(bar["High"])
    low = float(bar["Low"])
    open_ = float(bar["Open"])
    atr20 = compute_atr(daily, 20)
    hold = 0
    if structure_high is not None:
        for value in reversed(list(daily["Close"].astype(float))):
            if value > structure_high:
                hold += 1
            else:
                break
    return {
        "event_price": close,
        "atr20": atr20,
        "close_location_value": _clv(open_, high, low, close),
        "rvol_time_of_day": None,
        "upper_wick_ratio": (
            (high - max(open_, close)) / (high - low) if high > low else None
        ),
        "hold_bars_above_pivot": hold,
        "hold_bars_above_opening_range": 0,
        "hold_bars_convention": "daily_close_not_intraday",
        "opening_range_complete": False,
        "opening_range_high": None,
        "warnings": ["intraday_rvol_unavailable", "opening_range_unavailable"],
    }


def slice_daily_through(frame: pd.DataFrame, session: date) -> pd.DataFrame:
    """Inclusive session cutoff. Avoids rebuilding a python-date index each call."""

    if frame.empty:
        return frame
    return frame.loc[: pd.Timestamp(session)]


def _evaluate_ticker_session(
    ticker: str,
    daily: pd.DataFrame,
    session: date,
    settings,
) -> tuple[dict[str, Any] | None, str | None]:
    if daily.empty or daily.index.max().date() != session:
        return None, "missing_bar"
    last = daily.iloc[-1]
    if any(pd.isna(last.get(column)) for column in ("Open", "High", "Low", "Close")):
        return None, "incomplete_ohlc"
    prior = previous_trading_day(session)
    as_of = session_close(session)
    prior_close = session_close(prior)
    structure_cutoff = TemporalCutoff(
        event_at=prior_close,
        session=MarketSession.CLOSED,
        include_current_bar=True,
        completed_daily_session=prior,
    )
    event_cutoff = TemporalCutoff(
        event_at=as_of,
        session=MarketSession.REGULAR,
        include_current_bar=True,
        completed_daily_session=session,
    )
    structure = detect_base(ticker, daily, structure_cutoff, settings)
    if structure is None:
        return None, "no_base"
    features = _daily_features(
        daily,
        structure_high=float(structure.resistance_zone.high),
    )
    if features.get("event_price") is None:
        return None, "insufficient_history"
    previous_close = None
    if len(daily) >= 2 and daily.index[-2].date() == prior:
        previous_close = float(daily.iloc[-2]["Close"])
    candidate = BreakoutCandidate(
        ticker=ticker,
        price=features["event_price"],
        previous_regular_close=previous_close,
        provider_timestamp=as_of,
        source="research-daily-reconstruction",
        session=MarketSession.REGULAR,
    )
    detection = detect_breakout(
        candidate,
        structure,
        features,
        event_cutoff,
        settings,
    )
    if not detection.get("triggered"):
        return None, "not_triggered"
    setup = detection.get("setup_type")
    setup_value = setup.value if hasattr(setup, "value") else str(setup)
    event_id = event_identity(
        trading_date=session,
        ticker=ticker,
        setup_type=setup_value,
        pivot_id=structure.pivot_id,
    )
    return (
        {
            "event_id": event_id,
            "ticker": ticker,
            "trading_date": session.isoformat(),
            "setup_type": setup_value,
            "origin_setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT.value,
            "lifecycle_state": (
                detection["lifecycle_state"].value
                if hasattr(detection["lifecycle_state"], "value")
                else detection["lifecycle_state"]
            ),
            "triggered": True,
            "confirmed": bool(detection.get("confirmed")),
            "first_seen_at": prior_close.isoformat(),
            "triggered_at": as_of.isoformat(),
            "published_at": as_of.isoformat(),
            "feature_cutoff_at": as_of.isoformat(),
            "raw_as_of": as_of.isoformat(),
            "event_price": features["event_price"],
            "event_price_is_fill": False,
            "pivot_id": structure.pivot_id,
            "resistance_high": float(structure.resistance_zone.high),
            "breakout_distance_atr": detection.get("breakout_distance_atr"),
            "atr20": features.get("atr20"),
            "close_location_value": features.get("close_location_value"),
            "upper_wick_ratio": features.get("upper_wick_ratio"),
            "volume": float(last["Volume"]) if "Volume" in last else None,
            "invalidation_price": getattr(structure, "invalidation_price", None),
            "extended": bool(detection.get("extended")),
            "transition_reason": detection.get("transition_reason"),
            "warnings": list(features.get("warnings") or [])
            + list(detection.get("warnings") or []),
            "intraday_verified": False,
        },
        None,
    )


def reconstruct_ticker_dates(
    dataset: OfflineOHLCV,
    ticker: str,
    dates: Sequence[date],
    *,
    allow_sealed: bool = False,
    frame: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Walk one ticker across many sessions. Same detectors as the daily entry."""

    settings = get_breakout_settings()
    source = frame if frame is not None else dataset.frame(
        ticker,
        through=max(dates) if dates else None,
        allow_sealed=allow_sealed,
    )
    events: list[dict[str, Any]] = []
    skipped = {
        "insufficient_history": 0,
        "no_base": 0,
        "not_triggered": 0,
        "missing_bar": 0,
    }
    for session in dates:
        assert_split_access(session, allow_sealed=allow_sealed, purpose="radar_replay")
        daily = slice_daily_through(source, session)
        event, reason = _evaluate_ticker_session(ticker, daily, session, settings)
        if event is None:
            skipped[reason or "missing_bar"] = skipped.get(reason or "missing_bar", 0) + 1
            continue
        events.append(event)
    return {"ticker": ticker, "events": events, "skipped": skipped}


def reconstruct_daily_base_events(
    dataset: OfflineOHLCV,
    signal_date: date | str,
    *,
    tickers: Iterable[str] | None = None,
    allow_sealed: bool = False,
    frames: dict[str, pd.DataFrame] | None = None,
) -> dict[str, Any]:
    """Evaluate production daily-base logic at T using a T-1 structure.

    The event bar does not generate the resistance it is asked to break.
    """

    session = parse_session_date(signal_date)
    assert_split_access(session, allow_sealed=allow_sealed, purpose="radar_replay")
    as_of = session_close(session)
    settings = get_breakout_settings()
    universe, _meta = _theme_universe()
    symbols = list(dict.fromkeys(tickers or universe))
    events: list[dict[str, Any]] = []
    skipped = {
        "insufficient_history": 0,
        "no_base": 0,
        "not_triggered": 0,
        "missing_bar": 0,
    }
    for ticker in symbols:
        if frames is not None and ticker in frames:
            daily = slice_daily_through(frames[ticker], session)
        else:
            daily = dataset.frame(ticker, through=session, allow_sealed=allow_sealed)
        event, reason = _evaluate_ticker_session(ticker, daily, session, settings)
        if event is None:
            skipped[reason or "missing_bar"] = skipped.get(reason or "missing_bar", 0) + 1
            continue
        events.append(event)
    return {
        "signal_date": session.isoformat(),
        "as_of": as_of.isoformat(),
        "evidence_grade": "C",
        "pipeline": "daily_base_reconstruction_without_discovery",
        "unverifiable_setup_types": sorted(UNVERIFIABLE_SETUP_TYPES),
        "events": events,
        "skipped": skipped,
        "notes": [
            "未重放 TradingView Discovery，也没有历史盘中/盘前数据。",
            "结构在 T-1 收盘可见窗口上计算，突破 K 线不生成自身阻力。",
            "RVOL_TOD 与开盘区间缺失，因此几乎不会出现强单根确认。",
            "event_price 是触发标记，不是发布后可成交价。",
        ],
    }


def prior_screener_overlap(
    events: Iterable[dict[str, Any]],
    screener_rows: Iterable[dict[str, Any]],
    *,
    top: int = 20,
) -> list[dict[str, Any]]:
    """Mark radar events that already appeared in an earlier screener snapshot.

    Same-day close ranks are excluded: a T-close list cannot endorse a T-session
    breakout. The most recent strictly earlier snapshot is used.
    """

    leaders_by_date: dict[str, set[str]] = {}
    for row in screener_rows:
        ticker = row.get("ticker")
        session = str(row.get("signal_date") or "")
        rank = row.get("selected_view_rank")
        if not ticker or not session or rank is None:
            continue
        if int(rank) > top:
            continue
        leaders_by_date.setdefault(session, set()).add(str(ticker))
    dated = sorted(leaders_by_date)
    out = []
    for event in events:
        item = dict(event)
        event_date = str(item.get("trading_date") or item.get("signal_date") or "")
        prior_dates = [day for day in dated if day < event_date]
        if not prior_dates:
            item["in_prior_screener_top"] = False
            item["prior_screener_date"] = None
        else:
            prior = prior_dates[-1]
            item["in_prior_screener_top"] = str(item.get("ticker") or "") in leaders_by_date[prior]
            item["prior_screener_date"] = prior
        out.append(item)
    return out


def first_trigger_by_pivot(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Keep the first ticker+pivot_id hit. This is not an economic-event claim."""

    first_hits: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_triggers = 0
    ordered = sorted(
        (dict(event) for event in events),
        key=lambda item: (
            str(item.get("trading_date") or ""),
            str(item.get("ticker") or ""),
            str(item.get("pivot_id") or ""),
        ),
    )
    for event in ordered:
        key = (str(event.get("ticker") or ""), str(event.get("pivot_id") or ""))
        if key in first_hits:
            duplicate_triggers += 1
            continue
        first_hits[key] = event
    return {
        "events": list(first_hits.values()),
        "duplicate_triggers_dropped": duplicate_triggers,
        "protocol": "ticker_plus_pivot_id_first_seen",
        "note": (
            "pivot_id includes base_end, so a platform that rolls forward can "
            "mint a new id. This is not a first-economic-event guarantee."
        ),
    }


def platform_continuity_key(event: Mapping[str, Any]) -> tuple[str, str] | None:
    ticker = str(event.get("ticker") or "")
    resistance = event.get("resistance_high")
    if not ticker or resistance is None:
        return None
    try:
        rounded = f"{float(resistance):.2f}"
    except (TypeError, ValueError):
        return None
    return ticker, rounded


def platform_evolution_groups(events: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Group same-ticker events whose resistance stays on the same 1-cent platform.

    Adjacent or overlapping trading dates in that group are one evolving
    platform. Distinct pivot_ids inside the group are not independent first
    economic events.
    """

    buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        key = platform_continuity_key(event)
        if key is None:
            continue
        buckets.setdefault(key, []).append(dict(event))
    groups = []
    extra_after_pivot_dedupe = 0
    for key, items in buckets.items():
        items.sort(key=lambda item: str(item.get("trading_date") or ""))
        pivot_ids = {str(item.get("pivot_id") or "") for item in items}
        if len(items) > 1 and len(pivot_ids) > 1:
            extra_after_pivot_dedupe += len(items) - 1
        groups.append(
            {
                "ticker": key[0],
                "resistance_high": key[1],
                "event_count": len(items),
                "distinct_pivot_ids": len(pivot_ids),
                "first_trading_date": items[0].get("trading_date"),
                "last_trading_date": items[-1].get("trading_date"),
                "pivot_ids": sorted(pivot_ids),
            }
        )
    evolving = [group for group in groups if group["distinct_pivot_ids"] > 1]
    return {
        "group_count": len(groups),
        "evolving_platform_count": len(evolving),
        "events_in_evolving_platforms": sum(group["event_count"] for group in evolving),
        "extra_events_if_only_pivot_id_deduped": extra_after_pivot_dedupe,
        "groups": groups,
        "note": (
            "This is a Grade C continuity heuristic, not Discovery identity. "
            "ticker+pivot_id uniqueness does not prove the first economic event."
        ),
    }
