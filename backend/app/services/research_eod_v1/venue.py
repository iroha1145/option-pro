"""US venue eligibility from explicit security metadata — never from ticker dots.

A trailing ``.PA`` / ``.L`` suffix is not an exchange identifier we invented;
it is also not sufficient proof of a US listing. Callers must supply MIC,
exchange, country, and security type as they were known at the evaluation
date. Missing metadata is ``DATA_INSUFFICIENT``, not a guessed pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.research_eod_v1.constants import (
    OTC_EXCHANGES,
    OTC_MICS,
    US_MAJOR_EXCHANGES,
    US_MAJOR_MICS,
)


@dataclass(frozen=True)
class VenueDecision:
    eligible: bool
    reason: str
    track: str | None
    source: str


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


def classify_venue(metadata: Mapping[str, Any] | None) -> VenueDecision:
    """Decide US-major-exchange equity / US-listed ETF eligibility."""

    if not metadata:
        return VenueDecision(False, "MISSING_VENUE_METADATA", None, "required_fields")
    mic = _norm(metadata.get("mic") or metadata.get("primary_mic"))
    exchange = _norm(metadata.get("exchange") or metadata.get("primary_exchange"))
    country = _norm(metadata.get("listing_country") or metadata.get("country"))
    security_type = _norm(metadata.get("security_type") or metadata.get("type"))
    asset_class = _norm(metadata.get("asset_class") or metadata.get("asset_track"))
    if not any((mic, exchange, country, security_type)):
        return VenueDecision(False, "MISSING_VENUE_METADATA", None, "required_fields")
    if mic in OTC_MICS or exchange in OTC_EXCHANGES:
        return VenueDecision(False, "OTC_EXCLUDED", None, "metadata")
    if country and country not in {"US", "USA", "UNITED STATES"}:
        return VenueDecision(False, "NON_US_LISTING", None, "metadata")
    us_major = mic in US_MAJOR_MICS or exchange in US_MAJOR_EXCHANGES
    if not us_major:
        return VenueDecision(False, "NOT_US_MAJOR_EXCHANGE", None, "metadata")
    if asset_class in {"ETF", "ETP"} or security_type in {
        "ETF", "ETP", "ETN", "ETS", "ETV", "FUND",
    }:
        return VenueDecision(True, "US_LISTED_ETF", "etf", "metadata")
    if security_type in {
        "CS", "COMMON", "COMMON_STOCK", "ADR", "ADRC", "GDR", "EQUITY",
    }:
        return VenueDecision(True, "US_MAJOR_EQUITY", "stock", "metadata")
    if security_type:
        return VenueDecision(False, f"UNSUPPORTED_SECURITY_TYPE:{security_type}", None, "metadata")
    return VenueDecision(False, "MISSING_SECURITY_TYPE", None, "required_fields")


def ticker_dot_is_not_a_venue_test(ticker: str) -> bool:
    """Documented invariant: eligibility must ignore '.' in the symbol."""

    return "." in str(ticker or "")


# Current-theme membership only. These are 2026-09 static notes for U_current
# audit, not point-in-time listings and not a permission to backfill history.
CURRENT_UNIVERSE_VENUE_NOTES: dict[str, dict[str, str]] = {
    "RMS.PA": {
        "listing_country": "FR",
        "exchange": "EURONEXT PARIS",
        "decision": "NON_US_LISTING",
        "note": "Dot suffix is incidental; the listing country/exchange exclude it.",
    },
    "LVMUY": {
        "listing_country": "US",
        "exchange": "OTC",
        "decision": "OTC_EXCLUDED",
        "note": "US OTC ADR. Not a major-exchange common share.",
    },
    "CFRUY": {
        "listing_country": "US",
        "exchange": "OTC",
        "decision": "OTC_EXCLUDED",
        "note": "US OTC ADR. Not a major-exchange common share.",
    },
}
