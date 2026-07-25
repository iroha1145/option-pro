"""Deterministic synthetic fixtures for Optix Macro Conditions tests.

Every value here is generated locally from a fixed seed. No test in this
repository contacts FRED, Massive, Yahoo, MacroLens, OpenAI or any production
server, and no number from any third-party report is used as a golden assertion.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable, Mapping, Optional, Sequence

from app.services.macro_conditions.models import (
    EtfObservation,
    SeriesMetadata,
    SeriesObservation,
)
from app.services.macro_conditions.registry import (
    ETF_SYMBOLS,
    FRED_SERIES,
    SERIES_BY_ID,
)
from app.services.market_calendar import is_trading_day


#: Fixed "now" used by every macro fixture so snapshots are reproducible.
FIXED_NOW = datetime(2026, 7, 24, 22, 30, tzinfo=timezone.utc)

#: FRED units strings the fixtures report per unit family.
UNITS_BY_FAMILY: Mapping[str, str] = {
    "usd_amount": "Millions of U.S. Dollars",
    "percent": "Percent",
    "index": "Index",
    "usd_per_barrel": "Dollars per Barrel",
    "usd_per_mmbtu": "Dollars per Million BTU",
}

#: Base level and amplitude per series, in the series' own published units.
_SERIES_SHAPE: Mapping[str, tuple[float, float]] = {
    # Money series are published in millions here, so the canonical conversion
    # to billions has to divide by 1000 for the factors to make sense.
    "WALCL": (7_200_000.0, 240_000.0),
    "WTREGEN": (700_000.0, 180_000.0),
    "RRPONTSYD": (180_000.0, 170_000.0),
    "WRESBAL": (3_300_000.0, 200_000.0),
    "SOFR": (4.35, 0.22),
    "OBFR": (4.33, 0.18),
    "IORB": (4.40, 0.15),
    "RRPONTSYAWARD": (4.25, 0.15),
    "EFFR": (4.34, 0.16),
    "DCPF3M": (4.55, 0.30),
    "DTB3": (4.30, 0.20),
    "DGS2": (3.95, 0.45),
    "DGS10": (4.25, 0.55),
    "DGS30": (4.60, 0.50),
    "DFII5": (1.70, 0.35),
    "DFII10": (1.95, 0.30),
    "T10YIE": (2.25, 0.28),
    "NFCI": (-0.35, 0.25),
    "VIXCLS": (17.5, 6.5),
    "VXVCLS": (19.0, 5.0),
    "DTWEXBGS": (121.0, 6.0),
    "DCOILWTICO": (76.0, 14.0),
    "OVXCLS": (34.0, 9.0),
    "DHHNGSP": (3.10, 1.05),
}

_ETF_SHAPE: Mapping[str, tuple[float, float, float]] = {
    # (start price, annual drift, wave amplitude)
    "HYG": (78.0, 0.03, 0.05),
    "IEI": (117.0, 0.01, 0.03),
    "LQD": (110.0, 0.02, 0.04),
    "IEF": (96.0, 0.01, 0.035),
    "KRE": (52.0, 0.06, 0.14),
    "SPY": (430.0, 0.09, 0.09),
    "TLT": (92.0, -0.01, 0.08),
    "IWM": (196.0, 0.05, 0.12),
}


def _wave(index: int, period: float, phase: float) -> float:
    return math.sin(2.0 * math.pi * (index / period) + phase)


def _phase_for(key: str) -> float:
    # Deterministic per-key phase without touching the random module.
    return (sum(ord(character) for character in key) % 360) * math.pi / 180.0


def calendar_days(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return days


def trading_days(start: date, end: date) -> list[date]:
    return [day for day in calendar_days(start, end) if is_trading_day(day)]


def weekly_wednesdays(start: date, end: date) -> list[date]:
    return [day for day in calendar_days(start, end) if day.weekday() == 2]


def weekly_fridays(start: date, end: date) -> list[date]:
    return [day for day in calendar_days(start, end) if day.weekday() == 4]


def series_observation_dates(series_id: str, start: date, end: date) -> list[date]:
    spec = SERIES_BY_ID[series_id]
    if spec.expected_frequency == "W":
        # H.4.1 ends Wednesday; NFCI ends Friday.
        return (
            weekly_fridays(start, end)
            if series_id == "NFCI"
            else weekly_wednesdays(start, end)
        )
    return trading_days(start, end)


def synthetic_series_values(
    series_id: str,
    dates: Sequence[date],
) -> list[Optional[float]]:
    base, amplitude = _SERIES_SHAPE[series_id]
    phase = _phase_for(series_id)
    values: list[Optional[float]] = []
    for index, _day in enumerate(dates):
        slow = _wave(index, 252.0, phase)
        fast = _wave(index, 21.0, phase * 1.7)
        value = base + amplitude * (0.75 * slow + 0.25 * fast)
        if series_id in {"RRPONTSYD"}:
            # ON RRP has to reach the low-buffer regime somewhere in the sample
            # so the direct-score curve is exercised across its whole range.
            value = max(0.0, value * (0.5 + 0.5 * slow))
        if series_id == "NFCI":
            value = base + amplitude * slow
        values.append(round(value, 6))
    return values


def synthetic_metadata(series_id: str, *, last_updated: str = "2026-07-24") -> SeriesMetadata:
    spec = SERIES_BY_ID[series_id]
    units = UNITS_BY_FAMILY[spec.expected_units_family]
    from app.services.macro_conditions.registry import scale_to_canonical

    return SeriesMetadata(
        series_id=series_id,
        units=units,
        frequency_short=spec.expected_frequency,
        canonical_unit=spec.canonical_unit,
        scale_to_canonical=scale_to_canonical(units, spec.expected_units_family),
        source_last_updated=last_updated,
        realtime_start="2026-07-24",
        realtime_end="9999-12-31",
    )


def synthetic_series_fetch(
    series_id: str,
    *,
    start: date,
    end: date,
) -> tuple[SeriesMetadata, tuple[SeriesObservation, ...]]:
    metadata = synthetic_metadata(series_id)
    dates = series_observation_dates(series_id, start, end)
    values = synthetic_series_values(series_id, dates)
    observations = tuple(
        SeriesObservation(
            series_id,
            day,
            None if value is None else value * metadata.scale_to_canonical,
        )
        for day, value in zip(dates, values)
    )
    return metadata, observations


def synthetic_etf_observations(
    symbol: str,
    *,
    start: date,
    end: date,
    provider: str = "Massive",
) -> tuple[EtfObservation, ...]:
    price, drift, amplitude = _ETF_SHAPE[symbol]
    phase = _phase_for(symbol)
    days = trading_days(start, end)
    rows: list[EtfObservation] = []
    for index, day in enumerate(days):
        trend = math.exp(drift * index / 252.0)
        wave = 1.0 + amplitude * (
            0.7 * _wave(index, 252.0, phase) + 0.3 * _wave(index, 42.0, phase * 2.1)
        )
        rows.append(
            EtfObservation(
                symbol=symbol,
                observation_date=day,
                adjusted_close=round(price * trend * wave, 4),
                provider=provider,
            )
        )
    return tuple(rows)


def seed_repository(
    repository,
    *,
    start: date,
    end: date,
    history_basis: str = "latest_revised_backfill",
    observed_at: str = "2026-07-24T22:30:00Z",
    skip_series: Iterable[str] = (),
    skip_etfs: Iterable[str] = (),
) -> dict[str, int]:
    """Populate a macro repository with the full synthetic panel."""

    skipped_series = set(skip_series)
    skipped_etfs = set(skip_etfs)
    repository.initialize()
    series_count = 0
    for spec in FRED_SERIES:
        if spec.series_id in skipped_series:
            continue
        metadata, observations = synthetic_series_fetch(
            spec.series_id, start=start, end=end
        )
        repository.record_series_revisions(
            metadata,
            observations,
            history_basis=history_basis,
            observed_at=observed_at,
        )
        series_count += 1
    etf_count = 0
    for symbol in ETF_SYMBOLS:
        if symbol in skipped_etfs:
            continue
        repository.record_etf_observations(
            synthetic_etf_observations(symbol, start=start, end=end),
            data_through=end,
            history_basis=history_basis,
            observed_at=observed_at,
        )
        etf_count += 1
    return {"series": series_count, "etfs": etf_count}


def fixed_clock(moment: datetime = FIXED_NOW) -> Callable[[], datetime]:
    return lambda: moment


__all__ = [
    "FIXED_NOW",
    "UNITS_BY_FAMILY",
    "calendar_days",
    "fixed_clock",
    "seed_repository",
    "series_observation_dates",
    "synthetic_etf_observations",
    "synthetic_metadata",
    "synthetic_series_fetch",
    "synthetic_series_values",
    "trading_days",
    "weekly_fridays",
    "weekly_wednesdays",
]
