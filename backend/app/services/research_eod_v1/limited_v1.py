"""LIMITED_CURRENT_UNIVERSE_V1: wire existing research functions, do not invent a second engine.

Current-list Yahoo/local bars → compute_snapshot → PRICE_ONLY_DIAGNOSTIC rescore → M1 → atomic snapshot.
Not a full-market PIT certification and not a weight search.
"""

from __future__ import annotations

import hashlib
import html
import json
import pickle
import time
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.services.market_calendar import trading_sessions
from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.capability import (
    HARD_REJECTIONS,
    PRICE_ONLY_DIAGNOSTIC,
    SCORE_DERIVED_REASONS,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES, SCORE_FLOORS
from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.data.to_series import bars_to_series
from app.services.research_eod_v1.data.yahoo import YahooDiagnosticProvider
from app.services.research_eod_v1.data_readiness import TRUSTED_RELATIVE_PICKLES, load_trusted_research_bars
from app.services.research_eod_v1.eod_shadow import (
    atomic_write_json,
    publish_snapshot,
    read_snapshot,
)
from app.services.research_eod_v1.paths import REPO_ROOT, ensure_reference_on_path
from app.services.research_eod_v1.runs import canonical_json, run_signature
from app.services.research_eod_v1.factors import extract_raw
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.series import clip_panel_to_as_of
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS
from app.services.sectors import SECTORS

ensure_reference_on_path()
from registry import resolve_weights  # type: ignore  # noqa: E402

MODE = "LIMITED_CURRENT_UNIVERSE_V1"
COMPUTE_VERSION = "limited-current-v1.1"
EVIDENCE_STATUS = "UNVALIDATED_LIMITED_DATA"
MEMBER_POLICY = "CURRENT_MEMBERSHIP"
UNIVERSE_VERSION = "u_limited_current_v1_1"
ALLOWED_END = date(2024, 6, 28)
HOLDOUT_START = date(2024, 7, 1)
HISTORY_START = date(2018, 1, 2)
DOWNLOAD_END_EXCLUSIVE = date(2024, 6, 29)
ACCEPTANCE_SESSIONS = 20
WARMUP_SESSIONS = 330
VOLUME_SCOPE = "VENDOR_DAILY_UNVERIFIED"
MOMENTUM_BASIS = "price_return_not_total_return"
RETURN_BASIS = "close_price_return"
RETURN_TRANSFORM_VERSION = "limited-v1-close-price-return-v1"
DOLLAR_LIQUIDITY_UNVERIFIED = "DOLLAR_LIQUIDITY_UNVERIFIED"
VOLUME_SESSION_UNVERIFIED = "VOLUME_SESSION_UNVERIFIED"
CACHE_RELATIVE = Path("research/option_pro_us_eod_v1/data/cache/limited_current_universe_v1/bars.pkl")
PACK_RELATIVE = Path("research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1_1")
PREVIEW_STATE_NAME = "preview_state.json"

NETWORK_COUNTER = {"yahoo_batch_downloads": 0, "preview_provider_calls": 0}

LIMITATIONS = (
    "CURRENT_MEMBERSHIP only; not historical PIT constituents or delist union",
    "Yahoo auto_adjust=False does not prove Close is historical raw trade price",
    "volume_scope=VENDOR_DAILY_UNVERIFIED; not regular-session or share-basis proof",
    "momentum and labels are price returns, not total return",
    "execution / dollar risk / corporate-action ledger unverified",
    "G dropped once via PRICE_ONLY_DIAGNOSTIC; D is market-residual diagnostic",
    "industry_id/parent_industry_id stay None without independent classification",
    "M/D read Close via close_price_return; vendor Adj Close/tri is preserved aside",
    "dollar/share ADV unverified demotes every family to watch; session-volume proof is separate",
    "not a 10-year formal verification; evidence_status=UNVALIDATED_LIMITED_DATA",
    "holdout from 2024-07-01 stays sealed",
)

FEATURE_MAPPING = (
    {
        "source_module": "factors.extract_raw / pivots",
        "shared_feature": "structure, ATR, support/resistance, platform geometry",
        "used_by": "T, S, B/C setup, planned_invalidation",
        "raw_interpretable": True,
    },
    {
        "source_module": "factors.extract_raw momentum",
        "shared_feature": "price-return M windows (not TRI/total return)",
        "used_by": "M, D residual input",
        "raw_interpretable": True,
    },
    {
        "source_module": "residual.py",
        "shared_feature": "SPY-market residual (not economic-industry residual)",
        "used_by": "D_residual_momentum on D_MARKET_RESIDUAL_DIAGNOSTIC / PRICE_ONLY",
        "raw_interpretable": True,
    },
    {
        "source_module": "cross_section.q_star",
        "shared_feature": "same-track reference-pool midranks",
        "used_by": "T/M/S/R/G assembly before score_features",
        "raw_interpretable": True,
    },
    {
        "source_module": "algorithms.setup_for",
        "shared_feature": "A/B/C/D hard setup gates",
        "used_by": "eligible vs rejected; not UI copy",
        "raw_interpretable": True,
    },
    {
        "source_module": "Yahoo volume column",
        "shared_feature": "vendor daily volume under VENDOR_DAILY_UNVERIFIED",
        "used_by": "V diagnostic only; B/C liquidity hard-gate stays unverified",
        "raw_interpretable": False,
    },
)


def build_synthetic_panel(*, sessions: int = 380) -> dict[str, Any]:
    """Engineering fixture only. Never mixed into a real-cache identity."""

    from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close

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


def current_universe_tickers() -> dict[str, list[str]]:
    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(str(ticker).upper(), []).append(theme_id)
    for extra in ("SPY", "QQQ"):
        appearances.setdefault(extra, appearances.get(extra, []))
    return appearances


def inventory_inputs(root: Path | None = None) -> list[dict[str, Any]]:
    base = Path(root or REPO_ROOT)
    rows = []
    candidates = [
        *TRUSTED_RELATIVE_PICKLES,
        str(CACHE_RELATIVE),
        "research/option_pro_us_eod_v1/return_pack/yahoo_current_universe/daily_bars.parquet",
        "research/option_pro_us_eod_v1/return_pack/fixtures/synthetic_daily_bars.parquet",
    ]
    for rel in candidates:
        path = base / rel
        rows.append({
            "path": rel,
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else 0,
            "role": "trusted_yahoo_cache" if rel in TRUSTED_RELATIVE_PICKLES else "optional",
        })
    return rows


def acceptance_sessions(*, end: date = ALLOWED_END, count: int = ACCEPTANCE_SESSIONS) -> list[date]:
    """Last ``count`` official development sessions on or before end. Not chosen by performance."""

    official = trading_sessions(HISTORY_START, end)
    if len(official) < count:
        raise ValueError("not_enough_official_sessions")
    chosen = official[-count:]
    if chosen[-1] >= HOLDOUT_START:
        raise ValueError("acceptance_window_must_stay_in_development_zone")
    return chosen


def clip_bars(bars: Sequence[ResearchBar], *, end: date = ALLOWED_END) -> list[ResearchBar]:
    return [bar for bar in bars if bar.session_date <= end and bar.session_date < HOLDOUT_START]


def dataset_hash(bars: Mapping[str, Sequence[ResearchBar]], *, include_vendor_tri: bool = False) -> str:
    """Hash the series this limited path actually consumes. Vendor TRI is a source side-digest."""

    digest = hashlib.sha256()
    digest.update(f"return_basis={RETURN_BASIS}|transform={RETURN_TRANSFORM_VERSION}\n".encode())
    for symbol in sorted(bars):
        for bar in clip_bars(bars[symbol]):
            digest.update(
                (
                    f"{symbol}|{bar.session_date}|{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}"
                    f"|used_return={bar.close}|vscope={bar.volume_scope}|padj={bar.price_adjustment}\n"
                ).encode()
            )
            if include_vendor_tri:
                digest.update(f"vendor_tri|{bar.tri}\n".encode())
    return digest.hexdigest()


def source_dataset_hash(bars: Mapping[str, Sequence[ResearchBar]]) -> str:
    return dataset_hash(bars, include_vendor_tri=True)


def prepare_limited_panel(panel: Mapping[str, Any]) -> dict[str, Any]:
    """Close-price return view; drop unverified theme-as-industry labels."""

    prepared = {}
    for sid, series in panel.items():
        viewed = series.with_close_price_return() if hasattr(series, "with_close_price_return") else series
        viewed.industry_id = None
        viewed.parent_industry_id = None
        prepared[sid] = viewed
    return prepared


def _venue(track: str) -> dict[str, str]:
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "none_without_independent_classification",
        "return_basis": RETURN_BASIS,
        "return_transform_version": RETURN_TRANSFORM_VERSION,
    }


