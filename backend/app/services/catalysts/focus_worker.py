"""Independent producer for the signed MacroLens focus-context snapshot."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import math
import signal
import sqlite3
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from datetime import date, datetime, time, timedelta, timezone
from typing import Any

import pandas as pd

from app.services.breakouts.adapters.price_data import YahooPriceDataAdapter
from app.services.breakouts.clock import MarketClock, MarketClockSnapshot
from app.services.breakouts.feature_engine import compute_feature_snapshot
from app.services.breakouts.models import (
    DiscoveryProfile,
    MarketSession,
    TemporalCutoff,
    normalize_ticker,
)
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider
from app.services.market_calendar import ET, early_close_minutes, is_trading_day

from .errors import CatalystRepositoryError
from .focus_config import FocusContextSettings, get_focus_context_settings
from .focus_models import FocusContextDraft, FocusContextResponse, FocusSymbol
from .focus_publisher import _breakout_rows, _market_session, verify_focus_contract
from .focus_universe import build_focus_context
from .models import utc_iso
from .repository import (
    FOCUS_PRODUCER_WORKER_PREFIX,
    CatalystRepository,
    _as_utc,
)


logger = logging.getLogger("optix.catalysts.focus_worker")
LOCK_NAME = "focus-context-producer"
_ACTIVE_BREAKOUT_STATES = {
    "TRIGGERED",
    "CONFIRMED",
    "HOLDING",
    "RETESTING",
    "RETEST_HELD",
    "REACCELERATING",
    "EXTENDED",
}

StrengthLoader = Callable[[], Awaitable[Mapping[str, Any]]]
DiscoveryLoader = Callable[[MarketClockSnapshot], Awaitable[Any]]
IntradayLoader = Callable[
    [Sequence[str], TemporalCutoff], Awaitable[Mapping[str, Any]]
]
BreakoutLoader = Callable[[], Sequence[Mapping[str, Any]]]


def _latest_completed_trading_day(
    observed: datetime,
    *,
    settlement_delay_seconds: int = 1800,
) -> date:
    """Return the last session whose daily provider bar has had time to settle."""

    if settlement_delay_seconds < 0:
        raise ValueError("daily strength settlement delay must not be negative")
    local = observed.astimezone(ET)
    candidate = local.date()
    close_minutes = early_close_minutes(candidate) or 16 * 60
    close_at = datetime.combine(
        candidate,
        time(hour=close_minutes // 60, minute=close_minutes % 60),
        tzinfo=ET,
    )
    if (
        not is_trading_day(candidate)
        or local < close_at + timedelta(seconds=settlement_delay_seconds)
    ):
        candidate -= timedelta(days=1)
    for _ in range(14):
        if is_trading_day(candidate):
            return candidate
        candidate -= timedelta(days=1)
    raise RuntimeError("focus_completed_trading_day_unavailable")


def _daily_strength_cache_identity() -> dict[str, str]:
    from app.services.breakouts.config import get_breakout_settings
    from app.services.strength.scanner import (
        STRENGTH_FEATURE_VERSION,
        STRENGTH_NORMALIZATION_VERSION,
        STRENGTH_SCORE_VERSION,
        _canonical_universe_version,
        _theme_universe,
    )

    tickers, metadata = _theme_universe()
    breakout = get_breakout_settings()
    material = {
        "cache": "focus-daily-strength-v1",
        "universe": _canonical_universe_version(tickers, metadata),
        "score": STRENGTH_SCORE_VERSION,
        "feature": STRENGTH_FEATURE_VERSION,
        "normalization": STRENGTH_NORMALIZATION_VERSION,
        "range_mode": breakout.range_persistence_mode,
        "range_version": breakout.range_persistence_version,
        "range_length": breakout.range_persistence_length,
        "range_fast_length": breakout.range_persistence_fast_length,
        "range_slope_days": breakout.range_persistence_slope_days,
        "range_ratio_window": breakout.range_persistence_ratio_window,
        "range_ratio_threshold": breakout.range_persistence_ratio_threshold,
        "range_min_history_multiplier": (
            breakout.range_persistence_min_history_multiplier
        ),
        "range_trend_family_weight": (
            breakout.range_persistence_trend_family_weight
        ),
        "range_final_weight_cap": breakout.range_persistence_final_weight_cap,
    }
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    return {
        "cache_version": (
            f"focus-daily-strength-v1:{hashlib.sha256(encoded).hexdigest()}"
        ),
        "strength_feature_version": STRENGTH_FEATURE_VERSION,
        "strength_score_version": STRENGTH_SCORE_VERSION,
        "normalization_version": STRENGTH_NORMALIZATION_VERSION,
        "range_persistence_version": breakout.range_persistence_version,
    }


def _daily_strength_cache_version() -> str:
    return _daily_strength_cache_identity()["cache_version"]


def _daily_strength_status(payload: Mapping[str, Any]) -> str:
    explicit = str(payload.get("status") or "").lower()
    rows = [item for item in payload.get("_focus_rows") or () if isinstance(item, Mapping)]
    sources = payload.get("data_sources")
    prices = sources.get("prices") if isinstance(sources, Mapping) else None
    price_status = str(prices.get("status") or "") if isinstance(prices, Mapping) else ""
    if explicit == "unavailable" or price_status == "unavailable" or not rows:
        return "unavailable"
    if explicit in {"degraded", "stale"} or price_status in {
        "degraded",
        "fallback",
        "stale",
    }:
        return "degraded"
    return "active"


def _optional_utc(value: Any) -> datetime | None:
    try:
        return _as_utc(value)
    except (TypeError, ValueError):
        return None


def _daily_strength_payload(
    payload: Mapping[str, Any],
    *,
    completed_session_date: date | None = None,
) -> dict[str, Any]:
    allowed_row_fields = {
        "ticker",
        "sector_id",
        "primary_sector_id",
        "session_change_pct",
        "avg_dollar_volume_20d",
        "data_quality",
        "universe_member",
        "universe_version",
        "universe_as_of",
        "daily_data_through",
    }
    rows = [
        {key: value for key, value in dict(item).items() if key in allowed_row_fields}
        for item in payload.get("_focus_rows") or ()
        if isinstance(item, Mapping)
    ]
    sources = payload.get("data_sources")
    prices = sources.get("prices") if isinstance(sources, Mapping) else None
    valid_tickers = {
        ticker
        for row in rows
        if (ticker := _ticker(row.get("ticker"))) is not None
    }
    missing_tickers = {
        ticker
        for raw in (
            prices.get("missing_symbols")
            if isinstance(prices, Mapping)
            else ()
        )
        or ()
        if (ticker := _ticker(raw)) is not None
    }
    expected_symbol_count = 0
    for raw_count in (
        payload.get("universe_count"),
        payload.get("requested_count"),
    ):
        count = _finite(raw_count)
        if count is not None and count > 0:
            expected_symbol_count = max(expected_symbol_count, int(count))
    expected_symbol_count = max(
        expected_symbol_count,
        len(valid_tickers) + len(missing_tickers - valid_tickers),
    )
    available_symbol_count = len(valid_tickers)
    availability_coverage = (
        available_symbol_count / expected_symbol_count
        if expected_symbol_count
        else 0.0
    )
    explicit_coverage = (
        _finite(prices.get("coverage")) if isinstance(prices, Mapping) else None
    )
    if explicit_coverage is not None and explicit_coverage > 1:
        explicit_coverage /= 100.0
    if explicit_coverage is None or not 0 <= explicit_coverage <= 1:
        explicit_coverage = availability_coverage
    else:
        # A provider percentage cannot override missing rows visible in the
        # requested universe.
        explicit_coverage = min(explicit_coverage, availability_coverage)
    completed_session_symbol_count = available_symbol_count
    completed_session_coverage = availability_coverage
    if completed_session_date is not None:
        completed_tickers = {
            ticker
            for row in rows
            if (ticker := _ticker(row.get("ticker"))) is not None
            and (data_through := _optional_utc(row.get("daily_data_through")))
            is not None
            and data_through.astimezone(ET).date() == completed_session_date
        }
        completed_session_symbol_count = len(completed_tickers)
        completed_session_coverage = (
            completed_session_symbol_count / expected_symbol_count
            if expected_symbol_count
            else 0.0
        )
    effective_coverage = min(explicit_coverage, completed_session_coverage)
    canonical_symbols = sorted(
        {
            str(row.get("ticker") or "").strip().upper()
            for row in rows
            if row.get("universe_member") and _ticker(row.get("ticker")) is not None
        }
    )
    return {
        "as_of": payload.get("as_of"),
        "universe_as_of": payload.get("universe_as_of"),
        "universe_version": payload.get("universe_version") or "unknown",
        "score_version": payload.get("score_version"),
        "feature_version": payload.get("feature_version"),
        "normalization_version": payload.get("normalization_version"),
        "range_persistence_version": payload.get("range_persistence_version"),
        "canonical_symbols": canonical_symbols,
        "expected_symbol_count": expected_symbol_count,
        "available_symbol_count": available_symbol_count,
        "missing_symbol_count": max(
            len(missing_tickers),
            expected_symbol_count - available_symbol_count,
        ),
        "completed_session_date": (
            completed_session_date.isoformat()
            if completed_session_date is not None
            else None
        ),
        "completed_session_symbol_count": completed_session_symbol_count,
        "availability_coverage": round(availability_coverage, 6),
        "completed_session_coverage": round(completed_session_coverage, 6),
        "coverage": round(effective_coverage, 6),
        "data_sources": {"prices": dict(prices)} if isinstance(prices, Mapping) else {},
        "_focus_rows": rows,
    }


def _daily_strength_data_through(payload: Mapping[str, Any]) -> datetime | None:
    values = [
        value
        for item in payload.get("_focus_rows") or ()
        if isinstance(item, Mapping)
        and (value := _optional_utc(item.get("daily_data_through"))) is not None
    ]
    return min(values) if values else None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_available(*values: float | None) -> float | None:
    return next((value for value in values if value is not None), None)


def _fallback_quality(value: Any) -> float | None:
    quality = _finite(value)
    if quality is None:
        return None
    if quality > 1:
        quality /= 100.0
    if quality < 0 or quality > 1:
        return None
    return round(min(0.6, quality * 0.7), 4)


def _published_coverage(
    symbols: Sequence[FocusSymbol],
    *,
    market_volume_rank_scope: str,
) -> dict[str, Any]:
    """Summarize only the symbols in the immutable published snapshot."""

    if market_volume_rank_scope not in {"market", "candidate"}:
        market_volume_rank_scope = "candidate"
    symbol_count = len(symbols)
    data_through_values = [
        value
        for symbol in symbols
        if (value := _as_utc(symbol.data_through)) is not None
    ]

    def health_bucket(symbol: FocusSymbol) -> str:
        if symbol.data_status == "stale" or symbol.source_status == "stale":
            return "stale"
        if symbol.source_status == "fallback":
            return "fallback"
        if symbol.source_status in {"active", "degraded"}:
            return "active"
        return "unavailable"

    health_counts = Counter(health_bucket(symbol) for symbol in symbols)
    basis_counts = Counter(symbol.dollar_volume_basis for symbol in symbols)
    status_counts = Counter(symbol.source_status for symbol in symbols)
    intraday_exact_count = sum(
        symbol.dollar_volume_basis == "intraday_completed_bars" for symbol in symbols
    )
    rvol_available_count = sum(
        symbol.rvol_time_of_day is not None for symbol in symbols
    )
    session_change_available_count = sum(
        symbol.session_change_pct is not None for symbol in symbols
    )
    selected_min = min(data_through_values) if data_through_values else None
    selected_max = max(data_through_values) if data_through_values else None
    return {
        "published_symbol_count": symbol_count,
        "dollar_volume_basis": dict(sorted(basis_counts.items())),
        "source_status": dict(sorted(status_counts.items())),
        "symbol_sources": [
            {
                "ticker": symbol.ticker,
                "dollar_volume_basis": symbol.dollar_volume_basis,
                "dollar_volume": symbol.dollar_volume,
                "data_through": (
                    symbol.data_through.isoformat()
                    if symbol.data_through is not None
                    else None
                ),
                "source_status": symbol.source_status,
                "data_source": symbol.data_source or "unavailable",
            }
            for symbol in symbols
        ],
        "data_through": selected_min.isoformat() if selected_min else None,
        "selected_data_through_min": (
            selected_min.isoformat() if selected_min else None
        ),
        "selected_data_through_max": (
            selected_max.isoformat() if selected_max else None
        ),
        "data_through_symbol_count": len(data_through_values),
        "data_through_missing_count": symbol_count - len(data_through_values),
        "data_through_coverage": (
            round(len(data_through_values) / symbol_count, 4)
            if symbol_count
            else 0.0
        ),
        "active_symbol_count": health_counts["active"],
        "stale_symbol_count": health_counts["stale"],
        "fallback_symbol_count": health_counts["fallback"],
        "unavailable_symbol_count": health_counts["unavailable"],
        "intraday_exact_count": intraday_exact_count,
        "intraday_exact_ratio": (
            round(intraday_exact_count / symbol_count, 4) if symbol_count else 0.0
        ),
        "rvol_available_count": rvol_available_count,
        "rvol_available_ratio": (
            round(rvol_available_count / symbol_count, 4) if symbol_count else 0.0
        ),
        "rvol_unavailable_count": symbol_count - rvol_available_count,
        "session_change_unavailable_count": (
            symbol_count - session_change_available_count
        ),
        "market_volume_rank_scope": market_volume_rank_scope,
    }


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    return {}


def _ticker(value: Any) -> str | None:
    try:
        return normalize_ticker(value)
    except (TypeError, ValueError):
        return None


def fixed_refresh_times(day: date) -> tuple[datetime, ...]:
    """Return the three ET refreshes tied to fixed analysis cycles."""

    if not is_trading_day(day):
        return ()
    close = early_close_minutes(day) or 16 * 60
    minutes = (7 * 60 + 50, 11 * 60 + 50, close - 10)
    return tuple(
        datetime.combine(
            day,
            time(hour=value // 60, minute=value % 60),
            tzinfo=ET,
        )
        for value in sorted(set(minutes))
    )


def next_refresh_at(
    observed: datetime,
    *,
    interval_seconds: int = 1800,
) -> datetime:
    """Return the next half-hour or fixed ET refresh, whichever is earlier."""

    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("focus schedule requires a timezone-aware datetime")
    if interval_seconds != 1800:
        raise ValueError("focus producer interval must remain 30 minutes")
    local = observed.astimezone(ET)
    base = local.replace(second=0, microsecond=0)
    minute_of_day = base.hour * 60 + base.minute
    next_grid_minute = ((minute_of_day // 30) + 1) * 30
    grid_day = base.date()
    if next_grid_minute >= 24 * 60:
        grid_day += timedelta(days=1)
        next_grid_minute -= 24 * 60
    candidates = [
        datetime.combine(
            grid_day,
            time(
                hour=next_grid_minute // 60,
                minute=next_grid_minute % 60,
            ),
            tzinfo=ET,
        )
    ]
    for offset in range(8):
        candidates.extend(fixed_refresh_times(local.date() + timedelta(days=offset)))
    future = [candidate for candidate in candidates if candidate > local]
    if not future:
        raise RuntimeError("unable to determine next focus refresh")
    return min(future).astimezone(timezone.utc)


def _market_session_name(snapshot: MarketClockSnapshot) -> str:
    if snapshot.session is MarketSession.POSTMARKET:
        return "after_hours"
    return snapshot.session.value


def _cutoff(snapshot: MarketClockSnapshot) -> TemporalCutoff:
    return TemporalCutoff(
        event_at=snapshot.as_of,
        session=snapshot.session,
        include_current_bar=False,
    )


def _intraday_session_change_pct(
    frame: pd.DataFrame,
    *,
    as_of: datetime,
    session: MarketSession,
) -> float | None:
    if (
        not isinstance(frame, pd.DataFrame)
        or frame.empty
        or "Close" not in frame.columns
        or not isinstance(frame.index, pd.DatetimeIndex)
        or frame.index.tz is None
    ):
        return None
    local = frame.copy()
    local.index = local.index.tz_convert(ET)
    close = pd.to_numeric(local["Close"], errors="coerce")
    current_day = as_of.astimezone(ET).date()
    cutoff = as_of.astimezone(ET)
    current_mask = (
        (pd.Index(local.index.date) == current_day)
        & (local.index <= cutoff)
    )
    current = close[current_mask].dropna()
    if current.empty:
        return None

    baseline_day = current_day if session is MarketSession.POSTMARKET else None
    if baseline_day is None:
        earlier_days = sorted({item for item in local.index.date if item < current_day})
        if not earlier_days:
            return None
        baseline_day = earlier_days[-1]
    close_minutes = early_close_minutes(baseline_day) or 16 * 60
    minute_of_day = local.index.hour * 60 + local.index.minute
    regular_mask = (
        (pd.Index(local.index.date) == baseline_day)
        & (minute_of_day >= 9 * 60 + 30)
        & (minute_of_day < close_minutes)
    )
    regular_close = close[regular_mask].dropna()
    if regular_close.empty or float(regular_close.iloc[-1]) <= 0:
        return None
    return _finite(
        (float(current.iloc[-1]) / float(regular_close.iloc[-1]) - 1.0) * 100.0
    )


def _active_breakout_ticker(row: Mapping[str, Any]) -> str | None:
    state = str(
        row.get("breakout_state")
        or row.get("lifecycle_state")
        or row.get("state")
        or ""
    ).upper()
    return _ticker(row.get("ticker")) if state in _ACTIVE_BREAKOUT_STATES else None


def _strength_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, raw in enumerate(payload.get("_focus_rows") or (), start=1):
        if not isinstance(raw, Mapping):
            continue
        ticker = _ticker(raw.get("ticker"))
        if ticker is None:
            continue
        row = dict(raw)
        row["ticker"] = ticker
        # The daily scanner's change is not a live session change. It remains
        # absent unless discovery or completed intraday bars can supply it.
        row["session_change_pct"] = None
        row["_focus_strength_rank"] = rank
        row["_dollar_volume_basis"] = (
            "adv20_completed_sessions"
            if _finite(row.get("avg_dollar_volume_20d")) is not None
            else "unavailable"
        )
        row["_source_status"] = (
            "fallback" if row["_dollar_volume_basis"] != "unavailable" else "unavailable"
        )
        row["_data_source"] = "canonical_strength_daily"
        row["_fallback_data_through"] = row.get("daily_data_through")
        row["_data_through"] = row["_fallback_data_through"]
        rows.append(row)
    return rows


def _discovery_rows(
    payload: Any,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any]]:
    body = _mapping(payload)
    warnings = [str(item)[:200] for item in body.get("warnings") or ()]
    status = str(getattr(body.get("status"), "value", body.get("status") or "unknown"))
    as_of = body.get("as_of")
    payload_volume_leaders = {
        ticker
        for raw in body.get("_focus_volume_leader_tickers") or ()
        if (ticker := _ticker(raw)) is not None
    }
    payload_regular_movers = {
        ticker
        for raw in body.get("_focus_regular_mover_tickers") or ()
        if (ticker := _ticker(raw)) is not None
    }
    rows: list[dict[str, Any]] = []
    for raw in body.get("candidates") or ():
        item = _mapping(raw)
        ticker = _ticker(item.get("ticker"))
        price = _finite(item.get("price"))
        volume = _finite(item.get("provider_volume"))
        if ticker is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                "session_change_pct": _finite(item.get("provider_change_pct")),
                "_coarse_dollar_volume": (
                    price * volume
                    if price is not None and volume is not None and volume >= 0
                    else None
                ),
                "_source_status": status,
                "_data_source": str(item.get("source") or body.get("provider") or "unknown")[:80],
                "_data_through": as_of,
                "_focus_volume_leader": bool(
                    item.get("_focus_volume_leader")
                    or ticker in payload_volume_leaders
                ),
                "_focus_regular_mover": bool(
                    item.get("_focus_regular_mover")
                    or ticker in payload_regular_movers
                ),
            }
        )
    rows.sort(
        key=lambda item: (
            0 if item.get("_focus_volume_leader") else 1,
            -(
                value
                if (value := _finite(item.get("_coarse_dollar_volume"))) is not None
                else -1.0
            ),
            item["ticker"],
        )
    )
    profile = str(body.get("_focus_discovery_profile") or "unknown")
    leader_status = str(body.get("_focus_volume_leader_status") or status)
    volume_leader_tickers = [
        ticker
        for raw in body.get("_focus_volume_leader_tickers") or ()
        if (ticker := _ticker(raw)) is not None
    ]
    if not volume_leader_tickers:
        volume_leader_tickers = [
            str(row["ticker"]) for row in rows if row.get("_focus_volume_leader")
        ]
    capability_supported = bool(
        body.get("_focus_dollar_volume_leaders_supported")
        and DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS.value in profile
        and leader_status == "active"
    )
    return rows, warnings, {
        "provider": str(body.get("provider") or "unknown")[:80],
        "status": status,
        "profile": profile,
        "capability_supported": capability_supported,
        "coarse_candidate_count": len(rows),
        "volume_leader_candidate_count": len(volume_leader_tickers),
        "volume_leader_tickers": volume_leader_tickers,
        "regular_mover_tickers": [
            ticker
            for raw in body.get("_focus_regular_mover_tickers") or ()
            if (ticker := _ticker(raw)) is not None
        ],
        "as_of": (
            value.isoformat() if (value := _as_utc(as_of)) is not None else None
        ),
        "cache_key": str(body.get("cache_key") or "")[:128] or None,
    }


def _merge_candidate_rows(
    *,
    strength_rows: Sequence[Mapping[str, Any]],
    discovery_rows: Sequence[Mapping[str, Any]],
    breakout_rows: Sequence[Mapping[str, Any]],
    previous: Sequence[FocusSymbol],
    settings: FocusContextSettings,
) -> tuple[list[dict[str, Any]], list[str], list[str]]:
    by_ticker: dict[str, dict[str, Any]] = {}
    strength_order: list[str] = []
    discovery_order: list[str] = []
    for raw in strength_rows:
        ticker = _ticker(raw.get("ticker"))
        if ticker is None:
            continue
        by_ticker[ticker] = dict(raw)
        strength_order.append(ticker)
    for raw in discovery_rows:
        ticker = _ticker(raw.get("ticker"))
        if ticker is None:
            continue
        by_ticker.setdefault(ticker, {"ticker": ticker}).update(dict(raw))
        discovery_order.append(ticker)
    breakout_order: list[str] = []
    for raw in breakout_rows:
        ticker = _active_breakout_ticker(raw)
        if ticker is None:
            continue
        merged = by_ticker.setdefault(ticker, {"ticker": ticker})
        for key, value in raw.items():
            if value is not None and merged.get(key) is None:
                merged[key] = value
        breakout_order.append(ticker)
    for symbol in previous:
        by_ticker.setdefault(
            symbol.ticker,
            {
                "ticker": symbol.ticker,
                "sector_id": symbol.sector_id,
                "_source_status": "fallback",
                "_dollar_volume_basis": "unavailable",
            },
        )
    forced = list(
        dict.fromkeys(
            [
                *settings.priority_symbols,
                *breakout_order,
                *settings.index_constituent_symbols,
            ]
        )
    )
    final_order = [
        *forced,
        *discovery_order,
        *strength_order,
        *(symbol.ticker for symbol in previous),
    ]
    selected: list[str] = []
    for raw in final_order:
        ticker = _ticker(raw)
        if ticker is None or ticker in selected:
            continue
        by_ticker.setdefault(ticker, {"ticker": ticker})
        selected.append(ticker)
    enrichment_order = [
        *discovery_order,
        *forced,
        *strength_order,
        *(symbol.ticker for symbol in previous),
    ]
    enrichment_tickers: list[str] = []
    for raw in enrichment_order:
        ticker = _ticker(raw)
        if ticker is None or ticker in enrichment_tickers:
            continue
        enrichment_tickers.append(ticker)
        if len(enrichment_tickers) >= settings.producer_candidate_limit:
            break
    warnings: list[str] = []
    if len(discovery_order) > settings.producer_candidate_limit:
        warnings.append("focus_intraday_candidate_pool_bounded")
    if any(ticker not in enrichment_tickers for ticker in forced):
        warnings.append("focus_forced_symbols_using_fallback")
    return (
        [by_ticker[ticker] for ticker in selected],
        warnings,
        enrichment_tickers,
    )


async def _default_strength_loader(settings: FocusContextSettings) -> Mapping[str, Any]:
    from app.services.strength.scanner import scan_strength

    return await scan_strength(
        timeframe="all",
        profile="balanced",
        top=max(settings.producer_candidate_limit, settings.strength_count),
        include_options=False,
        _include_focus_rows=True,
        _publish_focus=False,
    )


async def _default_discovery_loader(snapshot: MarketClockSnapshot) -> Any:
    if snapshot.session not in {MarketSession.PREMARKET, MarketSession.REGULAR}:
        return {
            "provider": "tradingview",
            "status": "skipped",
            "as_of": snapshot.as_of,
            "warnings": ["discovery_session_not_supported"],
            "candidates": [],
        }
    provider = TradingViewDiscoveryProvider()
    try:
        if snapshot.session is MarketSession.PREMARKET:
            profile = DiscoveryProfile.PREMARKET_GAPPERS
            result = await provider.scan(
                session=snapshot.session,
                as_of=snapshot.as_of,
                profile=profile,
            )
            body = result.model_dump(mode="python")
            body["_focus_discovery_profile"] = profile.value
            body["_focus_dollar_volume_leaders_supported"] = False
            return body

        leaders, movers = await asyncio.gather(
            provider.scan(
                session=snapshot.session,
                as_of=snapshot.as_of,
                profile=DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS,
            ),
            provider.scan(
                session=snapshot.session,
                as_of=snapshot.as_of,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            ),
        )
        leader_body = leaders.model_dump(mode="python")
        mover_body = movers.model_dump(mode="python")
        merged: dict[str, dict[str, Any]] = {}
        leader_tickers: list[str] = []
        mover_tickers: list[str] = []
        for source, marker, target in (
            (leader_body, "_focus_volume_leader", leader_tickers),
            (mover_body, "_focus_regular_mover", mover_tickers),
        ):
            for raw in source.get("candidates") or ():
                item = _mapping(raw)
                ticker = _ticker(item.get("ticker"))
                if ticker is None:
                    continue
                if ticker not in target:
                    target.append(ticker)
                current = merged.setdefault(ticker, item)
                for key, value in item.items():
                    if current.get(key) is None and value is not None:
                        current[key] = value
                current[marker] = True
        leader_status = str(
            getattr(leaders.status, "value", leaders.status)
        )
        mover_status = str(getattr(movers.status, "value", movers.status))
        if leader_status == mover_status == "active":
            status = "active"
        elif merged and {leader_status, mover_status} & {
            "active",
            "degraded",
            "stale",
        }:
            status = "degraded"
        else:
            status = "unavailable"
        cache_material = "|".join(
            str(value or "")
            for value in (leaders.cache_key, movers.cache_key)
        ).encode()
        return {
            "provider": "tradingview",
            "status": status,
            "as_of": snapshot.as_of,
            "warnings": list(
                dict.fromkeys([*leaders.warnings, *movers.warnings])
            )[:64],
            "candidates": list(merged.values()),
            "cache_key": hashlib.sha256(cache_material).hexdigest(),
            "_focus_discovery_profile": (
                f"{DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS.value}+"
                f"{DiscoveryProfile.REGULAR_MOVERS.value}"
            ),
            "_focus_dollar_volume_leaders_supported": leader_status == "active",
            "_focus_volume_leader_status": leader_status,
            "_focus_regular_mover_status": mover_status,
            "_focus_volume_leader_tickers": leader_tickers,
            "_focus_regular_mover_tickers": mover_tickers,
        }
    finally:
        await provider.aclose()


async def _default_intraday_loader(
    tickers: Sequence[str],
    cutoff: TemporalCutoff,
) -> Mapping[str, Any]:
    return await YahooPriceDataAdapter().intraday(
        tickers,
        cutoff=cutoff,
        interval="5m",
    )


def _stale_draft(
    current: FocusContextResponse,
    *,
    observed: datetime,
    warning: str,
) -> FocusContextDraft | None:
    if not any(symbol.data_status == "active" for symbol in current.symbols):
        return None
    symbols: list[FocusSymbol] = []
    for symbol in current.symbols:
        reasons = list(symbol.universe_reasons)
        if "stale_retained" not in reasons:
            reasons = [*reasons[:11], "stale_retained"]
        symbols.append(
            symbol.model_copy(
                update={
                    "data_status": "stale",
                    "session_change_pct": None,
                    "rvol_time_of_day": None,
                    "data_quality": None,
                    "source_status": "stale",
                    "universe_reasons": reasons,
                }
            )
        )
    return FocusContextDraft(
        schema_version=current.schema_version,
        schema_sha256=current.schema_sha256,
        as_of=max(observed, current.as_of),
        data_through=current.data_through,
        market_session=_market_session(observed),
        universe_version=current.universe_version,
        symbols=symbols,
        major_market_symbols=current.major_market_symbols,
        warnings=list(
            dict.fromkeys([*current.warnings, "focus_snapshot_stale", warning])
        )[:50],
    )


class FocusContextProducer:
    def __init__(
        self,
        *,
        settings: FocusContextSettings | None = None,
        repository: CatalystRepository | None = None,
        clock: MarketClock | None = None,
        strength_loader: StrengthLoader | None = None,
        discovery_loader: DiscoveryLoader | None = None,
        intraday_loader: IntradayLoader | None = None,
        breakout_loader: BreakoutLoader = _breakout_rows,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        owner_id: str | None = None,
    ) -> None:
        self.settings = settings or get_focus_context_settings()
        self.repository = repository or CatalystRepository(
            self.settings.cache_db_path
        )
        self.clock = clock or MarketClock()
        self.strength_loader = strength_loader or (
            lambda: _default_strength_loader(self.settings)
        )
        self.discovery_loader = discovery_loader or _default_discovery_loader
        self.intraday_loader = intraday_loader or _default_intraday_loader
        self.breakout_loader = breakout_loader
        self.sleeper = sleeper
        self.owner_id = owner_id or (
            f"{FOCUS_PRODUCER_WORKER_PREFIX}{uuid.uuid4().hex}"
        )
        self._heartbeat_details: dict[str, Any] = {}

    def _heartbeat(self, status: str, details: Mapping[str, Any]) -> None:
        body = dict(details)
        if "revision" in body:
            self._heartbeat_details = body
        else:
            self._heartbeat_details.update(body)
        self.repository.heartbeat(
            self.owner_id,
            status,
            self._heartbeat_details,
            now=self.clock.now(),
        )

    async def _load_strength_payload(
        self,
        observed: datetime,
        fencing_token: int,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        trading_day = _latest_completed_trading_day(
            observed,
            settlement_delay_seconds=(
                self.settings.daily_strength_settlement_delay_seconds
            ),
        )
        cache_identity = _daily_strength_cache_identity()
        cache_version = cache_identity["cache_version"]
        cached = self.repository.daily_strength_snapshot(
            trading_day=trading_day,
            cache_version=cache_version,
            strength_feature_version=cache_identity["strength_feature_version"],
            strength_score_version=cache_identity["strength_score_version"],
            normalization_version=cache_identity["normalization_version"],
            range_persistence_version=cache_identity["range_persistence_version"],
            now=observed,
        )
        cached_coverage = (
            float(cached["coverage"])
            if cached is not None
            else None
        )
        if (
            cached is not None
            and cached_coverage is not None
            and cached_coverage >= self.settings.daily_strength_min_coverage
        ):
            payload = dict(cached["payload"])
            return payload, {
                "source": "persistent_cache",
                "status": cached["status"],
                "trading_day": cached["trading_day"],
                "cache_version": cache_version,
                "cached_at": cached["cached_at"],
                "expires_at": cached["expires_at"],
                "payload_hash": cached["payload_hash"],
                "coverage": cached["coverage"],
                "minimum_coverage": self.settings.daily_strength_min_coverage,
                "expected_symbol_count": payload.get("expected_symbol_count"),
                "available_symbol_count": payload.get("available_symbol_count"),
                "completed_session_symbol_count": payload.get(
                    "completed_session_symbol_count"
                ),
                "audit_versions": {
                    key: cached[key]
                    for key in (
                        "strength_feature_version",
                        "strength_score_version",
                        "normalization_version",
                        "range_persistence_version",
                    )
                },
            }

        loaded = dict(await self.strength_loader())
        status = _daily_strength_status(loaded)
        if status == "unavailable":
            raise RuntimeError("focus_strength_rows_unavailable")
        payload = _daily_strength_payload(
            loaded,
            completed_session_date=trading_day,
        )
        if float(payload["coverage"]) < self.settings.daily_strength_min_coverage:
            # Partial or lagging provider data remains usable as an explicitly
            # degraded short-lived input, but never becomes an all-day cache.
            status = "degraded"
        cached_at = self.clock.now()
        self.repository.cache_daily_strength_snapshot(
            trading_day=trading_day,
            cache_version=cache_version,
            universe_version=str(payload.get("universe_version") or "unknown"),
            strength_feature_version=cache_identity["strength_feature_version"],
            strength_score_version=cache_identity["strength_score_version"],
            normalization_version=cache_identity["normalization_version"],
            range_persistence_version=cache_identity["range_persistence_version"],
            coverage=float(payload["coverage"]),
            status=status,
            payload=payload,
            data_through=_daily_strength_data_through(payload),
            degraded_ttl_seconds=(
                self.settings.daily_strength_degraded_ttl_seconds
            ),
            now=cached_at,
            lock_name=LOCK_NAME,
            owner_id=self.owner_id,
            fencing_token=fencing_token,
        )
        return payload, {
            "source": "fresh_scan",
            "status": status,
            "trading_day": trading_day.isoformat(),
            "cache_version": cache_version,
            "cached_at": cached_at.isoformat(),
            "expires_at": (
                (
                    cached_at
                    + timedelta(
                        seconds=self.settings.daily_strength_degraded_ttl_seconds
                    )
                ).isoformat()
                if status == "degraded"
                else None
            ),
            "payload_hash": hashlib.sha256(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest(),
            "coverage": payload["coverage"],
            "minimum_coverage": self.settings.daily_strength_min_coverage,
            "expected_symbol_count": payload["expected_symbol_count"],
            "available_symbol_count": payload["available_symbol_count"],
            "completed_session_symbol_count": payload[
                "completed_session_symbol_count"
            ],
            "audit_versions": {
                key: cache_identity[key]
                for key in (
                    "strength_feature_version",
                    "strength_score_version",
                    "normalization_version",
                    "range_persistence_version",
                )
            },
        }

    async def _prepare(
        self,
        fencing_token: int,
    ) -> tuple[FocusContextDraft, dict[str, Any]]:
        observed = self.clock.now()
        market = self.clock.snapshot(observed)
        current = self.repository.current_focus_context()
        strength_payload, strength_cache = await self._load_strength_payload(
            observed,
            fencing_token,
        )
        strength_rows = _strength_rows(strength_payload)
        if not strength_rows:
            raise RuntimeError("focus_strength_rows_unavailable")

        warnings: list[str] = []
        discovery_details: dict[str, Any] = {
            "provider": "unavailable",
            "status": "unavailable",
            "profile": "unknown",
            "capability_supported": False,
            "coarse_candidate_count": 0,
            "as_of": None,
            "cache_key": None,
        }
        try:
            discovery_payload = await self.discovery_loader(market)
            (
                discovery_rows,
                discovery_warnings,
                discovery_details,
            ) = _discovery_rows(discovery_payload)
            warnings.extend(discovery_warnings)
        except Exception:
            discovery_rows = []
            warnings.append("focus_discovery_unavailable")

        breakout_rows = [
            dict(item)
            for item in self.breakout_loader()
            if isinstance(item, Mapping)
        ]
        rows, pool_warnings, enrichment_tickers = _merge_candidate_rows(
            strength_rows=strength_rows,
            discovery_rows=discovery_rows,
            breakout_rows=breakout_rows,
            previous=current.symbols if current else (),
            settings=self.settings,
        )
        warnings.extend(pool_warnings)
        row_by_ticker = {str(row["ticker"]): row for row in rows}
        exact_count = 0
        exact_tickers: set[str] = set()
        intraday_failed = 0
        non_typical_dollar_volume = 0
        rvol_missing = 0
        session_change_missing = 0
        cutoff = _cutoff(market)
        if market.session is not MarketSession.CLOSED and rows:
            try:
                snapshots = await self.intraday_loader(enrichment_tickers, cutoff)
            except Exception:
                snapshots = {}
                warnings.append("focus_intraday_batch_unavailable")
            for ticker in enrichment_tickers:
                row = row_by_ticker[ticker]
                snapshot = snapshots.get(ticker)
                frame = getattr(snapshot, "frame", None)
                if snapshot is None or not isinstance(frame, pd.DataFrame):
                    intraday_failed += 1
                    continue
                try:
                    feature = compute_feature_snapshot(
                        daily=pd.DataFrame(),
                        intraday=frame,
                        cutoff=cutoff,
                    )
                except Exception:
                    intraday_failed += 1
                    continue
                dollar_volume = _finite(feature.get("cumulative_dollar_volume"))
                if dollar_volume is None:
                    intraday_failed += 1
                    continue
                if (
                    feature.get("cumulative_dollar_volume_calculation_method")
                    != "typical_price_volume"
                ):
                    intraday_failed += 1
                    non_typical_dollar_volume += 1
                    continue
                exact_count += 1
                exact_tickers.add(ticker)
                row["cumulative_dollar_volume"] = dollar_volume
                row["rvol_time_of_day"] = _finite(feature.get("rvol_time_of_day"))
                regular_change = _intraday_session_change_pct(
                    frame,
                    as_of=observed,
                    session=market.session,
                )
                row["session_change_pct"] = (
                    _first_available(
                        regular_change,
                        _finite(row.get("session_change_pct")),
                    )
                    if market.session
                    in {MarketSession.PREMARKET, MarketSession.POSTMARKET}
                    else _first_available(
                        _finite(feature.get("session_change_pct")),
                        regular_change,
                        _finite(row.get("session_change_pct")),
                    )
                )
                row["_dollar_volume_basis"] = "intraday_completed_bars"
                row["_data_through"] = (
                    getattr(snapshot, "data_through", None)
                    or feature.get("data_through")
                )
                row["_data_source"] = str(
                    getattr(snapshot, "source", None) or "Yahoo/yfinance"
                )[:80]
                snapshot_warnings = list(getattr(snapshot, "warnings", ()) or ())
                quality = _finite(getattr(snapshot, "quality", None))
                rvol_quality = _finite(feature.get("quality"))
                if row["rvol_time_of_day"] is not None and rvol_quality is not None:
                    quality = min(quality if quality is not None else 1.0, rvol_quality)
                degraded = bool(snapshot_warnings)
                if (
                    market.session is MarketSession.REGULAR
                    and row["rvol_time_of_day"] is None
                ):
                    rvol_missing += 1
                    quality = min(quality if quality is not None else 1.0, 0.6)
                    degraded = True
                if (
                    market.session is MarketSession.REGULAR
                    and row["session_change_pct"] is None
                ):
                    session_change_missing += 1
                    quality = min(quality if quality is not None else 1.0, 0.7)
                    degraded = True
                row["_source_status"] = "degraded" if degraded else "active"
                row["data_quality"] = quality

        if exact_count == 0 and market.session is MarketSession.REGULAR:
            warnings.append("focus_intraday_unavailable_adv20_fallback")
        elif exact_count < len(enrichment_tickers) and market.session is MarketSession.REGULAR:
            warnings.append("focus_intraday_partial_adv20_fallback")
        if intraday_failed:
            warnings.append("focus_intraday_symbol_failures")
        if non_typical_dollar_volume:
            warnings.append("focus_intraday_non_typical_price_rejected")
        if rvol_missing:
            warnings.append("focus_rvol_time_of_day_partial")
        if session_change_missing:
            warnings.append("focus_session_change_partial")

        for row in rows:
            if row.get("_dollar_volume_basis") == "intraday_completed_bars":
                continue
            row["_dollar_volume_basis"] = (
                "adv20_completed_sessions"
                if _finite(row.get("avg_dollar_volume_20d")) is not None
                else "unavailable"
            )
            row["_source_status"] = (
                "fallback"
                if row["_dollar_volume_basis"] != "unavailable"
                else "unavailable"
            )
            row["_data_source"] = (
                "canonical_strength_daily"
                if row["_dollar_volume_basis"] != "unavailable"
                else "unavailable"
            )
            row["_data_through"] = (
                row.get("_fallback_data_through")
                or row.get("daily_data_through")
            )
            row["data_quality"] = (
                _fallback_quality(row.get("data_quality"))
                if row["_dollar_volume_basis"] != "unavailable"
                else None
            )
            row["rvol_time_of_day"] = None

        canonical = [
            str(row["ticker"])
            for row in strength_rows
            if bool(row.get("universe_member"))
        ]
        volume_leader_tickers = list(
            dict.fromkeys(discovery_details.get("volume_leader_tickers") or ())
        )
        volume_leader_set = set(volume_leader_tickers)
        required_market_leaders = [
            ticker
            for ticker in enrichment_tickers
            if ticker in volume_leader_set
        ]
        expected_leader_window_count = min(
            self.settings.producer_candidate_limit,
            len(volume_leader_tickers),
        )
        market_dollar_volume_scope = bool(
            discovery_details["capability_supported"]
            and len(volume_leader_tickers) >= self.settings.dollar_volume_count
            and len(required_market_leaders) == expected_leader_window_count
            and all(ticker in exact_tickers for ticker in required_market_leaders)
        )
        draft = build_focus_context(
            settings=self.settings,
            strength_rows=rows,
            breakout_rows=breakout_rows,
            canonical_symbols=canonical,
            previous_symbols=(
                [symbol.ticker for symbol in current.symbols] if current else ()
            ),
            previous_context=current.symbols if current else (),
            as_of=observed,
            data_through=None,
            market_session=_market_session_name(market),
            universe_version=str(
                strength_payload.get("universe_version") or "unknown"
            )[:200],
            dollar_volume_scope=(
                "market" if market_dollar_volume_scope else "candidate"
            ),
        )
        final_data_through_values = [
            value
            for symbol in draft.symbols
            if (value := _as_utc(symbol.data_through)) is not None
        ]
        data_through = (
            min(final_data_through_values) if final_data_through_values else None
        )
        draft = draft.model_copy(update={"data_through": data_through})
        if not market_dollar_volume_scope:
            warnings.append("focus_market_dollar_volume_capability_insufficient")
            if (
                len(required_market_leaders) != expected_leader_window_count
                or any(
                    ticker not in exact_tickers
                    for ticker in required_market_leaders
                )
            ):
                warnings.append("focus_market_leader_exact_coverage_incomplete")
        draft = draft.model_copy(
            update={
                "warnings": list(dict.fromkeys([*draft.warnings, *warnings]))[:50]
            }
        )
        coverage = _published_coverage(
            draft.symbols,
            market_volume_rank_scope=(
                "market" if market_dollar_volume_scope else "candidate"
            ),
        )
        details = {
            "market_session": draft.market_session,
            "candidate_count": len(rows),
            "candidate_semantics": (
                f"market_dollar_volume_top{self.settings.dollar_volume_count}"
                if market_dollar_volume_scope
                else f"candidate_dollar_volume_top{self.settings.dollar_volume_count}"
            ),
            "discovery_provider": discovery_details,
            "daily_strength_cache": strength_cache,
            "intraday_candidate_count": len(enrichment_tickers),
            "intraday_enriched_count": exact_count,
            "required_market_leader_count": len(required_market_leaders),
            "expected_market_leader_window_count": expected_leader_window_count,
            "required_market_leader_exact_count": sum(
                ticker in exact_tickers for ticker in required_market_leaders
            ),
            "intraday_failed_count": intraday_failed,
            "non_typical_dollar_volume_count": non_typical_dollar_volume,
            **coverage,
            "warnings": draft.warnings,
        }
        return draft, details

    async def _prepare_with_lease(
        self,
        fencing_token: int,
    ) -> tuple[FocusContextDraft, dict[str, Any]]:
        task = asyncio.create_task(self._prepare(fencing_token))
        while True:
            done, _ = await asyncio.wait(
                {task},
                timeout=self.settings.producer_heartbeat_seconds,
            )
            if task in done:
                return await task
            if not self.repository.renew_worker_lock(
                LOCK_NAME,
                self.owner_id,
                fencing_token,
                lease_seconds=self.settings.producer_lease_seconds,
                now=self.clock.now(),
            ):
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise RuntimeError("focus_producer_lease_lost")
            self._heartbeat("running", {"stage": "preparing"})

    async def run_once(self, *, fencing_token: int | None = None) -> dict[str, Any]:
        if not self.settings.producer_enabled:
            return {"status": "disabled", "enabled": False}
        owned_lease = fencing_token is None
        token = fencing_token
        if token is None:
            token = self.repository.acquire_worker_lock(
                LOCK_NAME,
                self.owner_id,
                lease_seconds=self.settings.producer_lease_seconds,
                now=self.clock.now(),
            )
        if token is None:
            return {"status": "locked", "enabled": True}
        self._heartbeat(
            "running",
            {
                "stage": "starting",
                "refresh_started_at": utc_iso(self.clock.now()),
            },
        )
        try:
            draft, details = await self._prepare_with_lease(token)
            response = self.repository.publish_focus_context(
                draft,
                now=self.clock.now(),
                lock_name=LOCK_NAME,
                owner_id=self.owner_id,
                fencing_token=token,
            )
            try:
                retention = self.repository.prune_focus_retention(
                    snapshot_days=self.settings.snapshot_retention_days,
                    snapshot_full_resolution_days=(
                        self.settings.snapshot_full_resolution_days
                    ),
                    snapshot_daily_rollup_enabled=(
                        self.settings.snapshot_daily_rollup_enabled
                    ),
                    daily_strength_days=(
                        self.settings.daily_strength_retention_days
                    ),
                    now=self.clock.now(),
                )
            except Exception as error:
                logger.warning(
                    "focus_retention_failed error_type=%s",
                    type(error).__name__,
                )
                retention = {"status": "degraded", "error_code": "retention_failed"}
            result = {
                "status": "completed",
                "enabled": True,
                "revision": response.revision,
                "retention": retention,
                **details,
            }
            self._heartbeat("idle", result)
            return result
        except Exception as error:
            error_code = (
                error.code
                if isinstance(error, CatalystRepositoryError)
                else type(error).__name__
            )
            stale_revision = None
            current = self.repository.current_focus_context()
            if current is not None and self.repository.renew_worker_lock(
                LOCK_NAME,
                self.owner_id,
                token,
                lease_seconds=self.settings.producer_lease_seconds,
                now=self.clock.now(),
            ):
                stale = _stale_draft(
                    current,
                    observed=self.clock.now(),
                    warning=(
                        error_code
                        if isinstance(error, CatalystRepositoryError)
                        else "focus_producer_failed"
                    ),
                )
                if stale is not None:
                    stale_revision = self.repository.publish_focus_context(
                        stale,
                        now=self.clock.now(),
                        lock_name=LOCK_NAME,
                        owner_id=self.owner_id,
                        fencing_token=token,
                    ).revision
                published_symbols = stale.symbols if stale is not None else current.symbols
                coverage = _published_coverage(
                    published_symbols,
                    market_volume_rank_scope=str(
                        self._heartbeat_details.get(
                            "market_volume_rank_scope",
                            "candidate",
                        )
                    ),
                )
                result = {
                    "status": "degraded",
                    "enabled": True,
                    "revision": stale_revision or current.revision,
                    "error_code": error_code,
                    "stale_revision": stale_revision,
                    **coverage,
                    "warnings": (
                        stale.warnings if stale is not None else current.warnings
                    ),
                }
                self._heartbeat("degraded", result)
                return result
            return {
                "status": "unavailable",
                "enabled": True,
                "error_code": error_code,
                "stale_revision": None,
            }
        finally:
            if owned_lease:
                self.repository.release_worker_lock(
                    LOCK_NAME,
                    self.owner_id,
                    token,
                )

    async def _wait_until(
        self,
        target: datetime,
        *,
        stop: asyncio.Event,
        fencing_token: int,
    ) -> bool:
        while not stop.is_set():
            remaining = (target - self.clock.now()).total_seconds()
            if remaining <= 0:
                return True
            await self.sleeper(
                min(float(self.settings.producer_heartbeat_seconds), remaining)
            )
            if stop.is_set():
                return False
            if not self.repository.renew_worker_lock(
                LOCK_NAME,
                self.owner_id,
                fencing_token,
                lease_seconds=self.settings.producer_lease_seconds,
                now=self.clock.now(),
            ):
                return False
            self._heartbeat(
                "idle",
                {"next_run_at": target.isoformat(), "stage": "waiting"},
            )
        return False

    async def run_forever(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            token = self.repository.acquire_worker_lock(
                LOCK_NAME,
                self.owner_id,
                lease_seconds=self.settings.producer_lease_seconds,
                now=self.clock.now(),
            )
            if token is None:
                await self.sleeper(5.0)
                continue
            try:
                while not stop.is_set():
                    await self.run_once(fencing_token=token)
                    target = next_refresh_at(
                        self.clock.now(),
                        interval_seconds=self.settings.producer_interval_seconds,
                    )
                    if not await self._wait_until(
                        target,
                        stop=stop,
                        fencing_token=token,
                    ):
                        break
            finally:
                self.repository.release_worker_lock(
                    LOCK_NAME,
                    self.owner_id,
                    token,
                )


def health_payload(
    settings: FocusContextSettings,
    *,
    repository: CatalystRepository | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return focus-only health; backend readiness never depends on it."""

    if not settings.producer_enabled:
        return {
            "healthy": True,
            "status": "disabled",
            "enabled": False,
            "ready_dependency": False,
        }
    contract_valid = verify_focus_contract()
    local_repository = repository or CatalystRepository(
        settings.cache_db_path,
        read_only=True,
    )
    try:
        database = local_repository.focus_producer_health(
            heartbeat_ttl_seconds=settings.producer_health_stale_seconds,
            snapshot_ttl_seconds=(
                settings.refresh_seconds
                + settings.producer_snapshot_grace_seconds
            ),
            snapshot_refresh_seconds=settings.refresh_seconds,
            startup_grace_seconds=settings.producer_snapshot_grace_seconds,
            now=now,
        )
    except Exception as error:
        if not isinstance(error, (OSError, ValueError, sqlite3.Error)):
            logger.warning(
                "focus_producer_health_failed error_type=%s",
                type(error).__name__,
            )
        database = {
            "healthy": False,
            "status": "unavailable",
            "error_code": "focus_cache_unavailable",
        }
    healthy = bool(contract_valid and database.get("healthy"))
    production_status = str(database.get("status") or "unavailable")
    public_status = (
        production_status
        if healthy and production_status in {"degraded", "unavailable"}
        else "ok" if healthy else "unhealthy"
    )
    return {
        "healthy": healthy,
        "status": public_status,
        "production_status": production_status,
        "enabled": True,
        "ready_dependency": False,
        "contract": {"valid": contract_valid},
        "database": database,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Option Pro focus-context producer")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="produce one focus snapshot")
    mode.add_argument(
        "--healthcheck",
        action="store_true",
        help="check only the independent focus producer",
    )
    return parser


def _install_stop_handlers(stop: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, stop.set)
        except (NotImplementedError, RuntimeError):
            pass


async def _wait_disabled(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            continue


async def _async_main(args: argparse.Namespace) -> int:
    try:
        settings = get_focus_context_settings()
    except Exception:
        print(
            json.dumps(
                {
                    "healthy": False,
                    "status": "invalid_configuration",
                    "error_code": "configuration_error",
                    "ready_dependency": False,
                },
                sort_keys=True,
            )
        )
        return 1
    if args.healthcheck:
        payload = health_payload(settings)
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["healthy"] else 1
    if not settings.producer_enabled:
        if args.once:
            print(json.dumps({"status": "disabled", "enabled": False}, sort_keys=True))
            return 0
        stop = asyncio.Event()
        _install_stop_handlers(stop)
        await _wait_disabled(stop)
        return 0

    repository = CatalystRepository(settings.cache_db_path)
    repository.initialize()
    producer = FocusContextProducer(settings=settings, repository=repository)
    if args.once:
        payload = await producer.run_once()
        print(json.dumps(payload, sort_keys=True))
        return 0 if payload["status"] in {"completed", "degraded", "locked"} else 1
    stop = asyncio.Event()
    _install_stop_handlers(stop)
    await producer.run_forever(stop)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO)
    return asyncio.run(_async_main(_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
