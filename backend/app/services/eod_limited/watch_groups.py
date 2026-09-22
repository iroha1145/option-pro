"""Optional research watch groups. Default off. No scoring and no live prices.

#185 shadow variants are a different parameter set from the research gates:
baseline uses ATR policy ``legacy``; track_atr uses ``track_liquid_v1``;
entry_state only relaxes a public EXTENDED flag; raw_momentum adds windowed
branches. None of those names is G1_stock_reference, G2_extension_discovery,
or G3_risk_discovery.

This module classifies an already frozen full-list payload. Ordinary strength
GETs do not call it unless EOD_RESEARCH_WATCH_GROUPS is explicitly enabled and
a precomputed sidecar is present. It does not replace the main board.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

G1_STOCK_REFERENCE = "G1_stock_reference"
G2_EXTENSION_DISCOVERY = "G2_extension_discovery"
G3_RISK_DISCOVERY = "G3_risk_discovery"
UNVERIFIED_REASONS = frozenset({"DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED"})
TECHNICAL = "technical_candidates"
EXTENSION = "extension_watch"
HIGH_VOL = "high_volatility_watch"

SHADOW_VARIANTS = {
    "baseline": {"atr_policy": "legacy", "research_gate": None},
    "track_atr": {"atr_policy": "track_liquid_v1", "research_gate": None},
    "entry_state": {"atr_policy": None, "research_gate": None},
    "raw_momentum": {"atr_policy": None, "research_gate": None},
}
QUALIFICATION_NOTE = "观察分组不进入主榜，不授予严格资格，也不表示可以买入。"


def _enabled() -> bool:
    return os.environ.get("EOD_RESEARCH_WATCH_GROUPS", "").strip().lower() in {"1", "true", "yes"}


def _sid(row: Mapping[str, Any]) -> Any:
    return row.get("security_id")


def _is_etf(row: Mapping[str, Any]) -> bool:
    track = row.get("stock_or_etf_track")
    if track is None:
        track = row.get("asset_track")
    return track == "etf"


def _reasons(row: Mapping[str, Any]) -> list[Any]:
    return list(row.get("rejection_reasons") or [])


def _qualified(row: Mapping[str, Any], qualified_ids: set[Any]) -> bool:
    if any(reason in UNVERIFIED_REASONS for reason in _reasons(row)):
        return False
    return _sid(row) in qualified_ids


def _stock_row(row: Mapping[str, Any], *, qualified_ids: set[Any], source_date: str) -> dict[str, Any]:
    return {
        "security_id": _sid(row),
        "score": row.get("score"),
        "algorithm_id": row.get("algorithm_id"),
        "sector_context": row.get("sector_context"),
        "rejection_reasons": _reasons(row),
        "qualified": _qualified(row, qualified_ids),
        "tradable": False,
        "source_date": source_date,
    }


def _unchanged(row: Mapping[str, Any]) -> dict[str, Any]:
    copied = dict(row)
    if "rejection_reasons" in copied:
        copied["rejection_reasons"] = list(copied.get("rejection_reasons") or [])
    return copied


def _qualified_ids(layers: Mapping[str, Any], variant: str) -> set[Any]:
    return {_sid(row) for row in layers[variant]["qualified_entry"]}


def classify_watch_groups(payload: Mapping[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Classify full source lists, then truncate. Does not mutate the input."""

    layers = payload["layers"]
    source_date = str(payload["identity"]["session_date"])
    g1_technical = list(layers[G1_STOCK_REFERENCE]["technical_entry"])
    g2_discovery = list(layers[G2_EXTENSION_DISCOVERY]["discovery"])
    g3_discovery = list(layers[G3_RISK_DISCOVERY]["discovery"])
    g1_ids = {_sid(row) for row in g1_technical}
    g2_ids = {_sid(row) for row in g2_discovery}
    technical_rows = [row for row in g1_technical if not _is_etf(row)]
    extension_rows = [row for row in g2_discovery if not _is_etf(row) and _sid(row) not in g1_ids]
    placed = set(g1_ids)
    placed.update(_sid(row) for row in extension_rows)
    high_rows = [
        row
        for row in g3_discovery
        if not _is_etf(row) and _sid(row) not in g2_ids and _sid(row) not in placed
    ]
    etf_rows: list[Mapping[str, Any]] = []
    seen_etf: set[Any] = set()
    for source in (g1_technical, g2_discovery, g3_discovery):
        for row in source:
            if not _is_etf(row) or _sid(row) in seen_etf:
                continue
            seen_etf.add(_sid(row))
            etf_rows.append(row)
    grouped = {
        TECHNICAL: (technical_rows, _qualified_ids(layers, G1_STOCK_REFERENCE)),
        EXTENSION: (extension_rows, _qualified_ids(layers, G2_EXTENSION_DISCOVERY)),
        HIGH_VOL: (high_rows, _qualified_ids(layers, G3_RISK_DISCOVERY)),
    }
    selected = {
        name: [_stock_row(row, qualified_ids=qualified_ids, source_date=source_date) for row in rows]
        for name, (rows, qualified_ids) in grouped.items()
    }
    if limit is not None:
        for name in (TECHNICAL, EXTENSION, HIGH_VOL):
            selected[name] = selected[name][:limit]
    return {
        "session_date": source_date,
        TECHNICAL: selected[TECHNICAL],
        EXTENSION: selected[EXTENSION],
        HIGH_VOL: selected[HIGH_VOL],
        "etf_unchanged": [_unchanged(row) for row in etf_rows],
    }


def _session_of(payload: Mapping[str, Any]) -> str | None:
    for key in ("served_session", "score_data_through"):
        value = payload.get(key)
        if isinstance(value, str) and len(value) >= 10:
            return value[:10]
    return None


def _load_sidecar(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or "layers" not in payload or "identity" not in payload:
        return None
    return payload


def maybe_attach_watch_groups(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the snapshot unchanged unless an explicit sidecar is enabled."""

    if not _enabled():
        return payload
    raw_path = os.environ.get("EOD_RESEARCH_WATCH_LAYERS", "").strip()
    if not raw_path:
        return payload
    sidecar = _load_sidecar(Path(raw_path))
    if sidecar is None:
        return payload
    try:
        grouped = classify_watch_groups(sidecar)
    except (KeyError, TypeError):
        return payload
    served = _session_of(payload)
    if served and served != grouped["session_date"]:
        return payload
    attached = dict(payload)
    attached["research_watch_groups"] = {
        "enabled": True,
        "displaces_main_board": False,
        "high_volatility_collapsed": True,
        "qualification_note": QUALIFICATION_NOTE,
        "extension_watch": grouped[EXTENSION],
        "high_volatility_watch": grouped[HIGH_VOL],
        "source_date": grouped["session_date"],
        "shadow_variants_are_research_gates": False,
    }
    return attached
