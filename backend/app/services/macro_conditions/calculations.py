"""The thirty raw factor calculations.

Every formula reads only observations dated at or before the grid date it is
computing, so no factor can see the future. A factor whose inputs are missing or
past their stale threshold produces ``None`` — never a neutral placeholder.
Where the score uses a transformed quantity (an absolute spread, a distance from
a target), the signed or original value is kept for display.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date, timedelta
from statistics import median
from typing import Callable, Mapping, Optional, Sequence

from .alignment import (
    LOOKUP_MISSING,
    LOOKUP_STALE,
    AsOfSeries,
    Lookup,
    combine_history_basis,
    earliest_data_through,
)
from .models import FactorPoint, finite
from .registry import (
    BREAKEVEN_TARGET_PERCENT,
    FACTORS,
    FACTORS_BY_ID,
    FUNDING_FRAGMENTATION_MINIMUM_SPREADS,
    FUNDING_FRAGMENTATION_WINDOW,
    FX_VOLATILITY_WINDOW,
    LONG_MEDIAN_WINDOW,
    NET_LIQUIDITY_MOMENTUM_WEEKS,
    ON_RRP_FULL_BUFFER_BILLIONS,
    RELATIVE_RETURN_MINIMUM_OBSERVATIONS,
    RELATIVE_RETURN_WINDOW_DAYS,
    SHORT_VOLATILITY_WINDOW,
    TGA_MEDIAN_WEEKLY_OBSERVATIONS,
    TRADING_DAYS_PER_YEAR,
    SHORT_VOLATILITY_WINDOW as RATE_VOLATILITY_WINDOW,
)


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------


def population_std(values: Sequence[float]) -> Optional[float]:
    """Population standard deviation (divides by N, not N-1)."""

    count = len(values)
    if count < 2:
        return None
    mean = math.fsum(values) / count
    variance = math.fsum((value - mean) ** 2 for value in values) / count
    return finite(math.sqrt(variance))


def rolling_median(values: Sequence[float]) -> Optional[float]:
    if not values:
        return None
    return finite(median(values))


def clip(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def relative_return(
    first_now: float,
    first_then: float,
    second_now: float,
    second_then: float,
) -> Optional[float]:
    """``100 × [ln(A_t/A_t-63) − ln(B_t/B_t-63)]`` in percentage points."""

    if min(first_now, first_then, second_now, second_then) <= 0:
        return None
    return finite(
        100.0
        * (
            math.log(first_now / first_then)
            - math.log(second_now / second_then)
        )
    )


# ---------------------------------------------------------------------------
# Input gathering
# ---------------------------------------------------------------------------


class _Inputs:
    """Reads a factor's declared inputs at one grid date and tracks provenance."""

    def __init__(
        self,
        when: date,
        series: Mapping[str, AsOfSeries],
        etfs: Mapping[str, AsOfSeries],
    ) -> None:
        self.when = when
        self._series = series
        self._etfs = etfs
        self.missing: list[str] = []
        self.stale: list[str] = []
        self.observation_dates: list[Optional[date]] = []
        self.available: list[str] = []
        self.basis: list[str] = []
        self.required = 0
        self.satisfied = 0

    def _record(self, key: str, lookup: Lookup) -> Optional[float]:
        self.required += 1
        if lookup.reason == LOOKUP_MISSING:
            self.missing.append(key)
            return None
        if lookup.reason == LOOKUP_STALE:
            self.stale.append(key)
            return None
        value = lookup.value
        assert value is not None
        self.satisfied += 1
        self.observation_dates.append(value.observation_date)
        self.available.append(value.available_at)
        self.basis.append(value.history_basis)
        return value.value

    def series(self, series_id: str) -> Optional[float]:
        source = self._series.get(series_id)
        if source is None:
            self.required += 1
            self.missing.append(series_id)
            return None
        return self._record(series_id, source.at(self.when))

    def note_window(
        self,
        source: AsOfSeries,
        count: int,
    ) -> None:
        """Fold a rolling window's provenance into this factor's provenance."""

        available = source.window_available_at(self.when, count)
        if available:
            self.available.append(available)
        basis = source.window_history_basis(self.when, count)
        if basis:
            self.basis.append(basis)

    def note_external(
        self,
        *,
        observation_date: Optional[date] = None,
        available_at: Optional[str] = None,
        history_basis: Optional[str] = None,
    ) -> None:
        if observation_date is not None:
            self.observation_dates.append(observation_date)
        if available_at:
            self.available.append(available_at)
        if history_basis:
            self.basis.append(history_basis)

    def mark_missing(self, key: str) -> None:
        self.required += 1
        self.missing.append(key)

    def mark_stale(self, key: str) -> None:
        self.required += 1
        self.stale.append(key)

    def mark_satisfied(self) -> None:
        self.required += 1
        self.satisfied += 1

    # -- provenance --------------------------------------------------------

    @property
    def data_through(self) -> Optional[date]:
        return earliest_data_through(self.observation_dates)

    @property
    def available_at(self) -> Optional[str]:
        return max(self.available) if self.available else None

    @property
    def history_basis(self) -> Optional[str]:
        return combine_history_basis(self.basis)

    @property
    def source_age_days(self) -> Optional[int]:
        through = self.data_through
        if through is None:
            return None
        return (self.when - through).days

    def point(
        self,
        factor_id: str,
        *,
        raw_value: Optional[float],
        score_value: Optional[float],
        signed_value: Optional[float] = None,
    ) -> FactorPoint:
        if raw_value is None:
            status = "stale" if self.stale else "missing"
        else:
            status = "ok"
        return FactorPoint(
            factor_id=factor_id,
            snapshot_date=self.when,
            raw_value=raw_value if raw_value is not None else None,
            score_value=score_value if raw_value is not None else None,
            signed_value=signed_value,
            status=status,
            data_through=self.data_through if raw_value is not None else None,
            available_at=self.available_at if raw_value is not None else None,
            history_basis=self.history_basis if raw_value is not None else None,
            source_age_days=self.source_age_days if raw_value is not None else None,
            missing_inputs=tuple(sorted(set(self.missing))),
            stale_inputs=tuple(sorted(set(self.stale))),
        )

    @property
    def confidence(self) -> Optional[float]:
        if self.required <= 0:
            return None
        return self.satisfied / self.required


