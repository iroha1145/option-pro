"""Keep adapter contract fields on signal reports. Do not drop them in summaries."""

from __future__ import annotations

from typing import Any, Mapping

CONTRACT_FIELDS = (
    "halted",
    "currently_tradable",
    "zero_volume",
    "price_adjustment",
    "volume_adjustment",
    "vintage_status",
    "tri_verified",
    "identity_confidence",
    "industry_source",
)


def summarize_signal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    setup = row.get("frozen_setup") or {}
    factors = row.get("factors") or {}
    out = {
        "security_id": row.get("security_id"),
        "status": row.get("status"),
        "score": row.get("score"),
        "setup_state": row.get("setup_state"),
        "rejection_reasons": list(row.get("rejection_reasons") or ()),
        "adv20": row.get("adv20"),
        "platform_distance_atr": row.get("platform_distance_atr"),
        "platform_setup_id": setup.get("setup_id") if isinstance(setup, Mapping) else None,
        "platform_lifecycle": setup.get("lifecycle") if isinstance(setup, Mapping) else None,
        "platform_events": [event.get("kind") for event in (setup.get("events") or [])] if isinstance(setup, Mapping) else [],
        "residual_status": row.get("residual_status"),
        "residual_raw": row.get("residual_raw"),
        "M": factors.get("M") if isinstance(factors, Mapping) else None,
        "T": factors.get("T") if isinstance(factors, Mapping) else None,
        "source": "yahoo_unverified_close",
    }
    for field in CONTRACT_FIELDS:
        out[field] = row.get(field)
    return out
