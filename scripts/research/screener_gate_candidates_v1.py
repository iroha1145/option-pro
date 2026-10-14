"""Frozen stock-gate candidates G1–G3. No market I/O, labels, or champion search.

This module is an independent research function pack. It does not change
production weights, score formulas, D residual windows, or B/C pattern
confirmation. ETF rules are not rewritten here.

HIGH_ATR uses percent units: atr_pct=4.0 means 4 percent, not 0.04.
The shared stock reference median uses the production upper-median
convention ``sorted(values)[n // 2]``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

B0_CURRENT = "B0_current"
G1_STOCK_REFERENCE = "G1_stock_reference"
G2_EXTENSION_DISCOVERY = "G2_extension_discovery"
G3_RISK_DISCOVERY = "G3_risk_discovery"
VARIANTS = (B0_CURRENT, G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)

MIN_RAW_PRICE_USD = 5.0
MIN_ADV20_USD = 20_000_000.0
MIN_HISTORY_SESSIONS = 252
MIN_REFERENCE_N = 30

HIGH_ATR = "HIGH_ATR"
EXTENDED = "EXTENDED"
DOLLAR_LIQUIDITY_UNVERIFIED = "DOLLAR_LIQUIDITY_UNVERIFIED"
VOLUME_SESSION_UNVERIFIED = "VOLUME_SESSION_UNVERIFIED"
REFERENCE_UNIVERSE_INSUFFICIENT = "REFERENCE_UNIVERSE_INSUFFICIENT"

SCORE_DERIVED_REASONS = frozenset(
    {"LOW_SCORE", "LOW_COVERAGE", "MISSING_SCORE", "DATA_INSUFFICIENT"}
)
UNVERIFIED_REASONS = frozenset({DOLLAR_LIQUIDITY_UNVERIFIED, VOLUME_SESSION_UNVERIFIED})
PRESERVED_STRUCTURAL_REASONS = frozenset(
    {
        "TOO_FAR_FROM_BASE",
        "LOW_EVENT_RVOL",
        "LOW_ADV",
        "ADV_TOO_LOW",
        "WEAK_STRUCTURE",
        "SHORT_HISTORY",
        "UNRESOLVED_UPTHRUST",
        "INVALIDATED",
        "SETUP_NOT_MET",
        "LOW_PRICE",
        "OUT_OF_SCOPE",
        "INCOMPLETE_DAILY_DATA",
        "INCOMPLETE_COMMON_INPUTS",
        "NOT_TRADABLE",
        "HALTED_SESSION",
        "MISSING_T_BAR",
        "LATE_SOURCE",
        "SOURCE_UNAVAILABLE",
        "TRACK_MISMATCH",
    }
)
VOLUME_SESSION_FAMILIES = frozenset({"B_confirmed_base_breakout", "C_trend_pullback"})
DISCOVERY_HINT_ONLY = {
    B0_CURRENT: frozenset(),
    G1_STOCK_REFERENCE: frozenset(),
    G2_EXTENSION_DISCOVERY: frozenset({EXTENDED}),
    G3_RISK_DISCOVERY: frozenset({EXTENDED, HIGH_ATR}),
}


class ReferenceError(ValueError):
    """Invalid reference-universe input. Not a license to relax gates."""


class InsufficientReference(ReferenceError):
    """Fewer than MIN_REFERENCE_N eligible stocks. Data limit, not a pass."""


@dataclass(frozen=True)
class Facts:
    security_id: str
    session_date: str
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


@dataclass(frozen=True)
class ReferenceInput:
    security_id: str
    session_date: str
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


@dataclass(frozen=True)
class ReferencePool:
    session_date: str
    member_ids: tuple[str, ...]
    atr_pcts: tuple[float, ...]
    median_atr_pct: float
    n: int

    def actual_cap(self, absolute_atr_cap: float, reference_multiplier: float) -> float:
        return min(float(absolute_atr_cap), float(reference_multiplier) * self.median_atr_pct)


@dataclass(frozen=True)
class Decision:
    variant: str
    discovery_passed: bool
    technical_entry_passed: bool
    qualified_entry_passed: bool
    research_reasons: tuple[str, ...]
    risk_hints: tuple[str, ...]
    high_atr: bool
    extended: bool
    old_reference_median: float | None
    new_reference_median: float | None
    actual_cap: float | None
    reference_n: int | None
    reference_limited: bool = False


def upper_median(values: Sequence[float]) -> float:
    """Production convention: the upper median ``sorted[n // 2]``."""

    if not values:
        raise ReferenceError("upper_median requires at least one value")
    ordered = sorted(float(value) for value in values)
    return ordered[len(ordered) // 2]


def high_atr_hit(
    atr_pct: float | None,
    *,
    median_atr_pct: float | None,
    absolute_atr_cap: float,
    reference_multiplier: float,
) -> bool:
    if atr_pct is None or median_atr_pct is None:
        return False
    if not (median_atr_pct > 0):
        return False
    cap = min(float(absolute_atr_cap), float(reference_multiplier) * float(median_atr_pct))
    return float(atr_pct) > cap


def _finite_number(value: Any) -> float | None:
    """Finite real number. Rejects bool, NaN, and +/-Inf."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def _finite_positive(value: Any) -> float | None:
    number = _finite_number(value)
    if number is None or number <= 0:
        return None
    return number


def _finite_score(value: Any) -> float | None:
    return _finite_number(value)


def reference_eligible(item: ReferenceInput) -> bool:
    if item.asset_track != "stock":
        return False
    if not item.venue_eligible or not item.complete_bar:
        return False
    if item.halted or item.zero_volume or not item.currently_tradable:
        return False
    if type(item.history_sessions) is not int or item.history_sessions < MIN_HISTORY_SESSIONS:
        return False
    close = _finite_positive(item.raw_close)
    adv = _finite_positive(item.adv20)
    atr = _finite_positive(item.atr_pct)
    if close is None or close < MIN_RAW_PRICE_USD:
        return False
    if adv is None or adv < MIN_ADV20_USD:
        return False
    return atr is not None


def build_reference(rows: Sequence[ReferenceInput]) -> ReferencePool:
    """T-day complete stock universe → shared HIGH_ATR reference.

    Theme membership, score/setup gates, Top-K, and future returns are
    not inputs. ETFs and illiquid names never fill a thin stock pool.
    """

    if not rows:
        raise InsufficientReference("reference universe is empty")
    dates = {item.session_date for item in rows}
    if len(dates) != 1:
        raise ReferenceError("reference universe mixes session dates")
    session_date = next(iter(dates))
    seen: set[str] = set()
    for item in rows:
        sid = item.security_id
        if not isinstance(sid, str) or not sid:
            raise ReferenceError("stable security_id required")
        if sid in seen:
            raise ReferenceError(f"duplicate security_id in reference universe: {sid}")
        seen.add(sid)
    members = [item for item in rows if reference_eligible(item)]
    members = sorted(members, key=lambda item: item.security_id)
    if len(members) < MIN_REFERENCE_N:
        raise InsufficientReference(
            f"eligible stock reference n={len(members)} < {MIN_REFERENCE_N}"
        )
    atr_pcts = tuple(float(item.atr_pct) for item in members)  # type: ignore[arg-type]
    return ReferencePool(
        session_date=session_date,
        member_ids=tuple(item.security_id for item in members),
        atr_pcts=atr_pcts,
        median_atr_pct=upper_median(atr_pcts),
        n=len(members),
    )


def _as_reason_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(item) for item in value)


def _family_of(row: Mapping[str, Any]) -> str:
    return str(row.get("algorithm_id") or row.get("family") or "")


def _unverified_reasons(
    row: Mapping[str, Any],
    *,
    dollar_liquidity_verified: bool,
    volume_session_verified: bool,
) -> tuple[str, ...]:
    reasons = set(_as_reason_tuple(row.get("rejection_reasons")))
    out: list[str] = []
    if DOLLAR_LIQUIDITY_UNVERIFIED in reasons or not dollar_liquidity_verified:
        out.append(DOLLAR_LIQUIDITY_UNVERIFIED)
    if _family_of(row) in VOLUME_SESSION_FAMILIES and (
        VOLUME_SESSION_UNVERIFIED in reasons or not volume_session_verified
    ):
        out.append(VOLUME_SESSION_UNVERIFIED)
    return tuple(out)


def _parsed_gates(row: Mapping[str, Any]) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    """Require the upstream common/setup pair. A partial map is not a license to soften gates."""

    gates = row.get("gate_results")
    if not isinstance(gates, Mapping) or "common" not in gates or "setup" not in gates:
        return None
    return _as_reason_tuple(gates.get("common")), _as_reason_tuple(gates.get("setup"))


def _upstream_flag(row: Mapping[str, Any], reason: str) -> bool:
    reasons = _as_reason_tuple(row.get("rejection_reasons"))
    parsed = _parsed_gates(row)
    common = parsed[0] if parsed else ()
    return reason in reasons or reason in common


def _production_extended(row: Mapping[str, Any]) -> bool:
    return _upstream_flag(row, EXTENDED)


def _production_high_atr(row: Mapping[str, Any]) -> bool:
    return _upstream_flag(row, HIGH_ATR)


def _required_row_text(row: Mapping[str, Any], key: str) -> str:
    if key not in row or row.get(key) in (None, ""):
        raise ValueError(f"scored row missing {key}")
    return str(row[key])


def b0_recompute_diagnostic(
    row: Mapping[str, Any],
    facts: Facts,
    *,
    old_reference_median: float | None,
    absolute_atr_cap: float,
    reference_multiplier: float,
) -> dict[str, Any]:
    """Compare upstream HIGH_ATR with an old-median recompute. Never a B0 override."""

    upstream = _production_high_atr(row)
    if facts.atr_pct is None or old_reference_median is None or not (old_reference_median > 0):
        return {"upstream_high_atr": upstream, "recomputed_high_atr": None, "agrees": None}
    recomputed = high_atr_hit(
        facts.atr_pct,
        median_atr_pct=old_reference_median,
        absolute_atr_cap=absolute_atr_cap,
        reference_multiplier=reference_multiplier,
    )
    return {
        "upstream_high_atr": upstream,
        "recomputed_high_atr": recomputed,
        "agrees": bool(upstream) == bool(recomputed),
    }


def _hard_blocks(reasons: Iterable[str], *, hints: frozenset[str]) -> tuple[str, ...]:
    blocked = []
    for reason in reasons:
        if reason in UNVERIFIED_REASONS or reason in hints:
            continue
        blocked.append(reason)
    return tuple(dict.fromkeys(blocked))


def _facts_block_reasons(facts: Facts) -> tuple[str, ...]:
    reasons: list[str] = []
    if not facts.complete_bar:
        reasons.append("INCOMPLETE_DAILY_DATA")
    if facts.asset_track == "stock" and not facts.venue_eligible:
        reasons.append("OUT_OF_SCOPE")
    if facts.halted:
        reasons.append("HALTED_SESSION")
    if not facts.currently_tradable or facts.zero_volume:
        reasons.append("NOT_TRADABLE")
    return tuple(reasons)


def evaluate(
    *,
    variant: str,
    row: Mapping[str, Any],
    facts: Facts,
    reference: ReferencePool | None,
    old_reference_median: float | None,
    absolute_atr_cap: float,
    reference_multiplier: float,
    dollar_liquidity_verified: bool = False,
    volume_session_verified: bool = False,
) -> Decision:
    """Re-decide HIGH_ATR / discovery layering. Never mutates ``row``."""

    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant}")
    row_security = _required_row_text(row, "security_id")
    row_session = _required_row_text(row, "session_date")
    row_track = _required_row_text(row, "stock_or_etf_track")
    if facts.security_id != row_security:
        raise ValueError("Facts.security_id must match the scored row")
    if facts.session_date != row_session:
        raise ValueError("Facts.session_date must match the scored row")
    if facts.asset_track != row_track:
        raise ValueError("Facts.asset_track must match the scored row")
    if reference is not None and reference.session_date != facts.session_date:
        raise ValueError("reference session_date must match the scored row")

    etf = facts.asset_track == "etf"
    parsed = _parsed_gates(row)
    can_replace_shared = parsed is not None and not etf and variant != B0_CURRENT
    common = parsed[0] if parsed else ()
    research_reasons = list(_as_reason_tuple(row.get("rejection_reasons")))
    extended = _production_extended(row)
    if extended and EXTENDED not in research_reasons:
        research_reasons.append(EXTENDED)

    new_median = None if reference is None else reference.median_atr_pct
    reference_n = None if reference is None else reference.n
    reference_limited = False
    high_atr = _production_high_atr(row)
    actual_cap = None
    if old_reference_median is not None and old_reference_median > 0:
        actual_cap = min(float(absolute_atr_cap), float(reference_multiplier) * float(old_reference_median))

    if can_replace_shared:
        replaced_common_high = HIGH_ATR in common
        if replaced_common_high:
            research_reasons = [reason for reason in research_reasons if reason != HIGH_ATR]
        if reference is None:
            reference_limited = True
            research_reasons.append(REFERENCE_UNIVERSE_INSUFFICIENT)
            high_atr = _production_high_atr(row)
        elif facts.atr_pct is None:
            high_atr = _production_high_atr(row)
        else:
            high_atr = high_atr_hit(
                facts.atr_pct,
                median_atr_pct=reference.median_atr_pct,
                absolute_atr_cap=absolute_atr_cap,
                reference_multiplier=reference_multiplier,
            )
            actual_cap = reference.actual_cap(absolute_atr_cap, reference_multiplier)
        if HIGH_ATR in research_reasons:
            high_atr = True
        if high_atr and HIGH_ATR not in research_reasons:
            research_reasons.append(HIGH_ATR)
        if not high_atr and replaced_common_high:
            research_reasons = [reason for reason in research_reasons if reason != HIGH_ATR]
    elif high_atr and HIGH_ATR not in research_reasons:
        research_reasons.append(HIGH_ATR)

    for extra in _facts_block_reasons(facts):
        if extra not in research_reasons:
            research_reasons.append(extra)
    for extra in _unverified_reasons(
        row,
        dollar_liquidity_verified=dollar_liquidity_verified,
        volume_session_verified=volume_session_verified,
    ):
        if extra not in research_reasons:
            research_reasons.append(extra)

    research_reasons = list(dict.fromkeys(research_reasons))
    hints = set(DISCOVERY_HINT_ONLY[variant])
    if HIGH_ATR not in common:
        hints.discard(HIGH_ATR)
    if EXTENDED not in common:
        hints.discard(EXTENDED)
    hint_set = frozenset(hints)
    risk_hints = tuple(reason for reason in (HIGH_ATR, EXTENDED) if reason in research_reasons and reason in hint_set)
    discovery_blocks = _hard_blocks(research_reasons, hints=hint_set)
    entry_blocks = _hard_blocks(research_reasons, hints=frozenset())
    discovery_passed = not discovery_blocks and not reference_limited
    technical_entry_passed = not entry_blocks and not reference_limited
    qualified_entry_passed = technical_entry_passed and not any(
        reason in UNVERIFIED_REASONS for reason in research_reasons
    )
    status = row.get("status")
    upstream_reasons = _as_reason_tuple(row.get("rejection_reasons"))
    status_ok = status == "eligible" or (status in {"watch", "rejected"} and bool(upstream_reasons))
    if _finite_score(row.get("score")) is None or not status_ok:
        discovery_passed = False
        technical_entry_passed = False
        qualified_entry_passed = False
    return Decision(
        variant=variant,
        discovery_passed=discovery_passed,
        technical_entry_passed=technical_entry_passed,
        qualified_entry_passed=qualified_entry_passed,
        research_reasons=tuple(research_reasons),
        risk_hints=risk_hints,
        high_atr=high_atr,
        extended=extended,
        old_reference_median=None if old_reference_median is None else float(old_reference_median),
        new_reference_median=new_median,
        actual_cap=actual_cap,
        reference_n=reference_n,
        reference_limited=reference_limited,
    )


def attach_research(row: Mapping[str, Any], decision: Decision) -> dict[str, Any]:
    """Copy the row and store the decision under research fields only."""

    copied = dict(row)
    copied["research_gate"] = {
        "variant": decision.variant,
        "discovery_passed": decision.discovery_passed,
        "technical_entry_passed": decision.technical_entry_passed,
        "qualified_entry_passed": decision.qualified_entry_passed,
        "research_reasons": list(decision.research_reasons),
        "risk_hints": list(decision.risk_hints),
        "high_atr": decision.high_atr,
        "extended": decision.extended,
        "old_reference_median": decision.old_reference_median,
        "new_reference_median": decision.new_reference_median,
        "actual_cap": decision.actual_cap,
        "reference_n": decision.reference_n,
        "reference_limited": decision.reference_limited,
    }
    return copied


def stock_rank(
    rows: Sequence[Mapping[str, Any]],
    *,
    track: str | None = "stock",
    top_k: int | None = None,
    score_key: str = "score",
) -> list[dict[str, Any]]:
    """Public observation semantics: best family/theme score per stock.

    M1 consensus_z is intentionally ignored. Do not mix this list with M1.
    """

    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        if track is not None and str(row.get("stock_or_etf_track") or "") != track:
            continue
        sid = str(row.get("security_id") or "")
        score = row.get(score_key)
        if not sid or score_key == "consensus_z":
            continue
        number = _finite_score(score)
        if number is None:
            continue
        current = dict(row)
        previous = best.get(sid)
        if previous is None:
            best[sid] = current
            continue
        prev_score = float(previous[score_key])
        prev_family = str(previous.get("algorithm_id") or "")
        family = str(current.get("algorithm_id") or "")
        prev_theme = str(previous.get("sector_context") or previous.get("theme_id") or "")
        theme = str(current.get("sector_context") or current.get("theme_id") or "")
        if number > prev_score or (
            number == prev_score and (family, theme) < (prev_family, prev_theme)
        ):
            best[sid] = current
    ranked = sorted(
        best.values(),
        key=lambda item: (-float(item[score_key]), str(item.get("security_id") or "")),
    )
    if top_k is None:
        return ranked
    if type(top_k) is not int or top_k < 0:
        raise ValueError("top_k must be a nonnegative integer")
    return ranked[:top_k]


def frozen_experiment_constants() -> dict[str, Any]:
    return {
        "min_raw_price_usd": MIN_RAW_PRICE_USD,
        "min_adv20_usd": MIN_ADV20_USD,
        "min_history_sessions": MIN_HISTORY_SESSIONS,
        "min_reference_n": MIN_REFERENCE_N,
        "upper_median": "sorted[n//2]",
        "variants": list(VARIANTS),
        "weights_changed": False,
        "factor_prior_reapplied": False,
        "d_residual_window_changed": False,
        "bc_pattern_confirmation_changed": False,
        "etf_algorithm_changed": False,
        "note": "frozen experimental settings, not claimed optima",
    }
