"""Bounded full-market tuning, applied before the existing PRICE_ONLY rescore (v1.5).

v1.5 keeps the v1.4 hook and changes four things, each backed by the offline
comparison in ``research/option_pro_us_eod_v1/return_pack/full_market_v1_5/``
(2,773 liquid US stocks, 2022-01 to 2026-06, top-20 every five sessions):

* The new momentum target is *volatility-scaled* relative return with a 21-session
  skip on the 126/252-day legs (Barroso & Santa-Clara 2015 style): return minus the
  SPY return over the same window, divided by the stock's own realised volatility
  over that window. Raw or residual momentum without scaling ranked lower.
* The stability factor R (low volatility / low gap / low drawdown percentile) is
  neutralised to 50 for balanced and aggressive: as a ranking reward it selected
  the worst forward returns of every factor tested. Conservative keeps it.
* The HIGH_ATR multiplier on the liquid-stock reference median is raised for
  balanced (1.75 -> 2.5) and aggressive (2.5 -> 3.0). The 1.75x cut removed the
  best-performing ATR bucket; absolute caps are unchanged.
* EXTENDED becomes an *entry state* instead of a hard rejection for balanced and
  aggressive: the row stays visible in the observation list tagged ``extended``
  and can never be ``eligible`` (``apply_entry_states`` downgrades it to
  ``watch``). Excluding extended names had no forward-return benefit at 20 or 63
  sessions. Conservative keeps every v1.4 rule and serves as the control profile.

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

TUNING_VERSION = "full-market-v1.5"
MIN_RAW_PRICE_USD = 5.0
MIN_ADV20_PROXY_USD = 20_000_000.0
MIN_HISTORY_SESSIONS = 252
MIN_REFERENCE_N = 30
M_DELTA_CAP = 30.0
# Conservative stays on the v1.4 definition (no blend, R active, registry ATR multiplier,
# EXTENDED strict): it is the control profile users can compare against.
M_ALPHA = MappingProxyType({"conservative": 0.0, "balanced": 0.60, "aggressive": 0.85})
# (sessions, skip): the return runs from T-sessions to T-skip; volatility uses the
# full window. Legs of 126+ sessions skip the latest month (short-term reversal).
WINDOWS = MappingProxyType({
    "short": ((63, 0), (126, 21)),
    "mid": ((126, 21), (252, 21)),
    "long": ((126, 21), (252, 21)),
})
WINDOW_WEIGHTS = MappingProxyType({"short": (0.5, 0.5), "mid": (0.6, 0.4), "long": (0.4, 0.6)})
# Realised-volatility floor (in return units over the window) so a flat synthetic
# series cannot divide by zero; it never binds for a real stock.
SIGMA_FLOOR = 0.01
# Overrides of the registry's atr_sector_median_multiplier; None keeps the registry value.
ATR_MULTIPLIER = MappingProxyType({"conservative": None, "balanced": 2.5, "aggressive": 3.0})
R_NEUTRAL_PROFILES = frozenset({"balanced", "aggressive"})
EXTENDED_STATE_PROFILES = frozenset({"balanced", "aggressive"})
R_NEUTRAL_VALUE = 50.0
EXTENDED_REASON = "EXTENDED"
EXTENDED_STATE = "extended"
EXTENDED_WAIT_REASON = "EXTENDED_ENTRY_WAIT"
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
    windows: tuple[tuple[int, int], ...]
    window_starts: tuple[date, ...]
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
            "momentum_windows": [list(item) for item in self.windows],
            "momentum_window_weights": list(WINDOW_WEIGHTS[self.horizon]),
            "momentum_basis": "volatility_scaled_relative_price_return",
            "window_starts": [day.isoformat() for day in self.window_starts],
            "benchmark": "SPY",
            "benchmark_status": self.benchmark_status,
            "context_hash": self.digest,
            "atr_multiplier_overrides": {k: v for k, v in ATR_MULTIPLIER.items()},
            "r_neutral_profiles": sorted(R_NEUTRAL_PROFILES),
            "extended_state_profiles": sorted(EXTENDED_STATE_PROFILES),
            "extended_is_entry_state": True,
            "empirically_optimized_m_blend": False,
        }


def finite_number(value: Any) -> float | None:
    """A finite real number as ``float``; booleans, strings and NaN/inf are None."""
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
    price, adv, atr = map(finite_number, (item.raw_close, item.adv20, item.atr_pct))
    return bool(price is not None and price >= MIN_RAW_PRICE_USD
                and adv is not None and adv >= MIN_ADV20_PROXY_USD
                and atr is not None and atr > 0)


def _window_stats(closes: Mapping[date, float], grid: Sequence[date], skip: int) -> tuple[float, float] | None:
    """(return from grid[0] to grid[-1-skip], realised volatility over the whole grid).

    Every exchange session is required, not merely the endpoints. Missing bars
    never extend the window, forward-fill or turn into zero return.
    """
    values = [finite_number(closes.get(day)) for day in grid]
    if any(value is None or value <= 0 for value in values):
        return None
    end = values[-1 - skip] if skip else values[-1]
    ret = end / values[0] - 1.0  # type: ignore[operator]
    logs = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]  # type: ignore[arg-type]
    mean = sum(logs) / len(logs)
    var = sum((x - mean) ** 2 for x in logs) / max(len(logs) - 1, 1)
    sigma = math.sqrt(var) * math.sqrt(len(logs))
    return ret, sigma


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

    The momentum reference uses the SAME evaluable members for both windows.
    Adding ETFs or modifying theme membership cannot change stock percentiles.
    The target for stock i and window (n, skip) is
        (r_i - r_SPY) / max(sigma_i * sqrt(n), SIGMA_FLOOR)
    so a large move earned with little volatility outranks the same move earned
    with a wild path; the benchmark subtraction labels it relative strength.
    """
    if horizon not in WINDOWS:
        raise ValueError(f"unknown horizon: {horizon}")
    grid = [day for day in calendar if day <= session]
    if not grid or grid[-1] != session or grid != sorted(set(grid)):
        raise ValueError("strictly increasing exchange calendar must end at signal session")
    windows = WINDOWS[horizon]
    if len(grid) < max(n for n, _skip in windows) + 1:
        raise ValueError("calendar lacks the exact window endpoints")
    grids = tuple(tuple(grid[-(n + 1):]) for n, _skip in windows)
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
        candidate = tuple(_window_stats(benchmark_closes, part, skip) for part, (_n, skip) in zip(grids, windows))
        if all(value is not None for value in candidate):
            benchmark_returns = tuple(value[0] for value in candidate)  # type: ignore[index]
    relative: dict[str, tuple[float, float]] = {}
    if benchmark_returns is not None:
        for item in reference:
            stats = tuple(_window_stats(item.closes, part, skip) for part, (_n, skip) in zip(grids, windows))
            if all(value is not None for value in stats):
                relative[item.security_id] = tuple(
                    (value[0] - bench) / max(value[1], SIGMA_FLOOR)  # type: ignore[index]
                    for value, bench in zip(stats, benchmark_returns)
                )  # type: ignore[assignment]
    ranks = tuple(_percentiles({sid: pair[i] for sid, pair in relative.items()}) for i in range(len(windows)))
    weights = WINDOW_WEIGHTS[horizon]
    momentum = {
        sid: sum(w * rank[sid] for w, rank in zip(weights, ranks))
        for sid in sorted(relative) if all(sid in rank for rank in ranks)
    }
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
        tuple(windows), tuple(part[0] for part in grids),
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
    grid = _exchange_grid(session, max(n for n, _skip in WINDOWS[horizon]))
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
            value = finite_number(series.close[index])
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


