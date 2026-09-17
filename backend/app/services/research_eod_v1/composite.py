"""M1–M4 second-layer filters. First-layer rejects stay rejected."""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.constants import (
    COMPOSITE_FLOORS,
    M2_LAMBDA_RISK,
    M4_BULL_WEIGHTS,
    M4_DEFENSE_WEIGHTS,
    M4_MIXED_WEIGHTS,
    M4_RANK_FLOORS,
)


def _eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("status") == "eligible" and row.get("score") is not None]


def _dedup_security(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_id.setdefault(str(row["security_id"]), []).append(row)
    out: list[dict[str, Any]] = []
    for sid, group in by_id.items():
        scores = [float(item["score"]) for item in group if item.get("score") is not None]
        merged = dict(group[0])
        merged["security_id"] = sid
        merged["consensus_z"] = median(scores) if scores else None
        merged["family_votes"] = tuple(sorted({item.get("algorithm_id") for item in group}))
        merged["R"] = group[0].get("factors", {}).get("R") if isinstance(group[0].get("factors"), dict) else group[0].get("R")
        merged["G"] = group[0].get("factors", {}).get("G") if isinstance(group[0].get("factors"), dict) else group[0].get("G")
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
        family_z = [float(item.get("score") or 0) for item in _eligible(rows) if item["security_id"] == row["security_id"]]
        if family_z and max(family_z) - min(family_z) > 25:
            row["status"] = "watch"
            continue
        row["status"] = "eligible"
        kept.append(row)
    kept.sort(key=lambda r: (-float(r["consensus_z"]), -(float(r["R"] or 0)), r["security_id"]))
    return kept[:top_k]


def m2_utility(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int,
               matured_returns: Mapping[str, Sequence[float]] | None = None) -> list[dict[str, Any]]:
    if not matured_returns:
        return []
    lam = M2_LAMBDA_RISK[profile]
    kept: list[dict[str, Any]] = []
    for row in _dedup_security(_eligible(rows)):
        sample = list(matured_returns.get(row["security_id"], []))
        if len(sample) < 100:
            continue
        ordered = sorted(sample)
        worst = ordered[: max(1, int(0.05 * len(ordered)))]
        mu = sum(sample) / len(sample)
        es = max(0.0, -sum(worst) / len(worst))
        u = mu - lam * es
        if u <= 0:
            continue
        row = dict(row)
        row["utility_lower"] = u
        row["status"] = "eligible"
        kept.append(row)
    kept.sort(key=lambda r: (-float(r["utility_lower"]), r["security_id"]))
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
    candidates.sort(key=lambda r: (-r["base"], r["security_id"]))
    selected: list[dict[str, Any]] = []
    industry_count: dict[str, int] = {}
    cap = max(1, int((0.30 * top_k) + 0.999))
    for item in candidates:
        if len(selected) >= top_k:
            break
        ind = str(item.get("primary_industry_id") or "unknown")
        if industry_count.get(ind, 0) >= cap:
            continue
        if selected and corr is not None:
            penalties = [max(0.0, corr.get((item["security_id"], other["security_id"]), 0.0)) for other in selected]
            if any(p > 0.85 for p in penalties):
                continue
            marginal = item["base"] - 25.0 * (sum(penalties) / len(penalties))
        else:
            if selected and corr is None:
                item["status"] = "data_insufficient"
                continue
            marginal = item["base"]
        if marginal < floor - 10:
            break
        item["status"] = "eligible"
        selected.append(item)
        industry_count[ind] = industry_count.get(ind, 0) + 1
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
    for row in _eligible(rows):
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