# ---------------------------------------------------------------------------
# Derived grid series shared by several factors
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DerivedPoint:
    """One intermediate value together with where it came from.

    A derived series that stores only ``value`` cannot answer when its inputs
    first became visible, so any rolling or lagged operation over it loses
    point-in-time provenance (incremental review P1): a revision to an old
    observation changes the factor value while its ``available_at`` still points
    at a moment before that revision existed. That is harmless for today's
    display and poison for a future walk-forward test, which is exactly the use
    the point-in-time storage was built for.
    """

    value: float
    observation_date: date
    available_at: Optional[str]
    history_basis: Optional[str]


class DerivedGrid:
    """Grid-indexed intermediate values with as-of lookups of their own."""

    __slots__ = ("grid", "values", "points", "_last_valid")

    def __init__(
        self,
        grid: Sequence[date],
        values: Sequence[Optional[float]],
        points: Sequence[Optional[DerivedPoint]] | None = None,
    ) -> None:
        if len(grid) != len(values):
            raise ValueError("derived grid length mismatch")
        if points is not None and len(points) != len(values):
            raise ValueError("derived grid provenance length mismatch")
        self.grid = tuple(grid)
        self.values = tuple(values)
        self.points: tuple[Optional[DerivedPoint], ...] = (
            tuple(points) if points is not None else tuple(None for _ in values)
        )
        last: list[Optional[int]] = []
        current: Optional[int] = None
        for index, value in enumerate(self.values):
            if value is not None:
                current = index
            last.append(current)
        self._last_valid = tuple(last)

    def value_at_index(self, index: int) -> Optional[float]:
        if not 0 <= index < len(self.values):
            return None
        return self.values[index]

    def as_of(self, when: date) -> Optional[float]:
        position = bisect_right(self.grid, when) - 1
        if position < 0:
            return None
        resolved = self._last_valid[position]
        return None if resolved is None else self.values[resolved]

    def trailing_valid(self, index: int, count: int) -> tuple[float, ...]:
        if not 0 <= index < len(self.values) or count <= 0:
            return ()
        collected: list[float] = []
        for position in range(index, -1, -1):
            value = self.values[position]
            if value is None:
                continue
            collected.append(value)
            if len(collected) == count:
                break
        collected.reverse()
        return tuple(collected)

    def point_as_of(self, when: date) -> Optional[DerivedPoint]:
        """The as-of value together with its provenance."""

        position = bisect_right(self.grid, when) - 1
        if position < 0:
            return None
        resolved = self._last_valid[position]
        return None if resolved is None else self.points[resolved]

    def trailing_valid_points(
        self,
        index: int,
        count: int,
    ) -> tuple[DerivedPoint, ...]:
        """Same selection as ``trailing_valid``, carrying provenance.

        A rolling window's visibility is the *latest* first-visible time among
        every row inside it, so the window has to be walked as points, not as
        bare floats.
        """

        if not 0 <= index < len(self.values) or count <= 0:
            return ()
        collected: list[DerivedPoint] = []
        for position in range(index, -1, -1):
            point = self.points[position]
            if point is None:
                continue
            collected.append(point)
            if len(collected) == count:
                break
        collected.reverse()
        return tuple(collected)


