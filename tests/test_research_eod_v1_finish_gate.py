"""Finish-gate contracts: bounded history requests, staged acceptance, sealed-row isolation."""

from __future__ import annotations

import json
import resource
import tracemalloc
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    classify_entitlement,
    parse_bulk_metadata,
    run_sharadar_probe,
)
from app.services.research_eod_v1.data.sharadar_acceptance import history_budget, trading_calendar_sessions
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate
from app.services.research_eod_v1.data.sharadar_reconcile_source import load_reconcile_rows
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTIONS_FIELDS,
    ALLOWED_END,
    DATE_BOUND_TABLES,
    DEFAULT_PAGE_LIMIT,
    ENV_KEY_NAME,
    FULL_HISTORY_START,
    REQUIRED_GATE_STAGES,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
    download_request_plan,
)
from app.services.research_eod_v1.data.sharadar_store import load_checkpoint, read_jsonl

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


def _mock_tables() -> dict[str, list[dict]]:
    return {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("BBBY", "2023", isdelisted="Y", lastpricedate="2023-04-26"),
        ],
        "stocks": [
            _price_row("MSFT", "2010-01-04", 30.0),
            _price_row("MSFT", "2010-01-05", 30.3),
            _price_row("BBBY", "2023-04-26", 0.8, 2_000_000),
        ],
        "funds": [],
        "actions": [
            _action_row("BBBY", "2023-04-26", "delisted", None, name="Chapter 11 bankruptcy"),
        ],
    }


def _table_opener(tables: dict[str, list[dict]], *, seen: list[dict] | None = None):
    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if seen is not None:
            seen.append({
                "table": table,
                "from": query.get("from", [None])[0],
                "to": query.get("to", [None])[0],
                "skip": query.get("skip", [None])[0],
            })
        return 200, json.dumps(tables.get(table, [])).encode(), url.split("?")[0]

    return opener


