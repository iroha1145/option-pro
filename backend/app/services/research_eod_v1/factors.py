"""Raw 8-family components. Cross-sectional ranks happen in snapshot.py."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np

from app.services.research_eod_v1.constants import (
    ATR_PERIOD,
    BASE_WINDOWS,
    SWING_SPAN,
    TOUCH_MIN_GAP,
)
from app.services.research_eod_v1.mathutil import atr_sma_at, clip100, clv_at, max_drawdown_magnitude, sma_at
from app.services.research_eod_v1.pivots import find_confirmed_pivots, known_before, structure_anchor
from app.services.research_eod_v1.residual import ResidualMomentum, residual_raw_momentum
from app.services.research_eod_v1.series import SecuritySeries, daily_returns


@dataclass
class RawComponents:
    security_id: str
    ticker_at_signal: str
    session_date: date
    asset_track: str
    industry_id: str | None
    parent_industry_id: str | None
    theme_ids: tuple[str, ...]
    history_sessions: int
    raw_close: float | None
    last_close: float | None
    adv20: float | None
    atr: float | None
    atr_pct: float | None
    sma20: float | None
    sma50: float | None
    sma200: float | None
    sma50_prev20: float | None
    slope50: float | None
    er63: float | None
    ma_state: float | None
    t_direction_ok: bool
    m63: float | None
    m126_skip21: float | None
    m252_skip21: float | None
    momentum_raw_blend: dict[str, float | None]
    residual: ResidualMomentum
    structure_score: float | None
    structure_label: str
    pivots: dict[str, Any]
    known_support: float | None
    known_resistance: float | None
    planned_invalidation: float | None
    unresolved_upthrust: bool
    structure_invalidated: bool
    lh_ll_unrepaired: bool
    b_score: float | None
    b_status: str
    frozen_setup: dict[str, Any] | None
    p_score: float | None
    p_depth: float | None
    p_anchor: float | None
    rebound: bool
    clv: float | None
    rvol: float | None
    imbalance20: float | None
    clv5: float | None
    down_ratio: float | None
    sigma20: float | None
    gap_tail252: float | None
    max_drawdown63: float | None
    industry_return63: float | None
    above_sma50: bool | None
    extension_atr: float | None
    venue_metadata: dict[str, Any] = field(default_factory=dict)
    volume_session_scope: str = "unknown"
    turnover_is_proxy: bool = True
    missing_reasons: tuple[str, ...] = ()


def _log_ret_vol(tri: np.ndarray, index: int, window: int) -> float | None:
    if index < window:
        return None
    rets = daily_returns(tri)[index - window + 1 : index + 1]
    rets = rets[np.isfinite(rets)]
    if rets.size < window - 1:
        return None
    return float(rets.std(ddof=1)) if rets.size > 1 else None


def _momentum(tri: np.ndarray, end: int, start: int) -> float | None:
    if start < 0 or end >= len(tri) or start >= end:
        return None
    if tri[start] <= 0 or not np.isfinite(tri[start]) or not np.isfinite(tri[end]):
        return None
    return float(tri[end] / tri[start] - 1.0)


def _pattern_adjust(open_: np.ndarray, high: np.ndarray, low: np.ndarray, close: np.ndarray, t: int) -> float:
    total = 0.0
    for i in range(max(1, t - 2), t + 1):
        o, h, l, c = open_[i], high[i], low[i], close[i]
        po, pc = open_[i - 1], close[i - 1]
        rng = h - l
        if rng <= 0:
            continue
        body = abs(c - o)
        upper = h - max(o, c)
        lower = min(o, c) - l
        prev_body = abs(pc - po)
        age = t - i
        scale = 3.0 * math.exp(-age / 3.0)
        if pc < po and c > o and c >= po and o <= pc and body > prev_body * 1.05:
            total += scale
        elif pc > po and c < o and c <= po and o >= pc and body > prev_body * 1.05:
            total -= scale
        window_lo = float(np.min(low[max(0, i - 10) : i + 1]))
        window_hi = float(np.max(high[max(0, i - 10) : i + 1]))
        if body > 0 and lower >= body * 2 and upper <= body * 0.6 and l <= window_lo * 1.01:
            total += scale
        elif body > 0 and upper >= body * 2 and lower <= body * 0.6 and h >= window_hi * 0.99:
            total -= scale
    return max(-5.0, min(5.0, total))


def _detect_upthrust(
    high: np.ndarray,
    close: np.ndarray,
    dates: list[date],
    highs,
    atr: float | None,
    t: int,
) -> bool:
    start = max(0, t - 4)
    for i in range(start, t + 1):
        known = known_before(highs, dates[i])
        if not known:
            continue
        level = known[-1].price
        buffer = max(level * 0.0025, (atr or 0.0) * 0.1)
        if high[i] > level + buffer and close[i] < level:
            if i == t or not (close[i + 1 : t + 1].size >= 2 and np.all(close[max(i + 1, t - 1) : t + 1] > level + buffer)):
                # Unresolved unless two later completed closes reclaimed the frozen level.
                later = close[i + 1 : t + 1]
                if later.size >= 2 and later[-1] > level + buffer and later[-2] > level + buffer:
                    continue
                return True
    return False


def _base_geometry(
    series: SecuritySeries,
    t: int,
    *,
    min_sessions: int,
    max_sessions: int,
    min_touches: int,
) -> tuple[float | None, str, dict[str, Any] | None]:
    """Platforms use T-1 and earlier only. Missing compute is null; none seen is 0."""

    if t < min_sessions + 5:
        return None, "insufficient_history", None
    end = t - 1
    allowed = [w for w in BASE_WINDOWS if min_sessions <= w <= max_sessions]
    best: dict[str, Any] | None = None
    best_score: float | None = None
    atr_t1 = atr_sma_at(series.high, series.low, series.close, end, ATR_PERIOD)
    if atr_t1 is None or atr_t1 <= 0:
        return None, "insufficient_atr", None
    for window in allowed:
        start = end - window + 1
        if start < 0:
            continue
        highs = series.high[start : end + 1]
        lows = series.low[start : end + 1]
        resistance = float(np.max(highs))
        support = float(np.min(lows))
        width = resistance - support
        if width <= 0:
            continue
        width_atr = width / atr_t1
        touch_idx = []
        for offset, high in enumerate(highs):
            if abs(high - resistance) <= max(resistance * 0.0025, atr_t1 * 0.5):
                abs_i = start + offset
                if not touch_idx or abs_i - touch_idx[-1] >= TOUCH_MIN_GAP:
                    touch_idx.append(abs_i)
        if len(touch_idx) < min_touches:
            continue
        duration = window
        score = (
            0.40 * (clip100(100.0 * (1.0 - width_atr / 12.0)) or 0.0)
            + 0.30 * 100.0 * min(len(touch_idx) / 4.0, 1.0)
            + 0.30 * 100.0 * min(duration / 60.0, 1.0)
        )
        candidate = {
            "window": window,
            "support": support,
            "resistance": resistance,
            "resistance_high": resistance,
            "width_atr": width_atr,
            "touches": len(touch_idx),
            "duration": duration,
            "formed_through": series.dates[end].isoformat(),
            "setup_id": f"{series.security_id}:{series.dates[end].isoformat()}:w{window}",
        }
        if best_score is None or score > best_score:
            best_score = score
            best = candidate
    if best is None:
        return 0.0, "no_base_observed", None
    return float(best_score), "observed", best


def extract_raw(
    series: SecuritySeries,
    *,
    market: SecuritySeries | None,
    panel: dict[str, SecuritySeries],
    horizon: str,
    momentum_blend: tuple[float, float, float],
    sector_gates: dict[str, Any],
    spy_residual_allowed: bool = True,
    matched_market: SecuritySeries | None = None,
) -> RawComponents:
    t = len(series.dates) - 1
    session = series.dates[t]
    missing: list[str] = []
    close, high, low, open_ = series.close, series.high, series.low, series.open
    sma20 = sma_at(close, t, 20)
    sma50 = sma_at(close, t, 50)
    sma200 = sma_at(close, t, 200)
    sma50_prev20 = sma_at(close, t - 20, 50) if t >= 20 else None
    sigma60 = _log_ret_vol(series.tri, t, 60)
    if sma50 and sma50_prev20 and sma50_prev20 > 0 and sigma60 and sigma60 > 0:
        slope50 = math.log(sma50 / sma50_prev20) / (sigma60 * math.sqrt(20.0))
    else:
        slope50 = None
        missing.append("slope50")
    if t >= 63:
        path = np.abs(np.diff(close[t - 63 : t + 1]))
        denom = float(path.sum())
        er63 = float((close[t] - close[t - 63]) / denom) if denom > 0 else None
        if er63 is None:
            missing.append("er63")
    else:
        er63 = None
        missing.append("er63")
    flags = []
    if sma50 is not None:
        flags.append(1.0 if close[t] > sma50 else 0.0)
    else:
        flags.append(None)
    if sma50 is not None and sma200 is not None:
        flags.append(1.0 if sma50 > sma200 else 0.0)
    else:
        flags.append(None)
    if sma50 is not None and sma50_prev20 is not None:
        flags.append(1.0 if sma50 > sma50_prev20 else 0.0)
    else:
        flags.append(None)
    ma_state = None if any(item is None for item in flags) else (100.0 / 3.0) * sum(flags)  # type: ignore[arg-type]
    t_direction_ok = bool(
        sma50 is not None
        and sma200 is not None
        and sma50_prev20 is not None
        and close[t] > sma50 > sma200
        and sma50 > sma50_prev20
    )
    m63 = _momentum(series.tri, t, t - 63)
    m126 = _momentum(series.tri, t - 21, t - 126)
    m252 = _momentum(series.tri, t - 21, t - 252)
    blend = {
        "short": momentum_blend,
        "mid": momentum_blend,
        "long": momentum_blend,
    }[horizon]
    raw_parts = (m63, m126, m252)
    if any(part is None for part in raw_parts):
        momentum_raw = None
        missing.append("momentum_blend")
    else:
        momentum_raw = sum(w * p for w, p in zip(blend, raw_parts))  # type: ignore[arg-type]
    residual = residual_raw_momentum(
        series,
        market or series,
        panel,
        spy_residual_allowed=spy_residual_allowed,
        matched_market=matched_market,
    )
    highs, lows = find_confirmed_pivots(high, low, series.dates, span=SWING_SPAN, as_of_index=t)
    structure_score, structure_label = structure_anchor(highs, lows)
    if structure_score is not None:
        structure_score = max(0.0, min(100.0, structure_score + _pattern_adjust(open_, high, low, close, t)))
    known_support = lows[-1].price if lows else None
    known_resistance = highs[-1].price if highs else None
    atr = atr_sma_at(high, low, close, t, ATR_PERIOD)
    atr_t1 = atr_sma_at(high, low, close, t - 1, ATR_PERIOD) if t >= 1 else None
    atr_pct = None if atr is None or close[t] <= 0 else 100.0 * atr / close[t]
    unresolved = _detect_upthrust(high, close, series.dates, highs, atr_t1 or atr, t)
    lh_ll = structure_label == "LH+LL"
    planned = known_support
    invalidated = bool(planned is not None and close[t] < planned)
    b_score, b_status, setup = _base_geometry(
        series,
        t,
        min_sessions=int(sector_gates.get("base_min_sessions", 20)),
        max_sessions=int(sector_gates.get("base_max_sessions", 80)),
        min_touches=int(sector_gates.get("base_min_distinct_touches", 2)),
    )
    anchor = known_support if known_support is not None else sma20
    p_score = None
    depth = None
    rebound = False
    clv = clv_at(high, low, close, t)
    if t >= 10 and atr_t1 and atr_t1 > 0 and anchor is not None:
        d_anchor = abs(close[t] - anchor) / atr_t1
        depth = (float(np.max(high[t - 10 : t])) - float(np.min(low[t - 4 : t + 1]))) / atr_t1
        rebound = bool(t >= 1 and close[t] > close[t - 1] and clv is not None and clv >= 0.55)
        rebound_score = 100.0 if rebound else 0.0
        p_score = (
            0.50 * (clip100(100.0 * (1.0 - d_anchor / 2.0)) or 0.0)
            + 0.25 * (clip100(100.0 * (1.0 - abs(depth - 1.5) / 2.0)) or 0.0)
            + 0.25 * rebound_score
        )
    elif t < 10:
        missing.append("pullback_window")
    dv = series.dollar_volume
    if t >= 20 and np.isfinite(dv[t - 20 : t]).all() and float(np.mean(dv[t - 20 : t])) > 0:
        rvol = float(dv[t] / np.mean(dv[t - 20 : t]))
        adv20 = float(np.mean(dv[t - 20 : t + 1])) if np.isfinite(dv[t - 20 : t + 1]).all() else float(np.mean(dv[t - 20 : t]))
    else:
        rvol = None
        adv20 = None
        missing.append("adv20")
    if t >= 20:
        signs = np.sign(np.diff(close[t - 20 : t + 1]))
        dollars = dv[t - 19 : t + 1]
        denom = float(np.nansum(np.abs(dollars)))
        imbalance20 = float(np.nansum(signs * dollars) / denom) if denom > 0 else None
    else:
        imbalance20 = None
    clvs = [clv_at(high, low, close, i) for i in range(max(0, t - 4), t + 1)]
    clv5 = None if any(item is None for item in clvs) or not clvs else float(np.mean(clvs))  # type: ignore[arg-type]
    down_ratio = None
    if t >= 30:
        down_idx = [i for i in range(t - 10, t) if close[i] < close[i - 1]]
        if down_idx:
            down_ratio = float(np.mean(dv[down_idx]) / np.mean(dv[t - 30 : t]))
        else:
            down_ratio = None
    sigma20 = _log_ret_vol(series.tri, t, 20)
    gap_tail = None
    if t >= 252:
        gaps = np.abs(open_[t - 251 : t + 1] / close[t - 252 : t] - 1.0)
        gaps = gaps[np.isfinite(gaps)]
        if gaps.size >= 20:
            k = max(1, int(math.ceil(0.05 * gaps.size)))
            gap_tail = float(np.mean(np.sort(gaps)[-k:]))
    dd63 = max_drawdown_magnitude(series.tri[max(0, t - 62) : t + 1]) if t >= 62 else None
    industry_return63 = None
    if market is not None:
        _ = _momentum(market.tri, t, t - 63)
    extension = None
    if sma20 is not None and atr_t1 and atr_t1 > 0:
        extension = max(0.0, (close[t] - sma20) / atr_t1)
    above = None if sma50 is None else bool(close[t] > sma50)
    return RawComponents(
        security_id=series.security_id,
        ticker_at_signal=series.ticker_at_signal,
        session_date=session,
        asset_track=series.asset_track,
        industry_id=series.industry_id,
        parent_industry_id=series.parent_industry_id,
        theme_ids=series.theme_ids,
        history_sessions=t + 1,
        raw_close=float(series.raw_close[t]) if np.isfinite(series.raw_close[t]) else None,
        last_close=float(close[t]) if np.isfinite(close[t]) else None,
        adv20=adv20,
        atr=atr,
        atr_pct=atr_pct,
        sma20=sma20,
        sma50=sma50,
        sma200=sma200,
        sma50_prev20=sma50_prev20,
        slope50=slope50,
        er63=er63,
        ma_state=ma_state,
        t_direction_ok=t_direction_ok,
        m63=m63,
        m126_skip21=m126,
        m252_skip21=m252,
        momentum_raw_blend={"short": None, "mid": None, "long": None, horizon: momentum_raw},
        residual=residual,
        structure_score=structure_score,
        structure_label=structure_label,
        pivots={
            "highs": [
                {"pivot_at": p.pivot_at.isoformat(), "confirmed_at": p.confirmed_at.isoformat(), "price": p.price}
                for p in highs[-6:]
            ],
            "lows": [
                {"pivot_at": p.pivot_at.isoformat(), "confirmed_at": p.confirmed_at.isoformat(), "price": p.price}
                for p in lows[-6:]
            ],
        },
        known_support=known_support,
        known_resistance=known_resistance,
        planned_invalidation=planned,
        unresolved_upthrust=unresolved,
        structure_invalidated=invalidated,
        lh_ll_unrepaired=lh_ll,
        b_score=b_score,
        b_status=b_status,
        frozen_setup=setup,
        p_score=p_score,
        p_depth=depth,
        p_anchor=anchor,
        rebound=rebound,
        clv=clv,
        rvol=rvol,
        imbalance20=imbalance20,
        clv5=clv5,
        down_ratio=down_ratio,
        sigma20=sigma20,
        gap_tail252=gap_tail,
        max_drawdown63=dd63,
        industry_return63=industry_return63,
        above_sma50=above,
        extension_atr=extension,
        venue_metadata=dict(series.venue_metadata),
        volume_session_scope=series.volume_session_scope,
        turnover_is_proxy=series.turnover_is_proxy,
        missing_reasons=tuple(missing),
    )


def assemble_unranked_factors(raw: RawComponents, residual_for_d: bool) -> dict[str, float | None]:
    """Local pieces that do not need a cross-section. Ranked pieces stay None."""

    er_term = None if raw.er63 is None else clip100(50.0 + 50.0 * raw.er63)
    t_local = raw.ma_state
    s = raw.structure_score
    b = raw.b_score
    p = raw.p_score
    return {
        "T": None if raw.slope50 is None or er_term is None or t_local is None else None,
        "T_er": er_term,
        "T_ma": t_local,
        "M": None,
        "S": s,
        "B": b,
        "P": p,
        "V": None,
        "R": None,
        "G": None,
        "residual_raw": raw.residual.raw if residual_for_d else None,
    }
