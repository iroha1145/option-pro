"""Independent market/sector context; no stock ranking computation or HTTP writes."""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from app.data_paths import get_data_paths
from app.services.research_eod_v1.calendar_asof import last_complete_eod_session
from app.services.sectors import SECTORS
from app.services.snapshot_read_cache import FingerprintedFileCache
from app.services.utils import sanitize

from .store import _atomic_write

CONTEXT_VERSION = 1
CONTEXT_TTL_SECONDS = 26 * 60 * 60
CONTEXT_MAX_BYTES = 2 * 1024 * 1024
_documents = FingerprintedFileCache("strength_context", max_paths=8, max_bytes=4 * CONTEXT_MAX_BYTES)


def _descriptive_market_regime(regime: Mapping[str, Any]) -> dict[str, Any]:
    """Keep market observations without claiming old ranking adjustments ran."""

    descriptions = {
        "市场宽度偏弱，突破型信号已降权": "市场宽度偏弱",
        "波动或信用压力偏高，期权热度已降权": "波动或信用压力偏高",
        "风险偏好价差偏弱，突破与期权信号已降权": "风险偏好价差偏弱",
    }
    result = dict(regime)
    result.pop("rules", None)
    result["ranking_adjustments_applied"] = False
    result["warnings"] = [descriptions.get(warning, warning) for warning in regime.get("warnings") or []]
    return result


def context_path(root: Path | None = None) -> Path:
    return Path(root or get_data_paths().root) / "strength-context-v1.json"


def _parse_document(raw: bytes) -> dict[str, Any] | None:
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != CONTEXT_VERSION:
        return None
    saved = value.get("published_at")
    if isinstance(saved, bool) or not isinstance(saved, (int, float)) or not math.isfinite(saved) or saved <= 0:
        return None
    payload = value.get("payload")
    if not isinstance(payload, dict) or not isinstance(payload.get("market_regime"), dict) or not isinstance(payload.get("sectors"), list):
        return None
    date.fromisoformat(str(payload.get("served_session") or ""))
    return value


