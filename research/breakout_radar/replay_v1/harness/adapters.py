"""Injected adapters: a settable clock, frozen-data prices, memoized strength, market shape.

Each adapter hands its frames to the same production helpers the live adapters use
(``scanner._complete_daily_frame``, ``trim_intraday_liquidity_bars``, ``_snapshot_state``,
``compute_market_regime``), so the ``PriceDataSnapshot`` and ``MarketShapeSnapshot``
objects the service sees are built by production code.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.adapters.price_data import _expected_intraday_through, _snapshot_state
from app.services.breakouts.adapters.strength import ExistingStrengthAdapter
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.feature_engine import (
    completed_daily_session,
    daily_data_through,
    intraday_data_through,
    regular_session_close,
    trim_intraday_liquidity_bars,
)
from app.services.breakouts.models import (
    MarketSession,
    StrengthScoreSnapshot,
    TemporalCutoff,
    normalize_ticker,
)
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.market_calendar import early_close_minutes
from app.services.strength import scanner
from app.services.strength.market_regime import MARKET_BENCHMARKS, compute_market_regime

from .memo import frame_fingerprint
from .stores import BAR_COLUMNS, DailyStore, FredStore, MinuteStore

NY = ZoneInfo("America/New_York")
DAILY_CALENDAR_DAYS = scanner._period_calendar_days("2y")  # production fetches 770 days
INTRADAY_CALENDAR_DAYS = 30  # adapters/price_data.py:174-175
SOURCE = "Massive"


class ReplayClock:
    """A clock the runner moves; ``MarketClock`` reads it through ``now``."""

    def __init__(self, start: datetime) -> None:
        self.now = start.astimezone(timezone.utc)

    def set(self, value: datetime) -> None:
        self.now = value.astimezone(timezone.utc)

    def market_clock(self) -> MarketClock:
        return MarketClock(now=lambda: self.now)


class WorkerSettingsView:
    """Forward every attribute of the real settings; only label the discovery provider."""

    def __init__(self, settings: Any) -> None:
        object.__setattr__(self, "_settings", settings)

    def __getattr__(self, name: str) -> Any:
        if name == "discovery_provider":
            return "replay_proxy"
        return getattr(object.__getattribute__(self, "_settings"), name)


class ReplayPriceDataAdapter:
    """Frozen-data stand-in for ``YahooPriceDataAdapter`` with identical snapshot assembly."""

    def __init__(
        self,
        daily_store: DailyStore,
        minute_store: MinuteStore,
        *,
        trim_sessions: bool = True,
        bar_delay_seconds: int = 0,
    ) -> None:
        """``bar_delay_seconds``: a bar becomes visible that long after it closes.

        Production's Massive feed delivered 5-minute bars about 10 minutes after
        their close (2026-09 export: the last bar ended 9.8 to 14.0 minutes before
        the scan on 19,818 Massive-sourced rows), so 600 reproduces production as it
        ran; 0 is the algorithm with bars available at their close.
        """

        self.daily_store = daily_store
        self.minute_store = minute_store
        self.trim_sessions = trim_sessions
        self.bar_delay = timedelta(seconds=int(bar_delay_seconds))
        self.source = SOURCE

    async def daily(self, tickers: Sequence[str], *, cutoff, period: str = "2y") -> dict[str, PriceDataSnapshot]:
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        completed = completed_daily_session(cutoff)
        as_of_day = cutoff.event_at.astimezone(NY).date()
        stamp = cutoff.event_at.astimezone(timezone.utc)
        earliest = completed - timedelta(days=scanner._period_calendar_days(period))
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            frame = self.daily_store.frame(symbol, through=completed, as_of_day=as_of_day)
            if frame.empty:
                continue
            frame = frame.loc[frame.index.date >= earliest]
            bounded, _ = scanner._complete_daily_frame(frame, cutoff.event_at)
            if bounded.empty:
                continue
            data_through = daily_data_through(bounded)
            if data_through is None:
                continue
            completeness, warnings, quality = _snapshot_state(
                data_through=data_through,
                expected_through=regular_session_close(completed),
                source_status="active",
                unit_seconds=24 * 60 * 60,
                complete_label="completed_daily_sessions",
                stale_label="stale_daily_sessions",
            )
            results[symbol] = PriceDataSnapshot(
                ticker=symbol, frame=bounded, source=self.source, raw_as_of=data_through, cutoff=cutoff,
                session=cutoff.session, adjustment="auto_adjusted", completeness=completeness,
                warnings=warnings, requested_at=stamp, received_at=stamp, data_through=data_through,
                feature_cutoff_at=stamp, quality=quality,
            )
        return results

    def _session_mask(self, frame: pd.DataFrame, cutoff, day: date) -> pd.Series:
        local = frame.index.tz_convert(NY)
        minutes = pd.Series(local.hour * 60 + local.minute, index=frame.index)
        if cutoff.session is MarketSession.REGULAR:
            close_by_day = {value: (early_close_minutes(value) or 16 * 60) for value in set(local.date)}
            closes = pd.Series([close_by_day[value] for value in local.date], index=frame.index)
            return (minutes >= 9 * 60 + 30) & (minutes < closes)
        if cutoff.session is MarketSession.PREMARKET:
            same_day = pd.Series(local.date == day, index=frame.index)
            return same_day & (minutes >= 4 * 60) & (minutes < 9 * 60 + 30)
        return pd.Series(True, index=frame.index)

    async def intraday(self, tickers: Sequence[str], *, cutoff, interval: str = "5m") -> dict[str, PriceDataSnapshot]:
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        if len(symbols) > 60:
            raise ValueError("intraday ticker set exceeds 60 symbols")
        if interval != "5m":
            raise ValueError("the replay store holds 5-minute bars only")
        stamp = cutoff.event_at.astimezone(timezone.utc)
        day = cutoff.event_at.astimezone(NY).date()
        expected_through = _expected_intraday_through(cutoff, 5)
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            frame = self.minute_store.bars(symbol, day - timedelta(days=INTRADAY_CALENDAR_DAYS), day)
            if frame.empty:
                continue
            # A bar is visible once it has closed and the feed delay has elapsed.
            frame = frame.loc[frame.index + pd.Timedelta(minutes=5) + self.bar_delay <= stamp]
            if self.trim_sessions:
                frame = frame.loc[self._session_mask(frame, cutoff, day).to_numpy()]
            if frame.empty:
                continue
            bounded = trim_intraday_liquidity_bars(frame, cutoff, interval_minutes=5)
            if bounded.empty:
                continue
            data_through = intraday_data_through(bounded, interval_minutes=5)
            if data_through is None:
                continue
            completeness, warnings, quality = _snapshot_state(
                data_through=data_through,
                expected_through=expected_through,
                unit_seconds=5 * 60,
                complete_label="complete_bars_through_cutoff",
                stale_label="stale_complete_bars",
            )
            results[symbol] = PriceDataSnapshot(
                ticker=symbol, frame=bounded, source=self.source, raw_as_of=data_through, cutoff=cutoff,
                session=cutoff.session, adjustment="unadjusted_intraday", completeness=completeness,
                warnings=warnings, requested_at=stamp, received_at=stamp, data_through=data_through,
                feature_cutoff_at=stamp, quality=quality,
            )
        return results


class MemoStrengthAdapter:
    """``ExistingStrengthAdapter`` with one intrinsic score per (ticker, completed session).

    ``_score_ticker_frames_sync`` scores tickers independently from their completed daily
    frames, so a per-ticker cache keyed on the frame content is exact; the snapshot's
    ``as_of`` is re-stamped for the current scan.
    """

    def __init__(self, inner: ExistingStrengthAdapter | None = None, cache: dict | None = None) -> None:
        self.inner = inner or ExistingStrengthAdapter()
        self.version = self.inner.version
        self._cache: dict[tuple, StrengthScoreSnapshot] = cache if cache is not None else {}
        self.hits = 0
        self.misses = 0

    async def score_ticker_set(self, *args: Any, **kwargs: Any) -> dict[str, StrengthScoreSnapshot]:
        raise RuntimeError("the replay never downloads; use score_from_daily_snapshots")

    async def score_from_daily_snapshots(
        self,
        tickers: Sequence[str],
        *,
        snapshots: Mapping[str, Any],
        as_of: datetime,
        include_options: bool = False,
        range_mode: str = "shadow",
        range_trend_weight: float = 0.15,
        range_final_cap: float = 0.04,
    ) -> dict[str, StrengthScoreSnapshot]:
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        spy = snapshots.get("SPY")
        spy_key = frame_fingerprint(spy.frame) if spy is not None and hasattr(spy, "frame") else None
        params = (range_mode, float(range_trend_weight), float(range_final_cap))
        results: dict[str, StrengthScoreSnapshot] = {}
        missing: list[str] = []
        keys: dict[str, tuple] = {}
        for symbol in symbols:
            snapshot = snapshots.get(symbol)
            if snapshot is None or not hasattr(snapshot, "frame"):
                missing.append(symbol)
                continue
            key = (symbol, frame_fingerprint(snapshot.frame), spy_key, params)
            keys[symbol] = key
            cached = self._cache.get(key)
            if cached is not None:
                self.hits += 1
                results[symbol] = cached.model_copy(update={"as_of": as_of})
            else:
                missing.append(symbol)
        if missing:
            self.misses += len(missing)
            subset = {symbol: snapshots[symbol] for symbol in missing if symbol in snapshots}
            if spy is not None:
                subset["SPY"] = spy
            fresh = await self.inner.score_from_daily_snapshots(
                missing, snapshots=subset, as_of=as_of, include_options=include_options,
                range_mode=range_mode, range_trend_weight=range_trend_weight, range_final_cap=range_final_cap,
            )
            for symbol, snapshot in fresh.items():
                results[symbol] = snapshot
                if symbol in keys:
                    self._cache[keys[symbol]] = snapshot
        return results


class MarketShapeReplay:
    """Replace ``scanner.market_strength`` with production's regime code on frozen frames."""

    def __init__(self, daily_store: DailyStore, fred_store: FredStore) -> None:
        self.daily_store = daily_store
        self.fred_store = fred_store
        self._cache: dict[date, dict[str, Any]] = {}
        self._original: Callable[..., Any] | None = None

    def _payload(self, as_of: datetime) -> dict[str, Any]:
        cutoff_day = as_of.astimezone(NY).date()
        completed = completed_daily_session(TemporalCutoff(event_at=as_of, session=MarketSession.CLOSED))
        cached = self._cache.get(completed)
        if cached is not None:
            return cached
        index_data: dict[str, pd.DataFrame] = {}
        for symbol in MARKET_BENCHMARKS:
            if symbol in ("^VIX", "^TNX"):
                frame = self.fred_store.frame(symbol, through=completed)
            else:
                frame = self.daily_store.frame(symbol, through=completed, as_of_day=cutoff_day)
                if not frame.empty:
                    frame = frame.loc[frame.index.date >= completed - timedelta(days=DAILY_CALENDAR_DAYS)]
            if frame.empty:
                index_data[symbol] = frame
                continue
            bounded, _ = scanner._complete_daily_frame(frame, as_of)
            index_data[symbol] = bounded
        market = compute_market_regime(index_data, as_of=as_of)
        payload = {
            "as_of": as_of.astimezone(timezone.utc).isoformat(),
            "market_regime": market,
            "data_sources": {"prices": {"provider": "Massive + FRED", "status": "active", "message": "replay frames"}},
        }
        self._cache[completed] = payload
        return payload

    def install(self) -> None:
        if self._original is None:
            self._original = scanner.market_strength

        async def market_strength(*, as_of: datetime | None = None) -> dict[str, Any]:
            if as_of is None:
                raise ValueError("the replay requires an explicit as_of")
            return self._payload(as_of)

        scanner.market_strength = market_strength  # type: ignore[assignment]

    def uninstall(self) -> None:
        if self._original is not None:
            scanner.market_strength = self._original  # type: ignore[assignment]
            self._original = None
