"""M1–M4 second-layer filters. First-layer rejects stay rejected."""

from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.constants import (
    COMPOSITE_FLOORS,
    M2_LAMBDA_RISK,
    M3_CORRELATION_PENALTY,
    M3_HARD_CORR,
    M4_BULL_WEIGHTS,
    M4_DEFENSE_WEIGHTS,
    M4_MIXED_WEIGHTS,
    M4_RANK_FLOORS,
)


def _eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("status") == "eligible" and row.get("score") is not None]


def _row_rg(row: Mapping[str, Any], key: str) -> float | None:
    factors = row.get("factors")
    if isinstance(factors, dict) and factors.get(key) is not None:
        return float(factors[key])
    value = row.get(key)
    return None if value is None else float(value)


def _row_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("security_id")),
        str(row.get("algorithm_id")),
        str(row.get("sector_context") or row.get("theme_id") or ""),
        str(row.get("horizon") or ""),
        str(row.get("profile") or ""),
        str(row.get("session_date") or ""),
    )


def _dedup_identical_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for row in rows:
        seen.setdefault(_row_identity(row), row)
    return list(seen.values())


def _collapse_same_family(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge the same security+family across themes. Input order cannot change R/G."""

    ordered = sorted(
        _dedup_identical_rows(rows),
        key=lambda r: (str(r.get("security_id")), str(r.get("algorithm_id")), str(r.get("sector_context") or "")),
    )
    family: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in ordered:
        family.setdefault((str(row["security_id"]), str(row.get("algorithm_id"))), []).append(row)
    collapsed: list[dict[str, Any]] = []
    for (sid, algo), group in family.items():
        scores = [float(item["score"]) for item in group if item.get("score") is not None]
        r_vals = [value for item in group if (value := _row_rg(item, "R")) is not None]
        g_vals = [value for item in group if (value := _row_rg(item, "G")) is not None]
        merged = dict(group[0])
        merged["security_id"] = sid
        merged["algorithm_id"] = algo
        merged["score"] = median(scores) if scores else None
        merged["R"] = median(r_vals) if r_vals else None
        merged["G"] = median(g_vals) if g_vals else None
        merged["theme_count"] = len(group)
        collapsed.append(merged)
    return collapsed


def _dedup_security(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Same family across themes first, then across families. Input order cannot change R/G."""

    collapsed = _collapse_same_family(rows)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in collapsed:
        by_id.setdefault(str(row["security_id"]), []).append(row)
    out: list[dict[str, Any]] = []
    for sid, group in by_id.items():
        scores = [float(item["score"]) for item in group if item.get("score") is not None]
        r_vals = [float(item["R"]) for item in group if item.get("R") is not None]
        g_vals = [float(item["G"]) for item in group if item.get("G") is not None]
        merged = dict(sorted(group, key=lambda item: str(item.get("algorithm_id")))[0])
        merged["security_id"] = sid
        merged["consensus_z"] = median(scores) if scores else None
        merged["family_votes"] = tuple(sorted({item.get("algorithm_id") for item in group}))
        merged["R"] = median(r_vals) if r_vals else None
        merged["G"] = median(g_vals) if g_vals else None
        out.append(merged)
    return out


def m1_consensus(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int) -> list[dict[str, Any]]:
    floor = COMPOSITE_FLOORS[profile]
    grouped = _dedup_security(_eligible(rows))
    kept: list[dict[str, Any]] = []
    for row in grouped:
        votes = [v for v in row["family_votes"] if v]
        if len(set(votes)) < 2:
            continue
        z = row["consensus_z"]
        if z is None or z < floor:
            continue
        folded = _collapse_same_family([item for item in _eligible(rows) if item["security_id"] == row["security_id"]])
        family_z = [float(item["score"]) for item in folded if item.get("score") is not None]
        if family_z and max(family_z) - min(family_z) > 25:
            row["status"] = "watch"
            continue
        row["status"] = "eligible"
        kept.append(row)
    kept.sort(key=lambda r: (-float(r["consensus_z"]), -(float(r["R"] or 0)), r["security_id"]))
    return kept[:top_k]


def m2_utility(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int,
               matured_returns: Mapping[str, Any] | None = None,
               as_of: Any | None = None) -> list[dict[str, Any]]:
    """Point-estimate utility. Research-only until block uncertainty is attached."""

    if not matured_returns:
        return []
    if as_of is None:
        raise ValueError("m2_utility requires as_of; future labels cannot be scored without an evaluation date")
    lam = M2_LAMBDA_RISK[profile]
    kept: list[dict[str, Any]] = []
    for row in _dedup_security(_eligible(rows)):
        meta = matured_returns.get(row["security_id"])
        if not isinstance(meta, Mapping):
            continue
        if meta.get("label_matured_at") is None:
            continue
        if meta["label_matured_at"] > as_of:
            continue
        if meta.get("fold") is None and meta.get("provenance") is None:
            continue
        sample = list(meta.get("returns") or [])
        if any(item is None for item in sample):
            continue
        try:
            finite_sample = [float(item) for item in sample]
        except (TypeError, ValueError):
            continue
        if any(not math.isfinite(item) for item in finite_sample):
            continue
        sample = finite_sample
        if len(sample) < 100:
            continue
        ordered = sorted(float(item) for item in sample)
        worst = ordered[: max(1, int(0.05 * len(ordered)))]
        mu = sum(ordered) / len(ordered)
        es = max(0.0, -sum(worst) / len(worst))
        u = mu - lam * es
        if u <= 0:
            continue
        row = dict(row)
        row["utility_point_estimate"] = u
        row["m2_status"] = "RESEARCH_POINT_ESTIMATE"
        row["status"] = "eligible"
        kept.append(row)
    kept.sort(key=lambda r: (-float(r["utility_point_estimate"]), r["security_id"]))
    return kept[:top_k]


def m3_diversified(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int,
                   corr: Mapping[tuple[str, str], float] | None = None) -> list[dict[str, Any]]:
    floor = COMPOSITE_FLOORS[profile]
    candidates = []
    for row in _dedup_security(_eligible(rows)):
        z = row["consensus_z"]
        r = row.get("R")
        g = row.get("G")
        if z is None or r is None or g is None:
            continue
        base = 0.65 * z + 0.20 * r + 0.15 * g
        if base < floor:
            continue
        item = dict(row)
        item["base"] = base
        candidates.append(item)
    remaining = list(candidates)
    selected: list[dict[str, Any]] = []
    industry_count: dict[str, int] = {}
    cap = max(1, int((0.30 * top_k) + 0.999))

    def _penalties(item: Mapping[str, Any]) -> tuple[list[float] | None, bool]:
        if not selected:
            return [], False
        if corr is None:
            return None, True
        values: list[float] = []
        for other in selected:
            key = (item["security_id"], other["security_id"])
            rev = (other["security_id"], item["security_id"])
            if key in corr:
                values.append(max(0.0, float(corr[key])))
            elif rev in corr:
                values.append(max(0.0, float(corr[rev])))
            else:
                return None, True
        return values, False

    while remaining and len(selected) < top_k:
        scored: list[tuple[float, dict[str, Any]]] = []
        blocked: list[dict[str, Any]] = []
        for item in remaining:
            ind = str(item.get("primary_industry_id") or "unknown")
            if industry_count.get(ind, 0) >= cap:
                continue
            penalties, missing = _penalties(item)
            if missing:
                blocked.append(item)
                continue
            if penalties and any(p > M3_HARD_CORR for p in penalties):
                continue
            if penalties:
                marginal = item["base"] - M3_CORRELATION_PENALTY * (sum(penalties) / len(penalties))
            else:
                marginal = item["base"]
            if marginal < floor - 10:
                continue
            scored.append((marginal, item))
        if not scored:
            break
        scored.sort(key=lambda pair: (-pair[0], pair[1]["security_id"]))
        _marginal, winner = scored[0]
        winner = dict(winner)
        winner["status"] = "eligible"
        winner["marginal"] = _marginal
        selected.append(winner)
        industry_count[str(winner.get("primary_industry_id") or "unknown")] = (
            industry_count.get(str(winner.get("primary_industry_id") or "unknown"), 0) + 1
        )
        remaining = [item for item in remaining if item["security_id"] != winner["security_id"]]
        for item in blocked:
            item["status"] = "data_insufficient"
    return selected


def m4_regime(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int,
              regime: str) -> list[dict[str, Any]]:
    if regime == "REGIME_UNKNOWN":
        return []
    if regime == "defense" and profile in {"conservative", "balanced"}:
        return []
    weights = {"bull": M4_BULL_WEIGHTS, "mixed": M4_MIXED_WEIGHTS, "defense": M4_DEFENSE_WEIGHTS}[regime]
    floor = M4_RANK_FLOORS[profile]
    by_sid: dict[str, dict[str, Mapping[str, Any]]] = {}
    for row in _collapse_same_family(_eligible(rows)):
        by_sid.setdefault(str(row["security_id"]), {})[str(row["algorithm_id"])] = row
    kept: list[dict[str, Any]] = []
    for sid, families in by_sid.items():
        score = 0.0
        for algo, weight in weights.items():
            row = families.get(algo)
            if row is None:
                continue
            score += weight * float(row["score"])
        if regime == "defense":
            d = families.get("D_residual_momentum")
            if d is None or float(d["score"]) < 75:
                continue
        if score < floor:
            continue
        kept.append({"security_id": sid, "status": "eligible", "score": score, "regime": regime})
    kept.sort(key=lambda r: (-r["score"], r["security_id"]))
    return kept[:top_k]


def classify_regime(*, spy_close: float | None, spy_sma200: float | None,
                    sma200_slope20: float | None, breadth50: float | None) -> str:
    if None in (spy_close, spy_sma200, sma200_slope20, breadth50):
        return "REGIME_UNKNOWN"
    if spy_close > spy_sma200 and sma200_slope20 > 0 and breadth50 >= 0.55:
        return "bull"
    if spy_close < spy_sma200 and sma200_slope20 < 0 and breadth50 <= 0.45:
        return "defense"
    return "mixed"


def timeframe_all_ok(*, t_score: float | None, long_momentum_raw: float | None,
                     long_structure_invalid: bool, unresolved_upthrust: bool,
                     extension_atr: float | None, max_extension: float,
                     below_invalidation: bool) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if t_score is None or t_score < 55:
        reasons.append("LONG_T")
    if long_momentum_raw is None or long_momentum_raw < 0:
        reasons.append("LONG_MOMENTUM")
    if long_structure_invalid:
        reasons.append("LONG_STRUCTURE")
    if unresolved_upthrust:
        reasons.append("SHORT_UPTHRUST")
    if extension_atr is not None and extension_atr > max_extension:
        reasons.append("SHORT_EXTENDED")
    if below_invalidation:
        reasons.append("SHORT_INVALIDATED")
    return (not reasons, tuple(reasons))
