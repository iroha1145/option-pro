"""Sharadar official-channel adapter: synthetic, no chat credentials."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    SharadarProvider,
    classify_entitlement,
    parse_table_body,
    redact_url,
    run_sharadar_probe,
)
from app.services.research_eod_v1.data.sharadar_acceptance import (
    DELIST_FIXTURES,
    evaluate_delist_fixture,
    history_budget,
    reconcile_aligned_returns,
    volume_scope_audit,
)
from app.services.research_eod_v1.data.sharadar_archive import CONTROL_LABEL, build_control_index
from app.services.research_eod_v1.data.sharadar_identity import (
    classify_terminal,
    daily_pool_row,
    identity_from_ticker_row,
    is_strategy_common_stock,
    venue_tag,
)
from app.services.research_eod_v1.data.sharadar_schema import DEFAULT_PAGE_LIMIT, ENV_KEY_NAME
from app.services.research_eod_v1.data.sharadar_tracks import convert_vendor_row, dollar_volume_ok


def test_probe_without_secret_is_auth_required_and_does_not_fall_back(monkeypatch) -> None:
    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    report = run_sharadar_probe(allow_network=True)
    assert report["credential_present"] is False
    assert report["terminal_status"] == "AUTH_REQUIRED"
    assert report["yahoo_fallback_used"] is False
    assert report["massive_key_used"] is False
    assert report["nasdaq_data_link_attempted"] is False
    assert report["chat_credentials_read"] is False
    for table in ("stocks", "funds", "tickers", "actions"):
        assert report["tables"][table]["status"] == "AUTH_REQUIRED"
    blob = json.dumps(report).lower()
    assert "ha6xroxg" not in blob
    assert "api_key=" not in blob


def test_redact_url_strips_key_and_signed_params() -> None:
    url = "https://example.com/file.csv?api_key=SECRET123&X-Amz-Signature=abcd&ticker=AAPL"
    redacted = redact_url(url)
    assert "SECRET123" not in redacted
    assert "abcd" not in redacted
    assert "REDACTED" in redacted
    assert "ticker=AAPL" in redacted


def test_pagination_does_not_treat_first_10000_as_universe(tmp_path: Path, monkeypatch) -> None:
    from app.services.research_eod_v1.data.sharadar_schema import TICKERS_FIELDS

    def _row(ticker: str, n: int) -> dict:
        row = {field: "x" for field in TICKERS_FIELDS}
        row["ticker"] = ticker
        row["permaticker"] = str(n)
        return row

    pages = {
        0: [_row("A", i) for i in range(DEFAULT_PAGE_LIMIT)],
        DEFAULT_PAGE_LIMIT: [_row("B", 1)],
    }

    def opener(url: str, follow_redirects: bool = False):
        from urllib.parse import parse_qs, urlsplit

        skip = int(parse_qs(urlsplit(url).query).get("skip", ["0"])[0])
        body = json.dumps(pages.get(skip, [])).encode()
        return 200, body, url.split("?")[0]

    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    payload = client.fetch_all("tickers", store=tmp_path)
    assert payload["row_count"] == DEFAULT_PAGE_LIMIT + 1
    assert payload["pages"] == 2
    assert payload["used_default_first_page_as_universe"] is False
    assert "dummy-not-a-real-secret" not in json.dumps(payload["page_hashes"])


def test_three_track_conversion_matches_official_reciprocal_volume() -> None:
    aapl = convert_vendor_row({
        "ticker": "AAPL",
        "date": "2020-08-28",
        "open": 124.0,
        "high": 125.0,
        "low": 123.0,
        "close": 124.808,
        "volume": 187_630_000,
        "closeadj": 30.0,
        "closeunadj": 499.23,
        "lastupdated": "2020-08-28",
    })
    assert aapl.derivation == "DERIVED_FROM_VENDOR_ADJUSTMENT"
    assert aapl.raw_volume == 187_630_000 * 124.808 / 499.23
    assert abs(aapl.raw_volume - 46_907_500) < 200
    assert dollar_volume_ok(aapl)
    ge = convert_vendor_row({
        "ticker": "GE",
        "date": "2021-07-30",
        "open": 103.0,
        "high": 104.0,
        "low": 102.0,
        "close": 103.6,
        "volume": 7_497_000,
        "closeadj": 100.0,
        "closeunadj": 12.95,
        "lastupdated": "2021-07-30",
    })
    assert abs(ge.raw_volume - 59_976_000) < 1.0
    assert dollar_volume_ok(ge)


def test_identity_pool_and_venue_tags() -> None:
    identity = identity_from_ticker_row({
        "permaticker": "199059",
        "ticker": "AAPL",
        "name": "Apple",
        "exchange": "NASDAQ",
        "isdelisted": "N",
        "category": "Domestic Common Stock",
        "currency": "USD",
        "relatedtickers": "",
        "firstpricedate": "1980-12-12",
        "lastpricedate": "2024-06-28",
    })
    assert identity.security_id == "sharadar:199059"
    assert identity.asset_track == "stock"
    assert "VENUE_HISTORY_UNVERIFIED" in identity.flags
    assert "CLASSIFICATION_CURRENT" in identity.flags
    assert is_strategy_common_stock("ADR Common Stock Secondary Class")
    assert not is_strategy_common_stock("Domestic Preferred Stock")
    assert venue_tag("NYSE") == "venue_ok"
    assert venue_tag("OTC") == "venue_unverified"
    assert venue_tag(None) == "venue_unverified"
    etf = identity_from_ticker_row({
        "permaticker": "1",
        "ticker": "SPY",
        "category": "ETF",
        "currency": "USD",
        "exchange": "NYSEARCA",
        "relatedtickers": "",
    })
    assert etf.asset_track == "etf"
    assert "ETF_SUBASSET_MAPPING_MANUAL" in etf.flags
    history = [
        convert_vendor_row({
            "ticker": "AAPL",
            "date": f"2024-01-{i:02d}",
            "open": 180,
            "high": 181,
            "low": 179,
            "close": 180,
            "volume": 50_000,
            "closeadj": 180,
            "closeunadj": 180,
        })
        for i in range(2, 22)
    ]
    today = convert_vendor_row({
        "ticker": "AAPL",
        "date": "2024-01-22",
        "open": 180,
        "high": 181,
        "low": 179,
        "close": 180,
        "volume": 80_000,
        "closeadj": 180,
        "closeunadj": 180,
    })
    row = daily_pool_row(identity, today, history)
    assert row.in_raw_universe is True
    assert row.venue_tag == "venue_ok"
    assert "UNADJ_ADV20" in row.reasons
    assert row.in_strategy_pool is False


def test_terminal_labels_do_not_guess_zero() -> None:
    bankrupt = classify_terminal(
        [{"action": "delisted", "name": "Chapter 11 bankruptcy", "value": None}],
        last_trade=2.5,
    )
    assert bankrupt["label"] == "bankruptcy_last_trade"
    assert bankrupt["value"] == 2.5
    cash = classify_terminal([{"action": "acquisitioncash", "value": 54.0}], last_trade=50.0)
    assert cash["label"] == "acquisition_cash"
    assert cash["value"] == 54.0
    unknown = classify_terminal([{"action": "acquisitionstock", "value": 0.2}], last_trade=50.0)
    assert unknown["label"] == "TERMINAL_UNKNOWN"
    assert unknown["value"] is None


def test_sixteen_delist_fixtures_are_fixed_and_live_auth_required() -> None:
    assert len(DELIST_FIXTURES) == 16
    tickers = [item["ticker"] for item in DELIST_FIXTURES]
    assert tickers == [
        "DELL", "DTV", "YHOO", "WFM", "MON", "SHLD", "CELG", "ETFC",
        "JCP", "XLNX", "TWTR", "ATVI", "SIVB", "BBBY", "PIR", "ASNA",
    ]
    evaluated = [evaluate_delist_fixture(item, [], None) for item in DELIST_FIXTURES]
    assert all(item["live_status"] == "AUTH_REQUIRED" for item in evaluated)


def test_entitlement_windows_and_volume_scope() -> None:
    short = classify_entitlement(earliest=date(2021, 1, 4), bulk_years="5")
    assert short["status"] == "ENTITLEMENT_SHORT_5Y"
    assert short["continue"] is False
    ten = classify_entitlement(earliest=date(2016, 9, 1), bulk_years="10")
    assert ten["status"] == "HISTORY_10Y"
    assert ten["continue"] is True
    full = classify_entitlement(earliest=date(1998, 1, 2), bulk_years="full")
    assert full["status"] == "READ_OK"
    audit = volume_scope_audit(minute_entitlement=False)
    assert audit["session_scope"] == "UNKNOWN"
    assert audit["status"] == "UNSUPPORTED"
    budget = history_budget(earliest=None, entitlement_status="AUTH_REQUIRED", calendar_sessions=None)
    assert budget["status"] == "AUTH_REQUIRED"
    assert budget["evaluable_years_not_claimed_from_2010_alone"] is True


def test_yahoo_reconcile_thresholds() -> None:
    result = reconcile_aligned_returns(
        [
            {"security_id": "A", "session_date": "2024-01-02", "return": 0.01, "volume": 100},
            {"security_id": "A", "session_date": "2024-01-03", "return": 0.02, "volume": 100},
        ],
        [
            {"security_id": "A", "session_date": "2024-01-02", "return": 0.0101, "volume": 100},
            {"security_id": "A", "session_date": "2024-01-03", "return": 0.03, "volume": 50},
        ],
    )
    assert result["rows"][0]["return_marked"] is False
    assert result["rows"][1]["return_marked"] is True
    assert result["rows"][1]["volume_marked"] is True
    assert result["status"] == "FAIL"
    empty = reconcile_aligned_returns([], [])
    assert empty["status"] == "AUTH_REQUIRED"


def test_parse_json_and_csv_bodies() -> None:
    rows = parse_table_body(b'[{"ticker":"AAPL","date":"2024-01-02"}]')
    assert rows[0]["ticker"] == "AAPL"
    csv_rows = parse_table_body(b"ticker,date\nAAPL,2024-01-02\n")
    assert csv_rows[0]["date"] == "2024-01-02"


def test_provider_without_key_does_not_claim_access(monkeypatch) -> None:
    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    provider = SharadarProvider(allow_network=True)
    caps = provider.probe_capabilities()
    assert caps.probe_status == "AUTH_REQUIRED"
    assert caps.volume_session_scope == "UNKNOWN"
    assert provider.fetch_daily_bars("AAPL", date(2024, 1, 2), date(2024, 1, 10)) == "AUTH_REQUIRED"
    assert provider.fetch_corporate_actions("AAPL", date(2020, 1, 1), date(2020, 12, 31)) == "AUTH_REQUIRED"


def test_control_archive_does_not_rewrite_old_values() -> None:
    index = build_control_index()
    assert index["archive_label"] == CONTROL_LABEL
    assert index["list_n"] == 214
    assert index["quality_valid_n"] == 213
    assert index["quality_insufficient_n"] == 1
    assert "CRWV" in (index["quality_outlier"] or {})
    assert index["automotive_dv"]["inherited_winner"] is False
    assert index["old_feature_version"] == "us-eod-research-features-v1.5"
    assert index["new_feature_version_not_backfilled"] is True
    assert all(item.get("rewritten") is not True for item in index["artifacts"].values() if "rewritten" in item)
