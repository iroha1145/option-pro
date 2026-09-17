from app.services.research_eod_v1.universe_audit import current_universe_audit, venue_from_notes
from app.services.research_eod_v1.venue import classify_venue, ticker_dot_is_not_a_venue_test


def test_dot_in_ticker_is_not_a_us_test() -> None:
    paris = classify_venue(
        {"listing_country": "FR", "exchange": "EURONEXT PARIS", "security_type": "CS"}
    )
    assert paris.eligible is False
    assert paris.reason == "NON_US_LISTING"
    assert ticker_dot_is_not_a_venue_test("RMS.PA")
    us_share_class = classify_venue(
        {"listing_country": "US", "exchange": "NYSE", "security_type": "CS", "mic": "XNYS"}
    )
    assert us_share_class.eligible is True
    missing = classify_venue({})
    assert missing.reason == "MISSING_VENUE_METADATA"


def test_otc_and_current_luxury_notes() -> None:
    otc = classify_venue(
        {"listing_country": "US", "exchange": "OTC", "security_type": "ADR"}
    )
    assert otc.eligible is False
    assert otc.reason == "OTC_EXCLUDED"
    lvmuy = venue_from_notes("LVMUY")
    assert lvmuy["eligible"] is False
    rms = venue_from_notes("RMS.PA")
    assert rms["eligible"] is False


def test_current_universe_records_overlaps_and_etf_mix() -> None:
    audit = current_universe_audit()
    assert audit["theme_count"] == 24
    assert "NVDA" in audit["overlap_tickers"]
    assert "semiconductors" in audit["overlap_tickers"]["NVDA"]
    assert "ai_cloud" in audit["overlap_tickers"]["NVDA"]
    assert audit["etf_subasset_hints_current_only"]["GLD"] == "gold"
    assert audit["etf_subasset_hints_current_only"]["TLT"] == "long_bond"
    assert audit["pit_classification"] == "MISSING"
