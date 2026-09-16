"""Frozen two-factor residual momentum for family D only."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from app.services.research_eod_v1.constants import (
    RESIDUAL_FIT_WINDOW,
    RESIDUAL_HISTORY_MIN,
    RESIDUAL_SUM_END,
    RESIDUAL_SUM_START,
)
from app.services.research_eod_v1.mathutil import ordinary_least_squares
from app.services.research_eod_v1.series import SecuritySeries


@dataclass(frozen=True)
class ResidualMomentum:
    raw: float | None
    status: str
    version: str = "us-eod-residual-v1"


def _date_index(series: SecuritySeries) -> dict[date, int]:
    return {session: index for index, session in enumerate(series.dates)}


def _session_returns_on_grid(series: SecuritySeries, grid: list[date]) -> np.ndarray:
    """Adjacent-session returns on an explicit date grid. Gaps stay NaN."""

    idx = _date_index(series)
    out = np.full(len(grid), np.nan)
    prices = series.tri
    for i, day in enumerate(grid):
        if i == 0 or day not in idx:
            continue
        prev = grid[i - 1]
        if prev not in idx:
            continue
        cur_i = idx[day]
        prev_i = idx[prev]
        if cur_i != prev_i + 1:
            continue
        prev_px = float(prices[prev_i])
        cur_px = float(prices[cur_i])
        if np.isfinite(prev_px) and np.isfinite(cur_px) and prev_px > 0:
            out[i] = cur_px / prev_px - 1.0
    return out


def _common_session_grid(*series_list: SecuritySeries) -> list[date]:
    sets = [set(item.dates) for item in series_list if item is not None]
    if not sets:
        return []
    common = sets[0]
    for extra in sets[1:]:
        common &= extra
    return sorted(common)


def _series_complete_on_grid(series: SecuritySeries, grid: list[date]) -> bool:
    idx = _date_index(series)
    for day in grid:
        if day not in idx:
            return False
        i = idx[day]
        if not np.isfinite(series.tri[i]) or float(series.tri[i]) <= 0:
            return False
    return True


def _industry_basket_returns(
    panel: dict[str, SecuritySeries],
    security_id: str,
    industry_id: str | None,
    grid: list[date],
) -> np.ndarray | None:
    if not industry_id:
        return None
    target = panel[security_id]
    peers: list[np.ndarray] = []
    for sid, item in panel.items():
        if sid == security_id:
            continue
        if item.industry_id != industry_id:
            continue
        if item.asset_track != target.asset_track:
            continue
        if not _series_complete_on_grid(item, grid):
            continue
        peers.append(_session_returns_on_grid(item, grid))
    if len(peers) < 2:
        return None
    stacked = np.vstack(peers)
    with np.errstate(all="ignore"):
        valid = np.isfinite(stacked)
        out = np.full(stacked.shape[1], np.nan)
        counts = valid.sum(axis=0)
        if np.any(counts):
            filled = np.where(valid, stacked, 0.0).sum(axis=0)
            mask = counts > 0
            out[mask] = filled[mask] / counts[mask]
        return out


def residual_raw_momentum(
    series: SecuritySeries,
    market: SecuritySeries,
    panel: dict[str, SecuritySeries],
    *,
    spy_residual_allowed: bool = True,
    matched_market: SecuritySeries | None = None,
) -> ResidualMomentum:
    if series.asset_track == "etf" and not spy_residual_allowed:
        return ResidualMomentum(None, "INSUFFICIENT_MATCHED_BENCHMARK")
    benchmark = matched_market or market
    if benchmark.security_id == series.security_id:
        return ResidualMomentum(None, "INSUFFICIENT_MATCHED_BENCHMARK")
    grid = _common_session_grid(series, benchmark)
    if len(grid) < RESIDUAL_HISTORY_MIN:
        if len(series.dates) < RESIDUAL_HISTORY_MIN:
            return ResidualMomentum(None, "SHORT_HISTORY")
        return ResidualMomentum(None, "UNALIGNED_BENCHMARK")
    if not _series_complete_on_grid(series, grid) or not _series_complete_on_grid(benchmark, grid):
        return ResidualMomentum(None, "UNALIGNED_BENCHMARK")
    r_i = _session_returns_on_grid(series, grid)
    r_m = _session_returns_on_grid(benchmark, grid)
    r_g = _industry_basket_returns(panel, series.security_id, series.industry_id, grid)
    t = len(grid) - 1
    start = t - RESIDUAL_SUM_START
    end = t - RESIDUAL_SUM_END
    if start < RESIDUAL_FIT_WINDOW + 2 or end <= start:
        return ResidualMomentum(None, "SHORT_HISTORY")
    residuals: list[float] = []
    for s in range(start, end + 1):
        fit_slice = slice(s - RESIDUAL_FIT_WINDOW, s)
        y = r_i[fit_slice]
        m = r_m[fit_slice]
        if r_g is None:
            beta = ordinary_least_squares(y, m)
            if beta is None:
                return ResidualMomentum(None, "SINGULAR_OR_THIN_REGRESSION")
            alpha, b_m = float(beta[0]), float(beta[1])
            if not np.isfinite(r_i[s]) or not np.isfinite(r_m[s]):
                return ResidualMomentum(None, "MISSING_DAY_RETURN")
            residuals.append(r_i[s] - alpha - b_m * r_m[s])
            continue
        g = r_g[fit_slice]
        orth_beta = ordinary_least_squares(g, m)
        if orth_beta is None:
            return ResidualMomentum(None, "SINGULAR_OR_THIN_REGRESSION")
        g_orth = g - orth_beta[0] - orth_beta[1] * m
        beta = ordinary_least_squares(y, np.column_stack([m, g_orth]))
        if beta is None:
            return ResidualMomentum(None, "SINGULAR_OR_THIN_REGRESSION")
        alpha, b_m, b_g = (float(beta[0]), float(beta[1]), float(beta[2]))
        if not np.isfinite(r_i[s]) or not np.isfinite(r_m[s]) or not np.isfinite(r_g[s]):
            return ResidualMomentum(None, "MISSING_DAY_RETURN")
        g_s_orth = r_g[s] - orth_beta[0] - orth_beta[1] * r_m[s]
        residuals.append(r_i[s] - alpha - b_m * r_m[s] - b_g * g_s_orth)
    arr = np.asarray(residuals, dtype=float)
    if arr.size != 63 or not np.isfinite(arr).all():
        return ResidualMomentum(None, "RESIDUAL_WINDOW_INVALID")
    denom = float(arr.std(ddof=1) * np.sqrt(63.0))
    if denom == 0 or not np.isfinite(denom):
        return ResidualMomentum(None, "ZERO_RESIDUAL_VOL")
    return ResidualMomentum(float(arr.sum() / denom), "OK")
