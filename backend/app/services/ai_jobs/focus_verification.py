"""Small public projection of an internally retained, verified focus result."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.ai_jobs.claude_provider import _public_source_url
from app.services.ai_jobs.models import CONCISE_FOCUS_SCHEMA_VERSIONS


def supported_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in result.get("event_verifications", []) if entry["verdict"] == "supported"]


_MODEL_FIELDS = (
    "output_language", "cycle_id", "as_of", "input_hash", "title_zh", "summary_zh",
    "headline_summary", "market_summary", "dominant_events", "market_uncertainties",
    "affected_sectors", "no_new_material_catalyst", "insufficient_context",
)


def _supported_assessments(result: dict[str, Any], allowed: set[str]) -> list[dict[str, Any]]:
    """A stock stance is published only when every event it cites is supported."""
    assessments = []
    for assessment in result.get("focus_ticker_assessments", []):
        refs = set(assessment["supporting_event_ids"]) | set(assessment["conflicting_event_ids"])
        if refs and refs <= allowed:
            assessments.append(deepcopy(assessment))
    return assessments


def public_focus_result(
    result: dict[str, Any], *, schema_version: Any = None, verified_at: Any = None,
) -> dict[str, Any]:
    """Public view of a verified focus result, decided by the job's own schema.

    Jobs created under the concise contract (2026-10-10, CONCISE_FOCUS_SCHEMA_VERSIONS)
    publish the model's own fields: the prompt gives each one a single job and
    tells the model these fields are public and must label anything that was
    not verified. Each event's verdict and the verification time are published
    so the card can badge it; evidence references (tool ids, URLs) stay
    internal and sources keep going through public_focus_sources.

    Older jobs keep the original projection: their global prose was written
    without that instruction and may mix in events that failed verification
    (823c3a74), so only supported events' own summaries are published. Reads
    re-project stored results, so this gate is what keeps old cycles unchanged.
    """
    events = supported_events(result)
    allowed = {event["event_group_id"] for event in events}
    if isinstance(schema_version, str) and schema_version in CONCISE_FOCUS_SCHEMA_VERSIONS:
        public = {field: deepcopy(result[field]) for field in _MODEL_FIELDS}
        public["focus_ticker_assessments"] = _supported_assessments(result, allowed)
        public["event_verifications"] = [
            {"event_group_id": entry["event_group_id"], "verdict": entry["verdict"], "verified_at": verified_at}
            for entry in result.get("event_verifications", [])
        ]
        return public
    dominant = [
        {"event_group_id": event["event_group_id"], "summary": event["summary_zh"],
         "affected_sectors": event["affected_sectors"]}
        for event in events[:8]
    ]
    sectors = list(dict.fromkeys(sector for event in events for sector in event["affected_sectors"]))[:20]
    summary = "\n".join(event["summary_zh"] for event in events)[:3000] if events else "当前暂无可展示热点。"
    return {
        "output_language": result["output_language"],
        "cycle_id": result["cycle_id"], "as_of": result["as_of"], "input_hash": result["input_hash"],
        "title_zh": "市场热点分析" if events else "当前暂无可展示热点",
        "summary_zh": summary, "headline_summary": summary, "market_summary": summary,
        "dominant_events": dominant, "market_uncertainties": [], "affected_sectors": sectors,
        "focus_ticker_assessments": _supported_assessments(result, allowed),
        "no_new_material_catalyst": not bool(events), "insufficient_context": not bool(events),
    }


def public_focus_sources(result: dict[str, Any], tool_evidence: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs = {
        (ref["tool_use_id"], _public_source_url(ref["url"]))
        for event in supported_events(result) for ref in event["evidence_refs"]
        if ref["relation"] == "supports"
    }
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    for receipt in tool_evidence:
        url = _public_source_url(receipt.get("url"))
        if (receipt.get("tool_use_id"), url) not in refs or url is None or url in seen:
            continue
        if receipt.get("status") != "success" or receipt.get("tool_name") not in {"web_search", "web_fetch"}:
            continue
        seen.add(url)
        title = " ".join("".join(char if char.isprintable() else " " for char in str(receipt.get("title", ""))).split())[:512]
        sources.append({"title": title, "url": url, "type": receipt["tool_name"]})
        if len(sources) == 10:
            break
    return sources