def _net_liquidity_grid(
    grid: Sequence[date],
    series: Mapping[str, AsOfSeries],
) -> DerivedGrid:
    values: list[Optional[float]] = []
    points: list[Optional[DerivedPoint]] = []
    for when in grid:
        walcl = series.get("WALCL")
        tga = series.get("WTREGEN")
        rrp = series.get("RRPONTSYD")
        parts = []
        available: list[str] = []
        basis: list[str] = []
        observed: Optional[date] = None
        ok = True
        for source in (walcl, tga, rrp):
            if source is None:
                ok = False
                break
            lookup = source.at(when)
            if not lookup.ok or lookup.value is None:
                ok = False
                break
            parts.append(lookup.value.value)
            available.append(lookup.value.available_at)
            basis.append(lookup.value.history_basis)
            # The combination is only as current as its oldest leg.
            if observed is None or lookup.value.observation_date < observed:
                observed = lookup.value.observation_date
        value = finite(parts[0] - parts[1] - parts[2]) if ok else None
        values.append(value)
        points.append(
            DerivedPoint(
                value=value,
                observation_date=observed,
                # Visible only once the last of its three inputs was visible.
                available_at=max(available) if available else None,
                history_basis=combine_history_basis(basis),
            )
            if value is not None and observed is not None
            else None
        )
    return DerivedGrid(grid, values, points)


#: The five signed funding spreads whose cross-sectional dispersion feeds
#: ``funding_fragmentation_21d``.
_FUNDING_SPREAD_PAIRS: tuple[tuple[str, str, str], ...] = (
    ("collateral_repo_friction", "SOFR", "OBFR"),
    ("corridor_friction_1", "SOFR", "IORB"),
    ("corridor_friction_2", "SOFR", "RRPONTSYAWARD"),
    ("effr_iorb_spread", "EFFR", "IORB"),
    ("cp_tbill_spread", "DCPF3M", "DTB3"),
)


