"""Raw 8-family components. Cross-sectional ranks happen in snapshot.py."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import numpy as np

from app.services.research_eod_v1.constants import (
    ATR_PERIOD,
    BASE_WINDOWS,
    BREAKOUT_TRACK_MAX_SESSIONS,
    PLATFORM_EXPIRE_MULTIPLE,
    PLATFORM_FAIL_CONFIRM_SESSIONS,
    PLATFORM_REPAIR_WINDOW,
    SWING_SPAN,
    TOUCH_MIN_GAP,
)
from app.services.research_eod_v1.mathutil import atr_sma_at, clip100, clv_at, max_drawdown_magnitude, sma_at
from app.services.research_eod_v1.pivots import find_confirmed_pivots, known_before, structure_anchor
from app.services.research_eod_v1.residual import ResidualMomentum, residual_raw_momentum
from app.services.research_eod_v1.series import SecuritySeries, daily_returns, session_is_halted


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
    breakout_track: dict[str, Any] | None
    halted: bool
    zero_volume: bool
    currently_tradable: bool
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
    ma_distance_atr: float | None
    platform_distance_atr: float | None
    invalidation_distance_atr: float | None
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


def _platform_event(setup: dict[str, Any], session: date, kind: str, note: str = "") -> dict[str, Any]:
    version = int(setup.get("version") or 1)
    return {
        "setup_id": setup.get("setup_id"),
        "session": session.isoformat(),
        "kind": kind,
        "version": version,
        "note": note,
    }


def _close_below_support(series: SecuritySeries, index: int, support: float) -> bool:
    price = float(series.close[index])
    return bool(np.isfinite(price) and price < support)


def _similar_platform_box(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("setup_id") and left.get("setup_id") == right.get("setup_id"):
        return True
    l_res = float(left["resistance_high"])
    r_res = float(right["resistance_high"])
    l_sup = float(left["support"])
    r_sup = float(right["support"])
    res_scale = max(abs(l_res), abs(r_res), 1e-9)
    sup_scale = max(abs(l_sup), abs(r_sup), 1e-9)
    return abs(l_res - r_res) / res_scale < 0.02 and abs(l_sup - r_sup) / sup_scale < 0.02


def _advance_live_platform(
    item: dict[str, Any],
    series: SecuritySeries,
    eval_t: int,
    max_sessions: int,
    events: list[dict[str, Any]],
) -> dict[str, Any] | None:
    session = series.dates[eval_t]
    support = float(item["support"])
    resistance = float(item["resistance_high"])
    width = max(resistance - support, 1e-9)
    close = float(series.close[eval_t])
    formed_index = series.dates.index(date.fromisoformat(str(item["formed_at"])))
    age = eval_t - formed_index
    fail_streak = int(item.get("fail_streak") or 0)
    repair_streak = int(item.get("repair_streak") or 0)
    if _close_below_support(series, eval_t, support):
        fail_streak += 1
        repair_streak = 0
    else:
        fail_streak = 0
        if support <= close <= resistance:
            repair_streak += 1
        else:
            repair_streak = 0
    item = dict(item)
    item["fail_streak"] = fail_streak
    item["repair_streak"] = repair_streak
    if item.get("lifecycle") != "failed" and fail_streak >= PLATFORM_FAIL_CONFIRM_SESSIONS:
        item["failed_at"] = session.isoformat()
        item["lifecycle"] = "failed"
        item["version"] = int(item.get("version") or 1) + 1
        item["fail_streak"] = 0
        item["repair_streak"] = 0
        events.append(_platform_event(item, session, "failed", "close_below_support"))
        item["events"] = list(events)
        return item
    too_old = age > int(max_sessions) * PLATFORM_EXPIRE_MULTIPLE
    left_range = np.isfinite(close) and (
        close > resistance + 2.0 * width or close < support - 2.0 * width
    )
    if item.get("lifecycle") != "failed" and too_old:
        item["expired_at"] = session.isoformat()
        item["lifecycle"] = "expired"
        item["version"] = int(item.get("version") or 1) + 1
        reason = "max_age" if not left_range else "left_range"
        events.append(_platform_event(item, session, "expired", reason))
        item["events"] = list(events)
        return None
    if item.get("lifecycle") == "failed":
        if repair_streak >= PLATFORM_FAIL_CONFIRM_SESSIONS:
            item["repaired_at"] = session.isoformat()
            item["lifecycle"] = "active"
            item["failed_at"] = None
            item["version"] = int(item.get("version") or 1) + 1
            item["fail_streak"] = 0
            item["repair_streak"] = 0
            events.append(_platform_event(item, session, "repaired", "back_in_range"))
            item["events"] = list(events)
            return item
        failed_at = date.fromisoformat(str(item["failed_at"]))
        failed_index = series.dates.index(failed_at)
        if eval_t - failed_index >= PLATFORM_REPAIR_WINDOW:
            events.append(_platform_event(item, session, "terminated", "repair_window_elapsed"))
            return None
    return item


def resolve_frozen_setup(
    series: SecuritySeries,
    t: int,
    *,
    min_sessions: int,
    max_sessions: int,
    min_touches: int,
) -> tuple[float | None, str, dict[str, Any] | None]:
    """Freeze currently valid platforms. Distinct live bases can coexist."""

    min_eval = int(min_sessions) + 5
    if t < min_eval:
        return None, "insufficient_history", None
    events: list[dict[str, Any]] = []
    live: list[dict[str, Any]] = []
    for eval_t in range(min_eval, t + 1):
        advanced: list[dict[str, Any]] = []
        for item in live:
            updated = _advance_live_platform(item, series, eval_t, max_sessions, events)
            if updated is not None:
                advanced.append(updated)
        live = advanced
        score, _status, setup = _base_geometry(
            series,
            eval_t,
            min_sessions=min_sessions,
            max_sessions=max_sessions,
            min_touches=min_touches,
        )
        if setup is None:
            continue
        formed_index = eval_t - 1
        candidate = dict(setup)
        candidate["formed_at"] = series.dates[formed_index].isoformat()
        candidate["known_at"] = candidate["formed_at"]
        candidate["confirmed_at"] = None
        candidate["failed_at"] = None
        candidate["repaired_at"] = None
        candidate["expired_at"] = None
        candidate["first_cross_at"] = None
        candidate["lifecycle"] = "active"
        candidate["version"] = 1
        candidate["fail_streak"] = 0
        candidate["repair_streak"] = 0
        candidate["_score"] = float(score)
        candidate["setup_id"] = (
            f"{series.security_id}:{candidate['formed_at']}:r{round(float(candidate['resistance_high']), 4)}"
        )
        if any(_similar_platform_box(candidate, item) for item in live):
            continue
        events.append(_platform_event(candidate, series.dates[formed_index], "formed"))
        candidate["events"] = list(events)
        live.append(candidate)
    if not live:
        return _base_geometry(
            series,
            t,
            min_sessions=min_sessions,
            max_sessions=max_sessions,
            min_touches=min_touches,
        )
    active = [item for item in live if item.get("lifecycle") == "active"]
    primary = max(active, key=lambda item: float(item.get("_score") or 0.0)) if active else live[-1]
    primary = dict(primary)
    primary["events"] = list(events)
    primary["concurrent_setups"] = [
        {
            "setup_id": item.get("setup_id"),
            "lifecycle": item.get("lifecycle"),
            "formed_at": item.get("formed_at"),
            "resistance_high": item.get("resistance_high"),
            "support": item.get("support"),
        }
        for item in live
    ]
    status = "observed" if primary.get("lifecycle") != "failed" else "failed"
    return primary.get("_score"), status, primary


def _breakout_track(
    series: SecuritySeries,
    t: int,
    setup: dict[str, Any] | None,
    sector_gates: dict[str, Any],
) -> dict[str, Any] | None:
    """Freeze the first close through resistance and track it for five sessions."""

    if setup is None:
        return None
    resistance = float(setup["resistance_high"])
    close = series.close
    known_at = setup.get("known_at")
    start_i = 1
    if known_at:
        known = date.fromisoformat(str(known_at))
        if known in series.dates:
            start_i = max(1, series.dates.index(known) + 1)
    first: int | None = None
    first_buffer = 0.0
    frozen_cross = setup.get("first_cross_at")
    if frozen_cross:
        cross_day = date.fromisoformat(str(frozen_cross))
        if cross_day in series.dates:
            first = series.dates.index(cross_day)
            atr_prev = atr_sma_at(series.high, series.low, series.close, first - 1, ATR_PERIOD) if first >= 1 else None
            first_buffer = max(
                float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * float(close[first]),
                float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr_prev or 0.0),
            )
    if first is None:
        for i in range(start_i, t + 1):
            atr_prev = atr_sma_at(series.high, series.low, series.close, i - 1, ATR_PERIOD)
            price = float(close[i])
            buffer = max(
                float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * price,
                float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr_prev or 0.0),
            )
            if price > resistance + buffer:
                first = i
                first_buffer = buffer
                break
    if first is None:
        return {
            "through": False,
            "still_through": False,
            "first_cross_date": None,
            "first_day_rvol": None,
            "first_day_clv": None,
            "consecutive_closes": 0,
            "current_consecutive_closes": 0,
            "max_consecutive": 0,
            "max_consecutive_closes": 0,
            "days_since_first": None,
            "tracking_expired": False,
        }
    atr_t1 = atr_sma_at(series.high, series.low, series.close, t - 1, ATR_PERIOD) if t >= 1 else None
    buffer_t = max(
        float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * float(close[t]),
        float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr_t1 or 0.0),
    )
    run = 0
    max_run = 0
    for i in range(first, t + 1):
        atr_prev = atr_sma_at(series.high, series.low, series.close, i - 1, ATR_PERIOD) if i >= 1 else None
        buffer = max(
            float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * float(close[i]),
            float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr_prev or 0.0),
        )
        if float(close[i]) > resistance + buffer:
            run += 1
            max_run = max(max_run, run)
        else:
            run = 0
    dv = series.dollar_volume
    if first >= 20 and np.isfinite(dv[first - 20 : first]).all() and float(np.mean(dv[first - 20 : first])) > 0:
        first_rvol = float(dv[first] / np.mean(dv[first - 20 : first]))
    else:
        first_rvol = None
    first_clv = clv_at(series.high, series.low, series.close, first)
    days_since = t - first
    confirmed_at = None
    streak = 0
    for i in range(first, t + 1):
        atr_prev = atr_sma_at(series.high, series.low, series.close, i - 1, ATR_PERIOD) if i >= 1 else None
        buffer = max(
            float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * float(close[i]),
            float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr_prev or 0.0),
        )
        if float(close[i]) > resistance + buffer:
            streak += 1
            if streak == 2 and confirmed_at is None:
                confirmed_at = series.dates[i].isoformat()
        else:
            streak = 0
    return {
        "through": True,
        "still_through": bool(float(close[t]) > resistance + buffer_t),
        "first_cross_date": series.dates[first].isoformat(),
        "first_day_rvol": first_rvol,
        "first_day_clv": first_clv,
        "first_day_buffer": first_buffer,
        "consecutive_closes": run,
        "current_consecutive_closes": run,
        "max_consecutive": max_run,
        "max_consecutive_closes": max_run,
        "days_since_first": days_since,
        "tracking_expired": days_since >= BREAKOUT_TRACK_MAX_SESSIONS,
        "setup_id": setup.get("setup_id"),
        "confirmed_at": confirmed_at,
        "frozen_resistance": resistance,
    }


def _theme_setup_from_gates(
    series: SecuritySeries,
    t: int,
    sector_gates: dict[str, Any],
    close: np.ndarray,
    atr_t1: float | None,
) -> dict[str, Any]:
    """Theme-gate B/setup/breakout fields. Shared residual and pivots stay elsewhere."""

    b_score, b_status, setup = resolve_frozen_setup(
        series,
        t,
        min_sessions=int(sector_gates.get("base_min_sessions", 20)),
        max_sessions=int(sector_gates.get("base_max_sessions", 80)),
        min_touches=int(sector_gates.get("base_min_distinct_touches", 2)),
    )
    breakout_track = _breakout_track(series, t, setup, sector_gates)
    if setup is not None and breakout_track is not None:
        setup = dict(setup)
        if breakout_track.get("first_cross_date") and not setup.get("first_cross_at"):
            setup["first_cross_at"] = breakout_track.get("first_cross_date")
        elif breakout_track.get("first_cross_date"):
            setup["first_cross_at"] = setup.get("first_cross_at") or breakout_track.get("first_cross_date")
        setup["confirmed_at"] = breakout_track.get("confirmed_at")
        if breakout_track.get("through") and not breakout_track.get("still_through"):
            setup["breakout_failed_at"] = series.dates[t].isoformat()
    platform_distance = None
    if setup is not None and atr_t1 and atr_t1 > 0:
        platform_distance = float((close[t] - float(setup["resistance_high"])) / atr_t1)
    return {
        "b_score": b_score,
        "b_status": b_status,
        "frozen_setup": setup,
        "breakout_track": breakout_track,
        "platform_distance_atr": platform_distance,
    }


def apply_sector_gates(
    raw: RawComponents,
    series: SecuritySeries,
    sector_gates: dict[str, Any],
) -> RawComponents:
    """Re-derive theme-gate fields on a shared extract. Does not recompute residual."""

    t = len(series.dates) - 1
    atr_t1 = atr_sma_at(series.high, series.low, series.close, t - 1, ATR_PERIOD) if t >= 1 else None
    gated = _theme_setup_from_gates(series, t, sector_gates, series.close, atr_t1)
    return replace(raw, **gated)


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
    gated = _theme_setup_from_gates(series, t, sector_gates, close, atr_t1)
    b_score, b_status, setup, breakout_track = (
        gated["b_score"],
        gated["b_status"],
        gated["frozen_setup"],
        gated["breakout_track"],
    )
    volume_t = float(series.volume[t])
    volume_missing = not np.isfinite(volume_t)
    zero_volume = (not volume_missing) and volume_t <= 0
    halted = session_is_halted(series, t)
    currently_tradable = not (halted or zero_volume)
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
        adv20 = float(np.mean(dv[t - 20 : t]))
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
    ma_distance = None
    if sma20 is not None and atr_t1 and atr_t1 > 0:
        ma_distance = float((close[t] - sma20) / atr_t1)
    platform_distance = gated["platform_distance_atr"]
    invalidation_distance = None
    if planned is not None and atr_t1 and atr_t1 > 0:
        invalidation_distance = float((close[t] - planned) / atr_t1)
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
        breakout_track=breakout_track,
        halted=halted,
        zero_volume=zero_volume,
        currently_tradable=currently_tradable,
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
        ma_distance_atr=ma_distance,
        platform_distance_atr=platform_distance,
        invalidation_distance_atr=invalidation_distance,
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