def read_context_snapshot(*, root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    observed = now or datetime.now(timezone.utc)
    expected = last_complete_eod_session(observed)
    try:
        document = _documents.read(context_path(root), _parse_document, now=observed.timestamp(), max_bytes=CONTEXT_MAX_BYTES)
    except (OSError, ValueError, TypeError, UnicodeError):
        document = None
    if document is None:
        return {
            "as_of": None, "served_session": None, "market_regime": None, "sectors": [],
            "_cached": True, "_stale": True, "source_status": "unavailable",
            "stale_reason": "context_snapshot_unavailable", "snapshot_source": "strength_context_worker",
        }
    payload = dict(document["payload"])
    payload["market_regime"] = _descriptive_market_regime(document["payload"]["market_regime"])
    # Macro publications can change within one price session. Read their own
    # read-only, fingerprint-cached source instead of freezing the seed's fit.
    from app.services.macro_conditions.linkage import factor_driver
    from app.services.macro_conditions.linkage_reader import load_macro_fit_reader

    reader = load_macro_fit_reader()
    payload["macro_context"] = {
        "available": reader.available, "reason": reader.reason, **reader.provenance(),
    }
    payload["sectors"] = []
    for saved_sector in document["payload"]["sectors"]:
        sector = dict(saved_sector)
        fit = reader.fit_for(sector.get("sector_id")) if reader.available else None
        sector.update({
            "macro_sector_fit": fit.score if fit else None,
            "macro_sector_tailwind": fit.tailwind if fit else None,
            "macro_sector_fit_confidence": round(fit.confidence, 4) if fit else None,
            "macro_sector_supporting_factors": [factor_driver(item) for item in fit.supporting] if fit else [],
            "macro_sector_opposing_factors": [factor_driver(item) for item in fit.opposing] if fit else [],
        })
        payload["sectors"].append(sector)
    saved = float(document["published_at"])
    served = date.fromisoformat(payload["served_session"])
    reason = (
        "future_context_timestamp" if saved > observed.timestamp() or served > expected
        else "newer_session_available" if served < expected
        else "context_snapshot_expired" if observed.timestamp() - saved > CONTEXT_TTL_SECONDS
        else None
    )
    payload.update({
        "_cached": True, "_stale": reason is not None,
        "source_status": "stale" if reason else payload.get("source_status", "active"),
        "stale_reason": reason, "snapshot_source": "strength_context_worker",
        "snapshot_saved_at": datetime.fromtimestamp(saved, timezone.utc).isoformat(),
        "cache_ttl_seconds": CONTEXT_TTL_SECONDS,
        "cache_expires_at": datetime.fromtimestamp(saved + CONTEXT_TTL_SECONDS, timezone.utc).isoformat(),
    })
    return payload


def build_context_snapshot(*, as_of: datetime) -> dict[str, Any]:
    # These helpers load adjusted market data and descriptive returns only.
    # Do not use sector_strength(), which invokes the old full ranking scan.
    from app.services.strength.scanner import (
        _complete_daily_frame, _download_history, _load_macro_reader,
        _sector_strength, _slice_ticker, _theme_universe,
    )
    from app.services.strength.features import _ret
    from app.services.strength.market_regime import MARKET_BENCHMARKS, compute_market_regime

    target = last_complete_eod_session(as_of)
    tickers, metadata = _theme_universe()
    symbols = list(dict.fromkeys([*tickers, *MARKET_BENCHMARKS]))
    raw = _download_history(symbols, period="2y")
    frames = {
        symbol: _complete_daily_frame(_slice_ticker(raw, symbol), as_of)[0]
        for symbol in symbols
    }
    for symbol in ("SPY", "QQQ"):
        frame = frames[symbol]
        if frame.empty or pd.Timestamp(frame.index[-1]).date() != target:
            raise ValueError("context_market_session_unavailable")
    regime = _descriptive_market_regime(compute_market_regime(
        {symbol: frames[symbol] for symbol in MARKET_BENCHMARKS}, as_of=as_of,
    ))
    if regime.get("status") == "insufficient_data":
        raise ValueError("context_market_inputs_incomplete")
    rows = []
    missing = []
    for ticker in tickers:
        frame = frames[ticker]
        close = pd.to_numeric(frame.get("Close", pd.Series(dtype=float)), errors="coerce").replace([math.inf, -math.inf], float("nan")).dropna()
        # Match the previous descriptive-sector sample's minimum history,
        # without computing any of its technical features or ranking scores.
        if len(close) < 63 or pd.Timestamp(close.index[-1]).date() != target:
            missing.append(ticker)
            continue
        rows.append({
            "ticker": ticker, "theme_ids": metadata[ticker]["theme_ids"], "final_score": None,
            **{f"return_{days}d": _ret(close, days) for days in (20, 63, 126)},
        })
    if not rows:
        raise ValueError("context_sector_inputs_unavailable")
    sectors = _sector_strength(rows, reader=_load_macro_reader())
    for sector in sectors:
        sector.pop("avg_strength", None)
        sector.pop("leaders", None)
        sector["member_count"] = len(set(SECTORS[sector["sector_id"]]["tickers"]))
    return sanitize({
        "as_of": as_of.astimezone(timezone.utc).isoformat(), "served_session": target.isoformat(),
        "market_regime": regime, "sectors": sectors,
        "source_status": "degraded" if missing or regime.get("status") != "active" else "active",
        "missing_symbols": missing, "price_basis": "existing_adjusted_history",
        "data_sources": {"prices": raw.attrs.get("price_source") or {}},
    })


def refresh_context_snapshot(*, root: Path | None = None, now: datetime | None = None) -> dict[str, Any]:
    observed = now or datetime.now(timezone.utc)
    previous = read_context_snapshot(root=root, now=observed)
    target = last_complete_eod_session(observed).isoformat()
    if previous.get("served_session") == target and not previous.get("_stale"):
        return {"status": "CURRENT", "served_session": target, "published": False}
    try:
        payload = build_context_snapshot(as_of=observed)
        document = {"version": CONTEXT_VERSION, "published_at": observed.timestamp(), "payload": payload}
        if len(json.dumps(document, ensure_ascii=False, allow_nan=False).encode()) > CONTEXT_MAX_BYTES:
            raise ValueError("context_snapshot_too_large")
        _atomic_write(context_path(root), document)
    except Exception as exc:
        return {
            "status": "UNAVAILABLE", "published": False, "error": type(exc).__name__,
            "served_session": previous.get("served_session"), "attempted_session": target,
        }
    return {"status": "RAN", "published": True, "served_session": target}


def sector_rows_with_scores(
    context: Mapping[str, Any], selection: Mapping[str, Any] | None, *, period: str,
) -> list[dict[str, Any]]:
    by_sector = {row["sector_id"]: row for row in context.get("sectors") or []}
    scores = {}
    for row in (selection or {}).get("observation_rows") or []:
        score = row.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score):
            scores[row["ticker"]] = float(score)
    score_status = str((selection or {}).get("source_status") or "unavailable")
    rows = []
    for sid, sector in SECTORS.items():
        members = set(sector["tickers"])
        scored = [(ticker, scores[ticker]) for ticker in members if ticker in scores]
        scored.sort(key=lambda item: (-item[1], item[0]))
        row = dict(by_sector.get(sid) or {
            "sector_id": sid, "id": sid, "name": sector["name"], "count": 0,
            "avg_return_1mo": None, "avg_return_3mo": None, "avg_return_6mo": None, "avg_return_3m": None,
        })
        selected_return = row.get(f"avg_return_{period}")
        row.update({
            "period": period, "period_days": {"1mo": 20, "3mo": 63, "6mo": 126}[period],
            "avg_return": selected_return, "avg_return_period": selected_return,
            "member_count": len(members), "scored_count": len(scored),
            "avg_strength": round(sum(score for _, score in scored) / len(scored), 1) if scored else None,
            "leaders": [{"ticker": ticker, "score": score} for ticker, score in scored[:4]],
            "score_data_through": (selection or {}).get("score_data_through"),
            "score_source_status": score_status,
        })
        rows.append(row)
    rows.sort(key=lambda row: (row.get("avg_return") is not None, row.get("avg_return") or 0, row.get("avg_strength") or 0), reverse=True)
    return rows