def _funding_dispersion_grid(
    grid: Sequence[date],
    series: Mapping[str, AsOfSeries],
) -> DerivedGrid:
    values: list[Optional[float]] = []
    points: list[Optional[DerivedPoint]] = []
    for when in grid:
        spreads: list[float] = []
        available: list[str] = []
        basis: list[str] = []
        observed: Optional[date] = None
        for _name, first_id, second_id in _FUNDING_SPREAD_PAIRS:
            first = series.get(first_id)
            second = series.get(second_id)
            if first is None or second is None:
                continue
            left = first.at(when)
            right = second.at(when)
            if not left.ok or not right.ok or left.value is None or right.value is None:
                continue
            spread = finite(left.value.value - right.value.value)
            if spread is not None:
                spreads.append(spread)
                available.extend([left.value.available_at, right.value.available_at])
                basis.extend([left.value.history_basis, right.value.history_basis])
                for leg in (left.value, right.value):
                    if observed is None or leg.observation_date < observed:
                        observed = leg.observation_date
        if len(spreads) < FUNDING_FRAGMENTATION_MINIMUM_SPREADS:
            values.append(None)
            points.append(None)
            continue
        value = population_std(spreads)
        values.append(value)
        points.append(
            DerivedPoint(
                value=value,
                observation_date=observed,
                available_at=max(available) if available else None,
                history_basis=combine_history_basis(basis),
            )
            if value is not None and observed is not None
            else None
        )
    return DerivedGrid(grid, values, points)


class _EtfPair:
    """Common trading days for one ETF pair plus its 63-day relative return."""

    __slots__ = ("first", "second", "dates", "_index")

    def __init__(self, first: AsOfSeries, second: AsOfSeries) -> None:
        self.first = first
        self.second = second
        self.dates = first.common_dates(second)
        self._index = {value: position for position, value in enumerate(self.dates)}

    def latest_common(self, when: date) -> Optional[date]:
        position = bisect_right(self.dates, when) - 1
        return self.dates[position] if position >= 0 else None

    def relative_return_63d(
        self,
        when: date,
    ) -> tuple[Optional[float], Optional[date], Optional[str], Optional[str], int]:
        """Return ``(value, data_through, available_at, history_basis, gap)``."""

        anchor = self.latest_common(when)
        if anchor is None:
            return None, None, None, None, -1
        gap = self.first.age(anchor, when)
        position = self._index[anchor]
        if position < RELATIVE_RETURN_WINDOW_DAYS:
            # Fewer than 64 shared closes; a shorter lookback would not be the
            # registered formula.
            return None, anchor, None, None, gap
        base = self.dates[position - RELATIVE_RETURN_WINDOW_DAYS]
        first_now = self.first.value_on(anchor)
        first_then = self.first.value_on(base)
        second_now = self.second.value_on(anchor)
        second_then = self.second.value_on(base)
        if None in (first_now, first_then, second_now, second_then):
            return None, anchor, None, None, gap
        value = relative_return(first_now, first_then, second_now, second_then)
        available = max(
            candidate
            for candidate in (
                self.first.available_on(anchor),
                self.first.available_on(base),
                self.second.available_on(anchor),
                self.second.available_on(base),
            )
            if candidate
        )
        basis = combine_history_basis(
            [
                value
                for value in (
                    self.first.basis_on(anchor),
                    self.first.basis_on(base),
                    self.second.basis_on(anchor),
                    self.second.basis_on(base),
                )
                if value
            ]
        )
        return value, anchor, available, basis, gap


# ---------------------------------------------------------------------------
# Factor computation
# ---------------------------------------------------------------------------


