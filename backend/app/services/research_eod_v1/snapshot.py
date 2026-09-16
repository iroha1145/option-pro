"""Pure ``compute_snapshot`` — no wall clock, no network."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION
from app.services.research_eod_v1.algorithms import setup_for
from app.services.research_eod_v1.calendar_asof import last_completed_session, require_aware, session_close_at
from app.services.research_eod_v1.cross_section import q_star
from app.services.research_eod_v1.factors import RawComponents, extract_raw
from app.services.research_eod_v1.mathutil import clip100
from app.services.research_eod_v1.paths import REFERENCE_DIR
from app.services.research_eod_v1.series import SecuritySeries, clip_panel_to_as_of
from app.services.research_eod_v1.venue import classify_venue

import sys

if str(REFERENCE_DIR) not in sys.path:
    sys.path.insert(0, str(REFERENCE_DIR))
from registry import CommonInputs, common_rejections, resolve_weights, score_features  # type: ignore


def _v_state(algorithm: str, raw: RawComponents) -> float | None:
    if raw.rvol is None and algorithm != "C_trend_pullback":
        return None
    if algorithm in {"A_trend_quality", "D_residual_momentum"}:
        if raw.rvol is None or raw.rvol <= 0:
            return None
        return clip100(50.0 + 30.0 * math.log(raw.rvol))
    if algorithm == "B_confirmed_base_breakout":
        if raw.rvol is None or raw.rvol <= 0:
            return None
        return clip100(50.0 + 30.0 * math.log(raw.rvol))
    if raw.down_ratio is None or raw.rvol is None:
        return None
    return 0.5 * (clip100(100.0 * (1.0 - raw.down_ratio / 1.5)) or 0.0) + 0.5 * (
        clip100(50.0 + 50.0 * (raw.rvol - 0.8)) or 0.0
    )


def _assemble_factors(
    raw: RawComponents,
    *,
    q_slope: float | None,
    q_m63: float | None,
    q_m126: float | None,
    q_m252: float | None,
    q_residual: float | None,
    q_imbalance: float | None,
    q_neg_sigma: float | None,
    q_neg_gap: float | None,
    q_neg_dd: float | None,
    q_industry_excess: float | None,
    industry_breadth: float | None,
    algorithm: str,
    blend: tuple[float, float, float],
) -> dict[str, float | None]:
    er = None if raw.er63 is None else clip100(50.0 + 50.0 * raw.er63)
    t = None
    if q_slope is not None and er is not None and raw.ma_state is not None:
        t = 0.40 * q_slope + 0.35 * er + 0.25 * raw.ma_state
    if algorithm == "D_residual_momentum":
        m = q_residual
    elif None in (q_m63, q_m126, q_m252):
        m = None
    else:
        m = blend[0] * q_m63 + blend[1] * q_m126 + blend[2] * q_m252  # type: ignore[operator]
    v_state = _v_state(algorithm, raw)
    v = None
    if q_imbalance is not None and raw.clv5 is not None and v_state is not None:
        v = 0.40 * q_imbalance + 0.30 * (100.0 * raw.clv5) + 0.30 * v_state
        v = clip100(v)
    r = None
    if None not in (q_neg_sigma, q_neg_gap, q_neg_dd):
        r = 0.45 * q_neg_sigma + 0.35 * q_neg_gap + 0.20 * q_neg_dd  # type: ignore[operator]
    g = None
    if q_industry_excess is not None and industry_breadth is not None:
        g = 0.60 * q_industry_excess + 0.40 * 100.0 * industry_breadth
    return {
        "T": None if t is None else float(t),
        "M": None if m is None else float(m),
        "S": raw.structure_score,
        "B": raw.b_score,
        "P": raw.p_score,
        "V": None if v is None else float(v),
        "R": None if r is None else float(r),
        "G": None if g is None else float(g),
    }


@dataclass(frozen=True)
class SnapshotRow:
    security_id: str
    ticker_at_signal: str
    session_date: str
    feature_version: str
    algorithm_id: str
    config_hash: str
    sector_context: str
    theme_ids: tuple[str, ...]
    primary_industry_id: str | None
    score: float | None
    score_components: dict[str, float]
    configured_weights: dict[str, float]
    effective_weights: dict[str, float]
    observed_feature_coverage: float
    setup_state: str
    gate_results: dict[str, Any]
    rejection_reasons: tuple[str, ...]
    known_support: float | None
    known_resistance: float | None
    planned_invalidation: float | None
    event_data_status: str
    capacity_status: str
    stock_or_etf_track: str
    status: str


def compute_snapshot(
    as_of: datetime,
    historical_data: Mapping[str, SecuritySeries],
    universe_version: str,
    config: Mapping[str, Any],
    *,
    sector_id: str,
    algorithm: str,
    profile: str = "balanced",
    horizon: str = "mid",
    event_calendar: Mapping[str, Any] | None = None,
    config_digest: str = "",
    spy_residual_allowed: bool = True,
    matched_benchmark_id: str | None = None,
) -> dict[str, Any]:
    """Deterministic snapshot. Adding bars after ``as_of`` must not change T."""

    require_aware(as_of)
    session = last_completed_session(as_of)
    panel = clip_panel_to_as_of(historical_data, as_of)
    registry = config["registry"] if "registry" in config else config
    sector = registry["sectors"][sector_id]
    profile_cfg = registry["profiles"][profile]
    horizon_cfg = registry["horizons"][horizon]
    blend = tuple(horizon_cfg["momentum_blend"])
    weights = resolve_weights(registry, sector_id, algorithm, profile, horizon)
    market = panel.get("SPY")
    matched = panel.get(matched_benchmark_id) if matched_benchmark_id else None
    raws: dict[str, RawComponents] = {}
    for sid, series in panel.items():
        if sid in {"SPY", "QQQ"} and series.asset_track != sector.get("asset_track", "stock"):
            pass
        raws[sid] = extract_raw(
            series,
            market=market,
            panel=panel,
            horizon=horizon,
            momentum_blend=blend,  # type: ignore[arg-type]
            sector_gates=sector["gates"],
            spy_residual_allowed=spy_residual_allowed,
            matched_market=matched,
        )
    tracks = {sid: raw.asset_track for sid, raw in raws.items()}
    industries = {sid: raw.industry_id for sid, raw in raws.items()}
    parents = {sid: raw.parent_industry_id for sid, raw in raws.items()}
    q_slope = q_star({s: r.slope50 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m63 = q_star({s: r.m63 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m126 = q_star({s: r.m126_skip21 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m252 = q_star({s: r.m252_skip21 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_resid = q_star({s: r.residual.raw for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_imb = q_star({s: r.imbalance20 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks)
    q_ns = q_star({s: r.sigma20 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)
    q_ng = q_star({s: r.gap_tail252 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)
    q_nd = q_star({s: r.max_drawdown63 for s, r in raws.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)

    industry_ret: dict[str, float | None] = {}
    breadth: dict[str, float | None] = {}
    spy_m63 = raws["SPY"].m63 if "SPY" in raws else None
    for sid, raw in raws.items():
        peers = [
            other
            for other, item in raws.items()
            if other != sid and item.industry_id and item.industry_id == raw.industry_id
        ]
        if len(peers) < 5:
            parent_peers = [
                other
                for other, item in raws.items()
                if other != sid and item.parent_industry_id and item.parent_industry_id == raw.parent_industry_id
            ]
            peers = parent_peers
        if len(peers) < 5:
            industry_ret[sid] = None
            breadth[sid] = None
            continue
        rets = [raws[p].m63 for p in peers if raws[p].m63 is not None]
        if not rets or spy_m63 is None:
            industry_ret[sid] = None
        else:
            industry_ret[sid] = float(sum(rets) / len(rets) - spy_m63)
        flags = [raws[p].above_sma50 for p in peers if raws[p].above_sma50 is not None]
        breadth[sid] = None if len(flags) < 5 else sum(1 for flag in flags if flag) / len(flags)
    q_g = q_star(industry_ret, industry=parents, parent=parents, tracks=tracks)

    event_status = "NOT_REGISTERED"
    if event_calendar is None:
        event_status = "DATA_INSUFFICIENT"

    rows: list[dict[str, Any]] = []
    required = ("T", "M", "S", "R")
    if algorithm == "B_confirmed_base_breakout":
        required = ("T", "M", "S", "R", "B", "V")
    if algorithm == "C_trend_pullback":
        required = ("T", "M", "S", "R", "P", "V")

    target_track = "etf" if sector.get("asset_track") == "etf" else "stock"
    for sid, raw in raws.items():
        if raw.asset_track != target_track and sid not in sector.get("members", []):
            if target_track == "stock" and raw.asset_track != "stock":
                continue
            if target_track == "etf" and raw.asset_track != "etf":
                continue
        venue = classify_venue(raw.venue_metadata)
        factors = _assemble_factors(
            raw,
            q_slope=q_slope.get(sid),
            q_m63=q_m63.get(sid),
            q_m126=q_m126.get(sid),
            q_m252=q_m252.get(sid),
            q_residual=q_resid.get(sid),
            q_imbalance=q_imb.get(sid),
            q_neg_sigma=q_ns.get(sid),
            q_neg_gap=q_ng.get(sid),
            q_neg_dd=q_nd.get(sid),
            q_industry_excess=q_g.get(sid),
            industry_breadth=breadth.get(sid),
            algorithm=algorithm,
            blend=blend,  # type: ignore[arg-type]
        )
        scored = score_features(factors, weights, coverage_min=profile_cfg["coverage_min"], required=required)
        setup = setup_for(algorithm, raw, profile_cfg, sector["gates"], horizon)
        atr_peers = [
            item.atr_pct
            for item in raws.values()
            if item.industry_id == raw.industry_id and item.atr_pct is not None
        ]
        median_atr = sorted(atr_peers)[len(atr_peers) // 2] if atr_peers else (raw.atr_pct or 0.0)
        common = ()
        if raw.raw_close is not None and raw.adv20 is not None and raw.atr_pct is not None and raw.extension_atr is not None and raw.structure_score is not None:
            common = common_rejections(
                registry,
                sector_id,
                algorithm,
                profile,
                CommonInputs(
                    raw_close=raw.raw_close,
                    adv20=raw.adv20,
                    atr_pct=raw.atr_pct,
                    sector_median_atr_pct=median_atr or raw.atr_pct,
                    extension_atr=raw.extension_atr,
                    structure_score=raw.structure_score,
                    history_sessions=raw.history_sessions,
                    us_venue_and_security_eligible=venue.eligible,
                    daily_data_complete=True,
                    currently_tradable=True,
                    unresolved_upthrust=raw.unresolved_upthrust,
                    structure_invalidated=raw.structure_invalidated,
                ),
                scored,
            )
        elif not venue.eligible:
            common = (venue.reason,)
        else:
            common = ("INCOMPLETE_COMMON_INPUTS",)
        reasons = tuple(dict.fromkeys(tuple(common) + setup.reasons))
        eligible = setup.passed and not reasons and scored.score is not None
        rows.append(
            {
                "security_id": sid,
                "ticker_at_signal": raw.ticker_at_signal,
                "session_date": session.isoformat(),
                "feature_version": FEATURE_VERSION,
                "algorithm_id": algorithm,
                "config_hash": config_digest,
                "sector_context": sector_id,
                "theme_ids": list(raw.theme_ids),
                "primary_industry_id": raw.industry_id,
                "score": scored.score,
                "score_components": scored.contributions,
                "configured_weights": weights,
                "effective_weights": scored.effective_weights,
                "observed_feature_coverage": scored.coverage,
                "setup_state": "eligible" if eligible else setup.state,
                "gate_results": {"setup": setup.reasons, "common": common, "venue": venue.reason},
                "rejection_reasons": reasons,
                "known_support": raw.known_support,
                "known_resistance": raw.known_resistance,
                "planned_invalidation": raw.planned_invalidation,
                "event_data_status": event_status,
                "capacity_status": "not_sized",
                "stock_or_etf_track": raw.asset_track,
                "status": "eligible" if eligible else "rejected",
                "pivots": raw.pivots,
                "frozen_setup": raw.frozen_setup,
                "factors": factors,
            }
        )
    fingerprint = hashlib.sha256(
        json.dumps(
            {"as_of": session.isoformat(), "universe": universe_version, "n": len(rows), "algo": algorithm},
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return {
        "session_date": session.isoformat(),
        "market_close_at": session_close_at(session).isoformat(),
        "as_of": as_of.isoformat(),
        "universe_version": universe_version,
        "feature_version": FEATURE_VERSION,
        "score_version": SCORE_VERSION,
        "algorithm_id": algorithm,
        "sector_id": sector_id,
        "profile": profile,
        "horizon": horizon,
        "fingerprint": fingerprint,
        "rows": rows,
        "network_calls": 0,
    }


def snapshot_fingerprint(payload: Mapping[str, Any]) -> str:
    clone = {k: v for k, v in payload.items() if k != "as_of"}
    return hashlib.sha256(json.dumps(clone, sort_keys=True, default=str).encode()).hexdigest()