def bars_to_panel(
    bars: Mapping[str, Sequence[ResearchBar]],
    appearances: Mapping[str, Sequence[str]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    panel: dict[str, Any] = {}
    coverage: list[dict[str, Any]] = []
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or list(themes) == ["etfs"] else "stock"
        usable = clip_bars(bars.get(ticker) or [])
        row = {"ticker": ticker, "themes": list(themes), "bars": len(usable), "status": "ok" if usable else "empty"}
        if not usable:
            coverage.append(row)
            continue
        try:
            series = bars_to_series(
                usable,
                security_id=ticker,
                asset_track=track,
                theme_ids=tuple(themes) or (("etfs",) if track == "etf" else ()),
                industry_id=None,
                parent_industry_id=None,
                venue_metadata=_venue(track),
            )
        except ValueError as exc:
            row["status"] = f"invalid:{exc}"
            coverage.append(row)
            continue
        if series is None:
            row["status"] = "empty"
            coverage.append(row)
            continue
        panel[ticker] = series.with_close_price_return()
        coverage.append(row)
    return panel, coverage


def _load_pickle_bars(path: Path) -> dict[str, list[ResearchBar]]:
    payload = pickle.loads(path.read_bytes())
    if not isinstance(payload, dict):
        return {}
    return {str(key).upper(): list(value or []) for key, value in payload.items()}


def load_or_fetch_bars(
    *,
    root: Path | None = None,
    allow_network: bool = False,
    provider: YahooDiagnosticProvider | None = None,
    sleep: Callable[[float], None] = time.sleep,
    batch_size: int = 20,
    max_retries: int = 3,
) -> dict[str, Any]:
    """Local trusted/limited cache first. Yahoo only fills missing current-list names."""

    base = Path(root or REPO_ROOT)
    appearances = current_universe_tickers()
    wanted = list(appearances)
    batched: dict[str, list[ResearchBar]] = {}
    sources: list[str] = []
    for rel in TRUSTED_RELATIVE_PICKLES:
        path = base / rel
        if path.is_file():
            try:
                loaded = load_trusted_research_bars(path, root=base)
            except Exception:
                loaded = _load_pickle_bars(path)
            batched.update({key: clip_bars(value) for key, value in loaded.items()})
            sources.append(rel)
    limited = base / CACHE_RELATIVE
    if limited.is_file():
        batched.update({key: clip_bars(value) for key, value in _load_pickle_bars(limited).items()})
        sources.append(str(CACHE_RELATIVE))

    missing = [ticker for ticker in wanted if not batched.get(ticker)]
    failures: list[dict[str, Any]] = []
    fetched = 0
    if missing and allow_network:
        adapter = provider or YahooDiagnosticProvider(allow_network=True)
        for offset in range(0, len(missing), batch_size):
            chunk = missing[offset : offset + batch_size]
            delay = 2.0
            got: dict[str, list[ResearchBar]] = {}
            for attempt in range(max_retries):
                got = adapter.fetch_daily_bars_batch(chunk, HISTORY_START, DOWNLOAD_END_EXCLUSIVE)
                NETWORK_COUNTER["yahoo_batch_downloads"] += 1
                if any(clip_bars(value) for value in got.values()) or attempt + 1 == max_retries:
                    break
                sleep(delay)
                delay *= 2
            for ticker, rows in got.items():
                clipped = clip_bars(rows)
                batched[ticker] = clipped
                if clipped:
                    fetched += 1
            failures.extend(list(adapter.failures))
            adapter.failures.clear()
        limited.parent.mkdir(parents=True, exist_ok=True)
        limited.write_bytes(pickle.dumps(batched))
        sources.append(str(CACHE_RELATIVE))
    elif missing and not allow_network:
        failures.append({"stage": "cache", "error": "missing_symbols_network_disabled", "count": len(missing)})

    return {
        "bars": batched,
        "appearances": appearances,
        "sources": sources,
        "missing": [ticker for ticker in wanted if not batched.get(ticker)],
        "fetched": fetched,
        "failures": failures,
        "dataset_hash": dataset_hash(batched),
        "source_dataset_hash": source_dataset_hash(batched),
        "inventory": inventory_inputs(base),
        "allow_network": allow_network,
    }


def theme_membership_identity(
    appearances: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, list[str]]:
    if appearances is not None:
        return {str(sid): [str(theme) for theme in themes] for sid, themes in appearances.items()}
    return {theme_id: list(sector["tickers"]) for theme_id, sector in SECTORS.items()}


def limited_config_hash(
    *,
    registry: Mapping[str, Any],
    profile: str,
    horizon: str,
    track: str,
    data_hash: str,
    theme_members: Mapping[str, Sequence[str]] | None = None,
) -> str:
    body = {
        "capability_policy": {
            "dollar_liquidity": "watch_all_families_when_unverified",
            "industry": "none_without_independent_source",
            "residual": "market_only",
            "track": track,
            "volume_session": VOLUME_SCOPE,
        },
        "compute_version": COMPUTE_VERSION,
        "data_hash": data_hash,
        "feature_version": FEATURE_VERSION,
        "horizon": horizon,
        "member_policy": MEMBER_POLICY,
        "mode": MODE,
        "profile": profile,
        "reference_policy": "same_track_plus_spy_qqq",
        "registry": registry,
        "return_basis": RETURN_BASIS,
        "return_transform_version": RETURN_TRANSFORM_VERSION,
        "theme_members": theme_membership_identity(theme_members),
        "track": track,
        "universe_version": UNIVERSE_VERSION,
    }
    return hashlib.sha256(canonical_json(body).encode()).hexdigest()


def apply_price_only_track(
    payload: Mapping[str, Any],
    *,
    registry: Mapping[str, Any],
    theme_id: str,
    family: str,
    profile: str,
    horizon: str,
    volume_verified: bool,
    dollar_liquidity_verified: bool | None = None,
    volume_session_verified: bool | None = None,
) -> dict[str, Any]:
    dollar_ok = volume_verified if dollar_liquidity_verified is None else dollar_liquidity_verified
    session_ok = volume_verified if volume_session_verified is None else volume_session_verified
    weights = resolve_weights(registry, theme_id, family, profile, horizon)
    track = PRICE_ONLY_DIAGNOSTIC
    diag = diagnostic_weights(weights, track=track, family=family)
    coverage_min = float(registry["profiles"][profile]["coverage_min"])
    required = family_required(family)
    score_floor = float(SCORE_FLOORS[profile])
    rows = []
    for row in payload.get("rows") or []:
        updated = dict(row)
        updated["track"] = track
        updated["volume_scope"] = VOLUME_SCOPE if not session_ok else row.get("volume_scope")
        updated["momentum_basis"] = MOMENTUM_BASIS
        updated["return_basis"] = RETURN_BASIS
        inherited = [str(reason) for reason in (updated.get("rejection_reasons") or ())]
        updated["rejection_reasons"] = [reason for reason in inherited if reason not in SCORE_DERIVED_REASONS]
        if updated.get("factors"):
            scored = rescore_row(
                updated,
                diag,
                coverage_min=coverage_min,
                required=required,
                score_floor=score_floor,
            )
            updated["score"] = scored["score"]
            updated["observed_feature_coverage"] = scored["coverage"]
            updated["effective_weights"] = diag
            hard = [
                reason
                for reason in scored["rejection_reasons"]
                if reason in HARD_REJECTIONS or reason not in SCORE_DERIVED_REASONS
            ]
            updated["rejection_reasons"] = list(dict.fromkeys(hard))
            updated["status"] = "eligible" if scored["final_eligible"] else "rejected"
        if updated.get("status") == "eligible" and not dollar_ok:
            updated["status"] = "watch"
            reasons = list(updated.get("rejection_reasons") or [])
            if DOLLAR_LIQUIDITY_UNVERIFIED not in reasons:
                reasons.append(DOLLAR_LIQUIDITY_UNVERIFIED)
            updated["rejection_reasons"] = reasons
        if (
            family in {"B_confirmed_base_breakout", "C_trend_pullback"}
            and not session_ok
            and updated.get("status") in {"eligible", "watch"}
        ):
            reasons = list(updated.get("rejection_reasons") or [])
            if VOLUME_SESSION_UNVERIFIED not in reasons:
                reasons.append(VOLUME_SESSION_UNVERIFIED)
            updated["rejection_reasons"] = reasons
            if updated.get("status") == "eligible":
                updated["status"] = "watch"
        rows.append(updated)
    out = dict(payload)
    out["rows"] = rows
    out["track"] = track
    out["capability_track"] = track
    return out


def _compact_row(row: Mapping[str, Any]) -> dict[str, Any]:
    keep = (
        "security_id",
        "ticker_at_signal",
        "session_date",
        "algorithm_id",
        "profile",
        "horizon",
        "sector_context",
        "status",
        "score",
        "rejection_reasons",
        "setup_state",
        "factors",
        "track",
        "volume_scope",
        "momentum_basis",
        "return_basis",
        "observed_feature_coverage",
        "stock_or_etf_track",
        "theme_ids",
        "gate_results",
        "effective_weights",
        "known_support",
        "known_resistance",
        "planned_invalidation",
        "residual_raw",
        "residual_status",
    )
    return {key: row.get(key) for key in keep}


def row_compare_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "security_id": row.get("security_id"),
        "algorithm_id": row.get("algorithm_id"),
        "sector_context": row.get("sector_context"),
        "status": row.get("status"),
        "score": row.get("score"),
        "factors": row.get("factors"),
        "rejection_reasons": list(row.get("rejection_reasons") or []),
        "stock_or_etf_track": row.get("stock_or_etf_track"),
    }


def _compact_composite(row: Mapping[str, Any]) -> dict[str, Any]:
    compact = _compact_row(row)
    votes = row.get("family_votes") or ()
    compact["consensus_z"] = row.get("consensus_z")
    compact["family_votes"] = list(votes)
    compact["theme_count"] = row.get("theme_count")
    compact["R"] = row.get("R")
    compact["G"] = row.get("G")
    compact["stock_or_etf_track"] = row.get("stock_or_etf_track")
    compact["return_basis"] = row.get("return_basis") or RETURN_BASIS
    return compact


def _compact_load(load_report: Mapping[str, Any], coverage: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    empty = [str(row.get("ticker")) for row in coverage if row.get("status") != "ok"]
    return {
        "sources": list(load_report.get("sources") or []),
        "missing": list(load_report.get("missing") or []),
        "fetched": load_report.get("fetched"),
        "dataset_hash": load_report.get("dataset_hash"),
        "inventory": load_report.get("inventory"),
        "allow_network": load_report.get("allow_network"),
        "wanted_n": len(load_report.get("appearances") or {}),
        "failures": load_report.get("failures") or [],
        "coverage_ok": sum(1 for row in coverage if row.get("status") == "ok"),
        "coverage_empty": empty,
    }


def precompute_session_raws(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    horizon: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """One extract_raw pass per name. Theme B/C gates are reapplied in compute_snapshot."""

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


def score_session(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    profile: str = "balanced",
    horizon: str = "mid",
    volume_verified: bool = False,
    data_hash: str = "",
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
    precomputed_raws: Mapping[str, Any] | None = None,
    clipped_panel: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if session >= HOLDOUT_START or session > ALLOWED_END:
        raise ValueError("session_outside_development_zone")
    panel = prepare_limited_panel(panel)
    as_of = eod_evaluation_as_of(session)
    theme_ids = list(themes or SECTORS)
    families = list(algorithms or ALGORITHMS)
    family_results: list[dict[str, Any]] = []
    first_layer: list[dict[str, Any]] = []
    theme_summaries: list[dict[str, Any]] = []
    if precomputed_raws is None or clipped_panel is None:
        raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon=horizon)
    else:
        raws, clipped = dict(precomputed_raws), dict(clipped_panel)
    for theme_id in theme_ids:
        family_block: dict[str, Any] = {}
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
                precomputed_raws=raws,
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
            )
            rows = [_compact_row(row) for row in scored["rows"]]
            counts = Counter(str(row.get("status")) for row in rows)
            reasons = Counter()
            for row in rows:
                for reason in row.get("rejection_reasons") or ():
                    reasons[str(reason)] += 1
            block = {
                "theme_id": theme_id,
                "algorithm_id": algorithm,
                "session_date": session.isoformat(),
                "profile": profile,
                "horizon": horizon,
                "track": PRICE_ONLY_DIAGNOSTIC,
                "candidates": len(scored.get("candidate_ids") or []),
                "reference": len(scored.get("reference_ids") or []),
                "eligible": counts.get("eligible", 0),
                "watch": counts.get("watch", 0),
                "rejected": counts.get("rejected", 0),
                "top_rejections": reasons.most_common(8),
                "fingerprint": scored.get("fingerprint"),
                "rows": rows,
            }
            family_block[algorithm] = {k: v for k, v in block.items() if k != "rows"}
            family_results.append(block)
            first_layer.extend(rows)
        theme_summaries.append({
            "theme_id": theme_id,
            "membership": MEMBER_POLICY,
            "members_listed": len(SECTORS[theme_id]["tickers"]),
            "members_in_panel": sum(1 for ticker in SECTORS[theme_id]["tickers"] if ticker in panel),
            "families": family_block,
        })
    stock_layer = [row for row in first_layer if row.get("stock_or_etf_track") != "etf"]
    etf_layer = [row for row in first_layer if row.get("stock_or_etf_track") == "etf"]
    composite_stock = [_compact_composite(row) for row in m1_consensus(stock_layer, profile, 20)]
    composite_etf = [_compact_composite(row) for row in m1_consensus(etf_layer, profile, 20)]
    for row in composite_stock:
        row["stock_or_etf_track"] = "stock"
    for row in composite_etf:
        row["stock_or_etf_track"] = "etf"
    composite = [*composite_stock, *composite_etf]
    watch = [row for row in first_layer if row.get("status") == "watch"]
    config_hash = limited_config_hash(
        registry=registry,
        profile=profile,
        horizon=horizon,
        track=PRICE_ONLY_DIAGNOSTIC,
        data_hash=data_hash,
    )
    identity = run_signature(
        registry=registry,
        profile=profile,
        horizon=horizon,
        label_horizons=(5, 20, 63),
        feature_version=FEATURE_VERSION,
        statistics_version=f"{COMPUTE_VERSION}-no-ic-promotion",
        data_hash=data_hash or "empty",
        universe_version=UNIVERSE_VERSION,
        member_policy=MEMBER_POLICY,
        reference_policy="same_track_plus_spy_qqq",
        start=session,
        end=session,
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="eod_evaluation_as_of",
        capability_mask={
            "track": PRICE_ONLY_DIAGNOSTIC,
            "actual_G_observed": False,
            "mode": MODE,
            "compute_version": COMPUTE_VERSION,
            "return_basis": RETURN_BASIS,
            "return_transform_version": RETURN_TRANSFORM_VERSION,
            "industry_policy": "none_without_independent_source",
            "residual": "market_only",
        },
    )
    return {
        "session_date": session.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": MODE,
        "compute_version": COMPUTE_VERSION,
        "evidence_status": EVIDENCE_STATUS,
        "universe_version": UNIVERSE_VERSION,
        "feature_version": FEATURE_VERSION,
        "config_hash": config_hash,
        "run_signature": identity,
        "profile": profile,
        "horizon": horizon,
        "capability_track": PRICE_ONLY_DIAGNOSTIC,
        "member_policy": MEMBER_POLICY,
        "volume_scope": VOLUME_SCOPE,
        "momentum_basis": MOMENTUM_BASIS,
        "panel_n": len(panel),
        "theme_n": len(theme_ids),
        "family_results": family_results,
        "theme_summaries": theme_summaries,
        "composite_results": composite,
        "composite_stock": composite_stock,
        "composite_etf": composite_etf,
        "watch_list": watch,
        "eligible_n": sum(1 for row in first_layer if row.get("status") == "eligible"),
        "watch_n": len(watch),
        "composite_n": len(composite),
        "composite_stock_n": len(composite_stock),
        "composite_etf_n": len(composite_etf),
        "limitations": list(LIMITATIONS),
        "holdout_sealed": True,
        "data_stale": False,
        "execution_unverified": True,
        "total_return_unverified": True,
        "historical_classification": "CLASSIFICATION_CURRENT",
    }


def smoke_864(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    volume_verified: bool = False,
    data_hash: str = "",
) -> list[dict[str, Any]]:
    rows = []
    for horizon in HORIZONS:
        raws, clipped = precompute_session_raws(panel, session, registry=registry, horizon=horizon)
        for profile in PROFILES:
            scored = score_session(
                panel,
                session,
                registry=registry,
                profile=profile,
                horizon=horizon,
                volume_verified=volume_verified,
                data_hash=data_hash,
                precomputed_raws=raws,
                clipped_panel=clipped,
            )
            for theme in scored["theme_summaries"]:
                for algorithm, block in theme["families"].items():
                    rows.append({
                        "session_date": session.isoformat(),
                        "theme_id": theme["theme_id"],
                        "algorithm_id": algorithm,
                        "profile": profile,
                        "horizon": horizon,
                        "eligible": block["eligible"],
                        "watch": block["watch"],
                        "rejected": block["rejected"],
                        "candidates": block["candidates"],
                        "reference": block["reference"],
                        "status": "RAN" if block["candidates"] or block["rejected"] else "NO_CANDIDATES",
                        "unavailable_reason": None if (block["candidates"] or block["rejected"]) else "no_theme_members_with_complete_bar",
                    })
    return rows


def publish_limited_snapshot(path: Path, scored: Mapping[str, Any]) -> dict[str, Any]:
    rows = []
    for block in scored.get("family_results") or []:
        rows.extend(block.get("rows") or [])
    extra = {
        "mode": MODE,
        "evidence_status": EVIDENCE_STATUS,
        "generated_at": scored.get("generated_at"),
        "profile": scored.get("profile"),
        "horizon": scored.get("horizon"),
        "capability_track": scored.get("capability_track"),
        "run_signature": scored.get("run_signature"),
        "theme_summaries": scored.get("theme_summaries"),
        "compute_version": COMPUTE_VERSION,
        "composite_results": scored.get("composite_results"),
        "composite_stock": scored.get("composite_stock"),
        "composite_etf": scored.get("composite_etf"),
        "watch_list": scored.get("watch_list"),
        "limitations": scored.get("limitations"),
        "volume_scope": VOLUME_SCOPE,
        "momentum_basis": MOMENTUM_BASIS,
        "return_basis": RETURN_BASIS,
        "member_policy": MEMBER_POLICY,
        "holdout_sealed": True,
        "execution_unverified": True,
        "total_return_unverified": True,
        "historical_classification": "CLASSIFICATION_CURRENT",
        "data_stale": False,
        "eligible_n": scored.get("eligible_n"),
        "watch_n": scored.get("watch_n"),
        "composite_n": scored.get("composite_n"),
        "composite_stock_n": scored.get("composite_stock_n"),
        "composite_etf_n": scored.get("composite_etf_n"),
        "panel_n": scored.get("panel_n"),
        "feature_mapping": list(FEATURE_MAPPING),
        "synthetic": bool(scored.get("synthetic")),
    }
    return publish_snapshot(
        path,
        session_date=str(scored["session_date"]),
        config_hash=str(scored["config_hash"]),
        universe_version=UNIVERSE_VERSION,
        rows=rows,
        extra=extra,
    )


def preview_html(snapshot: Mapping[str, Any], *, state: Mapping[str, Any] | None = None) -> str:
    themes = snapshot.get("theme_summaries") or []
    composite_stock = snapshot.get("composite_stock") or [
        row for row in (snapshot.get("composite_results") or []) if row.get("stock_or_etf_track") != "etf"
    ]
    composite_etf = snapshot.get("composite_etf") or [
        row for row in (snapshot.get("composite_results") or []) if row.get("stock_or_etf_track") == "etf"
    ]
    watch = snapshot.get("watch_list") or []
    limitations = snapshot.get("limitations") or LIMITATIONS
    rows = list(snapshot.get("rows") or [])
    if not rows:
        for block in snapshot.get("family_results") or []:
            rows.extend(block.get("rows") or [])
    by_theme: dict[str, dict[str, list[Mapping[str, Any]]]] = {}
    for row in rows:
        theme = str(row.get("sector_context") or "")
        algo = str(row.get("algorithm_id") or "")
        by_theme.setdefault(theme, {}).setdefault(algo, []).append(row)

    def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
        head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
        body = []
        for row in rows:
            body.append("<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    def factor_text(row: Mapping[str, Any]) -> str:
        factors = row.get("factors") or {}
        return " ".join(f"{key}={factors.get(key)}" for key in ("T", "M", "S", "B", "P", "V", "R", "G") if key in factors)

    def family_details(theme_id: str) -> str:
        blocks = []
        for algo in ALGORITHMS:
            family_rows = (by_theme.get(theme_id) or {}).get(algo) or []
            shown = [
                [
                    row.get("security_id"),
                    row.get("score"),
                    row.get("status"),
                    factor_text(row),
                    row.get("rejection_reasons") or row.get("gate_results"),
                    row.get("known_support"),
                    row.get("planned_invalidation"),
                ]
                for row in family_rows[:40]
            ]
            inner = table(("证券", "分数", "资格", "因子", "原因/门", "支撑", "失效"), shown) if shown else "<p>无行。</p>"
            blocks.append(
                f"<details><summary>{html.escape(algo)} · {len(family_rows)} 行</summary>{inner}</details>"
            )
        return "".join(blocks)

    theme_rows = []
    details = []
    for theme in themes:
        families = theme.get("families") or {}
        theme_id = str(theme.get("theme_id"))
        theme_rows.append([
            theme_id,
            theme.get("members_listed"),
            theme.get("members_in_panel"),
            sum(int((families.get(algo) or {}).get("eligible") or 0) for algo in ALGORITHMS),
            sum(int((families.get(algo) or {}).get("watch") or 0) for algo in ALGORITHMS),
            "; ".join(
                f"{algo.split('_')[0]} e={(families.get(algo) or {}).get('eligible', 0)}"
                for algo in ALGORITHMS
            ),
        ])
        details.append(f"<details><summary>主题 {html.escape(theme_id)}</summary>{family_details(theme_id)}</details>")

    def composite_table(items: Sequence[Mapping[str, Any]]) -> str:
        if not items:
            return "<p>零合格，名单为空。</p>"
        return table(
            ("证券", "轨", "共识分", "家族", "状态"),
            [
                [
                    row.get("security_id"),
                    row.get("stock_or_etf_track"),
                    row.get("consensus_z") if row.get("consensus_z") is not None else row.get("score"),
                    ",".join(str(item) for item in (row.get("family_votes") or ())),
                    row.get("status"),
                ]
                for row in items
            ],
        )

    watch_rows = [
        [row.get("security_id"), row.get("algorithm_id"), row.get("sector_context"), row.get("score"), row.get("rejection_reasons")]
        for row in watch[:40]
    ]
    state = state or {}
    integrity = snapshot.get("integrity") or state.get("integrity") or "complete"
    stale = bool(snapshot.get("publish_failed") or integrity == "stale_previous_retained" or state.get("stale"))
    attempted = state.get("attempted_session") or snapshot.get("attempted_session")
    served = state.get("served_session") or snapshot.get("session_date")
    synthetic = bool(snapshot.get("synthetic") or state.get("synthetic"))
    banners = []
    if synthetic:
        banners.append("<p class=\"note\" style=\"background:#fde2e2\"><strong>SYNTHETIC</strong> 合成输入，不是真实行情。</p>")
    if stale:
        banners.append(
            f"<p class=\"note\" style=\"background:#f8d7da\">发布失败或过期。"
            f"attempted_session={html.escape(str(attempted))} · served_session={html.escape(str(served))} · "
            f"integrity={html.escape(str(integrity))}</p>"
        )
    return "\n".join([
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>",
        f"<title>LIMITED_CURRENT_UNIVERSE_V1 {html.escape(str(snapshot.get('session_date')))}</title>",
        "<style>body{font-family:\"WenQuanYi Micro Hei\",\"Noto Sans CJK SC\",\"Droid Sans Fallback\",sans-serif;max-width:1100px;margin:24px auto;color:#111}",
        "table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #ccc;padding:6px;font-size:13px}",
        "th{background:#f3f3f3;text-align:left}.note{background:#fff7e6;padding:12px}",
        "details{margin:8px 0;padding:6px;border:1px solid #ddd}</style></head><body>",
        f"<h1>历史 EOD 预览 · {html.escape(str(snapshot.get('session_date')))}</h1>",
        *banners,
        "<p class=\"note\">这是明确标注日期的历史结果，不是今日选股。",
        f"mode={html.escape(MODE)} · compute={html.escape(COMPUTE_VERSION)} · evidence={html.escape(EVIDENCE_STATUS)} · "
        f"track={html.escape(PRICE_ONLY_DIAGNOSTIC)} · return={html.escape(RETURN_BASIS)}。",
        f"integrity={html.escape(str(integrity))}。不展示 IC、回测收益或研究账本。</p>",
        f"<p>profile={html.escape(str(snapshot.get('profile')))} horizon={html.escape(str(snapshot.get('horizon')))} "
        f"股票综合 {html.escape(str(snapshot.get('composite_stock_n') if snapshot.get('composite_stock_n') is not None else len(composite_stock)))} · "
        f"ETF 综合 {html.escape(str(snapshot.get('composite_etf_n') if snapshot.get('composite_etf_n') is not None else len(composite_etf)))} · "
        f"观察 {html.escape(str(snapshot.get('watch_n')))}</p>",
        "<h2>限制</h2><ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations) + "</ul>",
        "<h2>24 主题</h2>",
        table(("主题", "名单", "可用", "合格", "观察", "家族"), theme_rows),
        "<h2>主题家族明细</h2>",
        "".join(details),
        "<h2>M1 股票综合</h2>",
        composite_table(composite_stock),
        "<h2>M1 ETF 综合</h2>",
        composite_table(composite_etf),
        "<h2>观察列表</h2>",
        table(("证券", "家族", "主题", "分数", "原因"), watch_rows) if watch_rows else "<p>无观察项。</p>",
        "</body></html>",
    ])


def write_preview(path: Path, snapshot: Mapping[str, Any], *, state: Mapping[str, Any] | None = None) -> Path:
    path.write_text(preview_html(snapshot, state=state), encoding="utf-8")
    return path


def write_preview_state(out_dir: Path, payload: Mapping[str, Any]) -> Path:
    path = out_dir / PREVIEW_STATE_NAME
    atomic_write_json(path, payload)
    return path


def read_preview_state(out_dir: Path) -> dict[str, Any]:
    path = out_dir / PREVIEW_STATE_NAME
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def close_task(
    *,
    panel: Mapping[str, Any],
    session: date,
    out_dir: Path,
    registry: Mapping[str, Any],
    profile: str = "balanced",
    horizon: str = "mid",
    volume_verified: bool = False,
    data_hash: str = "",
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    """One complete session, one write. Retry/manual trigger reuse the same identity."""

    out_dir.mkdir(parents=True, exist_ok=True)
    scored = score_session(
        panel,
        session,
        registry=registry,
        profile=profile,
        horizon=horizon,
        volume_verified=volume_verified,
        data_hash=data_hash,
        themes=themes,
        algorithms=algorithms,
    )
    scored["synthetic"] = synthetic
    snap_path = out_dir / "research-eod-v1-snapshot.json"
    published = publish_limited_snapshot(snap_path, scored)
    failed = published.get("integrity") == "stale_previous_retained" or bool(published.get("publish_failed"))
    state = {
        "attempted_session": scored["session_date"],
        "served_session": published.get("session_date"),
        "integrity": published.get("integrity"),
        "publish_failed": failed,
        "stale": failed,
        "synthetic": synthetic,
        "cache_key": published.get("cache_key"),
    }
    write_preview_state(out_dir, state)
    day_dir = out_dir / "sessions" / session.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    if failed:
        atomic_write_json(day_dir / "publish_failed.json", {
            "attempted_session": scored["session_date"],
            "served_session": published.get("session_date"),
            "integrity": published.get("integrity"),
            "published": False,
        })
        write_preview(out_dir / "preview.html", published, state=state)
        return {"scored": scored, "published": published, "path": str(snap_path), "publish_failed": True}
    atomic_write_json(day_dir / "scored.json", scored)
    atomic_write_json(day_dir / "summary.json", {
        "session_date": scored["session_date"],
        "eligible_n": scored["eligible_n"],
        "watch_n": scored["watch_n"],
        "composite_n": scored["composite_n"],
        "composite_stock_n": scored.get("composite_stock_n"),
        "composite_etf_n": scored.get("composite_etf_n"),
        "config_hash": scored["config_hash"],
        "run_signature": scored["run_signature"],
        "theme_summaries": scored["theme_summaries"],
        "composite_results": scored["composite_results"],
        "composite_stock": scored.get("composite_stock"),
        "composite_etf": scored.get("composite_etf"),
        "mode": MODE,
        "compute_version": COMPUTE_VERSION,
        "evidence_status": EVIDENCE_STATUS,
        "published": True,
    })
    write_preview(day_dir / "preview.html", published, state=state)
    write_preview(out_dir / "preview.html", published, state=state)
    return {"scored": scored, "published": published, "path": str(snap_path), "publish_failed": False}


def replay_sessions(
    panel: Mapping[str, Any],
    sessions: Sequence[date],
    *,
    out_dir: Path,
    registry: Mapping[str, Any],
    profile: str = "balanced",
    horizon: str = "mid",
    volume_verified: bool = False,
    data_hash: str = "",
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
    synthetic: bool = False,
) -> dict[str, Any]:
    started = time.perf_counter()
    days = []
    for session in sessions:
        result = close_task(
            panel=panel,
            session=session,
            out_dir=out_dir,
            registry=registry,
            profile=profile,
            horizon=horizon,
            volume_verified=volume_verified,
            data_hash=data_hash,
            themes=themes,
            algorithms=algorithms,
            synthetic=synthetic,
        )
        scored = result["scored"]
        days.append({
            "session_date": scored["session_date"],
            "eligible_n": scored["eligible_n"],
            "watch_n": scored["watch_n"],
            "composite_n": scored["composite_n"],
            "run_signature": scored["run_signature"],
            "config_hash": scored["config_hash"],
        })
    return {
        "sessions": days,
        "elapsed_s": round(time.perf_counter() - started, 3),
        "network_yahoo_batches": NETWORK_COUNTER["yahoo_batch_downloads"],
        "preview_provider_calls": NETWORK_COUNTER["preview_provider_calls"],
        "ordered": [item["session_date"] for item in days] == [session.isoformat() for session in sessions],
    }


def run_limited_v1(
    *,
    root: Path | None = None,
    out_dir: Path | None = None,
    session: date | None = None,
    replay_days: int = ACCEPTANCE_SESSIONS,
    profile: str = "balanced",
    horizon: str = "mid",
    allow_network: bool = False,
    smoke: bool = False,
    provider: YahooDiagnosticProvider | None = None,
    panel: Mapping[str, Any] | None = None,
    volume_verified: bool = False,
    data_hash: str = "",
    synthetic: bool = False,
    themes: Sequence[str] | None = None,
    algorithms: Sequence[str] | None = None,
) -> dict[str, Any]:
    base = Path(root or REPO_ROOT)
    dest = Path(out_dir or (base / PACK_RELATIVE))
    dest.mkdir(parents=True, exist_ok=True)
    registry = load_registry()
    load_report: dict[str, Any]
    if synthetic and panel is None:
        panel = build_synthetic_panel()
        coverage = [
            {"ticker": sid, "status": "ok", "bars": len(getattr(series, "dates", []) or [])}
            for sid, series in panel.items()
        ]
        data_hash = data_hash or f"synthetic:{COMPUTE_VERSION}:{len(panel)}"
        load_report = {
            "bars": {},
            "appearances": {sid: list(getattr(series, "theme_ids", ()) or []) for sid, series in panel.items()},
            "sources": ["synthetic_fixture_panel"],
            "missing": [],
            "fetched": 0,
            "failures": [],
            "dataset_hash": data_hash,
            "inventory": inventory_inputs(base),
            "allow_network": False,
        }
    elif panel is None:
        load_report = load_or_fetch_bars(root=base, allow_network=allow_network, provider=provider)
        panel, coverage = bars_to_panel(load_report["bars"], load_report["appearances"])
        data_hash = data_hash or str(load_report["dataset_hash"])
    else:
        appearances = current_universe_tickers()
        coverage = [
            {"ticker": sid, "status": "ok", "bars": len(getattr(series, "dates", []) or [])}
            for sid, series in panel.items()
        ]
        missing = [ticker for ticker in appearances if ticker not in panel]
        coverage.extend({"ticker": ticker, "status": "empty", "bars": 0} for ticker in missing)
        load_report = {
            "bars": {},
            "appearances": appearances,
            "sources": ["caller_panel"],
            "missing": missing,
            "fetched": 0,
            "failures": [],
            "dataset_hash": data_hash or "caller_panel",
            "inventory": inventory_inputs(base),
            "allow_network": False,
        }
        data_hash = data_hash or "caller_panel"
    if not panel:
        report = {
            "status": "DATA_BLOCKED",
            "mode": MODE,
            "evidence_status": EVIDENCE_STATUS,
            "load": load_report,
            "coverage": coverage,
            "synthetic": synthetic,
            "message": "no usable OHLCV panel; synthetic path may still be demonstrated separately",
        }
        atomic_write_json(dest / "run_report.json", report)
        return report
    target = session or ALLOWED_END
    if synthetic:
        last = max(series.dates[-1] for series in panel.values() if getattr(series, "dates", None))
        if all(target not in getattr(series, "dates", []) for series in panel.values()):
            target = last
    elif target not in set(trading_sessions(HISTORY_START, ALLOWED_END)):
        target = acceptance_sessions(end=ALLOWED_END, count=1)[0]
    window = acceptance_sessions(end=target, count=min(replay_days, ACCEPTANCE_SESSIONS)) if replay_days > 1 else [target]
    replay = replay_sessions(
        panel,
        window,
        out_dir=dest,
        registry=registry,
        profile=profile,
        horizon=horizon,
        volume_verified=volume_verified,
        data_hash=data_hash,
        themes=themes,
        algorithms=algorithms,
        synthetic=synthetic,
    )
    smoke_rows: list[dict[str, Any]] = []
    if smoke:
        smoke_rows = smoke_864(
            panel,
            window[-1],
            registry=registry,
            volume_verified=volume_verified,
            data_hash=data_hash,
        )
        atomic_write_json(dest / "smoke_864.json", {"session_date": window[-1].isoformat(), "rows": smoke_rows})
    latest = read_snapshot(dest / "research-eod-v1-snapshot.json") or {}
    report = {
        "status": "RAN",
        "mode": MODE,
        "compute_version": COMPUTE_VERSION,
        "evidence_status": EVIDENCE_STATUS,
        "synthetic": synthetic,
        "session_date": window[-1].isoformat(),
        "acceptance_sessions": [item.isoformat() for item in window],
        "acceptance_rule": "last N official development-zone sessions on/before ALLOWED_END; not chosen by performance",
        "load": _compact_load(load_report, coverage),
        "coverage": coverage,
        "replay": replay,
        "smoke_n": len(smoke_rows),
        "latest_eligible_n": latest.get("eligible_n"),
        "latest_composite_n": latest.get("composite_n"),
        "preview": str(dest / "preview.html"),
        "snapshot": str(dest / "research-eod-v1-snapshot.json"),
        "limitations": list(LIMITATIONS),
        "feature_mapping": list(FEATURE_MAPPING),
        "production_default_unchanged": True,
    }
    atomic_write_json(dest / "run_report.json", report)
    return report
