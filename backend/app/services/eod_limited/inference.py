"""Live EOD inference using the same PRICE_ONLY + M1 math as research limited v1.1."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.constants import ALGORITHMS
from app.services.research_eod_v1.factors import apply_sector_gates, extract_raw
from app.services.research_eod_v1.membership import has_complete_session_bar, is_theme_candidate, source_is_available
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.series import clip_panel_to_as_of
from app.services.sectors import SECTORS

from . import COMPUTE_VERSION, MODE_ID, PURPOSE_LIVE, VOLUME_SCOPE
from .panel import prepare_limited_panel
from .price_only import apply_price_only_track, resolve_capability_flags

WARMUP_SESSIONS = 330
UNIVERSE_VERSION = "u_eod_limited_v1"


def observation_consensus(
    rows: Sequence[Mapping[str, Any]],
    profile: str,
    top_k: int,
) -> list[dict[str, Any]]:
    """Reuse M1 collapse rules on watch rows without changing stored status."""

    proxies = []
    for row in rows:
        if row.get("status") != "watch" or row.get("score") is None:
            continue
        copied = dict(row)
        copied["status"] = "eligible"
        copied["_original_status"] = "watch"
        proxies.append(copied)
    out = m1_consensus(proxies, profile, top_k)
    for row in out:
        row["status"] = "watch"
        row["qualification"] = "observation_only"
        row.pop("_original_status", None)
    return out


def precompute_session_raws(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    horizon: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    as_of = eod_evaluation_as_of(session)
    clipped = {
        sid: series.slice_through(session)
        for sid, series in clip_panel_to_as_of(panel, as_of).items()
        if series is not None
    }
    keep = WARMUP_SESSIONS + 40
    clipped = {
        sid: series.last_n(keep)
        for sid, series in clipped.items()
        if series is not None and has_complete_session_bar(series, session)
    }
    seed = next(iter(registry["sectors"]))
    gates = registry["sectors"][seed]["gates"]
    blend = tuple(registry["horizons"][horizon]["momentum_blend"])
    market = clipped.get("SPY")
    raws = {}
    for sid, series in clipped.items():
        raws[sid] = extract_raw(
            series,
            market=market,
            panel=clipped,
            horizon=horizon,
            momentum_blend=blend,  # type: ignore[arg-type]
            sector_gates=gates,
            spy_residual_allowed=True,
        )
    return raws, clipped


def precompute_theme_raws(
    raws: Mapping[str, Any],
    clipped: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    themes: Sequence[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Apply theme geometry once to this horizon's inputs, without changing them.

    Profile and algorithm decisions still run in compute_snapshot. Only the
    profile-independent apply_sector_gates call is shared within one job.
    """

    as_of = eod_evaluation_as_of(session)
    prepared = {}
    for theme_id in themes or SECTORS:
        sector = registry["sectors"][theme_id]
        target_track = "etf" if sector.get("asset_track") == "etf" else "stock"
        theme_raws = dict(raws)
        for sid, series in clipped.items():
            if sid not in raws or not source_is_available(series, as_of):
                continue
            candidate, _reason = is_theme_candidate(
                series, sector_id=theme_id, session=session, target_track=target_track,
            )
            if candidate:
                theme_raws[sid] = apply_sector_gates(raws[sid], series, sector["gates"])
        prepared[theme_id] = theme_raws
    return prepared


