"""Point-in-time price adapter built on the existing bounded Yahoo chain."""

from __future__ import annotations

import asyncio
from datetime import timezone
from typing import Sequence

import pandas as pd
import yfinance as yf

from app.services import yahoo
from app.services.breakouts.models import normalize_ticker
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.strength import scanner


class YahooPriceDataAdapter:
    source = "Yahoo/yfinance"

    async def daily(
        self,
        tickers: Sequence[str],
        *,
        cutoff,
        period: str = "2y",
    ) -> dict[str, PriceDataSnapshot]:
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        raw = await asyncio.to_thread(scanner._download_history, symbols, period)
        source_status = raw.attrs.get("price_source") or {}
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            bounded, _ = scanner._complete_daily_frame(
                scanner._slice_ticker(raw, symbol),
                cutoff.event_at,
            )
            if bounded.empty:
                continue
            results[symbol] = PriceDataSnapshot(
                ticker=symbol,
                frame=bounded,
                source=str(source_status.get("provider") or self.source),
                raw_as_of=cutoff.event_at.astimezone(timezone.utc),
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="auto_adjusted",
                completeness="completed_daily_sessions",
                warnings=tuple(source_status.get("missing_symbols") or ()),
            )
        return results

    async def intraday(
        self,
        tickers: Sequence[str],
        *,
        cutoff,
        interval: str = "5m",
    ) -> dict[str, PriceDataSnapshot]:
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        if len(symbols) > 40:
            raise ValueError("intraday ticker set exceeds 40 symbols")

        def download() -> pd.DataFrame:
            kwargs = {
                "tickers": " ".join(symbols),
                "period": "20d",
                "interval": interval,
                "group_by": "ticker",
                "threads": True,
                "progress": False,
                "auto_adjust": False,
                "prepost": True,
            }
            session = getattr(yahoo, "_yf_session", None)
            if session is not None:
                kwargs["session"] = session
            try:
                return yf.download(**kwargs)
            except Exception:
                return pd.DataFrame()

        raw = await asyncio.to_thread(download)
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            frame = scanner._slice_ticker(raw, symbol)
            if frame.empty:
                continue
            from app.services.breakouts.feature_engine import trim_intraday_bars

            bounded = trim_intraday_bars(frame, cutoff)
            if bounded.empty:
                continue
            results[symbol] = PriceDataSnapshot(
                ticker=symbol,
                frame=bounded,
                source=self.source,
                raw_as_of=cutoff.event_at.astimezone(timezone.utc),
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="unadjusted_intraday",
                completeness="complete_bars_through_cutoff",
            )
        return results
