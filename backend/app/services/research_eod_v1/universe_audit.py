from __future__ import annotations

from collections import defaultdict
from typing import Any

from app.services.sectors import SECTORS
from app.services.research_eod_v1.venue import CURRENT_UNIVERSE_VENUE_NOTES, classify_venue


ETF_SUBASSET_HINTS = {
    "SPY": "broad_equity",
    "QQQ": "broad_equity",
    "IWM": "broad_equity",
    "DIA": "broad_equity",
    "VTI": "broad_equity",
    "VOO": "broad_equity",
    "ARKK": "thematic_equity",
    "SOXX": "sector_equity",
    "XLF": "sector_equity",
    "XLE": "sector_equity",
    "GLD": "gold",
    "TLT": "long_bond",
}


def current_universe_audit() -> dict[str, Any]:
    appearances: dict[str, list[str]] = defaultdict(list)
    for sector_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances[str(ticker).upper()].append(sector_id)
    overlaps = {ticker: themes for ticker, themes in appearances.items() if len(themes) > 1}
    dotted = [ticker for ticker in appearances if "." in ticker]
    venue_notes = {
        ticker: CURRENT_UNIVERSE_VENUE_NOTES[ticker]
        for ticker in appearances
        if ticker in CURRENT_UNIVERSE_VENUE_NOTES
    }
    etf_mix = {
        ticker: ETF_SUBASSET_HINTS[ticker]
        for ticker in SECTORS["etfs"]["tickers"]
        if ticker in ETF_SUBASSET_HINTS
    }
    return {
        "theme_count": len(SECTORS),
        "unique_tickers": len(appearances),
        "overlap_tickers": overlaps,
        "dot_tickers_not_a_venue_test": dotted,
        "static_venue_notes": venue_notes,
        "etf_subasset_hints_current_only": etf_mix,
        "pit_classification": "MISSING",
        "primary_sector_rule": "first_listing_wins_not_authoritative",
        "historical_membership": "NOT_AVAILABLE_IN_REPO",
    }


def venue_from_notes(ticker: str) -> dict[str, Any]:
    note = CURRENT_UNIVERSE_VENUE_NOTES.get(ticker.upper())
    if not note:
        return {"ticker": ticker, "decision": "NO_STATIC_NOTE"}
    decision = classify_venue(
        {
            "listing_country": note.get("listing_country"),
            "exchange": note.get("exchange"),
            "security_type": "CS" if note.get("decision") != "US_LISTED_ETF" else "ETF",
        }
    )
    return {
        "ticker": ticker,
        "note": note,
        "eligible": decision.eligible,
        "reason": decision.reason,
    }
