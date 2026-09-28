"""The proxied stage: TradingView discovery recomputed from frozen 5-minute bars.

Everything TradingView does server-side is emulated here (the three filters, the sort
by change, the 150-row window); everything production does afterwards is production
code (``normalize_provider_row``, ``filter_and_deduplicate``). Output is labelled
``replay_proxy`` in the snapshot provider, its schema version, the candidate source
and the cache key.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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
)

NY = ZoneInfo("America/New_York")
PROVIDER = "replay_proxy"
SCHEMA_VERSION = "replay-proxy-discovery-v1"
_PREMARKET_LAST_SLOT = (9 * 60 + 30 - 4 * 60) // 5 - 1  # bar starting 09:25 ET


@dataclass
class DayContext:
    day: date
    tickers: list[str]
    meta: list[TickerMeta]
    close_ffill: np.ndarray  # tickers x slots, last close known at each slot
    cumulative_volume: np.ndarray  # tickers x slots
    previous_close: np.ndarray
    mean_volume_10: np.ndarray
    previous_relvol: np.ndarray  # yesterday's full-day volume over its 10-day average


# TradingView's volume-derived fields roll to the new session per symbol during the
# first minutes: at 09:37 and 09:43 ET every production candidate still carried the
# previous session's relative volume, by 09:56 most had rolled (smoke day 1).
STALE_RELVOL_UNTIL_MINUTE = 9 * 60 + 45
ROLLING_RELVOL_UNTIL_MINUTE = 10 * 60


def build_day_context(
    day: date,
    *,
    minute_store: MinuteStore,
    daily_store: DailyStore,
    metadata: Any,
    universe: set[str] | None = None,
) -> DayContext:
    tickers: list[str] = []
    metas: list[TickerMeta] = []
    closes: list[np.ndarray] = []
    volumes: list[np.ndarray] = []
    candidates = minute_store.tickers_with_bars_on(day)
    if universe is not None:
        candidates &= universe
    for ticker in sorted(candidates):
        meta = metadata.meta(ticker, day)
        if meta is None or meta.exchange.upper() == "OTC":
            continue
        slots = minute_store.day_slots(ticker, day)
        if slots is None:
            continue
        close, volume = slots
        tickers.append(ticker)
        metas.append(meta)
        closes.append(close)
        volumes.append(volume)
    if not tickers:
        empty = np.zeros((0, SLOTS_PER_DAY))
        return DayContext(day, [], [], empty, empty, np.zeros(0), np.zeros(0), np.zeros(0))
    close_matrix = np.vstack(closes)
    close_ffill = pd.DataFrame(close_matrix).ffill(axis=1).to_numpy()
    cumulative = np.nancumsum(np.vstack(volumes), axis=1)
    previous = np.array([daily_store.previous_close(t, day) or np.nan for t in tickers], dtype=float)
    mean10 = np.array([daily_store.mean_volume(t, day, 10) or np.nan for t in tickers], dtype=float)
    previous_relvol = np.array(
        [daily_store.previous_session_relvol(t, day, 10) or np.nan for t in tickers], dtype=float
    )
    return DayContext(day, tickers, metas, close_ffill, cumulative, previous, mean10, previous_relvol)


def last_complete_slot(as_of: datetime) -> int:
    local = as_of.astimezone(NY)
    minutes = local.hour * 60 + local.minute
    return (minutes - (4 * 60 + 5)) // 5


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
            return self.metadata.market_cap(ticker, as_of)
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
        """TradingView's three filters recomputed for every ticker with bars that day.

        Returns the day context, the slot and the per-ticker arrays (last close,
        cumulative volume, change, relative volume, passing mask); ``None`` when no
        bar has completed yet. ``scripts/discovery_misses.py`` reads the same arrays
        to explain misses, so the filter logic lives here only.
        """

        day = as_of.astimezone(NY).date()
        context = self.day_context(day)
        settings = self.settings
        slot = last_complete_slot(as_of)
        if session is MarketSession.PREMARKET:
            slot = min(slot, _PREMARKET_LAST_SLOT)
        if not context.tickers or slot < 0:
            return None
        slot = min(slot, SLOTS_PER_DAY - 1)
        last_close = context.close_ffill[:, slot]
        cumulative = context.cumulative_volume[:, slot]
        minute = as_of.astimezone(NY).hour * 60 + as_of.astimezone(NY).minute
        with np.errstate(invalid="ignore", divide="ignore"):
            change = (last_close / context.previous_close - 1.0) * 100.0
            today_relvol = cumulative / context.mean_volume_10 * self.relvol_scale
        stale = context.previous_relvol
        if session is MarketSession.PREMARKET or minute < STALE_RELVOL_UNTIL_MINUTE:
            relvol = stale
        elif minute < ROLLING_RELVOL_UNTIL_MINUTE:
            relvol = np.fmax(stale, today_relvol)
        else:
            relvol = today_relvol
        if session is MarketSession.PREMARKET:
            passing = (
                (last_close >= settings.min_price)
                & (change >= settings.premarket_min_change_pct)
                & (cumulative > 0)
            )
        else:
            passing = (
                (last_close >= settings.min_price)
                & (change >= settings.regular_min_change_pct)
                & (relvol >= settings.regular_min_relative_volume)
            )
        passing = passing & np.isfinite(change)
        return {
            "context": context, "slot": slot, "last_close": last_close, "cumulative": cumulative,
            "change": change, "relvol": relvol, "passing": passing,
        }

    def _tradingview_rows(self, session: MarketSession, as_of: datetime) -> list[list[Any]]:
        day = as_of.astimezone(NY).date()
        settings = self.settings
        ranked: list[tuple[float, str, list[Any]]] = []
        arrays = self.prefilter_arrays(session, as_of)
        if arrays is not None:
            context = arrays["context"]
            last_close, cumulative = arrays["last_close"], arrays["cumulative"]
            change, relvol, passing = arrays["change"], arrays["relvol"], arrays["passing"]
            for index in np.flatnonzero(passing):
                ticker = context.tickers[index]
                meta = context.meta[index]
                price = float(last_close[index])
                cap = self._market_cap(ticker, meta, price, day, as_of)
                relative = float(relvol[index]) if np.isfinite(relvol[index]) else None
                common = [ticker, meta.exchange, meta.name, meta.tv_type, list(meta.typespecs)]
                if session is MarketSession.PREMARKET:
                    row = common + [
                        float(context.previous_close[index]),
                        price,
                        float(change[index]),
                        float(cumulative[index]),
                        float(cumulative[index]),
                        relative,
                        cap,
                        meta.sector,
                    ]
                else:
                    row = common + [price, float(change[index]), float(cumulative[index]), relative, cap, meta.sector]
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
