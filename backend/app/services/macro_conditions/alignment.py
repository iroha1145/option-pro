"""Mixed-frequency alignment onto one daily score grid.

Rules, all enforced here rather than in each factor:

* the grid is US trading days, so a snapshot date is always a real session;
* every lookup is a *backward* as-of join — only ``observation_date <= grid_date``
  is ever visible, with zero tolerance for a future observation date;
* there is no interpolation and no backfilling toward the future; a value is
  carried forward only while it is inside its registered stale threshold, and
  once it goes past that threshold it stops being carried at all;
* freshness is measured in calendar days for FRED series and in trading days for
  ETF bars, matching how each source publishes.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional, Sequence

from app.services.market_calendar import is_trading_day

from .models import AlignedValue, finite
from .registry import ETF_MAX_STALE_TRADING_DAYS


LOOKUP_OK = "ok"
LOOKUP_STALE = "stale"
LOOKUP_MISSING = "missing"


@dataclass(frozen=True, slots=True)
class Lookup:
    """Outcome of one as-of read. ``value`` is set only when fresh enough."""

    reason: str
    value: Optional[AlignedValue] = None

    @property
    def ok(self) -> bool:
        return self.reason == LOOKUP_OK and self.value is not None


MISSING = Lookup(LOOKUP_MISSING)


def build_grid(start: date, end: date) -> tuple[date, ...]:
    """Trading days in ``[start, end]``, ascending."""

    if end < start:
        return ()
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


class AsOfSeries:
    """One source series exposed through backward as-of reads.

    Only observations with a finite value are retained: a missing observation
    must not occupy a slot in a rolling window.
    """

    __slots__ = (
        "key",
        "dates",
        "values",
        "available_at",
        "history_basis",
        "max_stale_calendar_days",
        "max_stale_trading_days",
        "_grid",
    )

    def __init__(
        self,
        key: str,
        rows: Sequence[tuple[date, Optional[float], str, str]],
        *,
        max_stale_calendar_days: int | None = None,
        max_stale_trading_days: int | None = None,
        grid: Sequence[date] | None = None,
    ) -> None:
        if (max_stale_calendar_days is None) == (max_stale_trading_days is None):
            raise ValueError("exactly one staleness unit must be configured")
        if max_stale_trading_days is not None and grid is None:
            raise ValueError("trading-day staleness needs the score grid")
        self.key = key
        dates: list[date] = []
        values: list[float] = []
        available: list[str] = []
        basis: list[str] = []
        previous: date | None = None
        for observation_date, value, available_at, history_basis in rows:
            number = finite(value)
            if number is None:
                continue
            if previous is not None and observation_date <= previous:
                # Callers hand over the active revision per date, ascending. A
                # duplicate or out-of-order date would silently corrupt every
                # rolling window, so drop it instead of guessing.
                continue
            previous = observation_date
            dates.append(observation_date)
            values.append(number)
            available.append(available_at)
            basis.append(history_basis)
        self.dates = tuple(dates)
        self.values = tuple(values)
        self.available_at = tuple(available)
        self.history_basis = tuple(basis)
        self.max_stale_calendar_days = max_stale_calendar_days
        self.max_stale_trading_days = max_stale_trading_days
        self._grid = tuple(grid or ())

    def __len__(self) -> int:
        return len(self.dates)

    # -- indexing ----------------------------------------------------------

    def index_as_of(self, when: date) -> Optional[int]:
        """Position of the newest observation at or before ``when``."""

        position = bisect_right(self.dates, when) - 1
        return position if position >= 0 else None

    def _trading_day_gap(self, observation_date: date, when: date) -> int:
        grid = self._grid
        if not grid:
            return (when - observation_date).days
        end = bisect_right(grid, when) - 1
        start = bisect_right(grid, observation_date) - 1
        if end < 0 or start < 0:
            return (when - observation_date).days
        return end - start

    def age(self, observation_date: date, when: date) -> int:
        if self.max_stale_trading_days is not None:
            return self._trading_day_gap(observation_date, when)
        return (when - observation_date).days

    def within_stale_threshold(self, gap: int) -> bool:
        if gap < 0:
            return False
        if self.max_stale_trading_days is not None:
            return gap <= self.max_stale_trading_days
        return gap <= int(self.max_stale_calendar_days or 0)

    # -- reads -------------------------------------------------------------

    def at(self, when: date) -> Lookup:
        position = self.index_as_of(when)
        if position is None:
            return MISSING
        observation_date = self.dates[position]
        gap = self.age(observation_date, when)
        if not self.within_stale_threshold(gap):
            return Lookup(LOOKUP_STALE)
        return Lookup(
            LOOKUP_OK,
            AlignedValue(
                value=self.values[position],
                observation_date=observation_date,
                available_at=self.available_at[position],
                history_basis=self.history_basis[position],
                source_age_days=gap,
            ),
        )

    def trailing(self, when: date, count: int) -> tuple[tuple[date, float], ...]:
        """The last ``count`` valid observations at or before ``when``."""

        position = self.index_as_of(when)
        if position is None or count <= 0:
            return ()
        start = max(0, position - count + 1)
        return tuple(
            (self.dates[index], self.values[index])
            for index in range(start, position + 1)
        )

    def window_available_at(self, when: date, count: int) -> Optional[str]:
        """Latest local visibility among the observations a window actually uses.

        A revision to an old observation carries a newer ``first_seen_at`` than
        its neighbours, so this is a real maximum rather than the newest date's
        timestamp.
        """

        position = self.index_as_of(when)
        if position is None or count <= 0:
            return None
        start = max(0, position - count + 1)
        return max(self.available_at[start : position + 1])

    def window_history_basis(self, when: date, count: int) -> Optional[str]:
        position = self.index_as_of(when)
        if position is None or count <= 0:
            return None
        start = max(0, position - count + 1)
        return combine_history_basis(self.history_basis[start : position + 1])

    def common_dates(self, other: "AsOfSeries") -> tuple[date, ...]:
        return tuple(sorted(set(self.dates) & set(other.dates)))

    def value_on(self, when: date) -> Optional[float]:
        position = bisect_right(self.dates, when) - 1
        if position < 0 or self.dates[position] != when:
            return None
        return self.values[position]

    def available_on(self, when: date) -> Optional[str]:
        position = bisect_right(self.dates, when) - 1
        if position < 0 or self.dates[position] != when:
            return None
        return self.available_at[position]

    def basis_on(self, when: date) -> Optional[str]:
        position = bisect_right(self.dates, when) - 1
        if position < 0 or self.dates[position] != when:
            return None
        return self.history_basis[position]


def combine_history_basis(values: Sequence[str]) -> Optional[str]:
    """``mixed`` whenever a value depends on both revision regimes."""

    distinct = {value for value in values if value}
    if not distinct:
        return None
    if len(distinct) == 1:
        return next(iter(distinct))
    return "mixed"


def series_from_rows(
    series_id: str,
    rows: Sequence[dict],
    *,
    max_stale_calendar_days: int,
) -> AsOfSeries:
    """Build an as-of series from :meth:`MacroRepository.active_series` rows."""

    prepared: list[tuple[date, Optional[float], str, str]] = []
    for row in rows:
        observation_date = _as_date(row.get("observation_date"))
        if observation_date is None:
            continue
        prepared.append(
            (
                observation_date,
                row.get("value"),
                str(row.get("first_seen_at") or ""),
                str(row.get("history_basis") or ""),
            )
        )
    prepared.sort(key=lambda item: item[0])
    return AsOfSeries(
        series_id,
        prepared,
        max_stale_calendar_days=max_stale_calendar_days,
    )


def etf_from_rows(
    symbol: str,
    rows: Sequence[dict],
    *,
    grid: Sequence[date],
) -> AsOfSeries:
    """Build an as-of series from :meth:`MacroRepository.active_etf` rows."""

    prepared: list[tuple[date, Optional[float], str, str]] = []
    for row in rows:
        observation_date = _as_date(row.get("observation_date"))
        if observation_date is None:
            continue
        prepared.append(
            (
                observation_date,
                row.get("adjusted_close"),
                str(row.get("available_at") or ""),
                str(row.get("history_basis") or ""),
            )
        )
    prepared.sort(key=lambda item: item[0])
    return AsOfSeries(
        symbol,
        prepared,
        max_stale_trading_days=ETF_MAX_STALE_TRADING_DAYS,
        grid=grid,
    )


def _as_date(value: object) -> Optional[date]:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            year, month, day = (int(part) for part in value.split("-"))
            return date(year, month, day)
        except (TypeError, ValueError):
            return None
    return None


def earliest_data_through(dates: Sequence[Optional[date]]) -> Optional[date]:
    """A mixed-source factor is only current as of its *oldest* valid input."""

    present = [value for value in dates if value is not None]
    return min(present) if present else None


__all__ = [
    "LOOKUP_MISSING",
    "LOOKUP_OK",
    "LOOKUP_STALE",
    "MISSING",
    "AsOfSeries",
    "Lookup",
    "build_grid",
    "combine_history_basis",
    "earliest_data_through",
    "etf_from_rows",
    "series_from_rows",
]
