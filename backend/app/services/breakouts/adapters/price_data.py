"""Point-in-time price adapter built on the existing bounded Yahoo chain."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Sequence
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

from app.services import yahoo
from app.services.breakouts.feature_engine import (
    completed_daily_session,
    daily_data_through,
    intraday_data_through,
    regular_session_close,
    trim_intraday_liquidity_bars,
)
from app.services.breakouts.models import normalize_ticker
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.strength import scanner
from app.services.yfinance_batch import download_in_bounded_batches


NEW_YORK = ZoneInfo("America/New_York")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _interval_minutes(interval: str) -> int:
    text = str(interval or "").strip().lower()
    if not text.endswith("m") or not text[:-1].isdigit():
        raise ValueError("intraday interval must be expressed in minutes")
    value = int(text[:-1])
    if value < 1:
        raise ValueError("intraday interval must be positive")
    return value


def _expected_intraday_through(cutoff, interval_minutes: int) -> datetime | None:
    """Return the latest bar end that could be complete at the cutoff."""

    local = cutoff.event_at.astimezone(NEW_YORK).replace(second=0, microsecond=0)
    minute = local.hour * 60 + local.minute
    floored = minute - minute % interval_minutes
    day = local.date()
    from app.services.market_calendar import early_close_minutes as _early_close_minutes

    regular_close = _early_close_minutes(day) or 16 * 60
    if cutoff.session.value == "premarket":
        start, end = 4 * 60, 9 * 60 + 30
    elif cutoff.session.value == "regular":
        start, end = 9 * 60 + 30, regular_close
    elif cutoff.session.value == "postmarket":
        start, end = regular_close, 20 * 60
    else:
        return None
    completed_end = min(floored, end)
    if completed_end <= start:
        return None
    expected = datetime(
        day.year,
        day.month,
        day.day,
        completed_end // 60,
        completed_end % 60,
        tzinfo=NEW_YORK,
    )
    return expected.astimezone(timezone.utc)


def _snapshot_state(
    *,
    data_through: datetime,
    expected_through: datetime | None,
    source_status: str = "active",
    unit_seconds: int,
    complete_label: str,
    stale_label: str,
) -> tuple[str, tuple[str, ...], float]:
    warnings: list[str] = []
    quality = 1.0
    completeness = complete_label
    if expected_through is not None and data_through < expected_through:
        missing_units = max(
            1.0,
            (expected_through - data_through).total_seconds() / max(unit_seconds, 1),
        )
        quality = max(0.2, 1.0 / (1.0 + missing_units))
        completeness = stale_label
        warnings.append("data_through_before_feature_cutoff")
    if source_status != "active":
        quality *= 0.85
        warnings.append("price_source_degraded")
    return completeness, tuple(warnings), round(max(0.0, min(1.0, quality)), 6)


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
        requested_at = _utc_now()
        raw = await asyncio.to_thread(scanner._download_history, symbols, period)
        received_at = _utc_now()
        source_status = raw.attrs.get("price_source") or {}
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            bounded, _ = scanner._complete_daily_frame(
                scanner._slice_ticker(raw, symbol),
                cutoff.event_at,
            )
            if bounded.empty:
                continue
            data_through = daily_data_through(bounded)
            if data_through is None:
                continue
            expected_through = regular_session_close(completed_daily_session(cutoff))
            completeness, warnings, quality = _snapshot_state(
                data_through=data_through,
                expected_through=expected_through,
                source_status=str(source_status.get("status") or "active"),
                unit_seconds=24 * 60 * 60,
                complete_label="completed_daily_sessions",
                stale_label="stale_daily_sessions",
            )
            results[symbol] = PriceDataSnapshot(
                ticker=symbol,
                frame=bounded,
                source=str(source_status.get("provider") or self.source),
                raw_as_of=data_through,
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="auto_adjusted",
                completeness=completeness,
                warnings=warnings,
                requested_at=requested_at,
                received_at=received_at,
                data_through=data_through,
                feature_cutoff_at=cutoff.event_at.astimezone(timezone.utc),
                quality=quality,
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
        if len(symbols) > 60:
            raise ValueError("intraday ticker set exceeds 60 symbols")

        def _massive_intraday() -> pd.DataFrame | None:
            """主源 Massive 分钟聚合 → yfinance 同形状 MultiIndex 帧;失败回落。"""
            from app.services import massive as massive_provider

            if not massive_provider.configured():
                return None
            minutes = _interval_minutes(interval)
            end_day = _utc_now().astimezone(NEW_YORK).date()
            start_day = end_day - timedelta(days=30)
            frames: dict[tuple[str, str], pd.Series] = {}
            for symbol in symbols:
                massive_symbol = massive_provider.to_symbol(symbol)
                if massive_symbol is None:
                    return None  # 集合含不支持代码:整体回落,保持单源语义
                try:
                    bars = massive_provider.ticker_range(
                        massive_symbol,
                        minutes,
                        "minute",
                        start_day.isoformat(),
                        end_day.isoformat(),
                    )
                except massive_provider.MassiveError:
                    return None
                if not bars:
                    continue
                index = pd.DatetimeIndex(
                    [
                        pd.Timestamp(bar["t"], unit="ms", tz="UTC")
                        for bar in bars
                        if isinstance(bar.get("t"), (int, float))
                    ]
                )
                for field, key in (
                    ("Open", "o"),
                    ("High", "h"),
                    ("Low", "l"),
                    ("Close", "c"),
                    ("Volume", "v"),
                ):
                    frames[(symbol, field)] = pd.Series(
                        [bar.get(key) for bar in bars if isinstance(bar.get("t"), (int, float))],
                        index=index,
                    )
            if not frames:
                return None
            frame = pd.DataFrame(frames)
            frame.columns = pd.MultiIndex.from_tuples(frame.columns)
            frame = frame.sort_index()
            frame.attrs["price_source"] = "Massive"
            return frame

        def download() -> pd.DataFrame:
            try:
                massive_frame = _massive_intraday()
            except Exception:
                massive_frame = None
            if massive_frame is not None and not massive_frame.empty:
                return massive_frame
            kwargs = {
                "period": "20d",
                "interval": interval,
                "group_by": "ticker",
                "progress": False,
                "auto_adjust": False,
                "prepost": True,
            }
            session = getattr(yahoo, "_yf_session", None)
            if session is not None:
                kwargs["session"] = session
            try:
                frame = download_in_bounded_batches(
                    yf.download,
                    tickers=symbols,
                    **kwargs,
                )
                frame.attrs["price_source"] = self.source
                return frame
            except Exception:
                frame = pd.DataFrame()
                frame.attrs["price_source"] = self.source
                return frame

        requested_at = _utc_now()
        raw = await asyncio.to_thread(download)
        received_at = _utc_now()
        source_value = raw.attrs.get("price_source")
        if isinstance(source_value, dict):
            source_value = source_value.get("provider")
        actual_source = (
            str(source_value).strip()
            if isinstance(source_value, str) and source_value.strip()
            else self.source
        )
        interval_value = _interval_minutes(interval)
        expected_through = _expected_intraday_through(cutoff, interval_value)
        results: dict[str, PriceDataSnapshot] = {}
        for symbol in symbols:
            frame = scanner._slice_ticker(raw, symbol)
            if frame.empty:
                continue
            # Preserve every session-bounded Close/Volume row.  Technical
            # features filter complete OHLC rows later, while liquidity must
            # still include a bar whose optional High/Low fields are missing.
            bounded = trim_intraday_liquidity_bars(
                frame,
                cutoff,
                interval_minutes=interval_value,
            )
            if bounded.empty:
                continue
            data_through = intraday_data_through(
                bounded,
                interval_minutes=interval_value,
            )
            if data_through is None:
                continue
            completeness, warnings, quality = _snapshot_state(
                data_through=data_through,
                expected_through=expected_through,
                unit_seconds=interval_value * 60,
                complete_label="complete_bars_through_cutoff",
                stale_label="stale_complete_bars",
            )
            results[symbol] = PriceDataSnapshot(
                ticker=symbol,
                frame=bounded,
                source=actual_source,
                raw_as_of=data_through,
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="unadjusted_intraday",
                completeness=completeness,
                warnings=warnings,
                requested_at=requested_at,
                received_at=received_at,
                data_through=data_through,
                feature_cutoff_at=cutoff.event_at.astimezone(timezone.utc),
                quality=quality,
            )
        return results
