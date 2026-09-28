"""The proxied stage: TradingView discovery recomputed from frozen 5-minute bars.

Everything TradingView does server-side is emulated here (the three filters, the sort
by change, the 150-row window); everything production does afterwards is production
code (``normalize_provider_row``, ``filter_and_deduplicate``). Output is labelled
``replay_proxy`` in the snapshot provider, its schema version, the candidate source
and the cache key.

The screener data production read was a delayed view (DATA_SPEC 20, PREREGISTRATION
修订 2): the fields lagged about 15 minutes, and until a symbol's first bar of the
session appeared in that view they still showed the previous session's values. The
proxy therefore evaluates a scan at ``t`` on the bars complete at ``t - tv_delay``,
and falls back to yesterday's close, change, volume and relative volume (regular
profile) or yesterday's pre-market close, change and volume (pre-market profile) for
symbols without a completed bar of the session in the view.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import (
    DiscoveryProfile,
    DiscoverySnapshot,
    MarketSession,
    ProviderStatus,
)
from app.services.breakouts.normalizer import filter_and_deduplicate, normalize_provider_row
from app.services.breakouts.providers.tradingview import PREMARKET_COLUMNS, REGULAR_COLUMNS

from .stores import (
    SLOTS_PER_DAY,
    DailyStore,
    MinuteStore,
    ProductionCandidateMetadata,
    SharesStore,
    TickerMeta,
    previous_trading_day,
)

NY = ZoneInfo("America/New_York")
PROVIDER = "replay_proxy"
SCHEMA_VERSION = "replay-proxy-discovery-v2"
TV_DELAY_MINUTES = 15  # DATA_SPEC 20.2: 10 to 15 minutes fit production's rows best, 15 slightly better
_REGULAR_FIRST_SLOT = (9 * 60 + 30 - 4 * 60) // 5  # bar starting 09:30 ET
_PREMARKET_LAST_SLOT = _REGULAR_FIRST_SLOT - 1  # bar starting 09:25 ET


@dataclass
class DayContext:
    day: date
    prev_day: date | None
    tickers: list[str]
    meta: list[TickerMeta]
    close_ffill: np.ndarray  # tickers x slots, last close known at each slot (any session)
    close_ffill_regular: np.ndarray  # same over regular-session bars only (TradingView's ``close``)
    cumulative_volume: np.ndarray  # tickers x slots, from 04:00 ET (TradingView's ``volume`` includes pre-market)
    previous_close: np.ndarray  # yesterday's regular close
    prev_prev_close: np.ndarray  # the close before that (yesterday's change)
    previous_volume: np.ndarray  # yesterday's full-day volume
    mean_volume_10: np.ndarray  # mean daily volume over the 10 sessions before the day
    previous_relvol: np.ndarray  # yesterday's volume over the 10 sessions before it
    prev_premarket_close: np.ndarray  # yesterday's last pre-market bar close
    prev_premarket_volume: np.ndarray  # yesterday's pre-market volume


def _empty_context(day: date, prev_day: date | None) -> DayContext:
    empty = np.zeros((0, SLOTS_PER_DAY))
    none = np.zeros(0)
    return DayContext(day, prev_day, [], [], empty, empty, empty, none, none, none, none, none, none, none)


def build_day_context(
    day: date,
    *,
    minute_store: MinuteStore,
    daily_store: DailyStore,
    metadata: Any,
    universe: set[str] | None = None,
) -> DayContext:
    """Per-ticker arrays for one day, for every ticker with bars today or yesterday.

    Yesterday's tickers are needed because the delayed view still lists yesterday's
    movers before their first bar of today appears (DATA_SPEC 20.3, 20.5).
    """

    prev_day = previous_trading_day(day)
    candidates = set(minute_store.tickers_with_bars_on(day))
    if prev_day is not None:
        candidates |= minute_store.tickers_with_bars_on(prev_day)
    if universe is not None:
        candidates &= universe
    tickers: list[str] = []
    metas: list[TickerMeta] = []
    closes: list[np.ndarray] = []
    volumes: list[np.ndarray] = []
    prev_pm_close: list[float] = []
    prev_pm_volume: list[float] = []
    for ticker in sorted(candidates):
        meta = metadata.meta(ticker, day)
        if meta is None or meta.exchange.upper() == "OTC":
            continue
        slots = minute_store.day_slots(ticker, day)
        if slots is None:
            close, volume = np.full(SLOTS_PER_DAY, np.nan), np.full(SLOTS_PER_DAY, np.nan)
        else:
            close, volume = slots
        prior = minute_store.day_slots(ticker, prev_day) if prev_day is not None else None
        if prior is None:
            pm_close, pm_volume = np.nan, np.nan
        else:
            premarket = prior[0][:_REGULAR_FIRST_SLOT]
            finite = np.flatnonzero(np.isfinite(premarket))
            pm_close = float(premarket[finite[-1]]) if len(finite) else np.nan
            pm_volume = float(np.nansum(prior[1][:_REGULAR_FIRST_SLOT]))
        tickers.append(ticker)
        metas.append(meta)
        closes.append(close)
        volumes.append(volume)
        prev_pm_close.append(pm_close)
        prev_pm_volume.append(pm_volume)
    if not tickers:
        return _empty_context(day, prev_day)
    close_matrix = np.vstack(closes)
    close_ffill = pd.DataFrame(close_matrix).ffill(axis=1).to_numpy()
    regular_only = close_matrix.copy()
    regular_only[:, :_REGULAR_FIRST_SLOT] = np.nan
    close_ffill_regular = pd.DataFrame(regular_only).ffill(axis=1).to_numpy()
    cumulative = np.nancumsum(np.vstack(volumes), axis=1)
    stats = [daily_store.prior_session_stats(ticker, day) for ticker in tickers]

    def column(name: str) -> np.ndarray:
        values = [getattr(item, name) if item is not None else None for item in stats]
        return np.array([np.nan if value is None else value for value in values], dtype=float)

    return DayContext(
        day, prev_day, tickers, metas, close_ffill, close_ffill_regular, cumulative,
        column("prev_close"), column("prev_prev_close"), column("prev_volume"),
        column("mean_volume"), column("prev_relvol"),
        np.array(prev_pm_close, dtype=float), np.array(prev_pm_volume, dtype=float),
    )


def last_complete_slot(as_of: datetime) -> int:
    local = as_of.astimezone(NY)
    minutes = local.hour * 60 + local.minute
    return (minutes - (4 * 60 + 5)) // 5


def _finite_or_none(value: float) -> float | None:
    return float(value) if np.isfinite(value) else None


class ReplayDiscoveryProvider:
    """Drop-in for ``TradingViewDiscoveryProvider.scan`` on frozen data."""

    def __init__(
        self,
        settings: BreakoutSettings,
        *,
        minute_store: MinuteStore,
        daily_store: DailyStore,
        metadata: Any,
        shares: SharesStore | None = None,
        market_cap_source: str = "shares",
        relvol_scale: float = 1.0,
        inject_production_otc: bool = False,
        day_contexts: dict[date, DayContext] | None = None,
        universe: set[str] | None = None,
        tv_delay_minutes: int = TV_DELAY_MINUTES,
    ) -> None:
        if market_cap_source not in {"shares", "production", "none"}:
            raise ValueError("market_cap_source must be shares, production or none")
        if market_cap_source == "production" and not isinstance(metadata, ProductionCandidateMetadata):
            raise ValueError("production market caps need ProductionCandidateMetadata")
        if inject_production_otc and not isinstance(metadata, ProductionCandidateMetadata):
            raise ValueError("OTC injection needs ProductionCandidateMetadata")
        self.settings = settings
        self.minute_store = minute_store
        self.daily_store = daily_store
        self.metadata = metadata
        self.shares = shares
        self.market_cap_source = market_cap_source
        self.relvol_scale = float(relvol_scale)
        self.inject_production_otc = inject_production_otc
        self._day_contexts = day_contexts if day_contexts is not None else {}
        self._universe = universe
        if tv_delay_minutes < 0:
            raise ValueError("tv_delay_minutes must not be negative")
        self.tv_delay = timedelta(minutes=int(tv_delay_minutes))
        self.last_prefilter_count = 0

    @property
    def health(self) -> dict[str, Any]:
        return {
            "provider": PROVIDER,
            "status": "active",
            "last_success_at": None,
            "consecutive_failures": 0,
            "circuit_open": False,
            "circuit_open_remaining_seconds": 0.0,
            "last_error_code": None,
            "stale_snapshot_available": False,
        }

    async def aclose(self) -> None:
        return None

    def day_context(self, day: date) -> DayContext:
        context = self._day_contexts.get(day)
        if context is None:
            context = build_day_context(
                day,
                minute_store=self.minute_store,
                daily_store=self.daily_store,
                metadata=self.metadata,
                universe=self._universe,
            )
            self._day_contexts[day] = context
        return context

    def _market_cap(self, ticker: str, meta: TickerMeta, price: float, day: date, as_of: datetime) -> float | None:
        if meta.is_fund:
            return None
        if self.market_cap_source == "production":
            return self.metadata.market_cap(ticker, as_of, price)
        if self.market_cap_source == "shares" and self.shares is not None:
            shares = self.shares.shares(ticker, day)
            return price * shares if shares else None
        return None

    def _otc_rows(self, session: MarketSession, as_of: datetime) -> list[tuple[float, str, list[Any]]]:
        if not self.inject_production_otc:
            return []
        rows: list[tuple[float, str, list[Any]]] = []
        tv_types = {"common_stock": ("stock", ["common"]), "adr": ("dr", ["adr"]), "etf": ("fund", ["etf"])}
        for body in self.metadata.otc_rows(as_of):
            ticker = str(body.get("ticker") or "").upper()
            tv_type, specs = tv_types.get(str(body.get("asset_type")), ("structured", []))
            change = float(body.get("provider_change_pct") or 0.0)
            common = [ticker, "OTC", body.get("name") or ticker, tv_type, specs]
            if session is MarketSession.PREMARKET:
                row = common + [
                    body.get("previous_regular_close"),
                    body.get("price"),
                    change,
                    body.get("provider_volume"),
                    body.get("provider_volume"),
                    body.get("provider_relative_volume"),
                    body.get("provider_market_cap"),
                    body.get("sector"),
                ]
            else:
                row = common + [
                    body.get("price"),
                    change,
                    body.get("provider_volume"),
                    body.get("provider_relative_volume"),
                    body.get("provider_market_cap"),
                    body.get("sector"),
                ]
            rows.append((change, ticker, row))
        return rows

    def prefilter_arrays(self, session: MarketSession, as_of: datetime) -> dict[str, Any] | None:
        """TradingView's fields and filters in the delayed view, for every ticker of the day.

        Returns the day context, the view's slot and per-ticker arrays: ``rolled`` (a
        completed bar of this session exists in the view), ``price``, ``change``,
        ``volume``, ``relvol`` and the ``passing`` mask; ``None`` when the day has no
        tickers. ``scripts/discovery_misses.py`` reads the same arrays to explain misses,
        so the field and filter logic lives here only.
        """

        day = as_of.astimezone(NY).date()
        context = self.day_context(day)
        settings = self.settings
        if not context.tickers:
            return None
        count = len(context.tickers)
        view = as_of - self.tv_delay
        slot = last_complete_slot(view) if view.astimezone(NY).date() == day else -1
        slot = min(slot, SLOTS_PER_DAY - 1)
        nothing = np.full(count, np.nan)
        with np.errstate(invalid="ignore", divide="ignore"):
            if session is MarketSession.PREMARKET:
                slot = min(slot, _PREMARKET_LAST_SLOT)
                today_price = context.close_ffill[:, slot] if slot >= 0 else nothing
                today_volume = context.cumulative_volume[:, slot] if slot >= 0 else np.zeros(count)
                rolled = np.isfinite(today_price)
                price = np.where(rolled, today_price, context.prev_premarket_close)
                change = np.where(
                    rolled,
                    (today_price / context.previous_close - 1.0) * 100.0,
                    (context.prev_premarket_close / context.prev_prev_close - 1.0) * 100.0,
                )
                volume = np.where(rolled, today_volume, context.prev_premarket_volume)
                relvol = context.previous_relvol
                passing = (
                    (price >= settings.min_price)
                    & (change >= settings.premarket_min_change_pct)
                    & (volume > 0)
                )
            else:
                today_price = context.close_ffill_regular[:, slot] if slot >= _REGULAR_FIRST_SLOT else nothing
                today_volume = context.cumulative_volume[:, slot] if slot >= 0 else np.zeros(count)
                rolled = np.isfinite(today_price)
                price = np.where(rolled, today_price, context.previous_close)
                change = np.where(
                    rolled,
                    (today_price / context.previous_close - 1.0) * 100.0,
                    (context.previous_close / context.prev_prev_close - 1.0) * 100.0,
                )
                volume = np.where(rolled, today_volume, context.previous_volume)
                relvol = np.where(
                    rolled,
                    today_volume / context.mean_volume_10 * self.relvol_scale,
                    context.previous_relvol,
                )
                passing = (
                    (price >= settings.min_price)
                    & (change >= settings.regular_min_change_pct)
                    & (relvol >= settings.regular_min_relative_volume)
                )
        passing = passing & np.isfinite(change) & np.isfinite(price)
        return {
            "context": context, "slot": slot, "rolled": rolled, "price": price, "change": change,
            "volume": volume, "relvol": relvol, "passing": passing,
        }

    def _tradingview_rows(self, session: MarketSession, as_of: datetime) -> list[list[Any]]:
        day = as_of.astimezone(NY).date()
        settings = self.settings
        ranked: list[tuple[float, str, list[Any]]] = []
        arrays = self.prefilter_arrays(session, as_of)
        if arrays is not None:
            context = arrays["context"]
            price, change, volume, relvol, passing = (
                arrays["price"], arrays["change"], arrays["volume"], arrays["relvol"], arrays["passing"]
            )
            for index in np.flatnonzero(passing):
                ticker = context.tickers[index]
                meta = context.meta[index]
                price_value = float(price[index])
                cap = self._market_cap(ticker, meta, price_value, day, as_of)
                relative = _finite_or_none(relvol[index])
                volume_value = _finite_or_none(volume[index])
                common = [ticker, meta.exchange, meta.name, meta.tv_type, list(meta.typespecs)]
                if session is MarketSession.PREMARKET:
                    row = common + [
                        _finite_or_none(context.previous_close[index]),
                        price_value,
                        float(change[index]),
                        volume_value,
                        volume_value,
                        relative,
                        cap,
                        meta.sector,
                    ]
                else:
                    row = common + [price_value, float(change[index]), volume_value, relative, cap, meta.sector]
                ranked.append((float(change[index]), ticker, row))
        ranked.extend(self._otc_rows(session, as_of))
        self.last_prefilter_count = len(ranked)
        # TradingView sorts by change and returns rows [0, 150) before production filters run.
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [row for _change, _ticker, row in ranked[: settings.provider_result_limit]]

    async def scan(
        self,
        *,
        session: MarketSession,
        as_of: datetime,
        profile: DiscoveryProfile,
    ) -> DiscoverySnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        if session not in (MarketSession.REGULAR, MarketSession.PREMARKET):
            return DiscoverySnapshot(
                provider=PROVIDER, status=ProviderStatus.UNAVAILABLE, as_of=as_of, session=session,
                schema_version=SCHEMA_VERSION, candidate_count=0,
                warnings=["session_not_supported"], candidates=[],
            )
        if profile is DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS:
            raise ValueError("the replay proxy only emulates the movers and gappers profiles")
        columns = PREMARKET_COLUMNS if session is MarketSession.PREMARKET else REGULAR_COLUMNS
        candidates = []
        warnings: list[str] = []
        for row in self._tradingview_rows(session, as_of):
            candidate, row_warnings = normalize_provider_row(
                symbol=f"{row[1] or 'UNKNOWN'}:{row[0]}",
                row=row,
                columns=columns,
                session=session,
                as_of=as_of,
                source=PROVIDER,
                profile=profile,
            )
            warnings.extend(row_warnings)
            if candidate is not None:
                candidates.append(candidate)
        normalized, filter_warnings = filter_and_deduplicate(
            candidates, settings=self.settings, session=session, profile=profile
        )
        warnings.extend(filter_warnings)
        unique_warnings = list(dict.fromkeys(warnings))[:64]
        return DiscoverySnapshot(
            provider=PROVIDER,
            status=ProviderStatus.ACTIVE,
            as_of=as_of,
            session=session,
            schema_version=SCHEMA_VERSION,
            candidate_count=len(normalized),
            warnings=unique_warnings,
            candidates=normalized,
            cache_key=f"{PROVIDER}|{session.value}|{as_of.astimezone(NY).isoformat()}",
        )
