"""Independent producer for the signed MacroLens focus-context snapshot."""

from __future__ import annotations

import argparse
import asyncio
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

from .focus_config import FocusContextSettings, get_focus_context_settings
from .focus_models import FocusContextDraft, FocusContextResponse, FocusSymbol
from .focus_publisher import _breakout_rows, _market_session, verify_focus_contract
from .focus_universe import build_focus_context
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
    current = close[pd.Index(local.index.date) == current_day].dropna()
    earlier_days = sorted({item for item in local.index.date if item < current_day})
    if current.empty or not earlier_days:
        return None
    previous = close[pd.Index(local.index.date) == earlier_days[-1]].dropna()
    if previous.empty or float(previous.iloc[-1]) <= 0:
        return None
    return _finite((float(current.iloc[-1]) / float(previous.iloc[-1]) - 1.0) * 100.0)


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
    universe_as_of = payload.get("universe_as_of") or payload.get("as_of")
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
        row["_fallback_data_through"] = (
            row.get("daily_data_through")
            or row.get("universe_as_of")
            or universe_as_of
        )
        row["_data_through"] = row["_fallback_data_through"]
        rows.append(row)
    return rows


def _discovery_rows(payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    body = _mapping(payload)
    warnings = [str(item)[:200] for item in body.get("warnings") or ()]
    status = str(getattr(body.get("status"), "value", body.get("status") or "unknown"))
    as_of = body.get("as_of")
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
            }
        )
    rows.sort(
        key=lambda item: (
            -(
                value
                if (value := _finite(item.get("_coarse_dollar_volume"))) is not None
                else -1.0
            ),
            item["ticker"],
        )
    )
    return rows, warnings


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
        profile = (
            DiscoveryProfile.PREMARKET_GAPPERS
            if snapshot.session is MarketSession.PREMARKET
            else DiscoveryProfile.REGULAR_MOVERS
        )
        return await provider.scan(
            session=snapshot.session,
            as_of=snapshot.as_of,
            profile=profile,
        )
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

    def _heartbeat(self, status: str, details: Mapping[str, Any]) -> None:
        self.repository.heartbeat(
            self.owner_id,
            status,
            dict(details),
            now=self.clock.now(),
        )

    async def _prepare(self) -> tuple[FocusContextDraft, dict[str, Any]]:
        observed = self.clock.now()
        market = self.clock.snapshot(observed)
        current = self.repository.current_focus_context()
        strength_payload = dict(await self.strength_loader())
        strength_rows = _strength_rows(strength_payload)
        if not strength_rows:
            raise RuntimeError("focus_strength_rows_unavailable")

        warnings: list[str] = []
        try:
            discovery_payload = await self.discovery_loader(market)
            discovery_rows, discovery_warnings = _discovery_rows(discovery_payload)
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
                row["cumulative_dollar_volume"] = dollar_volume
                row["rvol_time_of_day"] = _finite(feature.get("rvol_time_of_day"))
                row["session_change_pct"] = _first_available(
                    _finite(feature.get("session_change_pct")),
                    _intraday_session_change_pct(frame, as_of=observed),
                    _finite(row.get("session_change_pct")),
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
                or row.get("universe_as_of")
                or strength_payload.get("universe_as_of")
                or strength_payload.get("as_of")
            )
            row["data_quality"] = (
                _fallback_quality(row.get("data_quality"))
                if row["_dollar_volume_basis"] != "unavailable"
                else None
            )
            row["rvol_time_of_day"] = None

        data_through_values = [
            value
            for row in rows
            if (value := _as_utc(row.get("_data_through"))) is not None
        ]
        data_through = min(data_through_values) if data_through_values else None
        canonical = [
            str(row["ticker"])
            for row in strength_rows
            if bool(row.get("universe_member"))
        ]
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
            data_through=data_through,
            market_session=_market_session_name(market),
            universe_version=str(
                strength_payload.get("universe_version") or "unknown"
            )[:200],
        )
        draft = draft.model_copy(
            update={
                "warnings": list(dict.fromkeys([*draft.warnings, *warnings]))[:50]
            }
        )
        basis_counts = Counter(
            str(row.get("_dollar_volume_basis") or "unavailable") for row in rows
        )
        status_counts = Counter(
            str(row.get("_source_status") or "unavailable") for row in rows
        )
        symbol_sources = [
            {
                "ticker": str(row["ticker"]),
                "dollar_volume_basis": str(
                    row.get("_dollar_volume_basis") or "unavailable"
                ),
                "dollar_volume": (
                    _finite(row.get("cumulative_dollar_volume"))
                    if row.get("_dollar_volume_basis")
                    == "intraday_completed_bars"
                    else _finite(row.get("avg_dollar_volume_20d"))
                ),
                "data_through": (
                    value.isoformat()
                    if (value := _as_utc(row.get("_data_through"))) is not None
                    else None
                ),
                "source_status": str(
                    row.get("_source_status") or "unavailable"
                ),
                "data_source": str(row.get("_data_source") or "unavailable")[:80],
            }
            for row in rows
        ]
        details = {
            "market_session": draft.market_session,
            "candidate_count": len(rows),
            "intraday_candidate_count": len(enrichment_tickers),
            "published_symbol_count": len(draft.symbols),
            "intraday_enriched_count": exact_count,
            "intraday_failed_count": intraday_failed,
            "non_typical_dollar_volume_count": non_typical_dollar_volume,
            "rvol_unavailable_count": rvol_missing,
            "session_change_unavailable_count": session_change_missing,
            "dollar_volume_basis": dict(sorted(basis_counts.items())),
            "source_status": dict(sorted(status_counts.items())),
            "symbol_sources": symbol_sources,
            "data_through": data_through.isoformat() if data_through else None,
            "warnings": draft.warnings,
        }
        return draft, details

    async def _prepare_with_lease(
        self,
        fencing_token: int,
    ) -> tuple[FocusContextDraft, dict[str, Any]]:
        task = asyncio.create_task(self._prepare())
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
        self._heartbeat("running", {"stage": "starting"})
        try:
            draft, details = await self._prepare_with_lease(token)
            response = self.repository.publish_focus_context(
                draft,
                now=self.clock.now(),
                lock_name=LOCK_NAME,
                owner_id=self.owner_id,
                fencing_token=token,
            )
            result = {
                "status": "completed",
                "enabled": True,
                "revision": response.revision,
                **details,
            }
            self._heartbeat("idle", result)
            return result
        except Exception as error:
            error_code = type(error).__name__
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
                    warning="focus_producer_failed",
                )
                if stale is not None:
                    stale_revision = self.repository.publish_focus_context(
                        stale,
                        now=self.clock.now(),
                        lock_name=LOCK_NAME,
                        owner_id=self.owner_id,
                        fencing_token=token,
                    ).revision
                result = {
                    "status": "degraded",
                    "enabled": True,
                    "error_code": error_code,
                    "stale_revision": stale_revision,
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