def test_date_tables_request_the_real_history_window(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    plan = download_request_plan()
    for table in DATE_BOUND_TABLES:
        assert plan[table]["extra"]["from"] == FULL_HISTORY_START.isoformat()
        assert plan[table]["extra"]["to"] == ALLOWED_END.isoformat()
    assert plan["tickers"]["extra"] == {}
    assert plan["tickers"]["date_bound"] is False

    seen: list[dict] = []
    client = SharadarClient(allow_network=True, opener=_table_opener(_mock_tables(), seen=seen), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    downloads = {item["table"]: item for item in seen if item["skip"] is not None}
    for table in DATE_BOUND_TABLES:
        assert downloads[table]["from"] == FULL_HISTORY_START.isoformat()
        assert downloads[table]["to"] == ALLOWED_END.isoformat()
    assert downloads["tickers"]["from"] is None
    assert gate["checks"]["explicit_history_request_bounds"] == "PASS"

    checkpoint = load_checkpoint(tmp_path, "stocks")
    assert checkpoint is not None
    assert checkpoint["extra"]["from"] == FULL_HISTORY_START.isoformat()
    assert checkpoint["extra"]["to"] == ALLOWED_END.isoformat()
    rebound = client.fetch_all("stocks", extra={"from": "2016-09-01", "to": ALLOWED_END.isoformat()}, store=tmp_path)
    assert rebound["status"] == "CHECKPOINT_QUERY_MISMATCH"


def test_four_empty_tables_are_not_accepted(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    empty = {"tickers": [], "stocks": [], "funds": [], "actions": []}
    client = SharadarClient(allow_network=True, opener=_table_opener(empty), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    assert gate["credential_present"] is True
    assert gate["terminal_status"] != "DATA_GATE_ACCEPTED"
    assert gate["terminal_status"] == "DATA_GATE_INSUFFICIENT"
    assert gate["checks"]["live_sharadar_read"] == "INSUFFICIENT"
    assert gate["stage_summary"]["accepted"] is False
    assert "DOWNLOAD" in gate["stage_summary"]["insufficient"]
    assert gate["stages"]["DOWNLOAD"]["status"] == "INSUFFICIENT"
    assert gate["raw_download_status"] != "RAW_DOWNLOAD_COMPLETE"
    assert gate["probe"]["full_download_allowed"]["allowed"] is False
    assert gate["probe"]["samples"]["non_free_example"] == "EMPTY_NO_SAMPLE"


def test_failed_required_check_blocks_acceptance(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    client = SharadarClient(allow_network=True, opener=_table_opener(_mock_tables()), sleep=lambda _s: None)
    mismatched = [
        {"security_id": "sharadar:101", "session_date": "2010-01-05", "return": 0.9, "volume": 1_000_000},
    ]
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=mismatched, allow_network=True)

    assert gate["reconcile"]["status"] == "FAIL"
    assert gate["stages"]["RECONCILE"]["status"] == "FAIL"
    assert gate["stage_summary"]["accepted"] is False
    assert "RECONCILE" in gate["stage_summary"]["failed"]
    assert gate["terminal_status"] == "DATA_GATE_PARTIAL_REVIEW_REQUIRED"
    assert set(gate["stages"]) >= set(REQUIRED_GATE_STAGES)


def test_missing_reconcile_source_is_not_a_pass(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    client = SharadarClient(allow_network=True, opener=_table_opener(_mock_tables()), sleep=lambda _s: None)
    gate = execute_data_gate(
        client=client,
        store=tmp_path,
        allow_network=True,
        reconcile_cache_paths=[tmp_path / "absent.parquet"],
    )
    assert gate["reconcile"]["status"] == "RECONCILIATION_MISSING"
    assert gate["reconcile"]["comparison_source"]["available"] is False
    assert gate["terminal_status"] != "DATA_GATE_ACCEPTED"
    assert gate["yahoo_fallback_used"] is False

    empty = load_reconcile_rows(paths=[tmp_path / "absent.parquet"])
    assert empty["status"] == "RECONCILIATION_MISSING"
    assert empty["rows"] == []
    assert empty["source"]["live_yahoo_request"] is False


def test_holdout_appends_never_reach_acceptance_inputs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = _mock_tables()
    before_client = SharadarClient(allow_network=True, opener=_table_opener(tables), sleep=lambda _s: None)
    before = execute_data_gate(client=before_client, store=tmp_path / "before", yahoo_rows=[], allow_network=True)

    tables["stocks"].append(_price_row("BBBY", "2025-08-01", 999.0, 5_000_000))
    tables["actions"].append(_action_row("BBBY", "2025-08-01", "acquisitioncash", "42.0"))
    after_client = SharadarClient(allow_network=True, opener=_table_opener(tables), sleep=lambda _s: None)
    after = execute_data_gate(client=after_client, store=tmp_path / "after", yahoo_rows=[], allow_network=True)

    assert after["raw_row_counts"]["stocks"] > before["raw_row_counts"]["stocks"]
    assert after["isolated_row_counts"] == before["isolated_row_counts"]
    assert after["history_budget"]["calendar_last"] == before["history_budget"]["calendar_last"]
    assert after["transform"]["converted_rows"] == before["transform"]["converted_rows"]

    def case(gate: dict, ticker: str) -> dict:
        return next(item for item in gate["delist"] if item["ticker"] == ticker)

    assert case(after, "BBBY")["observed_terminal"] == case(before, "BBBY")["observed_terminal"]
    assert case(after, "BBBY")["observed_terminal"]["value"] == 0.8
    assert case(after, "BBBY")["live_status"] == case(before, "BBBY")["live_status"]
    assert all(
        date.fromisoformat(str(row["date"])[:10]) <= ALLOWED_END
        for row in read_jsonl(tmp_path / "after" / "stocks.jsonl")
        if row.get("date")
    ) is False  # the raw store keeps the sealed row; only the isolated view drops it


def test_reused_ticker_resolves_by_permaticker_and_event_year(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("DELL", "24420", isdelisted="Y", firstpricedate="2010-01-04", lastpricedate="2013-10-29"),
            _ticker_row("DELL", "122827", firstpricedate="2018-12-28", lastpricedate="2024-06-28"),
        ],
        "stocks": [
            _price_row("MSFT", "2010-01-04", 30.0),
            _price_row("DELL", "2013-10-29", 13.86),
            _price_row("DELL", "2024-06-28", 137.0),
        ],
        "funds": [],
        "actions": [
            _action_row("DELL", "2013-10-29", "acquisitioncash", "13.75"),
            _action_row("DELL", "2024-01-02", "dividend", "0.445"),
        ],
    }
    client = SharadarClient(allow_network=True, opener=_table_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    assert gate["identities"]["reused_tickers"] == ["DELL"]
    assert gate["identities"]["n"] == 3

    case = next(item for item in gate["delist"] if item["ticker"] == "DELL")
    window = case["identity_resolution"]["event_window"]
    assert window["candidates_n"] == 2
    assert window["covering_n"] == 1
    assert window["bounded_by_resolved_coverage"] is True
    assert window["start"] == "2013-01-01"
    assert window["end"] == "2013-10-29"
    assert case["identity_resolution"]["security_id"] == "sharadar:24420"
    assert case["identity_resolution"]["permaticker"] == "24420"
    assert case["observed_terminal"]["value"] == 13.75
    assert case["live_status"] == "PASS"

    # Both DELL price rows convert under their own permaticker, so three securities carry sessions.
    assert gate["history_budget"]["per_security"]["n"] == 3
    assert gate["transform"]["skipped_n"] == 0


def test_computed_budget_carries_dates_and_coverage_is_not_a_license() -> None:
    sessions = trading_calendar_sessions(date(2010, 1, 4), ALLOWED_END)
    assert len(sessions) > 3000
    computed = history_budget(
        earliest=date(2010, 1, 4),
        entitlement_status="ACCESS_VERIFIED_RANGE_UNKNOWN",
        calendar=sessions,
        security_sessions={"sharadar:1": sessions, "sharadar:2": sessions[:10]},
    )
    assert computed["status"] == "COMPUTED"
    for family, item in computed["families"].items():
        assert item["first_score_day"] is not None, family
        assert date.fromisoformat(item["first_score_day"]) <= ALLOWED_END
        assert item["last_valid_signal_day"] is not None
        for horizon in ("5", "20", "63"):
            assert item["label_maturity"][horizon]["mature_label_day"] is not None
            assert isinstance(item["label_maturity"][horizon]["evaluable_sessions"], int)
    assert computed["per_security"]["insufficient_n"] == 1
    assert computed["calendar_is_independent_of_price_rows"] is True

    without_calendar = history_budget(
        earliest=date(2010, 1, 4),
        entitlement_status="ACCESS_VERIFIED_RANGE_UNKNOWN",
        calendar=[],
    )
    assert without_calendar["status"] == "NOT_COMPUTED"
    assert without_calendar["first_score_day"] == {family: None for family in without_calendar["warmup_sessions"]}
    assert without_calendar["evaluable_sessions_after_warmup"] is None


def test_observed_earliest_is_not_subscription_proof() -> None:
    observed = classify_entitlement(earliest=date(2010, 1, 4), bulk_years=None, verified_access=True)
    assert observed["status"] == "ACCESS_VERIFIED_RANGE_UNKNOWN"
    assert observed["authorized_range_unknown"] is True
    assert observed["verified_access"] is True
    assert observed["research_start"] == "2010-01-04"
    assert observed["observed_coverage"]["earliest_observed_row"] == "2010-01-04"
    assert observed["observed_coverage"]["earliest_is_coverage_not_license"] is True

    licensed = classify_entitlement(earliest=date(2016, 9, 2), bulk_years="10", verified_access=True)
    assert licensed["status"] == "HISTORY_10Y"
    assert licensed["authorized_range_unknown"] is False
    assert licensed["research_start"] == "2016-09-01"

    short = classify_entitlement(earliest=date(2019, 1, 2), bulk_years="5", verified_access=True)
    assert short["status"] == "ENTITLEMENT_SHORT_5Y"
    assert short["continue"] is False

    absent = classify_entitlement(earliest=None, bulk_years=None, verified_access=False)
    assert absent["status"] == "AUTH_REQUIRED"
    assert absent["authorized_range_unknown"] is True


def test_bulk_status_reads_both_documented_shapes(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    flat = {"table": "SEP", "name": "SHARADAR_SEP.zip", "size": 1024, "sizeLabel": "1 KB", "modified": "2024-06-28"}
    listed = {"files": [{"name": "SHARADAR_ACTIONS.zip", "size": 512, "sizeLabel": "512 B", "modified": "2024-06-28"}]}

    assert parse_bulk_metadata(flat)["shape"] == "flat"
    assert parse_bulk_metadata(flat)["status"] == "READ_OK"
    parsed = parse_bulk_metadata(listed)
    assert parsed["status"] == "READ_OK"
    assert parsed["shape"] == "file_list"
    assert parsed["files"][0]["name"] == "SHARADAR_ACTIONS.zip"
    assert parse_bulk_metadata({"unrelated": 1})["status"] == "SCHEMA_MISMATCH"

    bodies = {"stocks": flat, "actions": listed}

    def opener(url: str, follow_redirects: bool = False):
        table = urlsplit(url).path.rsplit("/", 1)[-1]
        return 200, json.dumps(bodies[table]).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    assert client.bulk_status("stocks")["shape"] == "flat"
    actions = client.bulk_status("actions")
    assert actions["shape"] == "file_list"
    assert actions["files"][0]["sizeLabel"] == "512 B"
    assert actions["filename_does_not_prove_entitlement"] is True


def test_probe_blocks_full_download_on_auth_failure(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)

    def opener(url: str, follow_redirects: bool = False):
        return 403, b'{"error":"invalid api key"}', url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    probe = run_sharadar_probe(allow_network=True, client=client)
    assert probe["terminal_status"] == "AUTH_FAILED"
    assert probe["full_download_allowed"]["allowed"] is False
    assert any("AUTH_FAILED" in item for item in probe["full_download_allowed"]["blockers"])


def test_pagination_cost_is_per_page_not_per_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    limit = 200
    page_count = 6
    pages = {
        index * limit: [_ticker_row(f"S{index * limit + i:06d}", str(index * limit + i)) for i in range(limit)]
        for index in range(page_count)
    }
    pages[page_count * limit] = []
    reads = {"n": 0}

    def opener(url: str, follow_redirects: bool = False):
        skip = int(parse_qs(urlsplit(url).query).get("skip", ["0"])[0])
        table = urlsplit(url).path.rsplit("/", 1)[-1]
        if table != "tickers":
            return 200, b"[]", url.split("?")[0]
        reads["n"] += 1
        return 200, json.dumps(pages.get(skip, [])).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    tracemalloc.start()
    before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    payload = client.fetch_all("tickers", store=tmp_path, limit=limit)
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert payload["status"] == "READ_OK"
    assert payload["row_count"] == limit * page_count
    assert payload["complete"] is True
    assert reads["n"] == page_count + 1
    # A prefix rescan would read limit*page_count*(page_count+1)/2 rows from disk.
    assert peak < 40 * 1024 * 1024
    assert after - before < 200 * 1024

    index = (tmp_path / "keys" / "tickers.keys").read_text(encoding="utf-8").splitlines()
    assert len(index) == limit * page_count
    assert len(set(index)) == len(index)

    resumed = client.fetch_all("tickers", store=tmp_path, limit=limit)
    assert resumed["row_count"] == limit * page_count
    assert resumed["session_row_count"] == 0


def test_isolated_view_is_reiterable_and_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = _mock_tables()
    tables["stocks"].append(_price_row("MSFT", "2024-07-05", 400.0))
    client = SharadarClient(allow_network=True, opener=_table_opener(tables), sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)
    assert gate["raw_row_counts"]["stocks"] == len(tables["stocks"])
    assert gate["isolated_row_counts"]["stocks"] == len(tables["stocks"]) - 1
    assert gate["stages"]["TRANSFORM"]["status"] in {"PASS", "PARTIAL"}
    assert gate["transform"]["skipped_n"] == 0
    assert (tmp_path / "stocks.jsonl").is_file()
    assert DEFAULT_PAGE_LIMIT == 10_000
