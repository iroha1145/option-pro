"""Regressions for the data-gate fixes: transport, resume, identity, acceptance, bulk, real HTTP."""

from __future__ import annotations

import io
import json
import threading
import zipfile
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    classify_entitlement,
    probe_entitlement,
    redact_text,
    run_sharadar_probe,
    verify_paged_completeness,
)
from app.services.research_eod_v1.data.sharadar_acceptance import (
    history_budget,
    reconcile_aligned_returns,
    trading_calendar_sessions,
)
from app.services.research_eod_v1.data.sharadar_bulk import ingest_bulk_archive
from app.services.research_eod_v1.data.sharadar_identity import (
    identity_from_ticker_row,
    is_strategy_common_stock,
    resolve_identity_for_session,
    split_ticker_suffix,
    unadj_adv20,
    venue_tag,
)
from app.services.research_eod_v1.data import sharadar_pipeline
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTIONS_FIELDS,
    DEFAULT_PAGE_LIMIT,
    ENV_KEY_NAME,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
    download_request_plan,
)
from app.services.research_eod_v1.data.sharadar_store import load_checkpoint, read_jsonl
from app.services.research_eod_v1.data.sharadar_tracks import convert_vendor_row

SECRET = "dummy-not-a-real-secret"


def _ticker_row(ticker: str, permaticker: str, **overrides) -> dict:
    row = {field: "" for field in TICKERS_FIELDS}
    row.update({
        "permaticker": permaticker,
        "ticker": ticker,
        "name": ticker,
        "exchange": "NASDAQ",
        "isdelisted": "N",
        "category": "Domestic Common Stock",
        "currency": "USD",
        "firstpricedate": "2010-01-04",
        "lastpricedate": "2024-06-28",
    })
    row.update(overrides)
    return row


def _price_row(ticker: str, session: str, close: float = 10.0, volume: float = 1_000_000) -> dict:
    row = {field: "" for field in STOCKS_FIELDS}
    row.update({
        "ticker": ticker,
        "date": session,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": volume,
        "closeadj": close,
        "closeunadj": close,
        "lastupdated": session,
    })
    return row


def _action_row(ticker: str, session: str, action: str, value, **overrides) -> dict:
    row = {field: "" for field in ACTIONS_FIELDS}
    row.update({"date": session, "action": action, "ticker": ticker, "name": ticker, "value": value})
    row.update(overrides)
    return row


def _paged_opener(tables: dict[str, list[dict]], *, fail_after: dict[str, int] | None = None, bulk_zip: bytes | None = None):
    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if "years" in query:
            if bulk_zip is None:
                return 200, b'{"error":"bulk not subscribed"}', url.split("?")[0]
            if "status" in query:
                meta = {"table": table, "name": f"SHARADAR_{table.upper()}.zip", "size": len(bulk_zip), "sizeLabel": "1 KB", "modified": "2024-06-28"}
                return 200, json.dumps(meta).encode(), url.split("?")[0]
            return 200, bulk_zip, url.split("?")[0]
        skip = int(query.get("skip", ["0"])[0])
        limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
        if fail_after and table in fail_after and skip >= fail_after[table]:
            return 503, b"Service Unavailable", url.split("?")[0]
        rows = tables.get(table, [])[skip:skip + limit]
        return 200, json.dumps(rows).encode(), url.split("?")[0]

    return opener


def _sessions(start: str, n: int) -> list[str]:
    return [item.isoformat() for item in trading_calendar_sessions(date.fromisoformat(start), date(2024, 6, 28))[:n]]


# ----------------------------------------------------------------------------- transport


def test_redirect_is_not_completion(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)

    def opener(url: str, follow_redirects: bool = False):
        return 302, b"", "https://elsewhere.example/moved"

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    page = client.fetch_page("stocks", extra={"from": "2010-01-01", "to": "2010-01-08"})
    assert page.status == "REDIRECT_UNEXPECTED"
    assert page.complete is False
    payload = client.fetch_all("stocks", extra={"from": "2010-01-01", "to": "2010-01-08"})
    assert payload["status"] == "REDIRECT_UNEXPECTED"
    assert payload["complete"] is False
    assert payload["row_count"] == 0


