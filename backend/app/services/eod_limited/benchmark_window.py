"""The benchmark sessions the all-market scorers read, and the publish gate's check of them.

A stock score reads SPY over a window, not only on the target session:

* the D-family residual (``residual.residual_raw_momentum``) regresses each stock
  on SPY over the dates both series have and counts its window back from the
  last of them. A missing SPY bar inside the window fails every stock's
  residual; one among the skipped sessions after it moves every window one
  session earlier without any error.
* the relative-momentum M hook (``full_market_tuning.prepare_full_market_context``)
  needs SPY's close on every session of its longest window. With one missing,
  every stock keeps its original M.

The sessions come from the functions those scorers use
(``residual_benchmark_sessions``, ``momentum_grid``), so the gate neither asks for
a bar the scorers never read (an older gap is harmless) nor skips one they do.

Two other SPY reads add nothing. G (industry modes only) subtracts SPY's own
63-bar return from every peer mean before ranking, so only that return's
existence matters, and its two bars lie inside the residual window. The theme
statistics read SPY on sessions inside the same window and show a missing
return as missing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import math
from typing import Any, Sequence

from app.services.market_calendar import prior_trading_sessions
from app.services.research_eod_v1.constants import PROFILES, RESIDUAL_HISTORY_MIN
from app.services.research_eod_v1.residual import residual_benchmark_sessions
from app.services.research_eod_v1.series import SecuritySeries

from .full_market_tuning import TuningPolicy, momentum_grid
from .inference import SESSION_PANEL_BARS

MISSING_BAR = "missing_bar"
INVALID_PRICE = "invalid_price"
INSUFFICIENT_HISTORY = "insufficient_history"


@dataclass(frozen=True)
class BenchmarkRead:
    """Sessions on which one scorer reads the benchmark."""

    reader: str
    sessions: tuple[date, ...]
    # The price array read on each session; None when only the bar's presence counts.
    field: str | None
    # The reader skips a partial bar, so one counts as missing.
    complete_bar: bool = False


@dataclass(frozen=True)
class BenchmarkWindow:
    session: date
    reads: tuple[BenchmarkRead, ...]
    # Benchmark bars the residual grid of a stock with full history must reach...
    min_history: int
    # ...counted from here: the scorers keep no older bar of a stock.
    lookback_start: date

    @property
    def sessions(self) -> tuple[date, ...]:
        return tuple(sorted({day for read in self.reads for day in read.sessions}))

    def describe(self) -> dict[str, Any]:
        sessions = self.sessions
        return {
            "first_session": sessions[0].isoformat(),
            "last_session": sessions[-1].isoformat(),
            "session_count": len(sessions),
            "min_history": self.min_history,
        }


def benchmark_window(session: date, *, horizons: Sequence[str], policy: TuningPolicy) -> BenchmarkWindow:
    """What the scorers read of the benchmark for ``session`` under ``policy``.

    The M hook reads the benchmark only for a profile that blends M; every
    policy so far blends at least balanced and aggressive.
    """
    relative_momentum = any(policy.m_alpha.get(profile) for profile in PROFILES)
    return _window(session, tuple(dict.fromkeys(horizons)), relative_momentum)


@lru_cache(maxsize=8)
def _window(session: date, horizons: tuple[str, ...], relative_momentum: bool) -> BenchmarkWindow:
    priced, trailing = residual_benchmark_sessions(session)
    reads = [
        BenchmarkRead("D_residual", priced, "tri"),
        BenchmarkRead("D_residual_grid_end", trailing, None),
    ]
    if relative_momentum:
        reads.extend(
            BenchmarkRead(f"M_relative_{horizon}", momentum_grid(session, horizon), "close", complete_bar=True)
            for horizon in horizons
        )
    lookback = prior_trading_sessions(session, SESSION_PANEL_BARS - 1)
    return BenchmarkWindow(session, tuple(reads), RESIDUAL_HISTORY_MIN, lookback[0] if lookback else session)


def _positive(value: Any) -> bool:
    number = float(value)
    return math.isfinite(number) and number > 0


def window_problems(benchmark: str, series: SecuritySeries, window: BenchmarkWindow) -> list[dict[str, Any]]:
    """Why ``series`` cannot serve as the benchmark over ``window``; empty when it can.

    Three problems are told apart: a required session without a bar inside the
    series' history, a used price that is not a positive finite number, and a
    history that starts after a required session or is shorter than the
    residual needs.
    """
    # The scorers read the close-price-return view (``panel.prepare_limited_panel``).
    view = series.with_close_price_return()
    position = {day: index for index, day in enumerate(view.dates)}
    first = view.dates[0] if len(view.dates) else None
    partial = view.bar_partial
    missing: set[date] = set()
    before_history: set[date] = set()
    invalid: dict[date, set[str]] = {}
    for read in window.reads:
        values = None if read.field is None else getattr(view, read.field)
        for day in read.sessions:
            index = position.get(day)
            if index is None:
                (before_history if first is None or day < first else missing).add(day)
            elif read.complete_bar and partial is not None and bool(partial[index]):
                missing.add(day)
            elif values is not None and not _positive(values[index]):
                invalid.setdefault(day, set()).add(str(read.field))
    history = sum(1 for day in view.dates if window.lookback_start <= day <= window.session)
    problems: list[dict[str, Any]] = []
    if missing:
        problems.append({"benchmark": benchmark, "problem": MISSING_BAR, "sessions": _iso(missing)})
    if invalid:
        problems.append({
            "benchmark": benchmark, "problem": INVALID_PRICE, "sessions": _iso(invalid),
            "fields": sorted(set().union(*invalid.values())),
        })
    if before_history or history < window.min_history:
        problems.append({
            "benchmark": benchmark, "problem": INSUFFICIENT_HISTORY, "sessions": _iso(before_history),
            "history": history, "min_history": window.min_history,
        })
    return problems


def _iso(days: Any) -> list[str]:
    return [day.isoformat() for day in sorted(days)]


__all__ = [
    "INSUFFICIENT_HISTORY",
    "INVALID_PRICE",
    "MISSING_BAR",
    "BenchmarkRead",
    "BenchmarkWindow",
    "benchmark_window",
    "window_problems",
]
