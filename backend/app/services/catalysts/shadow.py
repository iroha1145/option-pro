from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Optional, Sequence


_FIELDS = (
    "catalyst_count_6h",
    "catalyst_count_24h",
    "catalyst_count_72h",
    "catalyst_weighted_impact",
    "catalyst_confidence",
    "catalyst_source_diversity",
    "catalyst_direction_conflict",
    "catalyst_hours_since_latest",
    "catalyst_positive_count",
    "catalyst_negative_count",
    "catalyst_stale",
)


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def empty_shadow() -> dict[str, None]:
    return {field: None for field in _FIELDS}


def compute_shadow(
    items: Sequence[dict[str, Any]], *, as_of: datetime
) -> dict[str, Any]:
    """Compute display-only research fields without mutating input scores.

    Missing data remains ``None``.  The helper has no access to ranking or
    breakout objects, making accidental production-score mutation impossible.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    as_of = as_of.astimezone(timezone.utc)
    unique: dict[str, dict[str, Any]] = {}
    for item in items:
        content_hash = str(item.get("content_hash") or "")
        if not content_hash or content_hash in unique:
            continue
        event_time = _parse(item.get("published_at") or item.get("fetched_at"))
        fetched_at = _parse(item.get("fetched_at"))
        available_at = _parse(item.get("available_at"))
        if event_time is None or fetched_at is None or event_time > as_of or fetched_at > as_of:
            continue
        safe_item = dict(item)
        if available_at is None or available_at > as_of:
            safe_item["analysis"] = None
            safe_item["impact_score"] = None
            safe_item["confidence"] = None
        safe_item["_event_time"] = event_time
        unique[content_hash] = safe_item
    values = list(unique.values())
    if not values:
        return empty_shadow()
    ages = [max(0.0, (as_of - item["_event_time"]).total_seconds() / 3600) for item in values]
    analyzed = [item for item in values if item.get("impact_score") is not None and item.get("confidence") is not None]
    weighted_sum = 0.0
    weight_total = 0.0
    confidences: list[float] = []
    positive = 0
    negative = 0
    for item in analyzed:
        age = max(0.0, (as_of - item["_event_time"]).total_seconds() / 3600)
        confidence = max(0.0, min(100.0, float(item["confidence"])))
        impact = max(-100.0, min(100.0, float(item["impact_score"])))
        weight = confidence / 100.0 * math.exp(-math.log(2) * age / 24.0)
        weighted_sum += impact * weight
        weight_total += weight
        confidences.append(confidence)
        positive += int(impact > 0)
        negative += int(impact < 0)
    return {
        "catalyst_count_6h": sum(age <= 6 for age in ages),
        "catalyst_count_24h": sum(age <= 24 for age in ages),
        "catalyst_count_72h": sum(age <= 72 for age in ages),
        "catalyst_weighted_impact": round(weighted_sum / weight_total, 4) if weight_total else None,
        "catalyst_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "catalyst_source_diversity": len({str(item.get("source") or "") for item in values if item.get("source")}),
        "catalyst_direction_conflict": (positive > 0 and negative > 0) if analyzed else None,
        "catalyst_hours_since_latest": round(min(ages), 4),
        "catalyst_positive_count": positive,
        "catalyst_negative_count": negative,
        "catalyst_stale": any(bool(item.get("is_stale")) for item in values),
    }
