"""Pure ``compute_snapshot`` — no wall clock, no network."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION
from app.services.research_eod_v1.algorithms import setup_for
from app.services.research_eod_v1.calendar_asof import last_complete_eod_session, require_aware, session_close_at
from app.services.research_eod_v1.data.capture_store import eod_pool_exclusions, series_is_late
from app.services.research_eod_v1.cross_section import q_star
from app.services.research_eod_v1.factors import RawComponents, apply_sector_gates, extract_raw
from app.services.research_eod_v1.mathutil import clip100, finite
from app.services.research_eod_v1.membership import (
    has_complete_session_bar,
    is_theme_candidate,
    source_is_available,
    theme_membership,
)
from app.services.research_eod_v1.registry_scoring import (
    CommonInputs,
    common_rejections,
    resolve_weights,
    score_features,
)
from app.services.research_eod_v1.series import SecuritySeries, clip_panel_to_as_of
from app.services.research_eod_v1.venue import classify_venue

_CS_MEMO: dict[tuple, dict[str, Any]] = {}

# Frozen research policy. ADV is a proxy filter, never a qualification flag.
ATR_REFERENCE_POLICIES = {
    "legacy": {},
    "track_liquid_v1": {"minimum_raw_price": 5.0, "minimum_adv20_proxy": 20_000_000.0, "minimum_group_n": 5},
}


def _atr_references(raws: Mapping[str, RawComponents], policy: str) -> dict[str, tuple[float | None, int, str]]:
    """Upper medians; missing industry identifiers never become an industry."""
    spec = ATR_REFERENCE_POLICIES[policy]
    groups: dict[tuple, list[float]] = defaultdict(list)
    for raw in raws.values():
        if raw.atr_pct is None:
            continue
        if policy == "legacy":
            groups[(raw.industry_id,)].append(raw.atr_pct)
            continue
        if not (
            math.isfinite(raw.atr_pct) and raw.atr_pct > 0
            and raw.raw_close is not None and math.isfinite(raw.raw_close)
            and raw.raw_close >= spec["minimum_raw_price"]
            and raw.last_close is not None and math.isfinite(raw.last_close) and raw.last_close > 0
            and raw.adv20 is not None and math.isfinite(raw.adv20)
            and raw.adv20 >= spec["minimum_adv20_proxy"]
            and raw.currently_tradable and classify_venue(raw.venue_metadata).eligible
        ):
            continue
        groups[(raw.asset_track, "track")].append(raw.atr_pct)
        if raw.industry_id:
            groups[(raw.asset_track, "industry", raw.industry_id)].append(raw.atr_pct)
        if raw.parent_industry_id:
            groups[(raw.asset_track, "parent", raw.parent_industry_id)].append(raw.atr_pct)
    medians = {key: (sorted(values)[len(values) // 2], len(values)) for key, values in groups.items()}
    result = {}
    for sid, raw in raws.items():
        if policy == "legacy":
            value, count = medians.get((raw.industry_id,), (raw.atr_pct or 0.0, 0))
            result[sid] = (value or raw.atr_pct, count, "legacy_all_tracks_industry" if raw.industry_id else "legacy_all_tracks_missing_industry")
            continue
        result[sid] = (None, 0, "unavailable")
        for kind, identifier in (("industry", raw.industry_id), ("parent", raw.parent_industry_id), ("track", None)):
            if kind != "track" and not identifier:
                continue
            key = (raw.asset_track, kind) if kind == "track" else (raw.asset_track, kind, identifier)
            value, count = medians.get(key, (None, 0))
            if count >= (1 if kind == "track" else spec["minimum_group_n"]):
                result[sid] = (value, count, f"{raw.asset_track}:{kind}")
                break
    return result


def _common_gate_checks(raw, registry, sector, profile, algorithm, scored, venue, median):
    """Independent pass/fail facts, including inputs the combined gate cannot assess."""
    p = registry["profiles"][profile]
    spec = registry["sectors"][sector]
    def ge(value, minimum):
        return None if value is None else value >= minimum
    return {
        "venue": venue.eligible,
        "currently_tradable": raw.currently_tradable,
        "price": ge(raw.raw_close, registry["global_rules"]["minimum_raw_price_usd"]),
        "adv20": ge(raw.adv20, max(spec["gates"]["minimum_adv_usd"], p["minimum_adv_usd"])),
        "atr": None if raw.atr_pct is None or median is None else raw.atr_pct <= min(p["atr_absolute_cap_pct"], p["atr_sector_median_multiplier"] * median),
        "extension": None if raw.extension_atr is None else raw.extension_atr <= p["max_extension_atr"],
        "structure": ge(raw.structure_score, p["structure_floor"]),
        "history": raw.history_sessions >= spec["candidates"][algorithm]["min_history_sessions"],
        "upthrust": not raw.unresolved_upthrust,
        "invalidation": not raw.structure_invalidated,
        "score": None if scored.score is None else scored.score >= p["score_floor"],
        "coverage": scored.coverage + 1e-12 >= p["coverage_min"],
    }


def _series_geometry_close(series: SecuritySeries) -> float | None:
    if series.close is None or len(series.close) == 0:
        return None
    value = float(series.close[-1])
    return value if math.isfinite(value) and value > 0 else None


def _geometry_fields(raw: RawComponents) -> dict[str, Any]:
    """Pass geometry through to sizing. Do not import ledger here (cycle)."""

    geometry = raw.last_close
    execution = raw.raw_close if raw.raw_close is not None else geometry
    fraction = None
    invalid = raw.planned_invalidation
    atr = raw.atr
    if (
        geometry is not None
        and execution is not None
        and invalid is not None
        and atr is not None
        and geometry > 0
        and execution > 0
        and invalid > 0
        and invalid < geometry
        and atr > 0
    ):
        scale = execution / geometry
        inv_exec = invalid * scale
        atr_exec = atr * scale
        fraction = max((execution - inv_exec) / execution, atr_exec / execution)
    return {
        "geometry_close": geometry,
        "last_close": geometry,
        "raw_close": raw.raw_close,
        "risk_distance_fraction": fraction,
    }


def _v_state(algorithm: str, raw: RawComponents) -> float | None:
    if algorithm == "B_confirmed_base_breakout":
        track = raw.breakout_track or {}
        first_day_rvol = finite(track.get("first_day_rvol"))
        if first_day_rvol is None or first_day_rvol <= 0:
            return None
        return clip100(50.0 + 30.0 * math.log(first_day_rvol))
    if raw.rvol is None and algorithm != "C_trend_pullback":
        return None
    if algorithm in {"A_trend_quality", "D_residual_momentum"}:
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
        "geometry_close": _series_geometry_close(series),
        "last_close": _series_geometry_close(series),
        "raw_close": None,
        "risk_distance_fraction": None,
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
    reapply_theme_gates: bool = True,
    already_session_clipped: bool = False,
    snapshot_cache: dict | None = None,
    atr_reference_policy: str = "legacy",
) -> dict[str, Any]:
    """Deterministic snapshot. Adding bars after ``as_of`` must not change T.

    ``snapshot_cache`` belongs to one job with an immutable panel and registry.
    Only theme/horizon-independent reference statistics are shared in that job.
    """

    require_aware(as_of)
    if atr_reference_policy not in ATR_REFERENCE_POLICIES:
        raise ValueError(f"unknown ATR reference policy: {atr_reference_policy}")
    session = last_complete_eod_session(
        as_of,
        source_finalized_through=source_finalized_through,
        late_securities=late_securities,
    )
    late = eod_pool_exclusions(late_securities)
    if already_session_clipped:
        panel = {sid: series for sid, series in historical_data.items() if series is not None}
    else:
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
        pool_rejects[-1].update(
            atr_pct=None, sector_median_atr_pct=None, atr_reference_n=0,
            atr_reference_source="unavailable", atr_reference_policy=atr_reference_policy,
            atr_threshold_pct=None, extension_atr=None,
            extension_limit_atr=profile_cfg["max_extension_atr"], common_gate_checks={},
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
    membership_key = ("membership", session, sector_id)
    membership_cache = None if snapshot_cache is None else snapshot_cache.get(membership_key)
    if membership_cache is None:
        membership_cache = {}
        if snapshot_cache is not None:
            snapshot_cache[membership_key] = membership_cache
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
        membership = membership_cache.get(sid)
        if membership is None:
            membership = is_theme_candidate(
                series, sector_id=sector_id, session=session,
                target_track=target_track, extra_members=extra_members,
            )
            membership_cache[sid] = membership
        ok, reason = membership
        if ok:
            candidate_ids.add(sid)
            if reapply_theme_gates and precomputed_raws is not None and sid in precomputed_raws:
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
    job_key = (session, target_track, universe_version)
    cached_cs = None if snapshot_cache is None else snapshot_cache.get(job_key)
    cs_key = None
    if cached_cs is None and snapshot_cache is None:
        cs_key = (
            session,
            tuple(
                sorted(
                    (
                        sid,
                        raw.slope50,
                        raw.m63,
                        raw.m126_skip21,
                        raw.m252_skip21,
                        raw.residual.raw,
                        raw.imbalance20,
                        raw.sigma20,
                        raw.gap_tail252,
                        raw.max_drawdown63,
                        raw.industry_id,
                        raw.parent_industry_id,
                        raw.asset_track,
                    )
                    for sid, raw in xref.items()
                )
            ),
        )
        cached_cs = _CS_MEMO.get(cs_key)
    if cached_cs is None:
        tracks = {sid: raw.asset_track for sid, raw in xref.items()}
        industries = {sid: raw.industry_id for sid, raw in xref.items()}
        parents = {sid: raw.parent_industry_id for sid, raw in xref.items()}
        cached_cs = {
            "q_slope": q_star({s: r.slope50 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_m63": q_star({s: r.m63 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_m126": q_star({s: r.m126_skip21 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_m252": q_star({s: r.m252_skip21 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_resid": q_star({s: r.residual.raw for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_imb": q_star({s: r.imbalance20 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks),
            "q_ns": q_star({s: r.sigma20 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True),
            "q_ng": q_star({s: r.gap_tail252 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True),
            "q_nd": q_star({s: r.max_drawdown63 for s, r in xref.items()}, industry=industries, parent=parents, tracks=tracks, invert=True),
        }
        industry_ret: dict[str, float | None] = {}
        breadth: dict[str, float | None] = {}
        spy_m63 = raws["SPY"].m63 if "SPY" in raws else None
        industry_members: dict[str, list[str]] = defaultdict(list)
        parent_members: dict[str, list[str]] = defaultdict(list)
        for sid, raw in xref.items():
            if raw.industry_id:
                industry_members[raw.industry_id].append(sid)
            if raw.parent_industry_id:
                parent_members[raw.parent_industry_id].append(sid)
        for sid, raw in xref.items():
            peers = [other for other in industry_members.get(raw.industry_id, ()) if other != sid]
            if len(peers) < 5:
                peers = [other for other in parent_members.get(raw.parent_industry_id, ()) if other != sid]
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
        cached_cs["q_g"] = q_star(industry_ret, industry=parents, parent=parents, tracks=tracks)
        cached_cs["breadth"] = breadth
        if snapshot_cache is None:
            if len(_CS_MEMO) >= 8:
                _CS_MEMO.clear()
            _CS_MEMO[cs_key] = cached_cs
        else:
            snapshot_cache[job_key] = cached_cs
    q_slope = cached_cs["q_slope"]
    q_m63 = cached_cs["q_m63"]
    q_m126 = cached_cs["q_m126"]
    q_m252 = cached_cs["q_m252"]
    q_resid = cached_cs["q_resid"]
    q_imb = cached_cs["q_imb"]
    q_ns = cached_cs["q_ns"]
    q_ng = cached_cs["q_ng"]
    q_nd = cached_cs["q_nd"]
    q_g = cached_cs["q_g"]
    breadth = cached_cs["breadth"]

    atr_key = ("atr_references", session, universe_version, atr_reference_policy)
    atr_references = None if snapshot_cache is None else snapshot_cache.get(atr_key)
    if atr_references is None:
        atr_references = _atr_references(raws, atr_reference_policy)
        if snapshot_cache is not None:
            snapshot_cache[atr_key] = atr_references
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
        median_atr, reference_n, reference_source = atr_references[sid]
        common = ()
        if median_atr is None and atr_reference_policy != "legacy":
            common = ("ATR_REFERENCE_UNAVAILABLE",)
        elif raw.raw_close is not None and raw.adv20 is not None and raw.atr_pct is not None and raw.extension_atr is not None and raw.structure_score is not None:
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
                "atr_pct": raw.atr_pct,
                "sector_median_atr_pct": median_atr,
                "atr_reference_n": reference_n,
                "atr_reference_source": reference_source,
                "atr_reference_policy": atr_reference_policy,
                "atr_threshold_pct": None if median_atr is None else min(profile_cfg["atr_absolute_cap_pct"], profile_cfg["atr_sector_median_multiplier"] * median_atr),
                "extension_atr": raw.extension_atr,
                "extension_limit_atr": profile_cfg["max_extension_atr"],
                "common_gate_checks": _common_gate_checks(raw, registry, sector_id, profile, algorithm, scored, venue, median_atr),
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
                **_geometry_fields(raw),
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