def test_empty_body_is_not_completion(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    client = SharadarClient(allow_network=True, opener=lambda url, follow_redirects=False: (200, b"", url), sleep=lambda _s: None)
    page = client.fetch_page("tickers")
    assert page.status == "EMPTY_BODY"
    assert page.complete is False
    assert client.fetch_all("tickers")["complete"] is False


def test_short_page_needs_a_confirming_empty_page(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    rows = [_ticker_row(f"S{i:03d}", str(i)) for i in range(13)]

    # The vendor caps every page at 5 rows although 20 were requested.
    def capped(url: str, follow_redirects: bool = False):
        skip = int(parse_qs(urlsplit(url).query).get("skip", ["0"])[0])
        return 200, json.dumps(rows[skip:skip + 5]).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=capped, sleep=lambda _s: None)
    payload = client.fetch_all("tickers", store=tmp_path, limit=20)
    assert payload["status"] == "READ_OK"
    assert payload["complete"] is True
    assert payload["row_count"] == 13
    assert payload["pages"] == 4  # 5 + 5 + 3 + confirming empty page
    assert payload["short_page_observed"] == 3
    assert len(read_jsonl(tmp_path / "tickers.jsonl")) == 13


def test_repeated_page_means_paging_unsupported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    rows = [_ticker_row(f"S{i:03d}", str(i)) for i in range(3)]
    client = SharadarClient(
        allow_network=True,
        opener=lambda url, follow_redirects=False: (200, json.dumps(rows).encode(), url.split("?")[0]),
        sleep=lambda _s: None,
    )
    payload = client.fetch_all("tickers", store=tmp_path, limit=10)
    assert payload["status"] == "PAGING_UNSUPPORTED"
    assert payload["complete"] is False
    assert payload["row_count"] == 3


def test_resume_appends_without_rewriting_merged_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    rows = [_ticker_row(f"S{i:04d}", str(i)) for i in range(25)]
    first = SharadarClient(allow_network=True, opener=_paged_opener({"tickers": rows}, fail_after={"tickers": 20}), sleep=lambda _s: None)
    partial = first.fetch_all("tickers", store=tmp_path, limit=10)
    assert partial["status"] == "NETWORK_UNAVAILABLE"
    assert partial["row_count"] == 20
    merged = tmp_path / "tickers.jsonl"
    first_size = merged.stat().st_size
    second = SharadarClient(allow_network=True, opener=_paged_opener({"tickers": rows}), sleep=lambda _s: None)
    done = second.fetch_all("tickers", store=tmp_path, limit=10)
    assert done["status"] == "READ_OK"
    assert done["row_count"] == 25
    assert done["session_row_count"] == 5
    assert merged.stat().st_size > first_size
    saved = read_jsonl(merged)
    assert len(saved) == 25
    assert len({row["permaticker"] for row in saved}) == 25
    checkpoint = load_checkpoint(tmp_path, "tickers")
    assert checkpoint is not None
    assert checkpoint["merged_row_count"] == 25
    assert (tmp_path / "keys" / "tickers.sqlite").is_file()


def test_rows_without_primary_key_are_counted_not_collapsed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    rows = [_ticker_row("A", "1"), _ticker_row("B", ""), _ticker_row("C", ""), _ticker_row("D", "4")]
    client = SharadarClient(allow_network=True, opener=_paged_opener({"tickers": rows}), sleep=lambda _s: None)
    payload = client.fetch_all("tickers", store=tmp_path, limit=10)
    assert payload["row_count"] == 2
    assert payload["pk_missing_rows"] == 2


def test_redact_text_covers_url_encoded_key(monkeypatch) -> None:
    key = "abc+def/ghi=="
    monkeypatch.setenv(ENV_KEY_NAME, key)
    blob = f"failed https://api.sharadar.com/v1.0/data/stocks?api_key={quote(key, safe='')}&x=1 and {key}"
    redacted = redact_text(blob)
    assert key not in redacted
    assert quote(key, safe="") not in redacted


def test_channel_confirmed_is_false_on_non_official_origin(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {"tickers": [_ticker_row("MSFT", "1")], "stocks": [_price_row("MSFT", "2010-01-04")], "funds": [], "actions": []}
    client = SharadarClient(allow_network=True, base_url="http://127.0.0.1:9/v1.0/data", opener=_paged_opener(tables), sleep=lambda _s: None)
    probe = run_sharadar_probe(allow_network=True, client=client)
    assert client.live_request_count > 0
    assert client.channel_confirmed() is False
    assert probe["channel_confirmed"] == "NON_OFFICIAL_ORIGIN_USED"
    assert all(item["api_key_attached"] is False for item in client.request_log)


# ------------------------------------------------------------------------------- bulk


def _zip_bytes(name: str, csv_text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, csv_text)
    return buffer.getvalue()


def test_bulk_urls_carry_years_not_format(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    seen: list[str] = []

    def opener(url: str, follow_redirects: bool = False):
        seen.append(url)
        return 200, json.dumps({"table": "SEP", "name": "SHARADAR_SEP.zip", "size": 1, "sizeLabel": "1 B", "modified": "2024-06-28"}).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    assert client.bulk_status("stocks", years="full")["status"] == "READ_OK"
    query = parse_qs(urlsplit(seen[-1]).query)
    assert query["years"] == ["full"]
    assert query["status"] == ["True"]
    assert "format" not in query
    assert "format" not in parse_qs(urlsplit(client.table_url("stocks", {"years": "full"}, with_format=False)).query)


def test_entitlement_comes_from_bulk_status(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)

    def five_only(url: str, follow_redirects: bool = False):
        years = parse_qs(urlsplit(url).query).get("years", [""])[0]
        if years == "5":
            return 200, json.dumps({"table": "SEP", "name": "SHARADAR_SEP_5.zip", "size": 1, "sizeLabel": "1 B", "modified": "2024-06-28"}).encode(), url
        return 200, b'{"error":"not subscribed to this history"}', url

    client = SharadarClient(allow_network=True, opener=five_only, sleep=lambda _s: None)
    probe = probe_entitlement(client)
    assert probe["bulk_years"] == "5"
    assert [item["years"] for item in probe["attempts"]] == ["full", "10", "5"]
    assert classify_entitlement(earliest=date(2019, 1, 2), bulk_years=probe["bulk_years"], verified_access=True)["status"] == "ENTITLEMENT_SHORT_5Y"

    def full(url: str, follow_redirects: bool = False):
        return 200, json.dumps({"table": "SEP", "name": "SHARADAR_SEP.zip", "size": 1, "sizeLabel": "1 B", "modified": "2024-06-28"}).encode(), url

    assert probe_entitlement(SharadarClient(allow_network=True, opener=full, sleep=lambda _s: None))["bulk_years"] == "full"
    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    assert probe_entitlement(SharadarClient(allow_network=True, opener=full, sleep=lambda _s: None))["status"] == "AUTH_REQUIRED"


def test_bulk_ingest_streams_csv_into_store_and_drops_out_of_window_rows(tmp_path: Path) -> None:
    header = ",".join(STOCKS_FIELDS)
    lines = [header]
    for session in ["2009-12-31", "2010-01-04", "2010-01-05", "2024-06-28", "2024-07-01"]:
        lines.append(",".join(str(_price_row("MSFT", session, 30.0)[field]) for field in STOCKS_FIELDS))
    lines.append(",".join(str(_price_row("MSFT", "2010-01-05", 30.0)[field]) for field in STOCKS_FIELDS))  # duplicate key
    archive = tmp_path / "stocks_full.zip"
    archive.write_bytes(_zip_bytes("SHARADAR_SEP.csv", "\n".join(lines) + "\n"))
    result = ingest_bulk_archive(tmp_path / "store", "stocks", archive, years="full", limit=2)
    assert result["status"] == "READ_OK"
    assert result["complete"] is True
    assert result["row_count"] == 3
    assert result["rows_dropped_outside_window"] == 2
    assert result["observed_min_date"] == "2010-01-04"
    assert result["observed_max_date"] == "2024-06-28"
    assert result["pages"] == 2
    checkpoint = load_checkpoint(tmp_path / "store", "stocks")
    assert checkpoint is not None and checkpoint["complete"] is True and checkpoint["download_mode"] == "bulk"
    saved = read_jsonl(tmp_path / "store" / "stocks.jsonl")
    assert [row["date"] for row in saved] == ["2010-01-04", "2010-01-05", "2024-06-28"]
    again = ingest_bulk_archive(tmp_path / "store", "stocks", archive, years="full", limit=2)
    assert again["already_ingested"] is True


def test_pipeline_uses_bulk_first_and_falls_back_to_paged(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    sessions = _sessions("2010-01-04", 30)
    tables = {
        "tickers": [_ticker_row("MSFT", "101")],
        "stocks": [_price_row("MSFT", day, 30.0) for day in sessions],
        "funds": [],
        "actions": [],
    }
    header = ",".join(STOCKS_FIELDS)
    csv_rows = [",".join(str(row[field]) for field in STOCKS_FIELDS) for row in tables["stocks"]]
    bulk = _zip_bytes("SHARADAR_SEP.csv", "\n".join([header, *csv_rows]) + "\n")

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if "years" in query:
            if table != "stocks":
                return 200, b'{"error":"not subscribed"}', url.split("?")[0]
            if "status" in query:
                return 200, json.dumps({"table": "SEP", "name": "SHARADAR_SEP.zip", "size": len(bulk), "sizeLabel": "1 KB", "modified": "2024-06-28"}).encode(), url.split("?")[0]
            return 200, bulk, url.split("?")[0]
        skip = int(query.get("skip", ["0"])[0])
        limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
        return 200, json.dumps(tables.get(table, [])[skip:skip + limit]).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, verify_completeness=False)
    assert gate["download_modes_used"]["stocks"] == "bulk"
    assert gate["download_modes_used"]["tickers"] == "paged"
    assert gate["tables"]["stocks"]["complete"] is True
    assert gate["raw_row_counts"]["stocks"] == len(sessions)
    assert gate["tables"]["tickers"]["bulk_attempt"]["status"] == "ENTITLEMENT_MISSING"
    assert gate["transform"]["converted_rows"] == len(sessions)


# ------------------------------------------------------------------------ resume in gate


def test_interrupted_download_rows_still_feed_the_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    monkeypatch.setattr(sharadar_pipeline, "download_request_plan", lambda **kw: download_request_plan(limit=5))
    sessions = _sessions("2010-01-04", 12)
    tables = {
        "tickers": [_ticker_row("MSFT", "101")],
        "stocks": [_price_row("MSFT", day, 30.0) for day in sessions],
        "funds": [],
        "actions": [],
    }
    client = SharadarClient(allow_network=True, opener=_paged_opener(tables, fail_after={"stocks": 5}), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, verify_completeness=False)
    assert gate["tables"]["stocks"]["status"] == "NETWORK_UNAVAILABLE"
    assert gate["tables"]["stocks"]["complete"] is False
    # Rows already committed are read from the store even though the download was interrupted.
    assert gate["raw_row_counts"]["stocks"] == 5
    assert gate["isolated_row_counts"]["stocks"] == 5
    assert gate["transform"]["converted_rows"] == 5
    assert gate["stages"]["DOWNLOAD"]["status"] == "PARTIAL"
    assert "stocks" in gate["stages"]["DOWNLOAD"]["evidence"]["incomplete_tables"]
    # Nothing downstream may claim PASS on an incomplete download.
    for name in ("TRANSFORM", "IDENTITY", "HISTORY", "RECONCILE"):
        assert gate["stages"][name]["status"] != "PASS", name
    assert gate["terminal_status"] != "DATA_GATE_ACCEPTED"
    assert gate["raw_download_status"] == "PARTIAL"


def test_completeness_sample_mismatch_marks_download_partial(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    sessions = _sessions("2010-01-04", 6)
    rows = [_price_row("MSFT", day, 30.0) for day in sessions]

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if "years" in query:
            return 200, b'{"error":"no"}', url.split("?")[0]
        if query.get("from") == query.get("to"):
            day = query["from"][0]
            # The vendor reports two rows for the sampled day; the store only has one.
            return 200, json.dumps([row for row in rows if row["date"] == day] * 2).encode(), url.split("?")[0]
        skip = int(query.get("skip", ["0"])[0])
        limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
        return 200, json.dumps(rows[skip:skip + limit]).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    full = client.fetch_all("stocks", store=tmp_path, extra={"from": "2010-01-01", "to": "2024-06-28", "sort": "date.asc"})
    assert full["complete"] is True
    check = verify_paged_completeness(client, "stocks", tmp_path, [sessions[0], sessions[1]], extra={"from": "2010-01-01", "to": "2024-06-28", "sort": "date.asc"})
    assert check["status"] == "FAIL"
    assert check["dates"][0]["vendor_rows"] == 2
    assert check["dates"][0]["store_rows"] == 1


# ---------------------------------------------------------------------------- identity


def test_suffixed_reused_ticker_resolves_to_the_delisted_company(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("DELL1", "24420", isdelisted="Y", firstpricedate="2010-01-04", lastpricedate="2013-10-29"),
            _ticker_row("DELL", "122827", firstpricedate="2018-12-28", lastpricedate="2024-06-28"),
        ],
        "stocks": [
            _price_row("MSFT", "2010-01-04", 30.0),
            _price_row("DELL1", "2013-10-28", 13.80),
            _price_row("DELL1", "2013-10-29", 13.86),
            _price_row("DELL", "2024-06-28", 137.0),
        ],
        "funds": [],
        "actions": [
            # The 2013 take-private settled in cash, so the synthetic row carries
            # the action code that states it. `acquisitionby` would resolve the
            # same identity while leaving the number's unit unproven.
            _action_row("DELL1", "2013-10-29", "acquisitioncash", "13.75", contraticker="MSD", contraname="Michael Dell / Silver Lake"),
            _action_row("DELL", "2024-01-02", "dividend", "0.445"),
        ],
    }
    client = SharadarClient(allow_network=True, opener=_paged_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, verify_completeness=False)
    assert gate["identities"]["reused_tickers"] == ["DELL"]
    case = next(item for item in gate["delist"] if item["ticker"] == "DELL")
    assert case["identity_resolution"]["security_id"] == "sharadar:24420"
    assert case["identity_resolution"]["ticker_as_stored"] == "DELL1"
    assert case["identity_resolution"]["event_window"]["candidate_tickers"] == ["DELL", "DELL1"]
    assert case["observed_terminal"]["label"] == "acquisition_cash"
    assert case["observed_terminal"]["value"] == 13.75
    assert case["settlement_evidence_source"]["action"] == "acquisitioncash"
    assert case["settlement_evidence_source"]["unit"] == "usd_per_share"
    assert case["live_status"] == "PASS"
    assert case["concrete_terminal"] is True
    assert gate["transform"]["skipped_n"] == 0


def test_single_candidate_still_checks_coverage() -> None:
    new_dell = identity_from_ticker_row(_ticker_row("DELL", "122827", firstpricedate="2018-12-28", lastpricedate="2024-06-28"))
    assert resolve_identity_for_session([new_dell], date(2012, 6, 1)) is None
    assert resolve_identity_for_session([new_dell], date(2020, 6, 1)) is new_dell
    assert split_ticker_suffix("DELL1") == ("DELL", "1")
    assert split_ticker_suffix("BRK.A") == ("BRK.A", None)
    assert split_ticker_suffix("DELL") == ("DELL", None)


def test_all_unknown_terminals_do_not_accept(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("BBBY", "2023", isdelisted="Y", lastpricedate="2023-04-26"),
        ],
        "stocks": [_price_row("MSFT", "2010-01-04", 30.0), _price_row("BBBY", "2023-04-26", 0.8)],
        "funds": [],
        # A bare "delisted" row without a bankruptcy reason is TERMINAL_UNKNOWN, never a pass.
        "actions": [_action_row("BBBY", "2023-04-26", "delisted", None, name="Bed Bath & Beyond")],
    }
    client = SharadarClient(allow_network=True, opener=_paged_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, verify_completeness=False)
    case = next(item for item in gate["delist"] if item["ticker"] == "BBBY")
    assert case["observed_terminal"]["label"] == "TERMINAL_UNKNOWN"
    assert case["concrete_terminal"] is False
    assert case["determinate_terminal"] is False
    assert gate["stages"]["IDENTITY"]["status"] != "PASS"
    assert gate["stages"]["IDENTITY"]["evidence"]["determinate_terminal_n"] == 0
    assert gate["stages"]["IDENTITY"]["evidence"]["settlement_evidenced_n"] == 0
    assert gate["stage_summary"]["accepted"] is False
    assert gate["terminal_status"] != "DATA_GATE_ACCEPTED"
    assert "delisted" in gate["action_vocabulary"]["observed"]


def test_pool_rule_helpers() -> None:
    assert is_strategy_common_stock("ADR Common Stock Primary Class")
    assert not is_strategy_common_stock("Domestic Common Stock Warrant")
    assert not is_strategy_common_stock("Domestic Preferred Stock")
    assert venue_tag("NASDAQ Global Select") == "venue_ok"
    assert venue_tag("NYSE Arca") == "venue_ok"
    assert venue_tag("BATS Global Markets") == "venue_ok"
    assert venue_tag("OTC") == "venue_unverified"
    stale = [convert_vendor_row(_price_row("X", (date(2015, 1, 5) + timedelta(days=i)).isoformat(), 10.0, 5_000_000)) for i in range(20)]
    assert unadj_adv20(stale, session=date(2020, 1, 6)) is None
    recent = [convert_vendor_row(_price_row("X", (date(2020, 1, 6) - timedelta(days=28 - i)).isoformat(), 10.0, 5_000_000)) for i in range(20)]
    assert unadj_adv20(recent, session=date(2020, 1, 6)) == 50_000_000.0


# ---------------------------------------------------------------------------- acceptance


def test_reconcile_requires_a_minimum_sample() -> None:
    sharadar = [{"security_id": "S", "session_date": f"2020-01-{i:02d}", "return": 0.01, "volume": 100} for i in range(2, 8)]
    thin = reconcile_aligned_returns(sharadar, sharadar)
    assert thin["status"] == "INSUFFICIENT"
    assert thin["securities_n"] == 1
    ok = reconcile_aligned_returns(sharadar, sharadar, min_return_coverage=5, min_securities=1)
    assert ok["status"] == "PASS"
    missing_source = reconcile_aligned_returns([], [], source_available=False, sharadar_available=True)
    assert missing_source["status"] == "RECONCILIATION_MISSING"


def test_transform_tolerance_distinguishes_one_bad_row_from_many(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    sessions = _sessions("2010-01-04", 1200)
    stocks = [_price_row("MSFT", day, 30.0) for day in sessions]
    stocks[10]["close"] = 0.0  # one unconvertible row among 1200
    tables = {
        "tickers": [_ticker_row("MSFT", "101"), _ticker_row("SPY", "1", category="ETF", exchange="NYSEARCA")],
        "stocks": stocks,
        "funds": [_price_row("SPY", sessions[0], 100.0)],
        "actions": [_action_row("MSFT", "2010-06-01", "dividend", "0.13")],
    }
    client = SharadarClient(allow_network=True, opener=_paged_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path / "a", yahoo_rows=[], allow_network=True, verify_completeness=False)
    assert gate["transform"]["skipped_n"] == 1
    assert gate["stages"]["TRANSFORM"]["status"] == "PASS"
    assert gate["stages"]["TRANSFORM"]["evidence"]["skipped_n"] == 1
    few = [_price_row("MSFT", day, 30.0) for day in sessions[:4]]
    few[0]["close"] = 0.0
    few[1]["close"] = 0.0
    tables_bad = {
        "tickers": [_ticker_row("MSFT", "101"), _ticker_row("SPY", "1", category="ETF", exchange="NYSEARCA")],
        "stocks": few,
        "funds": [_price_row("SPY", sessions[0], 100.0)],
        "actions": [_action_row("MSFT", "2010-06-01", "dividend", "0.13")],
    }
    client_bad = SharadarClient(allow_network=True, opener=_paged_opener(tables_bad), sleep=lambda _s: None)
    gate_bad = execute_data_gate(client=client_bad, store=tmp_path / "b", yahoo_rows=[], allow_network=True, verify_completeness=False)
    assert gate_bad["stages"]["TRANSFORM"]["status"] == "PARTIAL"
    assert gate_bad["transform"]["skipped_by_reason"]


def test_daily_pool_summary_and_per_security_first_score(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    msft_days = _sessions("2010-01-04", 400)
    late_days = _sessions("2012-01-03", 300)
    tables = {
        "tickers": [_ticker_row("MSFT", "101"), _ticker_row("LATE", "202", firstpricedate="2012-01-03")],
        "stocks": [_price_row("MSFT", day, 30.0, 5_000_000) for day in msft_days] + [_price_row("LATE", day, 12.0, 500) for day in late_days],
        "funds": [],
        "actions": [],
    }
    client = SharadarClient(allow_network=True, opener=_paged_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, verify_completeness=False)
    pool = gate["daily_pool"]
    assert pool["computed"] is True
    assert pool["session_n"] == len(set(msft_days) | set(late_days))
    assert pool["pool_size_max"] == 1  # LATE never clears the $20M ADV bar
    assert pool["exclusion_reason_counts"]["UNADJ_ADV20"] >= len(late_days)
    assert pool["venue_unverified_share"] == 0.0
    per_security = gate["history_budget"]["per_security"]
    assert per_security["first_score_day_is_per_security"] is True
    dist = per_security["first_score_day_distribution"]["A_trend_quality"]
    assert dist["securities_with_first_score"] == 2
    assert dist["earliest"] == msft_days[251]
    assert dist["latest"] == late_days[251]
    assert gate["history_budget"]["first_score_day"]["A_trend_quality"] == msft_days[251]
    # Unknown entitlement tier keeps HISTORY at PARTIAL even when the budget is computed.
    assert gate["history_budget"]["status"] == "COMPUTED"
    assert gate["stages"]["HISTORY"]["status"] == "PARTIAL"
    assert gate["stages"]["HISTORY"]["evidence"]["reason"] == "entitlement_tier_unknown"


def test_history_budget_accepts_session_summaries() -> None:
    sessions = trading_calendar_sessions(date(2010, 1, 4), date(2024, 6, 28))
    budget = history_budget(
        earliest=sessions[0],
        entitlement_status="READ_OK",
        calendar=sessions,
        security_sessions={
            "sharadar:1": {"first": sessions[0], "last": sessions[-1], "n": len(sessions)},
            "sharadar:2": {"first": sessions[500], "last": sessions[600], "n": 101},
        },
    )
    dist = budget["per_security"]["first_score_day_distribution"]
    # first/last/n cannot say when the 252nd valid bar arrived, so no date is claimed.
    assert dist["A_trend_quality"]["status"] == "NOT_COMPUTED"
    assert dist["A_trend_quality"]["securities_with_first_score"] == 0
    assert dist["A_trend_quality"]["securities_without_observation_dates"] == 1
    assert budget["per_security"]["insufficient_n"] == 1

    with_hits = history_budget(
        earliest=sessions[0],
        entitlement_status="READ_OK",
        calendar=sessions,
        security_sessions={
            "sharadar:1": {
                "first": sessions[0],
                "last": sessions[-1],
                "n": len(sessions),
                "warmup_hits": {"252": sessions[251].isoformat(), "330": sessions[329].isoformat()},
            },
        },
    )
    hit_dist = with_hits["per_security"]["first_score_day_distribution"]
    assert hit_dist["A_trend_quality"]["status"] == "COMPUTED"
    assert hit_dist["A_trend_quality"]["earliest"] == sessions[251].isoformat()
    assert hit_dist["D_residual_momentum"]["earliest"] == sessions[329].isoformat()


# ------------------------------------------------------------------------- real HTTP


class _FakeSharadar(BaseHTTPRequestHandler):
    calls: dict[str, int] = {}
    zip_payload = b""

    def log_message(self, *_args) -> None:  # silence
        return

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        query = parse_qs(parts.query)
        table = parts.path.rsplit("/", 1)[-1]
        _FakeSharadar.calls[table] = _FakeSharadar.calls.get(table, 0) + 1
        if parts.path.endswith("/signed.zip"):
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.end_headers()
            self.wfile.write(_FakeSharadar.zip_payload)
            return
        if table == "tickers":
            if _FakeSharadar.calls[table] == 1:
                self.send_response(503)
                self.send_header("Retry-After", "1")
                self.end_headers()
                self.wfile.write(b"busy")
                return
            skip = int(query.get("skip", ["0"])[0])
            rows = [] if skip else [_ticker_row("MSFT", "1")]
            body = json.dumps(rows).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
            return
        if table == "stocks":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        if table == "funds" and "years" in query:
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/signed.zip?X-Amz-Signature=abcd")
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()


def test_real_http_path_retry_after_redirect_and_unsigned_bulk(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    _FakeSharadar.calls = {}
    _FakeSharadar.zip_payload = _zip_bytes("SHARADAR_SFP.csv", ",".join(STOCKS_FIELDS) + "\n")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeSharadar)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    sleeps: list[float] = []
    try:
        client = SharadarClient(
            allow_network=True,
            base_url=f"http://127.0.0.1:{port}/v1.0/data",
            sleep=lambda seconds: sleeps.append(seconds),
        )
        page = client.fetch_page("tickers")
        assert page.status == "READ_OK"
        assert page.row_count == 1
        assert sleeps == [1.0]  # Retry-After honoured
        redirected = client.fetch_page("stocks", extra={"from": "2010-01-01", "to": "2010-01-08"})
        assert redirected.status == "REDIRECT_UNEXPECTED"
        assert redirected.complete is False
        dest = tmp_path / "funds.zip"
        bulk = client.bulk_download("funds", years="full", dest=dest)
        assert bulk["status"] == "READ_OK"
        assert dest.is_file()
        assert "abcd" not in bulk["url"]
        assert all("dummy-not-a-real-secret" not in json.dumps(item) for item in client.request_log)
        assert all(item["api_key_attached"] is False for item in client.request_log)
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------------------------------------- completeness evidence


def _date_filtering_opener(tables: dict[str, list[dict]], *, double_day_queries: bool = False):
    """Honour from/to like the vendor does, so single-day recounts are meaningful."""

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if "years" in query:
            return 200, b'{"error":"bulk not subscribed"}', url.split("?")[0]
        rows = tables.get(table, [])
        if "from" in query:
            lo, hi = query["from"][0], query["to"][0]
            rows = [row for row in rows if lo <= row["date"] <= hi]
            if double_day_queries and lo == hi:
                rows = rows * 2
        skip = int(query.get("skip", ["0"])[0])
        limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
        return 200, json.dumps(rows[skip:skip + limit]).encode(), url.split("?")[0]

    return opener


def _completeness_tables() -> dict[str, list[dict]]:
    sessions = [item.isoformat() for item in trading_calendar_sessions(date(2010, 1, 4), date(2024, 6, 28))]
    return {
        "tickers": [_ticker_row("MSFT", "101"), _ticker_row("SPY", "301", category="ETF", exchange="NYSEARCA")],
        "stocks": [_price_row("MSFT", day, 30.0) for day in sessions],
        "funds": [_price_row("SPY", day, 300.0, 50_000_000) for day in sessions[:5]],
        "actions": [_action_row("MSFT", sessions[10], "dividend", 0.2)],
    }


def test_download_evidence_records_completeness_check_per_table(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    client = SharadarClient(allow_network=True, opener=_date_filtering_opener(_completeness_tables()), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, mode="paged", completeness_sample_dates=2)
    evidence = gate["stages"]["DOWNLOAD"]["evidence"]
    assert gate["stages"]["DOWNLOAD"]["status"] == "PASS"
    assert evidence["completeness_checked"]["stocks"] == {"status": "PASS", "sampled_n": 2, "matched_n": 2, "vendor_rows_sampled": 2}
    assert evidence["completeness_checked"]["tickers"] == {"status": "NOT_RUN", "reason": "not_date_bound"}
    assert evidence["completeness_checked"]["actions"]["status"] == "PASS"
    assert evidence["completeness_failed_tables"] == []
    assert evidence["completeness_samples"] == {}


def test_download_evidence_keeps_samples_for_a_failed_completeness_check(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    client = SharadarClient(
        allow_network=True,
        opener=_date_filtering_opener(_completeness_tables(), double_day_queries=True),
        sleep=lambda _s: None,
    )
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True, mode="paged", completeness_sample_dates=2)
    evidence = gate["stages"]["DOWNLOAD"]["evidence"]
    assert gate["stages"]["DOWNLOAD"]["status"] == "PARTIAL"
    assert "completeness_sample_mismatch" in evidence["reasons"]
    assert evidence["completeness_checked"]["stocks"]["status"] == "FAIL"
    assert evidence["completeness_checked"]["stocks"]["matched_n"] == 0
    assert evidence["completeness_failed_tables"] == ["stocks"]
    assert [item["vendor_rows"] for item in evidence["completeness_samples"]["stocks"]["dates"]] == [2, 2]
    assert gate["terminal_status"] != "DATA_GATE_ACCEPTED"
