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
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS
from app.services.sectors import SECTORS

ensure_reference_on_path()
from registry import resolve_weights  # type: ignore  # noqa: E402

MODE = "LIMITED_CURRENT_UNIVERSE_V1"
EVIDENCE_STATUS = "UNVALIDATED_LIMITED_DATA"
MEMBER_POLICY = "CURRENT_MEMBERSHIP"
UNIVERSE_VERSION = "u_limited_current_v1"
ALLOWED_END = date(2024, 6, 28)
HOLDOUT_START = date(2024, 7, 1)
HISTORY_START = date(2018, 1, 2)
DOWNLOAD_END_EXCLUSIVE = date(2024, 6, 29)
ACCEPTANCE_SESSIONS = 20
WARMUP_SESSIONS = 330
VOLUME_SCOPE = "VENDOR_DAILY_UNVERIFIED"
MOMENTUM_BASIS = "price_return_not_total_return"
CACHE_RELATIVE = Path("research/option_pro_us_eod_v1/data/cache/limited_current_universe_v1/bars.pkl")
PACK_RELATIVE = Path("research/option_pro_us_eod_v1/return_pack/limited_current_universe_v1")

NETWORK_COUNTER = {"yahoo_batch_downloads": 0, "preview_provider_calls": 0}

LIMITATIONS = (
    "CURRENT_MEMBERSHIP only; not historical PIT constituents or delist union",
    "Yahoo auto_adjust=False does not prove Close is historical raw trade price",
    "volume_scope=VENDOR_DAILY_UNVERIFIED; not regular-session or share-basis proof",
    "momentum and labels are price returns, not total return",
    "execution / dollar risk / corporate-action ledger unverified",
    "G dropped once via PRICE_ONLY_DIAGNOSTIC; D is market-residual diagnostic",
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


def dataset_hash(bars: Mapping[str, Sequence[ResearchBar]]) -> str:
    digest = hashlib.sha256()
    for symbol in sorted(bars):
        for bar in clip_bars(bars[symbol]):
            digest.update(
                f"{symbol}|{bar.session_date}|{bar.open}|{bar.high}|{bar.low}|{bar.close}|{bar.volume}\n".encode()
            )
    return digest.hexdigest()


def _venue(track: str) -> dict[str, str]:
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "theme_tag_diagnostic_not_economic_parent",
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
                industry_id=(themes[0] if themes else "unknown"),
                parent_industry_id=(themes[0] if themes else "unknown"),
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
        panel[ticker] = series
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
        "inventory": inventory_inputs(base),
        "allow_network": allow_network,
    }