def atr_threshold(registry: Mapping[str, Any], profile: str, median_atr_pct: float | None) -> tuple[float | None, float, str]:
    """(threshold in percent points or None, multiplier used, multiplier source)."""
    profile_cfg = registry["profiles"][profile]
    cap = finite_number(profile_cfg["atr_absolute_cap_pct"])
    registry_multiplier = finite_number(profile_cfg["atr_sector_median_multiplier"])
    if cap is None or cap <= 0 or registry_multiplier is None or registry_multiplier <= 0:
        raise ValueError("finite positive existing ATR policy required")
    override = ATR_MULTIPLIER.get(profile)
    multiplier = float(override) if override is not None else registry_multiplier
    source = "v1.5_override" if override is not None else "registry"
    if median_atr_pct is None:
        return None, multiplier, source
    return min(cap, multiplier * median_atr_pct), multiplier, source


def tune_snapshot(
    payload: Mapping[str, Any], context: TuningContext, *, registry: Mapping[str, Any],
    profile: str, horizon: str,
) -> dict[str, Any]:
    """Adjust raw snapshot factors/gates, then let the existing scorer decide.

    Does NOT set eligible/watch, certify liquidity, or relax B/C setups. Missing
    old M stays missing, even if a new percentile exists. EXTENDED is moved from
    the common gate list to ``entry_state`` so the row is scored and visible;
    ``apply_entry_states`` keeps it out of the strict (eligible) list.
    """
    if profile not in M_ALPHA or horizon != context.horizon:
        raise ValueError("tuning context/profile mismatch")
    if payload.get("session_date") != context.session.isoformat():
        raise ValueError("snapshot/context session mismatch")
    threshold, multiplier, multiplier_source = atr_threshold(registry, profile, context.median_atr_pct)
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
        atr = finite_number(source.get("atr_pct"))
        relax_extended = profile in EXTENDED_STATE_PROFILES
        dropped = {"HIGH_ATR", ATR_REFERENCE_UNAVAILABLE} | ({EXTENDED_REASON} if relax_extended else set())
        new_common = [reason for reason in gates["common"] if reason not in dropped]
        entry_state = EXTENDED_STATE if relax_extended and EXTENDED_REASON in gates["common"] else "ok"
        if threshold is None:
            new_common.append(ATR_REFERENCE_UNAVAILABLE)
        elif atr is None or atr <= 0:
            new_common.append("INCOMPLETE_COMMON_INPUTS")
        elif atr > threshold:
            new_common.append("HIGH_ATR")
        row = _with_new_common_reasons(row, new_common)
        checks = dict(source.get("common_gate_checks") or {})
        checks["atr"] = None if threshold is None or atr is None else bool(atr <= threshold)
        row.update(
            sector_median_atr_pct=context.median_atr_pct,
            atr_reference_n=len(context.reference_ids),
            atr_reference_source="stock:full_market_liquid_G1",
            atr_reference_policy="G1_stock_reference",
            atr_threshold_pct=threshold, atr_multiplier=multiplier, atr_multiplier_source=multiplier_source,
            common_gate_checks=checks,
            entry_state=entry_state,
            entry_gate_reasons=[EXTENDED_REASON] if entry_state == EXTENDED_STATE else [],
        )
        new_factors = dict(factors)
        old_r = finite_number(factors.get("R"))
        r_neutralized = False
        if profile in R_NEUTRAL_PROFILES and old_r is not None:
            new_factors["R"] = R_NEUTRAL_VALUE
            r_neutralized = True
        old_m = finite_number(factors.get("M"))
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
                new_factors["M"] = new_m
                reason = "bounded_M_blend_applied"
        if new_factors != factors:
            row["factors"] = new_factors
        row["full_market_tuning"] = {
            "version": TUNING_VERSION, "context_hash": context.digest,
            "profile": profile, "horizon": horizon,
            "M_original": old_m, "M_window_target": target, "M_final": new_m,
            "alpha": alpha, "M_delta": delta, "M_delta_cap": M_DELTA_CAP,
            "windows": [list(item) for item in context.windows], "reference_n": len(context.momentum_reference_ids),
            "reason": reason,
            "R_original": old_r, "R_neutralized": r_neutralized,
            "entry_state": entry_state,
            "atr_multiplier": multiplier, "atr_multiplier_source": multiplier_source,
            "weights_changed": False,
            "empirically_optimized_m_blend": False,
        }
        rows.append(row)
    return {**payload, "rows": rows, "full_market_tuning": context.summary()}


def apply_entry_states(payload: Mapping[str, Any]) -> dict[str, Any]:
    """After the existing rescore: an ``extended`` row can be watched, never eligible.

    The strict list keeps the old entry rule; the observation list shows the row
    with the ``EXTENDED_ENTRY_WAIT`` tag instead of hiding it.
    """
    rows = []
    for source in payload.get("rows", ()):
        if not isinstance(source, Mapping):
            raise ValueError("snapshot row must be a mapping")
        row = dict(source)
        if row.get("entry_state") == EXTENDED_STATE and row.get("status") in {"eligible", "watch"}:
            reasons = [str(item) for item in (row.get("rejection_reasons") or ())]
            if EXTENDED_WAIT_REASON not in reasons:
                reasons.append(EXTENDED_WAIT_REASON)
            row["rejection_reasons"] = reasons
            row["status"] = "watch"
        rows.append(row)
    return {**payload, "rows": rows}
