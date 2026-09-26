"""Full-market EOD inference: bounded tuning before the existing PRICE_ONLY + M1 scorer."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, last_completed_session
from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.constants import ALGORITHMS
from app.services.research_eod_v1.factors import apply_sector_gates, extract_raw
from app.services.research_eod_v1.membership import has_complete_session_bar, is_theme_candidate, source_is_available
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.series import SecuritySeries, session_is_halted

from . import COMPUTE_VERSION, MODE_ID, PURPOSE_LIVE, VOLUME_SCOPE
from .panel import prepare_limited_panel
from .geometry_parallel import parallel_geometry, validate_geometry_workers
from .price_only import apply_price_only_track, resolve_capability_flags
from .diagnostics import VariantDiagnostics
from .full_market_tuning import apply_entry_states, prepare_full_market_context, tune_snapshot

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


def _through(series: SecuritySeries, cutoff: date) -> SecuritySeries | None:
    """``series`` cut after ``cutoff``; the same object when nothing would be cut.

    ``slice_through`` copies every array even when the series already ends on
    or before the cutoff, which for the live all-market panel duplicates the
    whole panel. Reuse is limited to series whose slice would be identical.
    """

    last = len(series.dates) - 1
    if (
        last >= 0
        and series.dates[last] <= cutoff
        and not series.dividend_events
        and all(day <= cutoff for day, _value in series.dividends)
        and all(day <= cutoff for day, _ratio in series.splits)
        and bool(series.halted) == session_is_halted(series, last)
    ):
        return series
    return series.slice_through(cutoff)


def session_panel(panel: Mapping[str, Any], session: date) -> dict[str, Any]:
    """Series known at the session's evaluation instant, each with a complete session bar.

    One cut per series at ``min(session, calendar cutoff)``; the result shares
    the input's series (and their date lists) wherever the cut changes nothing.
    """

    cutoff = min(session, last_completed_session(eod_evaluation_as_of(session)))
    keep = WARMUP_SESSIONS + 40
    clipped: dict[str, Any] = {}
    for sid, series in panel.items():
        through = _through(series, cutoff)
        if through is not None and has_complete_session_bar(through, session):
            clipped[sid] = through.last_n(keep)
    return clipped


def precompute_session_raws(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    horizon: str,
    include_setup: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    clipped = session_panel(panel, session)
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
            include_setup=include_setup,
        )
    return raws, clipped


def precompute_theme_raws(
    raws: Mapping[str, Any],
    clipped: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    themes: Sequence[str] | None = None,
    geometry_workers: int = 1,
) -> dict[str, dict[str, Any]]:
    """Apply theme geometry once to this horizon's inputs, without changing them.

    Profile and algorithm decisions still run in compute_snapshot. Only the
    profile-independent apply_sector_gates call is shared within one job.
    """

    validate_geometry_workers(geometry_workers)
    as_of = eod_evaluation_as_of(session)
    prepared = {}
    geometry_cache: dict = {}
    security_themes: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for theme_id in themes or registry["sectors"]:
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
                if geometry_workers == 1:
                    theme_raws[sid] = apply_sector_gates(
                        raws[sid], series, sector["gates"], geometry_cache=geometry_cache,
                    )
                else:
                    security_themes.setdefault(sid, []).append((theme_id, sector["gates"]))
        prepared[theme_id] = theme_raws
    if geometry_workers > 1:
        tasks = ((sid, raws[sid], clipped[sid], gates) for sid, gates in security_themes.items())
        for sid, results in parallel_geometry(tasks, workers=geometry_workers):
            for theme_id, raw in results:
                prepared[theme_id][sid] = raw
    return prepared


def derive_horizon_raws(
    raws: Mapping[str, Any], *, registry: Mapping[str, Any], horizon: str,
) -> dict[str, Any]:
    """Only the raw momentum blend varies by horizon; nested geometry is immutable."""
    weights = registry["horizons"][horizon]["momentum_blend"]
    derived = {}
    for sid, raw in raws.items():
        parts = (raw.m63, raw.m126_skip21, raw.m252_skip21)
        value = None if any(part is None for part in parts) else sum(w * p for w, p in zip(weights, parts))
        derived[sid] = replace(raw, momentum_raw_blend={"short": None, "mid": None, "long": None, horizon: value})
    return derived


def precompute_all_horizon_inputs(
    panel: Mapping[str, Any], session: date, *, registry: Mapping[str, Any],
    horizons: Sequence[str], themes: Sequence[str] | None = None,
    geometry_workers: int = 1,
) -> dict[str, tuple[dict, dict, dict]]:
    """One immutable session panel, one raw extraction, one geometry pass per job."""
    validate_geometry_workers(geometry_workers)
    wanted = list(dict.fromkeys(horizons))
    if not wanted:
        return {}
    panel = prepare_limited_panel(panel)
    seed = wanted[0]
    raws, clipped = precompute_session_raws(
        panel, session, registry=registry, horizon=seed, include_setup=False,
    )
    themed = precompute_theme_raws(
        raws, clipped, session, registry=registry, themes=themes,
        geometry_workers=geometry_workers,
    )
    result = {seed: (raws, clipped, themed)}
    for horizon in wanted[1:]:
        derived = derive_horizon_raws(raws, registry=registry, horizon=horizon)
        # Retain the sparse theme overrides rather than copying every RawComponents
        # object 25 times. Every mapping still exposes the full reference pool.
        derived_themes = {}
        for theme_id, source in themed.items():
            themed_derived = dict(derived)
            overrides = {sid: raw for sid, raw in source.items() if raw is not raws[sid]}
            themed_derived.update(derive_horizon_raws(overrides, registry=registry, horizon=horizon))
            derived_themes[theme_id] = themed_derived
        result[horizon] = (derived, clipped, derived_themes)
    return result


def _compact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Drop only historical geometry detail, keeping scores and all decision fields."""
    return {key: value for key, value in row.items() if key not in {"pivots", "frozen_setup"}}


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
    compact: bool = False,
    snapshot_cache: dict | None = None,
    on_family_rows: Callable[[str, str, Sequence[Mapping[str, Any]], Mapping[str, Any]], None] | None = None,
) -> dict[str, Any]:
    flags = resolve_capability_flags(
        volume_verified=volume_verified,
        dollar_liquidity_verified=dollar_liquidity_verified,
        volume_session_verified=volume_session_verified,
    )
    panel = prepare_limited_panel(panel)
    as_of = eod_evaluation_as_of(session)
    theme_ids = list(themes or registry["sectors"])
    families = list(algorithms or ALGORITHMS)
    family_results: list[dict[str, Any]] = []
    first_layer: list[dict[str, Any]] = []
    status_counts: Counter = Counter()
    security_status: dict[str, dict[str, Any]] = {}
    snapshot_cache = {} if snapshot_cache is None else snapshot_cache
    funnel_diagnostics = VariantDiagnostics(profile=profile, horizon=horizon)
    if precomputed_raws is None or clipped_panel is None:
        raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon=horizon)
    else:
        raws, clipped = dict(precomputed_raws), dict(clipped_panel)
    # A single market-wide context for this view, before any theme/score filters.
    tuning_context = prepare_full_market_context(raws, clipped, session=session, horizon=horizon)
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
                snapshot_cache=snapshot_cache,
            )
            raw = tune_snapshot(
                raw, tuning_context, registry=registry, profile=profile, horizon=horizon,
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
            # v1.5: an extended row is visible in the observation list but never eligible.
            scored = apply_entry_states(scored)
            rows = [dict(row) for row in scored["rows"]]
            for row in rows:
                series = clipped.get(str(row.get("security_id") or ""))
                if series is not None and getattr(series, "close", None) is not None and len(series.close):
                    close = float(series.close[-1])
                    row["price"] = close
                    row["close"] = close
                    row["name"] = dict(series.venue_metadata).get("name") or series.ticker_at_signal
                    row["display_sector_id"] = None if theme_id == "all_market_stocks" else theme_id
            counts = Counter(str(row.get("status")) for row in rows)
            reasons = Counter()
            for row in rows:
                for reason in row.get("rejection_reasons") or ():
                    reasons[str(reason)] += 1
                sid = str(row.get("security_id") or "")
                item = security_status.setdefault(sid, {"security_id": sid, "statuses": set(), "rejection_reasons": set()})
                item["statuses"].add(str(row.get("status")))
                item["rejection_reasons"].update(str(reason) for reason in row.get("rejection_reasons") or ())
            status_counts.update(counts)
            funnel_diagnostics.add_block(theme_id, algorithm, rows)
            if on_family_rows is not None:
                # The callback receives every final scoring decision before the
                # public snapshot drops rejected rows and expensive geometry.
                on_family_rows(theme_id, algorithm, rows, scored.get("weight_provenance") or {})
            retained_rows = rows if not compact else [
                _compact_row(row) for row in rows if row.get("status") in {"eligible", "watch"}
            ]
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
                "rows": retained_rows,
                "rejection_counts": dict(reasons),
                "weight_provenance": scored.get("weight_provenance") or {},
            })
            first_layer.extend(retained_rows)
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
    result = {
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
        "full_market_tuning": tuning_context.summary(),
        "capability_track": PRICE_ONLY_DIAGNOSTIC,
        "capability_flags": flags,
        "volume_scope": VOLUME_SCOPE,
        "panel_n": len(panel),
        "complete_bar_n": len(clipped),
        "scored_security_count": len(security_status),
        "security_status": [
            {"security_id": sid, "statuses": sorted(item["statuses"]), "rejection_reasons": sorted(item["rejection_reasons"])}
            for sid, item in sorted(security_status.items())
        ],
        "feature_failure_count": 0,
        "feature_failure_ids": [],
        "family_results": family_results,
        "composite_stock": composite_stock,
        "composite_etf": composite_etf,
        "composite_results": [*composite_stock, *composite_etf],
        "watch_list": watch,
        "observation_consensus": observation,
        "eligible_n": len(eligible),
        "watch_n": len(watch),
        "rejected_n": status_counts.get("rejected", 0),
        "composite_n": len(composite_stock) + len(composite_etf),
        "composite_stock_n": len(composite_stock),
        "composite_etf_n": len(composite_etf),
        "observation_consensus_n": len(observation),
        "historical_example": purpose != PURPOSE_LIVE,
        "synthetic": purpose == "synthetic",
    }
    result["family_funnels"] = funnel_diagnostics.funnel(result)
    return result