def score_eod_session(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    profile: str = "balanced",
    horizon: str = "mid",
    volume_verified: bool = False,
    dollar_liquidity_verified: bool | None = None,
    volume_session_verified: bool | None = None,
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
    purpose: str = PURPOSE_LIVE,
    precomputed_raws: Mapping[str, Any] | None = None,
    clipped_panel: Mapping[str, Any] | None = None,
    precomputed_theme_raws: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    flags = resolve_capability_flags(
        volume_verified=volume_verified,
        dollar_liquidity_verified=dollar_liquidity_verified,
        volume_session_verified=volume_session_verified,
    )
    panel = prepare_limited_panel(panel)
    as_of = eod_evaluation_as_of(session)
    theme_ids = list(themes or SECTORS)
    families = list(algorithms or ALGORITHMS)
    family_results: list[dict[str, Any]] = []
    first_layer: list[dict[str, Any]] = []
    if precomputed_raws is None or clipped_panel is None:
        raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon=horizon)
    else:
        raws, clipped = dict(precomputed_raws), dict(clipped_panel)
    for theme_id in theme_ids:
        theme_raws = None if precomputed_theme_raws is None else precomputed_theme_raws.get(theme_id)
        for algorithm in families:
            raw = compute_snapshot(
                as_of,
                clipped,
                UNIVERSE_VERSION,
                registry,
                sector_id=theme_id,
                algorithm=algorithm,
                profile=profile,
                horizon=horizon,
                source_finalized_through=session,
                precomputed_raws=raws if theme_raws is None else theme_raws,
                reapply_theme_gates=theme_raws is None,
                already_session_clipped=True,
            )
            scored = apply_price_only_track(
                raw,
                registry=registry,
                theme_id=theme_id,
                family=algorithm,
                profile=profile,
                horizon=horizon,
                volume_verified=volume_verified,
                dollar_liquidity_verified=dollar_liquidity_verified,
                volume_session_verified=volume_session_verified,
            )
            rows = [dict(row) for row in scored["rows"]]
            for row in rows:
                series = clipped.get(str(row.get("security_id") or ""))
                if series is not None and getattr(series, "close", None) is not None and len(series.close):
                    close = float(series.close[-1])
                    row["price"] = close
                    row["close"] = close
            counts = Counter(str(row.get("status")) for row in rows)
            reasons = Counter()
            for row in rows:
                for reason in row.get("rejection_reasons") or ():
                    reasons[str(reason)] += 1
            family_results.append({
                "theme_id": theme_id,
                "algorithm_id": algorithm,
                "session_date": session.isoformat(),
                "profile": profile,
                "horizon": horizon,
                "track": PRICE_ONLY_DIAGNOSTIC,
                "eligible": counts.get("eligible", 0),
                "watch": counts.get("watch", 0),
                "rejected": counts.get("rejected", 0),
                "top_rejections": reasons.most_common(8),
                "rows": rows,
            })
            first_layer.extend(rows)
    stock_layer = [row for row in first_layer if row.get("stock_or_etf_track") != "etf"]
    etf_layer = [row for row in first_layer if row.get("stock_or_etf_track") == "etf"]
    composite_stock = [dict(row) for row in m1_consensus(stock_layer, profile, 20)]
    composite_etf = [dict(row) for row in m1_consensus(etf_layer, profile, 20)]
    for row in composite_stock:
        row["stock_or_etf_track"] = "stock"
        row["qualification"] = "eligible"
    for row in composite_etf:
        row["stock_or_etf_track"] = "etf"
        row["qualification"] = "eligible"
    watch = [row for row in first_layer if row.get("status") == "watch"]
    observation = observation_consensus(watch, profile, 20)
    eligible = [row for row in first_layer if row.get("status") == "eligible"]
    return {
        "session_date": session.isoformat(),
        "as_of_session": session.isoformat(),
        "served_session": session.isoformat(),
        "attempted_session": session.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": MODE_ID,
        "purpose": purpose,
        "compute_version": COMPUTE_VERSION,
        "feature_version": FEATURE_VERSION,
        "profile": profile,
        "horizon": horizon,
        "capability_track": PRICE_ONLY_DIAGNOSTIC,
        "capability_flags": flags,
        "volume_scope": VOLUME_SCOPE,
        "panel_n": len(panel),
        "complete_bar_n": len(clipped),
        "family_results": family_results,
        "composite_stock": composite_stock,
        "composite_etf": composite_etf,
        "composite_results": [*composite_stock, *composite_etf],
        "watch_list": watch,
        "observation_consensus": observation,
        "eligible_n": len(eligible),
        "watch_n": len(watch),
        "rejected_n": sum(1 for row in first_layer if row.get("status") == "rejected"),
        "composite_n": len(composite_stock) + len(composite_etf),
        "composite_stock_n": len(composite_stock),
        "composite_etf_n": len(composite_etf),
        "observation_consensus_n": len(observation),
        "historical_example": purpose != PURPOSE_LIVE,
        "synthetic": purpose == "synthetic",
    }
