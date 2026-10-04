"""M1 consensus filter. First-layer rejects stay rejected.

M2–M4 stay registered names in ``COMPOSITE_METHODS``. This runtime does not
execute them.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.constants import COMPOSITE_FLOORS


def _eligible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if row.get("status") == "eligible" and row.get("score") is not None]


def _row_rg(row: Mapping[str, Any], key: str) -> float | None:
    factors = row.get("factors")
    if isinstance(factors, dict) and factors.get(key) is not None:
        return float(factors[key])
    value = row.get(key)
    return None if value is None else float(value)


def _row_identity(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("security_id")),
        str(row.get("algorithm_id")),
        str(row.get("sector_context") or row.get("theme_id") or ""),
        str(row.get("horizon") or ""),
        str(row.get("profile") or ""),
        str(row.get("session_date") or ""),
    )


def _dedup_identical_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    seen: dict[tuple[str, ...], Mapping[str, Any]] = {}
    for row in rows:
        seen.setdefault(_row_identity(row), row)
    return list(seen.values())


def _collapse_same_family(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Merge the same security+family across themes. Input order cannot change R/G."""

    ordered = sorted(
        _dedup_identical_rows(rows),
        key=lambda r: (str(r.get("security_id")), str(r.get("algorithm_id")), str(r.get("sector_context") or "")),
    )
    family: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in ordered:
        family.setdefault((str(row["security_id"]), str(row.get("algorithm_id"))), []).append(row)
    collapsed: list[dict[str, Any]] = []
    for (sid, algo), group in family.items():
        scores = [float(item["score"]) for item in group if item.get("score") is not None]
        r_vals = [value for item in group if (value := _row_rg(item, "R")) is not None]
        g_vals = [value for item in group if (value := _row_rg(item, "G")) is not None]
        merged = dict(group[0])
        merged["security_id"] = sid
        merged["algorithm_id"] = algo
        merged["score"] = median(scores) if scores else None
        merged["R"] = median(r_vals) if r_vals else None
        merged["G"] = median(g_vals) if g_vals else None
        merged["theme_count"] = len(group)
        collapsed.append(merged)
    return collapsed


def _dedup_security(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Same family across themes first, then across families. Input order cannot change R/G."""

    collapsed = _collapse_same_family(rows)
    by_id: dict[str, list[dict[str, Any]]] = {}
    for row in collapsed:
        by_id.setdefault(str(row["security_id"]), []).append(row)
    out: list[dict[str, Any]] = []
    for sid, group in by_id.items():
        scores = [float(item["score"]) for item in group if item.get("score") is not None]
        r_vals = [float(item["R"]) for item in group if item.get("R") is not None]
        g_vals = [float(item["G"]) for item in group if item.get("G") is not None]
        merged = dict(sorted(group, key=lambda item: str(item.get("algorithm_id")))[0])
        merged["security_id"] = sid
        merged["consensus_z"] = median(scores) if scores else None
        merged["family_votes"] = tuple(sorted({item.get("algorithm_id") for item in group}))
        merged["R"] = median(r_vals) if r_vals else None
        merged["G"] = median(g_vals) if g_vals else None
        out.append(merged)
    return out


def m1_consensus(rows: Sequence[Mapping[str, Any]], profile: str, top_k: int) -> list[dict[str, Any]]:
    floor = COMPOSITE_FLOORS[profile]
    eligible = _eligible(rows)
    grouped = _dedup_security(eligible)
    by_security: dict[str, list[Mapping[str, Any]]] = {}
    for item in eligible:
        by_security.setdefault(str(item["security_id"]), []).append(item)
    kept: list[dict[str, Any]] = []
    for row in grouped:
        votes = [v for v in row["family_votes"] if v]
        if len(set(votes)) < 2:
            continue
        z = row["consensus_z"]
        if z is None or z < floor:
            continue
        folded = _collapse_same_family(by_security[str(row["security_id"])])
        family_z = [float(item["score"]) for item in folded if item.get("score") is not None]
        if family_z and max(family_z) - min(family_z) > 25:
            row["status"] = "watch"
            continue
        row["status"] = "eligible"
        kept.append(row)
    kept.sort(key=lambda r: (-float(r["consensus_z"]), -(float(r["R"] or 0)), r["security_id"]))
    return kept[:top_k]
