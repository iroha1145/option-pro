"""EOD limited worker: one shared bar fetch, then profile/horizon variants."""

from __future__ import annotations

import time
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import last_complete_eod_session
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import HORIZONS, PROFILES
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close

from . import (
    COMPUTE_VERSION,
    PURPOSE_HISTORICAL,
    PURPOSE_LIVE,
    PURPOSE_SYNTHETIC,
    RESEARCH_SEALED_SESSION,
)
from .bars import fetch_current_universe_bars, last_bar_session
from .inference import precompute_session_raws, score_eod_session
from .panel import bars_to_panel, current_universe_tickers
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
    )
    return {key: scored.get(key) for key in keep}


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
) -> dict[str, Any]:
    started = time.perf_counter()
    registry = load_registry()
    target = session
    coverage: list[dict[str, Any]] = []
    if panel is None:
        if purpose == PURPOSE_SYNTHETIC:
            panel = build_synthetic_panel()
            target = target or max(series.dates[-1] for series in panel.values())
        else:
            target = target or resolve_inference_session(now)
            bars = fetch_current_universe_bars(end=target)
            bar_session = last_bar_session(bars)
            if bar_session is not None:
                target = min(target, bar_session)
            panel, coverage = bars_to_panel(bars, current_universe_tickers(), end=target)
    if target is None:
        raise ValueError("eod_limited_session_unresolved")
    if purpose == PURPOSE_LIVE and session is None:
        calendar = resolve_inference_session(now)
        if target > calendar:
            target = calendar
    if purpose == PURPOSE_LIVE and target == RESEARCH_SEALED_SESSION:
        purpose = PURPOSE_HISTORICAL
    wanted = [(profile, horizon)]
    if all_variants:
        wanted = [(item_profile, item_horizon) for item_horizon in HORIZONS for item_profile in PROFILES]
    previous = read_batch(root) or {}
    variants = dict(previous.get("variants") or {}) if previous.get("served_session") == target.isoformat() else {}
    for item_profile, item_horizon in wanted:
        raws, clipped = precompute_session_raws(panel, target, registry=registry, horizon=item_horizon)
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
            themes=themes,
            algorithms=algorithms,
        )
        variants[variant_key(item_profile, item_horizon)] = _compact_variant(scored)
    batch = {
        "version": 1,
        "purpose": purpose,
        "compute_version": COMPUTE_VERSION,
        "attempted_session": target.isoformat(),
        "served_session": target.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "available_variants": sorted(variants),
        "coverage_empty": [row["ticker"] for row in coverage if row.get("status") != "ok"],
        "coverage_ok": sum(1 for row in coverage if row.get("status") == "ok"),
        "variants": variants,
    }
    published = publish_batch(batch, root=root)
    return {
        "status": "RAN" if published.get("ok") else "PUBLISH_FAILED",
        "session": target.isoformat(),
        "served_session": published.get("served_session"),
        "purpose": purpose,
        "available_variants": sorted(variants),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "publish": published,
    }


def build_synthetic_panel(*, sessions: int = 380) -> dict[str, Any]:
    days = trading_days(date(2022, 1, 3), sessions)
    specs = (
        ("NVDA", ("semiconductors", "ai_cloud"), "stock", "CS", 40, 0.12),
        ("AMD", ("semiconductors",), "stock", "CS", 30, 0.10),
        ("SPY", ("etfs",), "etf", "ETF", 210, 0.07),
        ("QQQ", ("etfs",), "etf", "ETF", 200, 0.06),
    )
    panel = {}
    for ticker, themes, track, security_type, start, drift in specs:
        panel[ticker] = make_series(
            ticker,
            days,
            trending_close(sessions, start, drift),
            theme_ids=themes,
            asset_track=track,
            security_type=security_type,
            industry_id=None,
            parent_industry_id=None,
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
        panel=build_synthetic_panel(),
        root=root,
        themes=themes,
        algorithms=algorithms,
    )