def compute_factor_points(
    grid: Sequence[date],
    series: Mapping[str, AsOfSeries],
    etfs: Mapping[str, AsOfSeries],
) -> dict[str, list[FactorPoint]]:
    """Compute all thirty factors across the grid.

    Returns ``{factor_id: [FactorPoint, ...]}`` aligned to ``grid`` order.
    """

    net_liquidity = _net_liquidity_grid(grid, series)
    dispersion = _funding_dispersion_grid(grid, series)
    pairs: dict[str, _EtfPair] = {}
    for factor in FACTORS:
        if len(factor.required_etfs) == 2:
            first = etfs.get(factor.required_etfs[0])
            second = etfs.get(factor.required_etfs[1])
            if first is not None and second is not None:
                pairs[factor.factor_id] = _EtfPair(first, second)

    output: dict[str, list[FactorPoint]] = {
        factor.factor_id: [] for factor in FACTORS
    }
    for index, when in enumerate(grid):
        for factor in FACTORS:
            handler = _HANDLERS[factor.factor_id]
            output[factor.factor_id].append(
                handler(
                    when,
                    index,
                    series,
                    etfs,
                    net_liquidity=net_liquidity,
                    dispersion=dispersion,
                    pairs=pairs,
                )
            )
    return output


# Handlers receive (when, index, series, etfs, **derived) and return a FactorPoint.
# They are written out one per factor so each formula is readable next to its
# registry entry rather than hidden behind a generic expression evaluator.


def _fed_net_liquidity(when, index, series, etfs, *, net_liquidity, **_extra):
    inputs = _Inputs(when, series, etfs)
    walcl = inputs.series("WALCL")
    tga = inputs.series("WTREGEN")
    rrp = inputs.series("RRPONTSYD")
    value = (
        finite(walcl - tga - rrp)
        if None not in (walcl, tga, rrp)
        else None
    )
    return inputs.point("fed_net_liquidity", raw_value=value, score_value=value)


