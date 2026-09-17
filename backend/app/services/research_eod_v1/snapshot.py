"""Pure ``compute_snapshot`` — no wall clock, no network."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION
from app.services.research_eod_v1.algorithms import setup_for
from app.services.research_eod_v1.calendar_asof import last_complete_eod_session, require_aware, session_close_at
from app.services.research_eod_v1.data.capture_store import eod_pool_exclusions, series_is_late
from app.services.research_eod_v1.cross_section import q_star
from app.services.research_eod_v1.factors import RawComponents, apply_sector_gates, extract_raw
from app.services.research_eod_v1.mathutil import clip100
from app.services.research_eod_v1.membership import (
    has_complete_session_bar,
    is_theme_candidate,
    source_is_available,
    theme_membership,
)
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.series import SecuritySeries, clip_panel_to_as_of
from app.services.research_eod_v1.venue import classify_venue

ensure_reference_on_path()
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


def _structured_reject_row(
    series: SecuritySeries,
    *,
    session: date,
    algorithm: str,
    profile: str,
    horizon: str,
    config_digest: str,
    sector_id: str,
    reasons: tuple[str, ...],
    event_status: str,
) -> dict[str, Any]:
    return {
        "security_id": series.security_id,
        "ticker_at_signal": series.ticker_at_signal,
        "session_date": session.isoformat(),
        "feature_version": FEATURE_VERSION,
        "algorithm_id": algorithm,
        "profile": profile,
        "horizon": horizon,
        "adv20": None,
        "atr": None,
        "ma_distance_atr": None,
        "platform_distance_atr": None,
        "invalidation_distance_atr": None,
        "config_hash": config_digest,
        "sector_context": sector_id,
        "theme_ids": list(series.theme_ids),
        "primary_industry_id": series.industry_id,
        "score": None,
        "score_components": {},
        "configured_weights": {},
        "effective_weights": {},
        "observed_feature_coverage": 0.0,
        "setup_state": "rejected",
        "gate_results": {"setup": (), "common": reasons, "venue": reasons[0] if reasons else ""},
        "rejection_reasons": reasons,
        "known_support": None,
        "known_resistance": None,
        "planned_invalidation": None,
        "event_data_status": event_status,
        "capacity_status": "not_sized",
        "stock_or_etf_track": series.asset_track,
        "status": "rejected",
        "pivots": {},
        "frozen_setup": None,
        "factors": {},
        "residual_status": None,
        "residual_raw": None,
        "price_adjustment": (series.price_adjustment[-1] if series.price_adjustment else "unverified"),
        "volume_adjustment": (series.volume_adjustment[-1] if series.volume_adjustment else "unverified"),
        "vintage_status": (series.vintage_status[-1] if series.vintage_status else None),
        "tri_verified": bool(series.tri_verified),
        "reconstruction_mode": series.reconstruction_mode,
        "identity_confidence": dict(series.venue_metadata).get("identity_confidence"),
        "industry_source": dict(series.venue_metadata).get("industry_source"),
        "halted": bool(series.halted),
        "currently_tradable": False,
        "zero_volume": False,
    }


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
    extra_members: set[str] | None = None,
    source_finalized_through: date | None = None,
    late_securities: tuple[str, ...] = (),
    precomputed_raws: Mapping[str, RawComponents] | None = None,
) -> dict[str, Any]:
    """Deterministic snapshot. Adding bars after ``as_of`` must not change T."""

    require_aware(as_of)
    session = last_complete_eod_session(
        as_of,
        source_finalized_through=source_finalized_through,
        late_securities=late_securities,
    )
    late = eod_pool_exclusions(late_securities)
    panel = clip_panel_to_as_of(historical_data, as_of)
    panel = {sid: series.slice_through(session) for sid, series in panel.items()}
    panel = {sid: series for sid, series in panel.items() if series is not None}
    registry = config["registry"] if "registry" in config else config
    sector = registry["sectors"][sector_id]
    profile_cfg = registry["profiles"][profile]
    horizon_cfg = registry["horizons"][horizon]
    blend = tuple(horizon_cfg["momentum_blend"])
    weights = resolve_weights(registry, sector_id, algorithm, profile, horizon)
    target_track = "etf" if sector.get("asset_track") == "etf" else "stock"
    def _usable(series: SecuritySeries) -> bool:
        if series_is_late(series, late):
            return False
        return source_is_available(series, as_of) and has_complete_session_bar(series, session)

    market = panel.get("SPY")
    if market is not None and not _usable(market):
        market = None
    matched = panel.get(matched_benchmark_id) if matched_benchmark_id else None
    if matched is not None and not _usable(matched):
        matched = None
    event_status = "DATA_INSUFFICIENT" if event_calendar is None else "NOT_REGISTERED"
    t_complete: dict[str, SecuritySeries] = {}
    pool_rejects: list[dict[str, Any]] = []

    def _keep_reject(series: SecuritySeries, reason: str) -> None:
        member, member_reason = theme_membership(
            series,
            sector_id=sector_id,
            target_track=target_track,
            extra_members=extra_members,
        )
        if not member and member_reason != "TRACK_MISMATCH":
            return
        reasons = [reason]
        if member_reason == "TRACK_MISMATCH" and reason != "TRACK_MISMATCH":
            reasons.append(member_reason)
        pool_rejects.append(
            _structured_reject_row(
                series,
                session=session,
                algorithm=algorithm,
                profile=profile,
                horizon=horizon,
                config_digest=config_digest,
                sector_id=sector_id,
                reasons=tuple(dict.fromkeys(reasons)),
                event_status=event_status,
            )
        )

    for sid, series in panel.items():
        if series_is_late(series, late):
            _keep_reject(series, "LATE_SOURCE")
            continue
        if not source_is_available(series, as_of):
            _keep_reject(series, "SOURCE_UNAVAILABLE")
            continue
        if not has_complete_session_bar(series, session):
            _keep_reject(series, "MISSING_T_BAR")
            continue
        t_complete[sid] = series

    residual_panel = dict(t_complete)
    raws: dict[str, RawComponents] = {}
    candidate_ids: set[str] = set()
    reference_ids: set[str] = set()
    for sid, series in t_complete.items():
        if precomputed_raws is not None and sid in precomputed_raws:
            raws[sid] = precomputed_raws[sid]
        else:
            raws[sid] = extract_raw(
                series,
                market=market,
                panel=residual_panel,
                horizon=horizon,
                momentum_blend=blend,  # type: ignore[arg-type]
                sector_gates=sector["gates"],
                spy_residual_allowed=spy_residual_allowed,
                matched_market=matched,
            )
        ok, reason = is_theme_candidate(
            series,
            sector_id=sector_id,
            session=session,
            target_track=target_track,
            extra_members=extra_members,
        )
        if ok:
            candidate_ids.add(sid)
            if precomputed_raws is not None and sid in precomputed_raws:
                raws[sid] = apply_sector_gates(raws[sid], series, sector["gates"])
        elif reason == "TRACK_MISMATCH":
            _keep_reject(series, "TRACK_MISMATCH")
        if sid in {"SPY", "QQQ"} or series.asset_track == target_track:
            reference_ids.add(sid)
    if not reference_ids:
        reference_ids = {
            sid
            for sid, series in panel.items()
            if has_complete_session_bar(series, session)
        }
    xref = {sid: raw for sid, raw in raws.items() if sid in reference_ids}
    tracks = {sid: raw.asset_track for sid, raw in xref.items()}
    industries = {sid: raw.industry_id for sid, raw in xref.items()}
    parents = {sid: raw.parent_industry_id for sid, raw in xref.items()}
    q_slope = q_star({s: r.slope50 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m63 = q_star({s: r.m63 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m126 = q_star({s: r.m126_skip21 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_m252 = q_star({s: r.m252_skip21 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_resid = q_star({s: r.residual.raw for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_imb = q_star({s: r.imbalance20 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks)
    q_ns = q_star({s: r.sigma20 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)
    q_ng = q_star({s: r.gap_tail252 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)
    q_nd = q_star({s: r.max_drawdown63 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True)

    industry_ret: dict[str, float | None] = {}
    breadth: dict[str, float | None] = {}
    spy_m63 = raws["SPY"].m63 if "SPY" in raws else None
    for sid, raw in xref.items():
        peers = [
            other
            for other, item in xref.items()
            if other != sid and item.industry_id and item.industry_id == raw.industry_id
        ]
        if len(peers) < 5:
            parent_peers = [
                other
                for other, item in xref.items()
                if other != sid and item.parent_industry_id and item.parent_industry_id == raw.parent_industry_id
            ]
            peers = parent_peers
        if len(peers) < 5:
            industry_ret[sid] = None
            breadth[sid] = None
            continue
        rets = [xref[p].m63 for p in peers if xref[p].m63 is not None]
        if not rets or spy_m63 is None:
            industry_ret[sid] = None
        else:
            industry_ret[sid] = float(sum(rets) / len(rets) - spy_m63)
        flags = [xref[p].above_sma50 for p in peers if xref[p].above_sma50 is not None]
        breadth[sid] = None if len(flags) < 5 else sum(1 for flag in flags if flag) / len(flags)
    q_g = q_star(industry_ret, industry=parents, parent=parents, tracks=tracks)

    rows: list[dict[str, Any]] = []
    required = ("T", "M", "S", "R")
    if algorithm == "B_confirmed_base_breakout":
        required = ("T", "M", "S", "R", "B", "V")
    if algorithm == "C_trend_pullback":
        required = ("T", "M", "S", "R", "P", "V")

    for sid, raw in raws.items():
        if sid not in candidate_ids:
            continue
        series = panel[sid]
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
                    currently_tradable=raw.currently_tradable,
                    unresolved_upthrust=raw.unresolved_upthrust,
                    structure_invalidated=raw.structure_invalidated,
                ),
                scored,
            )
        elif not venue.eligible:
            common = (venue.reason,)
        elif not raw.currently_tradable:
            common = ("NOT_TRADABLE",)
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
                "profile": profile,
                "horizon": horizon,
                "adv20": raw.adv20,
                "atr": raw.atr,
                "ma_distance_atr": raw.ma_distance_atr,
                "platform_distance_atr": raw.platform_distance_atr,
                "invalidation_distance_atr": raw.invalidation_distance_atr,
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
                "residual_status": raw.residual.status if raw.residual is not None else None,
                "residual_raw": raw.residual.raw if raw.residual is not None else None,
                "price_adjustment": (series.price_adjustment[-1] if series.price_adjustment else "unverified"),
                "volume_adjustment": (series.volume_adjustment[-1] if series.volume_adjustment else "unverified"),
                "vintage_status": (series.vintage_status[-1] if series.vintage_status else None),
                "tri_verified": bool(series.tri_verified),
                "reconstruction_mode": series.reconstruction_mode,
                "identity_confidence": dict(series.venue_metadata).get("identity_confidence"),
                "industry_source": dict(series.venue_metadata).get("industry_source"),
                "halted": bool(raw.halted),
                "currently_tradable": bool(raw.currently_tradable),
                "zero_volume": bool(raw.zero_volume),
            }
        )
    seen = {row["security_id"] for row in rows}
    for reject in pool_rejects:
        if reject["security_id"] not in seen:
            rows.append(reject)
            seen.add(reject["security_id"])
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
        "candidate_ids": sorted(candidate_ids),
        "reference_ids": sorted(reference_ids),
        "late_securities": sorted(late),
        "rows": rows,
        "network_calls": 0,
    }


def snapshot_fingerprint(payload: Mapping[str, Any]) -> str:
    clone = {k: v for k, v in payload.items() if k != "as_of"}
    return hashlib.sha256(json.dumps(clone, sort_keys=True, default=str).encode()).hexdigest()
