"""Bounded full-market tuning, applied before the existing PRICE_ONLY rescore.

Evidence boundary:
* G1's stock-only ATR reference follows the PR184 historical experiment.
* Existing factor weights, T/V/R, D63skip5, B/C setups and strict entry survive.
* The 0/10/20% M blend is a new bounded design choice, NOT a backtest winner.

No ticker/theme whitelist, labels, network, files or historical database. The
caller supplies the entire as-of panel; one context serves every theme/family.
ETF rows are deliberately unchanged. All public score contributions are still
computed by apply_price_only_track, not patched into the final score afterwards.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
import hashlib
import json
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Mapping, Sequence

TUNING_VERSION = "full-market-bounded-m-v1"
MIN_RAW_PRICE_USD = 5.0
MIN_ADV20_PROXY_USD = 20_000_000.0
MIN_HISTORY_SESSIONS = 252
MIN_REFERENCE_N = 30
M_DELTA_CAP = 10.0
M_ALPHA = MappingProxyType({"conservative": 0.0, "balanced": 0.10, "aggressive": 0.20})
WINDOWS = MappingProxyType({"short": (20, 63), "mid": (63, 126), "long": (126, 252)})
TUNED_FAMILIES = frozenset({"A_trend_quality", "D_residual_momentum"})
ATR_REFERENCE_UNAVAILABLE = "REFERENCE_UNIVERSE_INSUFFICIENT"


@dataclass(frozen=True)
class StockInput:
    security_id: str
    session: date
    asset_track: str
    raw_close: float | None
    adv20: float | None
    atr_pct: float | None
    history_sessions: int
    currently_tradable: bool
    halted: bool
    zero_volume: bool
    complete_bar: bool
    venue_eligible: bool
    source_available: bool
    closes: Mapping[date, float]


@dataclass(frozen=True)
class TuningContext:
    session: date
    horizon: str
    stock_ids: frozenset[str]
    reference_ids: tuple[str, ...]
    median_atr_pct: float | None
    momentum_reference_ids: tuple[str, ...]
    momentum_percentiles: Mapping[str, float]
    relative_returns: Mapping[str, tuple[float, float]]
    windows: tuple[int, int]
    window_starts: tuple[date, date]
    benchmark_status: str
    digest: str

    def summary(self) -> dict[str, Any]:
        return {
            "version": TUNING_VERSION,
            "session": self.session.isoformat(),
            "horizon": self.horizon,
            "reference_scope": "all_eligible_stocks_in_input_no_theme_filter",
            "stock_input_n": len(self.stock_ids),
            "atr_reference_n": len(self.reference_ids),
            "atr_reference_median": self.median_atr_pct,
            "momentum_reference_n": len(self.momentum_reference_ids),
            "momentum_windows": list(self.windows),
            "window_starts": [day.isoformat() for day in self.window_starts],
            "benchmark": "SPY",
            "benchmark_status": self.benchmark_status,
            "context_hash": self.digest,
            "empirically_optimized_m_blend": False,
        }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def reference_eligible(item: StockInput) -> bool:
    if item.asset_track != "stock":
        return False
    if not (item.complete_bar and item.venue_eligible and item.source_available):
        return False
    if not item.currently_tradable or item.halted or item.zero_volume:
        return False
    if type(item.history_sessions) is not int or item.history_sessions < MIN_HISTORY_SESSIONS:
        return False
    price, adv, atr = map(_number, (item.raw_close, item.adv20, item.atr_pct))
    return bool(price is not None and price >= MIN_RAW_PRICE_USD
                and adv is not None and adv >= MIN_ADV20_PROXY_USD
                and atr is not None and atr > 0)


def _window_return(closes: Mapping[date, float], grid: Sequence[date]) -> float | None:
    # Every exchange session is required, not merely the two endpoints. Missing
    # bars never extend the window, forward-fill or turn into zero return.
    values = [_number(closes.get(day)) for day in grid]
    if any(value is None or value <= 0 for value in values):
        return None
    return values[-1] / values[0] - 1.0  # type: ignore[operator]


def _percentiles(values: Mapping[str, float]) -> dict[str, float]:
    """Average-rank percentile; ties are neutral and ordering is deterministic."""
    if len(values) < MIN_REFERENCE_N:
        return {}
    ordered = sorted(values.values())
    scale = 100.0 / (len(ordered) - 1)
    return {
        sid: 0.5 * (bisect_left(ordered, value) + bisect_right(ordered, value) - 1) * scale
        for sid, value in values.items()
    }


def build_context(
    inputs: Sequence[StockInput], *, session: date, horizon: str,
    calendar: Sequence[date], benchmark_closes: Mapping[date, float] | None,
) -> TuningContext:
    """Pure context builder. Calendar is the exchange grid through signal T.

    The new M reference uses the SAME evaluable members for both windows.
    Adding ETFs or modifying theme membership cannot change stock percentiles.
    Subtracting common SPY returns labels relative strength; within one window
    it does not, by itself, change the rank of stock price returns.
    """
    if horizon not in WINDOWS:
        raise ValueError(f"unknown horizon: {horizon}")
    grid = [day for day in calendar if day <= session]
    if not grid or grid[-1] != session or grid != sorted(set(grid)):
        raise ValueError("strictly increasing exchange calendar must end at signal session")
    windows = WINDOWS[horizon]
    if len(grid) < max(windows) + 1:
        raise ValueError("calendar lacks the exact window endpoints")
    grids = tuple(tuple(grid[-(n + 1):]) for n in windows)
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item.security_id, str) or not item.security_id or item.security_id in seen:
            raise ValueError("unique, case-preserving security_id required")
        seen.add(item.security_id)
        if item.session != session:
            raise ValueError("input session mismatch")
    stocks = [item for item in inputs if item.asset_track == "stock"]
    reference = sorted((item for item in stocks if reference_eligible(item)), key=lambda item: item.security_id)
    reference_ids = tuple(item.security_id for item in reference)
    median = None
    if len(reference) >= MIN_REFERENCE_N:
        atrs = sorted(float(item.atr_pct) for item in reference)
        median = atrs[len(atrs) // 2]  # PR184 convention, not the averaged median.
    benchmark_returns = None
    if benchmark_closes is not None:
        candidate = tuple(_window_return(benchmark_closes, part) for part in grids)
        if all(value is not None for value in candidate):
            benchmark_returns = candidate
    relative: dict[str, tuple[float, float]] = {}
    if benchmark_returns is not None:
        for item in reference:
            returns = tuple(_window_return(item.closes, part) for part in grids)
            if all(value is not None for value in returns):
                relative[item.security_id] = tuple(
                    value - bench for value, bench in zip(returns, benchmark_returns)
                )  # type: ignore[assignment,operator]
    ranks = tuple(_percentiles({sid: pair[i] for sid, pair in relative.items()}) for i in range(2))
    momentum = {sid: 0.5 * ranks[0][sid] + 0.5 * ranks[1][sid]
                for sid in sorted(relative) if sid in ranks[0] and sid in ranks[1]}
    # Hash only the information actually used by this context. Future bars and
    # excluded funds are intentionally absent. This is not a raw-file checksum.
    digest_payload = {
        "version": TUNING_VERSION, "session": session.isoformat(), "horizon": horizon,
        "atr": [(item.security_id, float(item.atr_pct)) for item in reference],
        "relative": sorted(relative.items()), "benchmark_returns": benchmark_returns,
    }
    digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return TuningContext(
        session, horizon, frozenset(item.security_id for item in stocks), reference_ids,
        median, tuple(sorted(relative)), MappingProxyType(momentum), MappingProxyType(relative),
        windows, (grids[0][0], grids[1][0]),
        "ok" if benchmark_returns is not None else "unavailable_or_incomplete", digest,
    )


@lru_cache(maxsize=16)
def _exchange_grid(session: date, maximum_window: int) -> tuple[date, ...]:
    from app.services.research_eod_v1.calendar_asof import shift_sessions
    reverse = [session]
    for _ in range(maximum_window):
        reverse.append(shift_sessions(reverse[-1], -1))
    return tuple(reversed(reverse))


def prepare_full_market_context(
    raws: Mapping[str, Any], panel: Mapping[str, Any], *, session: date, horizon: str,
) -> TuningContext:
    """Adapter for production RawComponents/SecuritySeries, once per view.

    References come from ALL provided raws, never the current theme, family,
    score-filtered winners or a post-Top-K list. The worker owns directory-wide
    coverage validation; this function cannot magically expand a supplied subset.
    """
    from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
    from app.services.research_eod_v1.membership import has_complete_session_bar, source_is_available
    from app.services.research_eod_v1.venue import classify_venue

    if horizon not in WINDOWS:
        raise ValueError(f"unknown horizon: {horizon}")
    grid = _exchange_grid(session, max(WINDOWS[horizon]))
    wanted = frozenset(grid)
    as_of = eod_evaluation_as_of(session)

    def closes_of(series: Any) -> dict[date, float]:
        out: dict[date, float] = {}
        partial = series.bar_partial
        for index, day in enumerate(series.dates):
            if day not in wanted:
                continue
            if day in out:
                raise ValueError("duplicate session in price series")
            if partial is not None and bool(partial[index]):
                continue
            value = _number(series.close[index])
            if value is not None and value > 0:
                out[day] = value
        return out

    samples: list[StockInput] = []
    for sid, raw in raws.items():
        series = panel.get(sid)
        if series is None or raw.asset_track != "stock" or series.asset_track != "stock":
            continue
        facts = StockInput(
            sid, session, "stock", raw.raw_close, raw.adv20, raw.atr_pct,
            raw.history_sessions, bool(raw.currently_tradable), bool(raw.halted),
            bool(raw.zero_volume), has_complete_session_bar(series, session),
            classify_venue(dict(raw.venue_metadata)).eligible,
            source_is_available(series, as_of), {},
        )
        prices = closes_of(series) if reference_eligible(facts) else {}
        samples.append(StockInput(**{**facts.__dict__, "closes": prices}))
    spy = panel.get("SPY")
    benchmark = None
    if spy is not None and source_is_available(spy, as_of) and has_complete_session_bar(spy, session):
        benchmark = closes_of(spy)
    return build_context(samples, session=session, horizon=horizon, calendar=grid, benchmark_closes=benchmark)


def _with_new_common_reasons(row: Mapping[str, Any], common: Sequence[str]) -> dict[str, Any]:
    gates = row["gate_results"]
    previous_common = set(gates["common"])
    setup = tuple(gates.get("setup") or ())
    # Never erase an identically named setup-specific or external reason.
    reasons = [reason for reason in row.get("rejection_reasons", ()) if reason not in previous_common]
    reasons.extend(common)
    reasons.extend(setup)
    return {**row, "gate_results": {**gates, "common": tuple(common)},
            "rejection_reasons": list(dict.fromkeys(reasons))}


def tune_snapshot(
    payload: Mapping[str, Any], context: TuningContext, *, registry: Mapping[str, Any],
    profile: str, horizon: str,
) -> dict[str, Any]:
    """Adjust raw snapshot factors/gates, then let the existing scorer decide.

    Does NOT set eligible/watch, certify liquidity, remove EXTENDED, or relax
    B/C setups. Missing old M stays missing, even if a new percentile exists.
    """
    if profile not in M_ALPHA or horizon != context.horizon:
        raise ValueError("tuning context/profile mismatch")
    if payload.get("session_date") != context.session.isoformat():
        raise ValueError("snapshot/context session mismatch")
    profile_cfg = registry["profiles"][profile]
    cap = _number(profile_cfg["atr_absolute_cap_pct"])
    multiplier = _number(profile_cfg["atr_sector_median_multiplier"])
    if cap is None or cap <= 0 or multiplier is None or multiplier <= 0:
        raise ValueError("finite positive existing ATR policy required")
    threshold = None if context.median_atr_pct is None else min(cap, multiplier * context.median_atr_pct)
    rows = []
    for source in payload.get("rows", ()):
        if not isinstance(source, Mapping):
            raise ValueError("snapshot row must be a mapping")
        sid = source.get("security_id")
        if source.get("stock_or_etf_track") != "stock" or sid not in context.stock_ids:
            rows.append(dict(source))
            continue
        previous = source.get("full_market_tuning")
        if previous is not None:
            if (previous.get("version"), previous.get("context_hash"), previous.get("profile")) != (
                TUNING_VERSION, context.digest, profile,
            ):
                raise ValueError("refusing to stack different tuning policies")
            rows.append(dict(source))
            continue
        row = dict(source)
        factors = source.get("factors") or {}
        gates = source.get("gate_results")
        # A source-level reject without factors is not repaired by an ATR tweak.
        if not factors or not isinstance(gates, Mapping) or "common" not in gates:
            rows.append(row)
            continue
        atr = _number(source.get("atr_pct"))
        new_common = [reason for reason in gates["common"]
                      if reason not in {"HIGH_ATR", ATR_REFERENCE_UNAVAILABLE}]
        if threshold is None:
            new_common.append(ATR_REFERENCE_UNAVAILABLE)
        elif atr is None or atr <= 0:
            new_common.append("INCOMPLETE_COMMON_INPUTS")
        elif atr > threshold:
            new_common.append("HIGH_ATR")
        row = _with_new_common_reasons(row, new_common)
        checks = dict(source.get("common_gate_checks") or {})
        checks["atr"] = None if threshold is None or atr is None else atr <= threshold
        row.update(
            sector_median_atr_pct=context.median_atr_pct,
            atr_reference_n=len(context.reference_ids),
            atr_reference_source="stock:full_market_liquid_G1",
            atr_reference_policy="G1_stock_reference",
            atr_threshold_pct=threshold, common_gate_checks=checks,
        )
        old_m = _number(factors.get("M"))
        alpha = M_ALPHA[profile] if source.get("algorithm_id") in TUNED_FAMILIES else 0.0
        target = context.momentum_percentiles.get(sid)
        new_m, delta = old_m, 0.0
        reason = "profile_or_family_unchanged"
        if alpha:
            if old_m is None or not 0 <= old_m <= 100:
                reason = "original_M_missing_or_invalid"
            elif target is None:
                reason = "new_momentum_unavailable_keep_original"
            else:
                delta = max(-M_DELTA_CAP, min(M_DELTA_CAP, alpha * (target - old_m)))
                new_m = max(0.0, min(100.0, old_m + delta))
                row["factors"] = {**factors, "M": new_m}
                reason = "bounded_M_blend_applied"
        row["full_market_tuning"] = {
            "version": TUNING_VERSION, "context_hash": context.digest,
            "profile": profile, "horizon": horizon,
            "M_original": old_m, "M_window_target": target, "M_final": new_m,
            "alpha": alpha, "M_delta": delta, "M_delta_cap": M_DELTA_CAP,
            "windows": list(context.windows), "reference_n": len(context.momentum_reference_ids),
            "reason": reason, "weights_changed": False,
            "empirically_optimized_m_blend": False,
        }
        rows.append(row)
    return {**payload, "rows": rows, "full_market_tuning": context.summary()}