def _bank_reserves(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("WRESBAL")
    return inputs.point("bank_reserves", raw_value=value, score_value=value)


def _net_liquidity_momentum(when, index, series, etfs, *, net_liquidity, **_extra):
    inputs = _Inputs(when, series, etfs)
    walcl = inputs.series("WALCL")
    tga = inputs.series("WTREGEN")
    rrp = inputs.series("RRPONTSYD")
    current = (
        finite(walcl - tga - rrp) if None not in (walcl, tga, rrp) else None
    )
    value: Optional[float] = None
    if current is not None:
        target = when - timedelta(weeks=NET_LIQUIDITY_MOMENTUM_WEEKS)
        earlier = net_liquidity.point_as_of(target)
        if earlier is None:
            inputs.mark_missing("net_liquidity_13w_ago")
        else:
            inputs.mark_satisfied()
            value = finite(current - earlier.value)
            # The 13-week-ago leg has its own first-visible time; without it a
            # revision to that older reading would move this factor while its
            # available_at still predated the revision (incremental review P1).
            #
            # Only visibility is folded in, not observation_date: data_through
            # answers "what is this value current to", and the value is current
            # to today's reading. Folding the older date in would report the
            # factor as thirteen weeks stale, which is a different claim and a
            # false one.
            inputs.note_external(
                available_at=earlier.available_at,
                history_basis=earlier.history_basis,
            )
    return inputs.point(
        "net_liquidity_momentum_13w", raw_value=value, score_value=value
    )


def _tga_deviation(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    current = inputs.series("WTREGEN")
    value: Optional[float] = None
    if current is not None:
        source = series["WTREGEN"]
        window = source.trailing(when, TGA_MEDIAN_WEEKLY_OBSERVATIONS)
        if len(window) < TGA_MEDIAN_WEEKLY_OBSERVATIONS:
            inputs.mark_missing("wtregen_52_week_median")
        else:
            inputs.mark_satisfied()
            inputs.note_window(source, TGA_MEDIAN_WEEKLY_OBSERVATIONS)
            centre = rolling_median([item[1] for item in window])
            value = finite(current - centre) if centre is not None else None
    return inputs.point("tga_deviation_52w", raw_value=value, score_value=value)


def _on_rrp_buffer_risk(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    balance = inputs.series("RRPONTSYD")
    risk: Optional[float] = None
    score: Optional[float] = None
    if balance is not None:
        ratio = clip(balance / ON_RRP_FULL_BUFFER_BILLIONS, 0.0, 1.0)
        risk = finite((1.0 - ratio) ** 2)
        if risk is not None:
            score = finite(clip(100.0 * (1.0 - risk), 0.0, 100.0))
    return inputs.point("on_rrp_buffer_risk", raw_value=risk, score_value=score)


def _absolute_spread(factor_id: str, first_id: str, second_id: str):
    def handler(when, index, series, etfs, **_extra):
        inputs = _Inputs(when, series, etfs)
        first = inputs.series(first_id)
        second = inputs.series(second_id)
        signed = finite(first - second) if None not in (first, second) else None
        score_value = finite(abs(signed)) if signed is not None else None
        return inputs.point(
            factor_id,
            raw_value=signed,
            score_value=score_value,
            signed_value=signed,
        )

    return handler


def _cp_tbill_spread(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    commercial_paper = inputs.series("DCPF3M")
    treasury_bill = inputs.series("DTB3")
    signed = (
        finite(commercial_paper - treasury_bill)
        if None not in (commercial_paper, treasury_bill)
        else None
    )
    # Only a positive credit premium is stress; a negative print is not scored
    # as an improvement beyond zero.
    score_value = finite(max(signed, 0.0)) if signed is not None else None
    return inputs.point(
        "cp_tbill_spread",
        raw_value=signed,
        score_value=score_value,
        signed_value=signed,
    )


def _funding_fragmentation(when, index, series, etfs, *, dispersion, **_extra):
    inputs = _Inputs(when, series, etfs)
    today = dispersion.value_at_index(index)
    if today is None:
        inputs.mark_missing("funding_spread_panel")
        return inputs.point(
            "funding_fragmentation_21d", raw_value=None, score_value=None
        )
    inputs.mark_satisfied()
    window_points = dispersion.trailing_valid_points(
        index,
        FUNDING_FRAGMENTATION_WINDOW,
    )
    window = tuple(point.value for point in window_points)
    value = finite(math.fsum(window) / len(window)) if window else None
    # Visibility covers the whole 21-day window, not only today's legs
    # (incremental review P1): the mean moves when any observation inside the
    # window is revised, so the factor is not knowable until the last of those
    # rows was knowable.
    #
    # data_through stays with the newest point in the window -- that is what the
    # mean is current to. Taking the oldest row's date instead would report the
    # factor as three weeks stale, which is a different claim and a false one.
    available: list[str] = []
    basis: list[str] = []
    for point in window_points:
        if point.available_at:
            available.append(point.available_at)
        if point.history_basis:
            basis.append(point.history_basis)
    inputs.note_external(
        observation_date=window_points[-1].observation_date if window_points else None,
        available_at=max(available) if available else None,
        history_basis=combine_history_basis(basis),
    )
    return inputs.point(
        "funding_fragmentation_21d", raw_value=value, score_value=value
    )


def _spread(factor_id: str, first_id: str, second_id: str):
    def handler(when, index, series, etfs, **_extra):
        inputs = _Inputs(when, series, etfs)
        first = inputs.series(first_id)
        second = inputs.series(second_id)
        value = finite(first - second) if None not in (first, second) else None
        return inputs.point(factor_id, raw_value=value, score_value=value)

    return handler


def _rate_volatility(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    current = inputs.series("DGS10")
    value: Optional[float] = None
    if current is not None:
        source = series["DGS10"]
        # 21 changes need 22 consecutive valid observations.
        window = source.trailing(when, RATE_VOLATILITY_WINDOW + 1)
        if len(window) < RATE_VOLATILITY_WINDOW + 1:
            inputs.mark_missing("dgs10_daily_changes")
        else:
            inputs.mark_satisfied()
            inputs.note_window(source, RATE_VOLATILITY_WINDOW + 1)
            levels = [item[1] for item in window]
            changes = [
                levels[position] - levels[position - 1]
                for position in range(1, len(levels))
            ]
            value = population_std(changes)
    return inputs.point("rate_volatility_10y_21d", raw_value=value, score_value=value)


def _curve_curvature(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    ten = inputs.series("DGS10")
    two = inputs.series("DGS2")
    thirty = inputs.series("DGS30")
    value = (
        finite(abs(2.0 * ten - two - thirty))
        if None not in (ten, two, thirty)
        else None
    )
    return inputs.point("curve_curvature_abs", raw_value=value, score_value=value)


def _real_rate_level(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    five = inputs.series("DFII5")
    ten = inputs.series("DFII10")
    value = (
        finite(0.6 * five + 0.4 * ten) if None not in (five, ten) else None
    )
    return inputs.point("real_rate_level", raw_value=value, score_value=value)


def _breakeven(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    level = inputs.series("T10YIE")
    distance = (
        finite(abs(level - BREAKEVEN_TARGET_PERCENT)) if level is not None else None
    )
    return inputs.point(
        "breakeven_10y",
        raw_value=level,
        score_value=distance,
        signed_value=distance,
    )


def _nfci(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("NFCI")
    return inputs.point("nfci", raw_value=value, score_value=value)


def _relative_return_factor(factor_id: str):
    def handler(when, index, series, etfs, *, pairs, **_extra):
        inputs = _Inputs(when, series, etfs)
        spec = FACTORS_BY_ID[factor_id]
        pair = pairs.get(factor_id)
        if pair is None:
            for symbol in spec.required_etfs:
                inputs.mark_missing(symbol)
            return inputs.point(factor_id, raw_value=None, score_value=None)
        value, anchor, available, basis, gap = pair.relative_return_63d(when)
        if anchor is None:
            for symbol in spec.required_etfs:
                inputs.mark_missing(symbol)
            return inputs.point(factor_id, raw_value=None, score_value=None)
        if not pair.first.within_stale_threshold(gap):
            for symbol in spec.required_etfs:
                inputs.mark_stale(symbol)
            return inputs.point(factor_id, raw_value=None, score_value=None)
        if value is None:
            for symbol in spec.required_etfs:
                inputs.mark_missing(symbol)
            return inputs.point(factor_id, raw_value=None, score_value=None)
        for _symbol in spec.required_etfs:
            inputs.mark_satisfied()
        inputs.note_external(
            observation_date=anchor,
            available_at=available,
            history_basis=basis,
        )
        return inputs.point(factor_id, raw_value=value, score_value=value)

    return handler


def _vix(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("VIXCLS")
    return inputs.point("vix", raw_value=value, score_value=value)


def _vix_term_structure(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    near = inputs.series("VIXCLS")
    far = inputs.series("VXVCLS")
    value: Optional[float] = None
    if None not in (near, far):
        if far <= 0:
            # A non-positive denominator is missing data, not a ratio.
            inputs.mark_missing("VXVCLS")
        else:
            value = finite(near / far)
    return inputs.point("vix_term_structure", raw_value=value, score_value=value)


def _broad_dollar(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("DTWEXBGS")
    return inputs.point("broad_dollar_index", raw_value=value, score_value=value)


def _fx_realized_volatility(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    current = inputs.series("DTWEXBGS")
    value: Optional[float] = None
    if current is not None:
        source = series["DTWEXBGS"]
        window = source.trailing(when, FX_VOLATILITY_WINDOW + 1)
        if len(window) < FX_VOLATILITY_WINDOW + 1:
            inputs.mark_missing("dtwexbgs_log_returns")
        else:
            inputs.mark_satisfied()
            inputs.note_window(source, FX_VOLATILITY_WINDOW + 1)
            levels = [item[1] for item in window]
            if min(levels) <= 0:
                value = None
            else:
                returns = [
                    math.log(levels[position] / levels[position - 1])
                    for position in range(1, len(levels))
                ]
                deviation = population_std(returns)
                value = (
                    finite(deviation * math.sqrt(TRADING_DAYS_PER_YEAR))
                    if deviation is not None
                    else None
                )
    return inputs.point(
        "fx_realized_volatility_63d", raw_value=value, score_value=value
    )


def _wti_oil(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("DCOILWTICO")
    return inputs.point("wti_oil", raw_value=value, score_value=value)


def _oil_volatility_deviation(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    current = inputs.series("OVXCLS")
    value: Optional[float] = None
    if current is not None:
        source = series["OVXCLS"]
        window = source.trailing(when, LONG_MEDIAN_WINDOW)
        if len(window) < LONG_MEDIAN_WINDOW:
            inputs.mark_missing("ovxcls_252_median")
        else:
            inputs.mark_satisfied()
            inputs.note_window(source, LONG_MEDIAN_WINDOW)
            centre = rolling_median([item[1] for item in window])
            if centre is not None:
                value = finite(max(current - centre, 0.0))
    return inputs.point(
        "oil_volatility_deviation", raw_value=value, score_value=value
    )


def _natural_gas(when, index, series, etfs, **_extra):
    inputs = _Inputs(when, series, etfs)
    value = inputs.series("DHHNGSP")
    return inputs.point("natural_gas", raw_value=value, score_value=value)


_HANDLERS: Mapping[str, Callable[..., FactorPoint]] = {
    "fed_net_liquidity": _fed_net_liquidity,
    "bank_reserves": _bank_reserves,
    "net_liquidity_momentum_13w": _net_liquidity_momentum,
    "tga_deviation_52w": _tga_deviation,
    "on_rrp_buffer_risk": _on_rrp_buffer_risk,
    "collateral_repo_friction": _absolute_spread(
        "collateral_repo_friction", "SOFR", "OBFR"
    ),
    "corridor_friction_1": _absolute_spread("corridor_friction_1", "SOFR", "IORB"),
    "corridor_friction_2": _absolute_spread(
        "corridor_friction_2", "SOFR", "RRPONTSYAWARD"
    ),
    "effr_iorb_spread": _absolute_spread("effr_iorb_spread", "EFFR", "IORB"),
    "cp_tbill_spread": _cp_tbill_spread,
    "funding_fragmentation_21d": _funding_fragmentation,
    "term_premium_30y_10y": _spread("term_premium_30y_10y", "DGS30", "DGS10"),
    "rate_volatility_10y_21d": _rate_volatility,
    "curve_curvature_abs": _curve_curvature,
    "real_rate_level": _real_rate_level,
    "real_curve_10y_5y": _spread("real_curve_10y_5y", "DFII10", "DFII5"),
    "breakeven_10y": _breakeven,
    "nfci": _nfci,
    "hy_credit": _relative_return_factor("hy_credit"),
    "ig_credit": _relative_return_factor("ig_credit"),
    "regional_banks_vs_spy": _relative_return_factor("regional_banks_vs_spy"),
    "vix": _vix,
    "vix_term_structure": _vix_term_structure,
    "risk_vs_safe": _relative_return_factor("risk_vs_safe"),
    "high_beta_preference": _relative_return_factor("high_beta_preference"),
    "broad_dollar_index": _broad_dollar,
    "fx_realized_volatility_63d": _fx_realized_volatility,
    "wti_oil": _wti_oil,
    "oil_volatility_deviation": _oil_volatility_deviation,
    "natural_gas": _natural_gas,
}


def handler_coverage() -> tuple[str, ...]:
    """Factor ids that have no computation. Must always be empty."""

    return tuple(
        factor.factor_id for factor in FACTORS if factor.factor_id not in _HANDLERS
    )


__all__ = [
    "AsOfSeries",
    "DerivedGrid",
    "clip",
    "compute_factor_points",
    "handler_coverage",
    "population_std",
    "relative_return",
    "rolling_median",
]