def limited_config_hash(
    *,
    registry: Mapping[str, Any],
    profile: str,
    horizon: str,
    track: str,
    data_hash: str,
) -> str:
    body = {
        "data_hash": data_hash,
        "feature_version": FEATURE_VERSION,
        "horizon": horizon,
        "mode": MODE,
        "profile": profile,
        "registry_sectors": list(registry["sectors"]),
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
) -> dict[str, Any]:
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
        updated["volume_scope"] = VOLUME_SCOPE if not volume_verified else row.get("volume_scope")
        updated["momentum_basis"] = MOMENTUM_BASIS
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
            hard = [reason for reason in (updated.get("rejection_reasons") or ()) if reason in HARD_REJECTIONS]
            updated["rejection_reasons"] = list(dict.fromkeys([*hard, *scored["rejection_reasons"]]))
            if scored["final_eligible"] and updated.get("status") != "rejected":
                updated["status"] = "eligible"
            elif not scored["final_eligible"]:
                updated["status"] = "rejected"
        if (
            not volume_verified
            and family in {"B_confirmed_base_breakout", "C_trend_pullback"}
            and updated.get("status") == "eligible"
        ):
            updated["status"] = "watch"
            reasons = list(updated.get("rejection_reasons") or [])
            if "LIQUIDITY_HARD_GATE_UNVERIFIED" not in reasons:
                reasons.append("LIQUIDITY_HARD_GATE_UNVERIFIED")
            updated["rejection_reasons"] = reasons
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
        "observed_feature_coverage",
        "stock_or_etf_track",
        "theme_ids",
    )
    return {key: row.get(key) for key in keep}


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
) -> dict[str, Any]:
    if session >= HOLDOUT_START or session > ALLOWED_END:
        raise ValueError("session_outside_development_zone")
    as_of = eod_evaluation_as_of(session)
    theme_ids = list(themes or SECTORS)
    families = list(algorithms or ALGORITHMS)
    family_results: list[dict[str, Any]] = []
    first_layer: list[dict[str, Any]] = []
    theme_summaries: list[dict[str, Any]] = []
    for theme_id in theme_ids:
        family_block: dict[str, Any] = {}
        for algorithm in families:
            raw = compute_snapshot(
                as_of,
                panel,
                UNIVERSE_VERSION,
                registry,
                sector_id=theme_id,
                algorithm=algorithm,
                profile=profile,
                horizon=horizon,
                source_finalized_through=session,
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
    composite = [
        _compact_row(row) if "algorithm_id" in row else dict(row)
        for row in m1_consensus(first_layer, profile, 20)
    ]
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
        statistics_version="limited-v1-no-ic-promotion",
        data_hash=data_hash or "empty",
        universe_version=UNIVERSE_VERSION,
        member_policy=MEMBER_POLICY,
        reference_policy="same_track_plus_spy_qqq",
        start=session,
        end=session,
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="eod_evaluation_as_of",
        capability_mask={"track": PRICE_ONLY_DIAGNOSTIC, "actual_G_observed": False, "mode": MODE},
    )
    return {
        "session_date": session.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": MODE,
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
        "watch_list": watch,
        "eligible_n": sum(1 for row in first_layer if row.get("status") == "eligible"),
        "watch_n": len(watch),
        "composite_n": len(composite),
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
    for profile in PROFILES:
        for horizon in HORIZONS:
            scored = score_session(
                panel,
                session,
                registry=registry,
                profile=profile,
                horizon=horizon,
                volume_verified=volume_verified,
                data_hash=data_hash,
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
        "composite_results": scored.get("composite_results"),
        "watch_list": scored.get("watch_list"),
        "limitations": scored.get("limitations"),
        "volume_scope": VOLUME_SCOPE,
        "momentum_basis": MOMENTUM_BASIS,
        "member_policy": MEMBER_POLICY,
        "holdout_sealed": True,
        "execution_unverified": True,
        "total_return_unverified": True,
        "historical_classification": "CLASSIFICATION_CURRENT",
        "data_stale": False,
        "eligible_n": scored.get("eligible_n"),
        "watch_n": scored.get("watch_n"),
        "composite_n": scored.get("composite_n"),
        "panel_n": scored.get("panel_n"),
        "feature_mapping": list(FEATURE_MAPPING),
    }
    return publish_snapshot(
        path,
        session_date=str(scored["session_date"]),
        config_hash=str(scored["config_hash"]),
        universe_version=UNIVERSE_VERSION,
        rows=rows,
        extra=extra,
    )


def preview_html(snapshot: Mapping[str, Any]) -> str:
    NETWORK_COUNTER["preview_provider_calls"] += 0
    themes = snapshot.get("theme_summaries") or []
    composite = snapshot.get("composite_results") or []
    watch = snapshot.get("watch_list") or []
    limitations = snapshot.get("limitations") or LIMITATIONS

    def table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
        head = "".join(f"<th>{html.escape(str(item))}</th>" for item in headers)
        body = []
        for row in rows:
            body.append("<tr>" + "".join(f"<td>{html.escape(str(item))}</td>" for item in row) + "</tr>")
        return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"

    theme_rows = []
    for theme in themes:
        families = theme.get("families") or {}
        theme_rows.append([
            theme.get("theme_id"),
            theme.get("members_listed"),
            theme.get("members_in_panel"),
            sum(int((families.get(algo) or {}).get("eligible") or 0) for algo in ALGORITHMS),
            sum(int((families.get(algo) or {}).get("watch") or 0) for algo in ALGORITHMS),
            "; ".join(
                f"{algo.split('_')[0]} e={(families.get(algo) or {}).get('eligible', 0)}"
                for algo in ALGORITHMS
            ),
        ])
    composite_rows = [
        [
            row.get("security_id"),
            row.get("consensus_z") if row.get("consensus_z") is not None else row.get("score"),
            ",".join(str(item) for item in (row.get("family_votes") or ())),
            row.get("status"),
        ]
        for row in composite
    ]
    watch_rows = [
        [row.get("security_id"), row.get("algorithm_id"), row.get("sector_context"), row.get("score"), row.get("rejection_reasons")]
        for row in watch[:40]
    ]
    return "\n".join([
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"/>",
        f"<title>LIMITED_CURRENT_UNIVERSE_V1 {html.escape(str(snapshot.get('session_date')))}</title>",
        "<style>body{font-family:sans-serif;max-width:1100px;margin:24px auto;color:#111}",
        "table{border-collapse:collapse;width:100%;margin:12px 0}th,td{border:1px solid #ccc;padding:6px;font-size:13px}",
        "th{background:#f3f3f3;text-align:left}.note{background:#fff7e6;padding:12px}</style></head><body>",
        f"<h1>历史 EOD 预览 · {html.escape(str(snapshot.get('session_date')))}</h1>",
        "<p class=\"note\">这是明确标注日期的历史结果，不是今日选股。",
        f"mode={html.escape(MODE)} · evidence={html.escape(EVIDENCE_STATUS)} · track={html.escape(PRICE_ONLY_DIAGNOSTIC)}。",
        "不展示 IC、回测收益或研究账本。</p>",
        f"<p>profile={html.escape(str(snapshot.get('profile')))} horizon={html.escape(str(snapshot.get('horizon')))} "
        f"综合入选 {html.escape(str(snapshot.get('composite_n')))} · 观察 {html.escape(str(snapshot.get('watch_n')))}</p>",
        "<h2>限制</h2><ul>" + "".join(f"<li>{html.escape(str(item))}</li>" for item in limitations) + "</ul>",
        "<h2>24 主题</h2>",
        table(("主题", "名单", "可用", "合格", "观察", "家族"), theme_rows),
        "<h2>M1 综合</h2>",
        table(("证券", "共识分", "家族", "状态"), composite_rows) if composite_rows else "<p>零合格，名单为空。</p>",
        "<h2>观察列表</h2>",
        table(("证券", "家族", "主题", "分数", "原因"), watch_rows) if watch_rows else "<p>无观察项。</p>",
        "</body></html>",
    ])


def write_preview(path: Path, snapshot: Mapping[str, Any]) -> Path:
    NETWORK_COUNTER["preview_provider_calls"] += 0
    path.write_text(preview_html(snapshot), encoding="utf-8")
    return path


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
    snap_path = out_dir / "research-eod-v1-snapshot.json"
    published = publish_limited_snapshot(snap_path, scored)
    day_dir = out_dir / "sessions" / session.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(day_dir / "scored.json", scored)
    write_preview(day_dir / "preview.html", published)
    write_preview(out_dir / "preview.html", published)
    return {"scored": scored, "published": published, "path": str(snap_path)}


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
    if panel is None:
        load_report = load_or_fetch_bars(root=base, allow_network=allow_network, provider=provider)
        panel, coverage = bars_to_panel(load_report["bars"], load_report["appearances"])
        data_hash = data_hash or str(load_report["dataset_hash"])
    else:
        load_report = {
            "bars": {},
            "appearances": current_universe_tickers(),
            "sources": ["caller_panel"],
            "missing": [],
            "fetched": 0,
            "failures": [],
            "dataset_hash": data_hash or "caller_panel",
            "inventory": inventory_inputs(base),
            "allow_network": False,
        }
        coverage = [{"ticker": sid, "status": "ok", "bars": len(getattr(series, "dates", []) or [])} for sid, series in panel.items()]
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
    if target not in set(trading_sessions(HISTORY_START, ALLOWED_END)):
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
        "evidence_status": EVIDENCE_STATUS,
        "synthetic": synthetic,
        "session_date": window[-1].isoformat(),
        "acceptance_sessions": [item.isoformat() for item in window],
        "acceptance_rule": "last N official development-zone sessions on/before ALLOWED_END; not chosen by performance",
        "load": {k: v for k, v in load_report.items() if k != "bars"},
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
