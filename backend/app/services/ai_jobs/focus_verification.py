"""Small public projection of an internally retained, verified focus result."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.ai_jobs.claude_provider import _public_source_url


def supported_events(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [entry for entry in result.get("event_verifications", []) if entry["verdict"] == "supported"]


def public_focus_result(result: dict[str, Any]) -> dict[str, Any]:
    """Never copy mixed-event prose from the original paid response."""
    events = supported_events(result)
    allowed = {event["event_group_id"] for event in events}
    dominant = [
        {"event_group_id": event["event_group_id"], "summary": event["summary_zh"],
         "affected_sectors": event["affected_sectors"]}
        for event in events[:8]
    ]
    assessments = []
    for assessment in result.get("focus_ticker_assessments", []):
        refs = set(assessment["supporting_event_ids"]) | set(assessment["conflicting_event_ids"])
        if refs and refs <= allowed:
            assessments.append(deepcopy(assessment))
    sectors = list(dict.fromkeys(sector for event in events for sector in event["affected_sectors"]))[:20]
    summary = "\n".join(event["summary_zh"] for event in events)[:3000] if events else "当前暂无可展示热点。"
    return {
        "output_language": result["output_language"],
        "cycle_id": result["cycle_id"], "as_of": result["as_of"], "input_hash": result["input_hash"],
        "title_zh": "市场热点分析" if events else "当前暂无可展示热点",
        "summary_zh": summary, "headline_summary": summary, "market_summary": summary,
        "dominant_events": dominant, "market_uncertainties": [], "affected_sectors": sectors,
        "focus_ticker_assessments": assessments,
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
