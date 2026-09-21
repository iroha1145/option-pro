"""EOD limited worker: one shared bar fetch, then profile/horizon variants."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import last_complete_eod_session
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.research_eod_v1.fixtures import make_series, structured_close, trading_days_ending
from app.services.research_eod_v1.membership import has_complete_session_bar

from . import (
    COMPUTE_VERSION,
    PURPOSE_HISTORICAL,
    PURPOSE_LIVE,
    PURPOSE_SYNTHETIC,
    RESEARCH_SEALED_SESSION,
)
from .bars import fetch_current_universe_bars, last_bar_session
from .inference import precompute_session_raws, precompute_theme_raws, score_eod_session
from .panel import bars_to_panel, prepare_limited_panel, select_universe_tickers
from .store import publish_batch, read_batch, variant_key


def resolve_inference_session(now: datetime | None = None, *, source_finalized_through: date | None = None) -> date:
    return last_complete_eod_session(now or datetime.now(timezone.utc), source_finalized_through=source_finalized_through)


def _compact_variant(scored: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "session_date",
        "as_of_session",
        "served_session",
        "attempted_session",
        "generated_at",
        "mode",
        "purpose",
        "compute_version",
        "feature_version",
        "profile",
        "horizon",
        "capability_flags",
        "volume_scope",
        "panel_n",
        "complete_bar_n",
        "family_results",
        "composite_stock",
        "composite_etf",
        "composite_results",
        "watch_list",
        "observation_consensus",
        "eligible_n",
        "watch_n",
        "rejected_n",
        "composite_n",
        "composite_stock_n",
        "composite_etf_n",
        "observation_consensus_n",
        "historical_example",
        "synthetic",
        "scored_security_count",
        "security_status",
        "feature_failure_count",
        "feature_failure_ids",
    )
    compact = {key: scored.get(key) for key in keep}
    # The public projection reads only eligible rows here. Watch rows already
    # live in watch_list; rejected rows contribute only the retained counts and
    # reason summaries. Their full platform histories need not be persisted.
    compact["family_results"] = [
        {
            **block,
            "rows": [row for row in block.get("rows") or [] if row.get("status") == "eligible"],
        }
        for block in scored.get("family_results") or []
    ]
    return compact


def run_eod_limited_job(
    *,
    profile: str = "balanced",
    horizon: str = "mid",
    session: date | None = None,
    purpose: str = PURPOSE_LIVE,
    all_variants: bool = False,
    panel: Mapping[str, Any] | None = None,
    volume_verified: bool = False,
    root=None,
    now: datetime | None = None,
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
    tickers: Sequence[str] | None = None,
    synthetic_input: bool = False,
    refresh_context: bool | None = None,
) -> dict[str, Any]:
    started = time.perf_counter()
    live_input = purpose == PURPOSE_LIVE and not synthetic_input
    context_requested = panel is None and live_input if refresh_context is None else refresh_context
    market_input = live_input and panel is None
    if market_input and tickers is not None:
        raise ValueError("live_all_market_job_does_not_allow_ticker_subsets")
    if market_input and (themes is not None or algorithms is not None):
        raise ValueError("live_all_market_job_requires_all_scoring_contexts")
    if market_input and volume_verified:
        raise ValueError("all_market_volume_qualification_is_unverified")
    if market_input or any("all_market_stocks" in getattr(series, "theme_ids", ()) for series in (panel or {}).values()):
        from .market_registry import load_market_registry
        registry = load_market_registry()
    else:
        registry = load_registry()
    target = session
    attempted_session = session
    coverage: list[dict[str, Any]] = []
    manifest: dict[str, Any] = {}
    if panel is None:
        if purpose == PURPOSE_SYNTHETIC:
            panel = build_synthetic_panel(end=target)
            target = target or max(series.dates[-1] for series in panel.values())
        elif market_input:
            from .market_data import load_all_market_panel
            target = target or resolve_inference_session(now)
            attempted_session = target
            panel, coverage, manifest = load_all_market_panel(end=target, root=root, tickers=tickers)
        else:
            target = target or resolve_inference_session(now)
            attempted_session = target
            appearances = select_universe_tickers(tickers)
            bars = fetch_current_universe_bars(end=target, tickers=list(appearances))
            bar_session = last_bar_session(bars)
            if bar_session is not None:
                target = min(target, bar_session)
            panel, coverage = bars_to_panel(bars, appearances, end=target)
    if target is None:
        raise ValueError("eod_limited_session_unresolved")
    if purpose == PURPOSE_LIVE and session is None:
        calendar = resolve_inference_session(now)
        if target > calendar:
            target = calendar
    if purpose == PURPOSE_LIVE and target == RESEARCH_SEALED_SESSION:
        purpose = PURPOSE_HISTORICAL
    attempted_session = attempted_session or target
    previous = read_batch(root) or {}
    try:
        previous_session = date.fromisoformat(str(previous.get("served_session") or ""))
    except ValueError:
        previous_session = None
    failure_reason = None
    if (
        live_input
        and previous.get("purpose") == PURPOSE_LIVE
        and not previous.get("synthetic")
        and previous_session is not None
        and target < previous_session
    ):
        failure_reason = "older_session_than_published"
    elif not any(has_complete_session_bar(series, target) for series in panel.values()):
        failure_reason = "no_complete_session_bars"
    elif market_input and (
        manifest.get("status") != "complete"
        or int(manifest.get("eligible_count") or 0) <= 0
        or int(manifest.get("complete_bar_count") or 0) < 0.90 * int(manifest["eligible_count"])
    ):
        failure_reason = "all_market_coverage_incomplete"
    if failure_reason:
        return {
            "status": "DATA_UNAVAILABLE",
            "session": attempted_session.isoformat(),
            "served_session": previous.get("served_session"),
            "purpose": purpose,
            "compute_version": COMPUTE_VERSION,
            "available_variants": sorted(previous.get("variants") or {}),
            "elapsed_s": round(time.perf_counter() - started, 3),
            "coverage": manifest,
            "publish": {
                "ok": False,
                "reason": failure_reason,
                "integrity": "stale_previous_retained" if previous else "unavailable",
                "served_session": previous.get("served_session"),
                "attempted_session": attempted_session.isoformat(),
            },
        }
    panel = prepare_limited_panel(panel)
    wanted = [(profile, horizon)]
    if all_variants or market_input:
        wanted = [(item_profile, item_horizon) for item_horizon in HORIZONS for item_profile in PROFILES]
    same_inputs = (
        previous.get("served_session") == target.isoformat()
        and previous.get("compute_version") == COMPUTE_VERSION
        and not market_input
    )
    variants = dict(previous.get("variants") or {}) if same_inputs else {}
    horizon_inputs = {}
    snapshot_cache = {}
    if market_input:
        from .inference import precompute_all_horizon_inputs
        horizon_inputs = precompute_all_horizon_inputs(
            panel, target, registry=registry,
            horizons=list(dict.fromkeys(item_horizon for _, item_horizon in wanted)),
            themes=themes,
        )
    for item_profile, item_horizon in wanted:
        if item_horizon not in horizon_inputs:
            raws, clipped = precompute_session_raws(panel, target, registry=registry, horizon=item_horizon)
            theme_raws = precompute_theme_raws(raws, clipped, target, registry=registry, themes=themes)
            horizon_inputs[item_horizon] = (raws, clipped, theme_raws)
        raws, clipped, theme_raws = horizon_inputs[item_horizon]
        scored = score_eod_session(
            panel,
            target,
            registry=registry,
            profile=item_profile,
            horizon=item_horizon,
            volume_verified=volume_verified,
            purpose=purpose,
            precomputed_raws=raws,
            clipped_panel=clipped,
            precomputed_theme_raws=theme_raws,
            themes=themes,
            algorithms=algorithms,
            **({"compact": True, "snapshot_cache": snapshot_cache} if market_input else {}),
        )
        if synthetic_input:
            scored["synthetic"] = True
        variants[variant_key(item_profile, item_horizon)] = _compact_variant(scored)
    if market_input:
        manifest["scored_count"] = max(int(item.get("scored_security_count") or 0) for item in variants.values())
        if any(int(item.get("scored_security_count") or 0) != len(panel) for item in variants.values()):
            raise RuntimeError("all_market_scoring_coverage_incomplete")
        for item in variants.values():
            item["volume_scope"] = manifest.get("volume_session_scope")
    batch = {
        "version": 1,
        "purpose": purpose,
        "synthetic": synthetic_input or purpose == PURPOSE_SYNTHETIC,
        "compute_version": COMPUTE_VERSION,
        "attempted_session": attempted_session.isoformat(),
        "served_session": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available_variants": sorted(variants),
        "coverage_empty": [row["ticker"] for row in coverage if row.get("status") != "ok"],
        "coverage_ok": sum(1 for row in coverage if row.get("status") == "ok"),
        "universe": "all_market" if market_input else "injected_panel",
        "coverage": manifest,
        "coverage_records": coverage,
        "variants": variants,
    }
    published = publish_batch(batch, root=root)
    context_outcome = None
    if published.get("ok") and context_requested:
        try:
            from .context_snapshot import refresh_context_snapshot

            context_outcome = refresh_context_snapshot(root=root, now=now)
        except Exception as exc:
            # Ranking publication is already complete and must stay available.
            context_outcome = {"status": "UNAVAILABLE", "published": False, "error": type(exc).__name__}
    return {
        "status": "RAN" if published.get("ok") else "PUBLISH_FAILED",
        "compute_version": COMPUTE_VERSION,
        "session": target.isoformat(),
        "served_session": published.get("served_session"),
        "published_at": published.get("published_at"),
        "purpose": purpose,
        "available_variants": sorted(variants),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "publish": published,
        "context": context_outcome,
        "coverage": manifest,
    }


def build_synthetic_panel(*, sessions: int = 380, end: date | None = None) -> dict[str, Any]:
    last = end or date(2023, 7, 10)
    days = trading_days_ending(last, sessions)
    specs = (
        ("NVDA", ("semiconductors", "ai_cloud"), "stock", "CS", "semiconductors", "technology", 40, 0.12, 16),
        ("AMD", ("semiconductors",), "stock", "CS", "semiconductors", "technology", 30, 0.10, 18),
        ("AVGO", ("semiconductors",), "stock", "CS", "semiconductors", "technology", 55, 0.09, 15),
        ("TSM", ("semiconductors",), "stock", "CS", "semiconductors", "technology", 48, 0.08, 17),
        ("MU", ("semiconductors",), "stock", "CS", "semiconductors", "technology", 28, 0.11, 14),
        ("INTC", ("semiconductors",), "stock", "CS", "semiconductors", "technology", 22, 0.04, 19),
        ("SPY", ("etfs",), "etf", "ETF", None, None, 210, 0.07, 20),
        ("QQQ", ("etfs",), "etf", "ETF", None, None, 200, 0.06, 16),
    )
    panel = {}
    for ticker, themes, track, security_type, industry, parent, start, drift, cycle in specs:
        panel[ticker] = make_series(
            ticker,
            days,
            structured_close(len(days), start, drift, cycle),
            theme_ids=themes,
            asset_track=track,
            security_type=security_type,
            industry_id=industry,
            parent_industry_id=parent,
        ).with_close_price_return()
    return panel


def seed_labeled_batch(
    *,
    purpose: str,
    session: date | None = None,
    profile: str = "balanced",
    horizon: str = "mid",
    root=None,
    all_variants: bool = False,
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Write a labeled historical/synthetic snapshot. Never marks 2024-06-28 as latest."""

    if purpose == PURPOSE_LIVE:
        raise ValueError("seed_labeled_batch refuses purpose=live_eod_inference")
    target = session
    if purpose == PURPOSE_HISTORICAL:
        target = target or RESEARCH_SEALED_SESSION
    return run_eod_limited_job(
        profile=profile,
        horizon=horizon,
        session=target,
        purpose=purpose,
        all_variants=all_variants,
        panel=build_synthetic_panel(end=target),
        root=root,
        themes=themes,
        algorithms=algorithms,
        synthetic_input=True,
    )
