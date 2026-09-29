"""Mutually exclusive display buckets for frozen G1/G2/G3 lists.

selection_policy.patch was not attached. The grouping below is reconstructed
from REVIEW(5): classify the full source lists, then apply Top-K. This module
does not score, search weights, or promote unverified liquidity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from screener_gate_candidates_v1 import (
    DOLLAR_LIQUIDITY_UNVERIFIED,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    VOLUME_SESSION_UNVERIFIED,
)

SELECTION_NOTE = (
    "selection_policy.patch was not attached; exclusive buckets were reconstructed from REVIEW(5)"
)
UNVERIFIED_REASONS = frozenset({DOLLAR_LIQUIDITY_UNVERIFIED, VOLUME_SESSION_UNVERIFIED})
TECHNICAL = "technical_candidates"
EXTENSION = "extension_watch"
HIGH_VOL = "high_volatility_watch"


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


def select_buckets(payload: Mapping[str, Any], *, limit: int | None = None) -> dict[str, Any]:
    """Split one frozen session. Does not mutate the input."""

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
    groups = {
        TECHNICAL: (technical_rows, _qualified_ids(layers, G1_STOCK_REFERENCE)),
        EXTENSION: (extension_rows, _qualified_ids(layers, G2_EXTENSION_DISCOVERY)),
        HIGH_VOL: (high_rows, _qualified_ids(layers, G3_RISK_DISCOVERY)),
    }
    selected = {
        name: [_stock_row(row, qualified_ids=qualified_ids, source_date=source_date) for row in rows]
        for name, (rows, qualified_ids) in groups.items()
    }
    if limit is not None:
        for name in (TECHNICAL, EXTENSION, HIGH_VOL):
            selected[name] = selected[name][:limit]
    return {
        "session_date": source_date,
        "note": SELECTION_NOTE,
        TECHNICAL: selected[TECHNICAL],
        EXTENSION: selected[EXTENSION],
        HIGH_VOL: selected[HIGH_VOL],
        "etf_unchanged": [_unchanged(row) for row in etf_rows],
    }


def _snapshot(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, default=str)


def summarize_frozen_checkpoints(directory: str | Path) -> dict[str, Any]:
    """Count exclusive buckets on frozen checkpoints. Does not rescore."""

    folder = Path(directory)
    files = sorted(folder.glob("*.json"))
    counts = {TECHNICAL: 0, EXTENSION: 0, HIGH_VOL: 0}
    empty_primary = []
    duplicated = []
    promoted = []
    input_unchanged = True
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        before = _snapshot(payload)
        result = select_buckets(payload)
        if _snapshot(payload) != before:
            input_unchanged = False
        groups = {name: result[name] for name in (TECHNICAL, EXTENSION, HIGH_VOL)}
        for name, rows in groups.items():
            counts[name] += len(rows)
        seen: dict[Any, str] = {}
        for name, rows in groups.items():
            for row in rows:
                sid = row["security_id"]
                if sid in seen:
                    duplicated.append({"session_date": result["session_date"], "security_id": sid, "groups": [seen[sid], name]})
                seen[sid] = name
                if row["qualified"] or row["tradable"]:
                    promoted.append({"session_date": result["session_date"], "security_id": sid, "group": name})
        if all(len(rows) == 0 for rows in groups.values()):
            empty_primary.append({"date": result["session_date"], **{name: 0 for name in groups}})
    n = len(files)
    return {
        "checkpoints": n,
        "counts": counts,
        "mean_counts": {name: (counts[name] / n if n else None) for name in counts},
        "no_duplicate_or_unqualified_promotion": not duplicated and not promoted,
        "duplicate_examples": duplicated[:8],
        "promotion_examples": promoted[:8],
        "input_unchanged": input_unchanged,
        "empty_primary_days": empty_primary,
        "market_performance_replayed": False,
        "note": SELECTION_NOTE,
    }
