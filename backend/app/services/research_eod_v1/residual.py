"""Frozen two-factor residual momentum for family D only."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.research_eod_v1.constants import (
    RESIDUAL_FIT_WINDOW,
    RESIDUAL_HISTORY_MIN,
    RESIDUAL_SUM_END,
    RESIDUAL_SUM_START,
)
from app.services.research_eod_v1.mathutil import ordinary_least_squares
from app.services.research_eod_v1.series import SecuritySeries, daily_returns


@dataclass(frozen=True)
class ResidualMomentum:
    raw: float | None
    status: str
    version: str = "us-eod-residual-v1"


def _aligned_returns(series: SecuritySeries) -> np.ndarray:
    return daily_returns(series.tri)


def _industry_basket_returns(
    panel: dict[str, SecuritySeries],
    security_id: str,
    industry_id: str | None,
) -> np.ndarray | None:
    if not industry_id:
        return None
    target = panel[security_id]
    peers = [
        daily_returns(item.tri)
        for sid, item in panel.items()
        if sid != security_id
        and item.industry_id == industry_id
        and item.asset_track == target.asset_track
        and len(item.dates) == len(target.dates)
        and item.dates == target.dates
    ]
    if len(peers) < 2:
        return None
    stacked = np.vstack(peers)
    with np.errstate(all="ignore"):
        return np.nanmean(stacked, axis=0)


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
    if len(series.dates) < RESIDUAL_HISTORY_MIN:
        return ResidualMomentum(None, "SHORT_HISTORY")
    if series.dates != benchmark.dates:
        # Require a caller-aligned calendar; silent date joins invent future rows.
        n = min(len(series.dates), len(benchmark.dates))
        if n < RESIDUAL_HISTORY_MIN or series.dates[-n:] != benchmark.dates[-n:]:
            return ResidualMomentum(None, "UNALIGNED_BENCHMARK")
    r_i = _aligned_returns(series)
    r_m = _aligned_returns(benchmark)
    r_g = _industry_basket_returns(panel, series.security_id, series.industry_id)
    t = len(series.dates) - 1
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
