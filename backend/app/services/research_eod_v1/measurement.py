"""Measurement contracts: reference pool, labels, snapshot matrix, row persistence."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, next_session, shift_sessions
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.series import SecuritySeries
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.runs import (
    CheckpointSignatureError,
    cache_hit_definitions,
    committed_row_key,
    load_signed_checkpoint,
    run_signature,
    write_signed_checkpoint,
)
from app.services.research_eod_v1.stats import STATISTICS_VERSION, snapshot_identity_key
from app.services.sectors import SECTORS

ET = ZoneInfo("America/New_York")
ROW_SCHEMA_VERSION = "us-eod-research-factor-row-v1.0"


def session_open_at(session: date) -> datetime:
    return datetime.combine(session, time(9, 30), tzinfo=ET)


def earliest_entry_session(signal_session: date, signal_available_at: datetime | None) -> date:
    """T+1 open only if the signal was actually available by that open."""

    planned = next_session(signal_session)
    if signal_available_at is None:
        return planned
    if signal_available_at.tzinfo is None or signal_available_at.utcoffset() is None:
        raise ValueError("signal_available_at must be timezone-aware")
    cursor = planned
    for _ in range(12):
        if signal_available_at <= session_open_at(cursor):
            return cursor
        cursor = next_session(cursor)
    return cursor


def next_day_confirm_available_at(signal_session: date) -> datetime:
    """Research policy, not a vendor finalized_at field."""

    return eod_evaluation_as_of(next_session(signal_session))


def reference_panel(
    panel: Mapping[str, SecuritySeries],
    theme_id: str,
    session: date,
) -> dict[str, SecuritySeries]:
    """Full same-track T-complete pool plus SPY/QQQ. Candidates stay theme-limited."""

    sector = SECTORS.get(theme_id) or {}
    target_track = "etf" if theme_id == "etfs" or sector.get("asset_track") == "etf" else "stock"
    out: dict[str, SecuritySeries] = {}
    for sid, series in panel.items():
        if sid in {"SPY", "QQQ"}:
            if has_complete_session_bar(series, session) or session in series.dates:
                out[sid] = series
            continue
        if series.asset_track == target_track and has_complete_session_bar(series, session):
            out[sid] = series
    return out


def attach_forward_label(
    series: SecuritySeries,
    session: date,
    horizon: int,
    *,
    last_allowed: date,
    holdout_start: date,
) -> dict[str, Any]:
    try:
        matured_at = shift_sessions(session, horizon)
    except Exception:
        return {"label": None, "label_matured_at": None, "reason": "LABEL_CALENDAR_INVALID"}
    if matured_at >= holdout_start or matured_at > last_allowed:
        return {"label": None, "label_matured_at": matured_at.isoformat(), "reason": "LABEL_CROSSES_BOUNDARY"}
    if session not in series.dates or matured_at not in series.dates:
        return {"label": None, "label_matured_at": matured_at.isoformat(), "reason": "LABEL_IMMATURE"}
    start_px = float(series.tri[series.dates.index(session)])
    end_px = float(series.tri[series.dates.index(matured_at)])
    if not np.isfinite(start_px) or not np.isfinite(end_px) or start_px <= 0:
        return {"label": None, "label_matured_at": matured_at.isoformat(), "reason": "LABEL_MISSING"}
    return {
        "label": end_px / start_px - 1.0,
        "label_matured_at": matured_at.isoformat(),
        "reason": None,
    }


def _row_payload(
    row: Mapping[str, Any],
    *,
    theme_id: str,
    algorithm: str,
    profile: str,
    horizon: str,
    label_horizon: int | None,
    label: Mapping[str, Any] | None,
) -> dict[str, Any]:
    return {
        "schema_version": ROW_SCHEMA_VERSION,
        "security_id": row.get("security_id"),
        "signal_session": row.get("session_date"),
        "theme_id": theme_id,
        "algorithm": algorithm,
        "profile": profile,
        "horizon": horizon,
        "label_horizon": label_horizon,
        "setup_id": _setup_or_episode(row, theme_id, algorithm, profile, horizon),
        "signal_episode_id": _signal_episode_id(row, theme_id, algorithm, profile, horizon),
        "source": row.get("reconstruction_mode") or "historical_reconstruction",
        "feature_version": row.get("feature_version") or FEATURE_VERSION,
        "score_version": SCORE_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "label_matured_at": None if label is None else label.get("label_matured_at"),
        "label": None if label is None else label.get("label"),
        "label_reason": None if label is None else label.get("reason"),
        "factors": row.get("factors"),
        "score": row.get("score"),
        "setup_gate": row.get("setup_state"),
        "final_eligible": row.get("status") == "eligible",
        "status": row.get("status"),
        "rejection_reasons": list(row.get("rejection_reasons") or ()),
        "industry_source": row.get("industry_source"),
        "primary_industry_id": row.get("primary_industry_id"),
        "geometry_close": row.get("geometry_close") if row.get("geometry_close") is not None else row.get("last_close"),
        "last_close": row.get("last_close"),
        "raw_close": row.get("raw_close"),
        "risk_distance_fraction": row.get("risk_distance_fraction"),
        "planned_invalidation": row.get("planned_invalidation"),
        "atr": row.get("atr"),
        "adv20": row.get("adv20"),
        "frozen_setup": row.get("frozen_setup"),
    }


def _signal_episode_id(
    row: Mapping[str, Any],
    theme_id: str,
    algorithm: str,
    profile: str,
    horizon: str,
) -> str:
    session = row.get("session_date") or row.get("signal_session")
    return "|".join(
        [
            str(row.get("security_id")),
            str(theme_id),
            str(algorithm),
            str(profile),
            str(horizon),
            str(session),
        ]
    )


def _setup_or_episode(
    row: Mapping[str, Any],
    theme_id: str,
    algorithm: str,
    profile: str,
    horizon: str,
) -> str | None:
    if algorithm == "B_confirmed_base_breakout":
        frozen = row.get("frozen_setup")
        if isinstance(frozen, Mapping) and frozen.get("setup_id"):
            return str(frozen.get("setup_id"))
        if isinstance(row.get("setup_id"), str) and ":" in str(row.get("setup_id")):
            return str(row.get("setup_id"))
        return None
    return _signal_episode_id(row, theme_id, algorithm, profile, horizon)


def persist_factor_rows(rows: Sequence[Mapping[str, Any]], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            line = json.dumps(row, sort_keys=True, default=str)
            handle.write(line + "\n")
            digest.update(line.encode())
    return digest.hexdigest()


def _identity_map(payloads: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for payload in payloads:
        theme = payload["sector_id"]
        algo = payload["algorithm_id"]
        profile = payload.get("profile") or "balanced"
        horizon = payload.get("horizon") or "mid"
        session = payload["session_date"]
        for row in payload.get("rows") or ():
            key = snapshot_identity_key(
                session=session,
                theme_id=theme,
                algorithm=algo,
                profile=profile,
                horizon=horizon,
                security_id=str(row.get("security_id")),
            )
            out[key] = {
                "score": row.get("score"),
                "status": row.get("status"),
                "rejection_reasons": list(row.get("rejection_reasons") or ()),
                "factors": row.get("factors"),
            }
        out[snapshot_identity_key(session=session, theme_id=theme, algorithm=algo, profile=profile, horizon=horizon)] = {
            "candidate_ids": list(payload.get("candidate_ids") or ()),
            "reference_ids": list(payload.get("reference_ids") or ()),
            "fingerprint": payload.get("fingerprint"),
        }
    return out


def run_snapshot_matrix(
    panel: Mapping[str, SecuritySeries],
    sessions: Sequence[date],
    *,
    themes: Sequence[str],
    algorithms: Sequence[str],
    registry: Mapping[str, Any],
    profile: str = "balanced",
    horizon: str = "mid",
    attach_labels: bool = False,
    last_allowed: date | None = None,
    holdout_start: date = date(2024, 7, 1),
    label_horizons: Sequence[int] = (5, 20, 63),
    chunk_size: int | None = None,
    checkpoint_path: Path | None = None,
    precomputed_raws: Mapping[str, Any] | None = None,
    data_hash: str = "unspecified",
    universe_version: str = "u_measurement",
    registry_version: Any = None,
    row_store_path: Path | None = None,
    window_start: date | str | None = None,
    window_end: date | str | None = None,
) -> dict[str, Any]:
    """Deterministic theme/family snapshots. Input dict order must not matter."""

    del precomputed_raws
    ordered_sessions = sorted(sessions)
    signature = run_signature(
        registry=registry,
        profile=profile,
        horizon=horizon,
        label_horizons=label_horizons,
        feature_version=FEATURE_VERSION,
        statistics_version=STATISTICS_VERSION,
        data_hash=data_hash,
        universe_version=universe_version,
        member_policy="known_theme_members",
        reference_policy="same_track_t_complete_plus_spy_qqq",
        start=window_start if window_start is not None else (ordered_sessions[0] if ordered_sessions else ""),
        end=window_end if window_end is not None else (ordered_sessions[-1] if ordered_sessions else ""),
        available_factors=("T", "M", "S", "B", "P", "V", "R", "G"),
        timing_policy="NEXT_DAY_CONFIRM",
        registry_version=registry_version,
    )
    done: set[tuple[str, str, str, str, str]] = set()
    committed: set[str] = set()
    payloads: list[Any] = []
    rows: list[Any] = []
    calls = 0
    recomputes = 0
    if checkpoint_path and checkpoint_path.exists():
        saved = load_signed_checkpoint(checkpoint_path, signature)
        done = {tuple(item) for item in saved.get("done", [])}
        payloads = list(saved.get("payloads", []))
        rows = list(saved.get("rows", []))
        calls = int(saved.get("function_calls", 0))
        recomputes = int(saved.get("recomputes", 0))
        committed = set(saved.get("committed_row_keys") or ())
    elif row_store_path and row_store_path.exists():
        # Rows without a signed checkpoint are not done. Replay the session,
        # skip committed keys, and do not treat a partial write as complete.
        for line in row_store_path.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            key = committed_row_key(
                session=str(rec.get("signal_session")),
                theme_id=str(rec.get("theme_id")),
                algorithm=str(rec.get("algorithm")),
                profile=str(rec.get("profile")),
                horizon=str(rec.get("horizon")),
                security_id=str(rec.get("security_id")),
                label_horizon=rec.get("label_horizon"),
            )
            committed.add(key)
            rows.append(rec)
    work: list[tuple[date, str, str]] = []
    for session in ordered_sessions:
        for theme_id in themes:
            for algorithm in algorithms:
                work.append((session, theme_id, algorithm))
    if chunk_size:
        remaining = [
            item
            for item in work
            if (item[0].isoformat(), item[1], item[2], profile, horizon) not in done
        ]
        work = remaining[:chunk_size]
    for session, theme_id, algorithm in work:
        tuple_key = (session.isoformat(), theme_id, algorithm, profile, horizon)
        if tuple_key in done:
            continue
        refs = reference_panel(panel, theme_id, session)
        payload = compute_snapshot(
            eod_evaluation_as_of(session),
            refs,
            "u_measurement",
            registry,
            sector_id=theme_id,
            algorithm=algorithm,
            profile=profile,
            horizon=horizon,
            source_finalized_through=session,
        )
        calls += 1
        payloads.append(payload)
        new_rows: list[dict[str, Any]] = []
        for row in payload.get("rows") or ():
            horizons = label_horizons if attach_labels else (None,)
            if attach_labels:
                series = panel.get(str(row.get("security_id")))
            else:
                series = None
            for label_horizon in horizons:
                label = None
                if attach_labels:
                    label = (
                        {"label": None, "label_matured_at": None, "reason": "NO_SERIES"}
                        if series is None
                        else attach_forward_label(
                            series,
                            session,
                            int(label_horizon),
                            last_allowed=last_allowed or session,
                            holdout_start=holdout_start,
                        )
                    )
                record = _row_payload(
                    row,
                    theme_id=theme_id,
                    algorithm=algorithm,
                    profile=profile,
                    horizon=horizon,
                    label_horizon=label_horizon,
                    label=label,
                )
                key = committed_row_key(
                    session=session.isoformat(),
                    theme_id=theme_id,
                    algorithm=algorithm,
                    profile=profile,
                    horizon=horizon,
                    security_id=str(row.get("security_id")),
                    label_horizon=label_horizon,
                )
                if key in committed:
                    continue
                committed.add(key)
                new_rows.append(record)
                rows.append(record)
        if row_store_path and new_rows:
            row_store_path.parent.mkdir(parents=True, exist_ok=True)
            with row_store_path.open("a", encoding="utf-8") as handle:
                for record in new_rows:
                    handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        done.add(tuple_key)
        if checkpoint_path:
            write_signed_checkpoint(
                checkpoint_path,
                {
                    "done": [list(item) for item in sorted(done)],
                    "payloads": payloads,
                    "rows": rows,
                    "function_calls": calls,
                    "recomputes": recomputes,
                    "committed_row_keys": sorted(committed),
                },
                signature,
            )
    return {
        "payloads": payloads,
        "rows": rows,
        "identity": _identity_map(payloads),
        "function_calls": calls,
        "recomputes": recomputes,
        "unique_snapshots": len(payloads),
        "unique_committed_rows": len(committed),
        "run_signature": signature,
        "cache_hits": cache_hit_definitions(),
    }


def classify_funnel_row(row: Mapping[str, Any]) -> str:
    """One rejection bucket. Warmup is not setup-not-met."""

    reasons = [str(item) for item in (row.get("rejection_reasons") or ())]
    if row.get("status") == "eligible" or row.get("final_eligible") is True:
        return "eligible"
    if any(reason in {"MISSING_T_BAR", "LATE_SOURCE", "SOURCE_UNAVAILABLE", "INCOMPLETE_COMMON_INPUTS"} for reason in reasons):
        return "data_missing"
    if any(reason == "SHORT_HISTORY" or "WARMUP" in reason for reason in reasons):
        return "warmup"
    if any("LOW_SCORE" in reason or reason == "LOW_SCORE" for reason in reasons):
        return "score_floor"
    if any(reason in {"ADV_TOO_LOW", "HIGH_ATR", "EXTENDED", "NOT_TRADABLE", "HALTED_SESSION"} for reason in reasons):
        return "risk_or_tradability"
    if reasons:
        return "setup_not_met"
    return "other_reject"


def pairing_diff(old_payload: Mapping[str, Any], new_payload: Mapping[str, Any]) -> dict[str, Any]:
    old_rows = {row["security_id"]: row for row in old_payload.get("rows") or ()}
    new_rows = {row["security_id"]: row for row in new_payload.get("rows") or ()}
    names = sorted(set(old_rows) | set(new_rows))
    diffs = []
    for sid in names:
        left, right = old_rows.get(sid), new_rows.get(sid)
        if left is None or right is None:
            diffs.append({"security_id": sid, "change": "membership", "old": left is not None, "new": right is not None})
            continue
        if left.get("score") != right.get("score") or list(left.get("rejection_reasons") or ()) != list(right.get("rejection_reasons") or ()):
            diffs.append(
                {
                    "security_id": sid,
                    "old_score": left.get("score"),
                    "new_score": right.get("score"),
                    "old_reasons": list(left.get("rejection_reasons") or ()),
                    "new_reasons": list(right.get("rejection_reasons") or ()),
                    "old_factors": left.get("factors"),
                    "new_factors": right.get("factors"),
                }
            )
    return {
        "old_candidates": list(old_payload.get("candidate_ids") or ()),
        "new_candidates": list(new_payload.get("candidate_ids") or ()),
        "old_references": list(old_payload.get("reference_ids") or ()),
        "new_references": list(new_payload.get("reference_ids") or ()),
        "row_diffs": diffs,
        "n_changed": len(diffs),
    }
