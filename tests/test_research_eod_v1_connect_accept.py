"""Connect-and-accept contracts: mock transport only, no chat credentials."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    SharadarProvider,
    inspect_table_body,
    official_https_origin,
    run_sharadar_probe,
)
from app.services.research_eod_v1.data.sharadar_acceptance import reconcile_aligned_returns
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTIONS_FIELDS,
    DEFAULT_PAGE_LIMIT,
    ENV_KEY_NAME,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import load_checkpoint, read_jsonl


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
    row.update({
        "date": session,
        "action": action,
        "ticker": ticker,
        "name": ticker,
        "value": value,
    })
    row.update(overrides)
    return row


def _paged_tickers() -> dict[int, list[dict]]:
    first = [_ticker_row(f"S{i:05d}", str(i)) for i in range(DEFAULT_PAGE_LIMIT)]
    second = [_ticker_row("S10000", "10000"), _ticker_row("S10001", "10001")]
    return {0: first, DEFAULT_PAGE_LIMIT: second}


def _opener_from_pages(pages: dict[int, list[dict]], *, table: str = "tickers"):
    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        assert "api_key=" in url
        query = parse_qs(parts.query)
        skip = int(query.get("skip", ["0"])[0])
        if table not in parts.path:
            return 200, b"[]", parts.path
        return 200, json.dumps(pages.get(skip, [])).encode(), parts.path

    return opener


def test_no_key_makes_no_transport_call(monkeypatch) -> None:
    called = []

    def opener(url: str, follow_redirects: bool = False):
        called.append(url)
        return 200, b"[]", url

    monkeypatch.delenv(ENV_KEY_NAME, raising=False)
    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    page = client.fetch_page("tickers")
    report = run_sharadar_probe(allow_network=True, client=client)
    assert page.status == "AUTH_REQUIRED"
    assert page.row_count == 0
    assert called == []
    assert client.live_request_count == 0
    assert report["credential_present"] is False
    assert report["terminal_status"] == "AUTH_REQUIRED"


def test_valid_page_and_error_envelope(monkeypatch) -> None:
    bodies = {
        "ok": json.dumps([_ticker_row("MSFT", "1")]).encode(),
        "error": b'{"error":"invalid request"}',
    }
    state = {"mode": "ok"}

    def opener(url: str, follow_redirects: bool = False):
        return 200, bodies[state["mode"]], url.split("?")[0]

    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    ok = client.fetch_page("tickers")
    assert ok.status == "READ_OK"
    assert ok.row_count == 1
    state["mode"] = "error"
    bad = client.fetch_page("tickers")
    assert bad.status == "VENDOR_ERROR"
    assert bad.row_count == 0
    assert inspect_table_body(bodies["error"])["kind"] == "error"


def test_bulk_http_error_and_html_not_committed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    dest = tmp_path / "stocks.zip"

    def opener_503(url: str, follow_redirects: bool = False):
        return 503, b"Service Unavailable", url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener_503, sleep=lambda _s: None)
    result = client.bulk_download("stocks", years="full", dest=dest)
    assert result["status"] == "NETWORK_UNAVAILABLE"
    assert result["final_file_exists"] is False
    assert dest.exists() is False

    def opener_html(url: str, follow_redirects: bool = False):
        return 200, b"<html>not a zip</html>", url.split("?")[0]

    html_client = SharadarClient(allow_network=True, opener=opener_html, sleep=lambda _s: None)
    html = html_client.bulk_download("stocks", years="full", dest=dest)
    assert html["status"] == "SCHEMA_MISMATCH"
    assert dest.exists() is False

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("stocks.csv", "ticker,date\nMSFT,2010-01-04\n")

    def opener_zip(url: str, follow_redirects: bool = False):
        return 200, buffer.getvalue(), url.split("?")[0]

    zip_client = SharadarClient(allow_network=True, opener=opener_zip, sleep=lambda _s: None)
    ok = zip_client.bulk_download("stocks", years="full", dest=dest)
    assert ok["status"] == "READ_OK"
    assert dest.exists() is True
    assert ok["streamed"] is True

    dest.unlink()

    def opener_trunc(url: str, follow_redirects: bool = False):
        return 200, b"PK\x03\x04truncated", url.split("?")[0]

    trunc = SharadarClient(allow_network=True, opener=opener_trunc, sleep=lambda _s: None)
    bad_zip = trunc.bulk_download("stocks", years="full", dest=dest)
    assert bad_zip["status"] == "SCHEMA_MISMATCH"
    assert dest.exists() is False

    def opener_status_503(url: str, follow_redirects: bool = False):
        return 503, b"Service Unavailable", url.split("?")[0]

    status_client = SharadarClient(allow_network=True, opener=opener_status_503, sleep=lambda _s: None)
    meta = status_client.bulk_status("stocks")
    assert meta["status"] == "NETWORK_UNAVAILABLE"
    assert meta.get("metadata") == {}


def test_api_key_stays_on_official_https_origin_only(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    s3 = "https://s3.amazonaws.com/bucket/file.zip?X-Amz-Signature=abcd"
    official = "https://api.sharadar.com/v1.0/data/stocks?format=json"
    client = SharadarClient(allow_network=False)
    assert client._attach_key_if_official(s3) == s3
    assert "dummy-not-a-real-secret" not in client._attach_key_if_official(s3)
    attached = client._attach_key_if_official(official)
    assert "dummy-not-a-real-secret" in attached
    assert official_https_origin(official) is True
    assert official_https_origin(s3) is False


def test_pagination_partial_and_resume(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    pages = _paged_tickers()
    client = SharadarClient(allow_network=True, opener=_opener_from_pages(pages), sleep=lambda _s: None)
    full = client.fetch_all("tickers", store=tmp_path)
    assert full["status"] == "READ_OK"
    assert full["row_count"] == 10002
    # full page, short page, confirming empty page
    assert full["pages"] == 3
    assert full["complete"] is True

    store = tmp_path / "limited"
    limited = SharadarClient(allow_network=True, opener=_opener_from_pages(pages), sleep=lambda _s: None)
    first = limited.fetch_all("tickers", store=store, max_pages=1)
    assert first["status"] == "PARTIAL"
    assert first["row_count"] == 10000
    assert first["complete"] is False
    assert first["resume_skip"] == 10000
    assert first["used_default_first_page_as_universe"] is False
    checkpoint = load_checkpoint(store, "tickers")
    assert checkpoint is not None
    assert checkpoint["next_skip"] == 10000
    assert len(read_jsonl(store / "tickers.jsonl")) == 10000

    resumed = limited.fetch_all("tickers", store=store)
    assert resumed["session_row_count"] == 2
    assert resumed["row_count"] == 10002
    assert resumed["complete"] is True
    saved = read_jsonl(store / "tickers.jsonl")
    assert len(saved) == 10002
    assert saved[0]["ticker"] == "S00000"
    assert {row["ticker"] for row in saved} >= {"S00000", "S10000", "S10001"}

    again = limited.fetch_all("tickers", store=store)
    assert again["status"] == "READ_OK"
    assert again["session_row_count"] == 0
    assert again["row_count"] == 10002


def test_checkpoint_query_change_and_crashes(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    pages = _paged_tickers()
    store = tmp_path / "q"
    client = SharadarClient(allow_network=True, opener=_opener_from_pages(pages), sleep=lambda _s: None)
    client.fetch_all("tickers", store=store, max_pages=1)
    changed = client.fetch_all("tickers", store=store, extra={"ticker": "MSFT"})
    assert changed["status"] == "CHECKPOINT_QUERY_MISMATCH"
    assert len(read_jsonl(store / "tickers.jsonl")) == 10000

    def boom(_page) -> None:
        raise RuntimeError("crash_before_save")

    crash_store = tmp_path / "crash_before"
    crashing = SharadarClient(
        allow_network=True,
        opener=_opener_from_pages(pages),
        sleep=lambda _s: None,
        persistence_hooks={"before_commit_page": boom},
    )
    try:
        crashing.fetch_all("tickers", store=crash_store, max_pages=1)
    except RuntimeError:
        pass
    assert load_checkpoint(crash_store, "tickers") is None
    assert list((crash_store / "pages" / "tickers").glob("*.jsonl")) == []

    def boom_after(_page) -> None:
        raise RuntimeError("crash_after_page")

    after_store = tmp_path / "crash_after"
    after = SharadarClient(
        allow_network=True,
        opener=_opener_from_pages(pages),
        sleep=lambda _s: None,
        persistence_hooks={"after_page_durable": boom_after},
    )
    try:
        after.fetch_all("tickers", store=after_store, max_pages=1)
    except RuntimeError:
        pass
    page_files = list((after_store / "pages" / "tickers").glob("skip_*.jsonl"))
    assert len(page_files) == 1
    ckpt = load_checkpoint(after_store, "tickers")
    assert ckpt is None or ckpt.get("committed_pages") in (None, [])

    recovered = SharadarClient(allow_network=True, opener=_opener_from_pages(pages), sleep=lambda _s: None)
    payload = recovered.fetch_all("tickers", store=after_store)
    assert payload["row_count"] == 10002


def test_http_error_mid_pagination_keeps_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    pages = _paged_tickers()

    def opener(url: str, follow_redirects: bool = False):
        skip = int(parse_qs(urlsplit(url).query).get("skip", ["0"])[0])
        if skip:
            return 500, b"nope", url.split("?")[0]
        return 200, json.dumps(pages[0]).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    payload = client.fetch_all("tickers", store=tmp_path)
    assert payload["status"] == "NETWORK_UNAVAILABLE"
    assert payload["complete"] is False
    assert payload["row_count"] == 10000


def test_provider_access_tested_only_after_success(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")

    def opener_fail(url: str, follow_redirects: bool = False):
        return 200, b'{"error":"no entitlement"}', url.split("?")[0]

    provider = SharadarProvider(allow_network=True, client=SharadarClient(allow_network=True, opener=opener_fail, sleep=lambda _s: None))
    caps = provider.probe_capabilities()
    assert caps.capability_level == "DOCUMENTED_ONLY"
    assert caps.probe_status != "ACCESS_TESTED"

    def opener_ok(url: str, follow_redirects: bool = False):
        return 200, json.dumps([_ticker_row("MSFT", "9")]).encode(), url.split("?")[0]

    ok = SharadarProvider(allow_network=True, client=SharadarClient(allow_network=True, opener=opener_ok, sleep=lambda _s: None))
    caps_ok = ok.probe_capabilities()
    assert caps_ok.capability_level == "ACCESS_TESTED"


def test_actions_keep_non_numeric_and_identity_not_listed_at(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        path = parts.path
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        if skip:
            return 200, b"[]", path
        if path.endswith("/actions"):
            rows = [
                _action_row("MSFT", "2020-01-02", "dividend", "not-a-number", contraticker="USD"),
                _action_row("MSFT", "2020-02-03", "split", None),
            ]
            return 200, json.dumps(rows).encode(), path
        if path.endswith("/tickers"):
            return 200, json.dumps([_ticker_row("MSFT", "199059", firstpricedate="1986-03-13")]).encode(), path
        return 200, b"[]", path

    provider = SharadarProvider(allow_network=True, client=SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None))
    master = provider.load_security_master()
    assert isinstance(master, list)
    assert master[0].listed_at is None
    assert master[0].delisted_at is None
    assert master[0].aliases == ()
    assert master[0].security_id == "sharadar:199059"
    actions = provider.fetch_corporate_actions("MSFT", date(2020, 1, 1), date(2021, 1, 1), identity=master[0])
    assert isinstance(actions, list)
    assert actions[0].security_id == "sharadar:199059"
    assert actions[0].value is None
    assert actions[0].raw_value == "not-a-number"
    assert actions[0].economic_status == "UNSUPPORTED"
    assert actions[1].value is None
    assert actions[1].reason == "missing_value"


def test_mock_four_table_pipeline(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, "dummy-not-a-real-secret")
    tables = {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("AAPL", "199059"),
            _ticker_row("BBBY", "2023", isdelisted="Y", lastpricedate="2023-04-26"),
            _ticker_row("SPY", "1", category="ETF", exchange="NYSEARCA"),
        ],
        "stocks": [
            _price_row("MSFT", "2010-01-04", 30.0),
            _price_row("MSFT", "2010-01-05", 30.3),
            _price_row("AAPL", "2010-01-04", 7.0),
            _price_row("BBBY", "2023-04-26", 0.8, 2_000_000),
        ],
        "funds": [_price_row("SPY", "2010-01-04", 110.0)],
        "actions": [
            _action_row("BBBY", "2023-04-26", "delisted", None, name="Chapter 11 bankruptcy"),
            _action_row("MSFT", "2010-06-01", "dividend", "0.13"),
        ],
    }

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        path = parts.path.rsplit("/", 1)[-1]
        query = parse_qs(parts.query)
        if "years" in query:
            return 200, b'{"error":"bulk not subscribed"}', url.split("?")[0]
        skip = int(query.get("skip", ["0"])[0])
        limit = int(query.get("limit", [str(DEFAULT_PAGE_LIMIT)])[0])
        rows = tables.get(path, [])[skip:skip + limit]
        return 200, json.dumps(rows).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    yahoo = [
        {"security_id": "sharadar:101", "session_date": "2010-01-05", "return": 0.01, "volume": 1_000_000},
    ]
    gate = execute_data_gate(
        client=client,
        store=tmp_path,
        yahoo_rows=yahoo,
        allow_network=True,
        reconcile_min_return_coverage=1,
        reconcile_min_securities=1,
    )
    assert gate["credential_present"] is True
    assert gate["live_sharadar_request_count"] > 0
    assert all(gate["raw_row_counts"][table] > 0 for table in ("stocks", "funds", "tickers", "actions"))
    assert all(gate["tables"][table]["status"] == "READ_OK" for table in gate["tables"])
    assert gate["tables"]["stocks"]["complete"] is True
    bbby = next(item for item in gate["delist"] if item["ticker"] == "BBBY")
    assert bbby["verification"] == "store_or_live_matched"
    assert bbby["live_status"] == "PASS"
    dell = next(item for item in gate["delist"] if item["ticker"] == "DELL")
    assert dell["verification"] == "fixture_list_only"
    assert gate["reconcile"]["status"] == "PASS"
    assert gate["history_budget"]["status"] == "COMPUTED"
    assert gate["history_budget"]["evaluable_sessions_after_warmup"] is not None
    assert gate["yahoo_fallback_used"] is False
    assert (tmp_path / "stocks.jsonl").is_file()
    assert gate["terminal_status"] in {"DATA_GATE_ACCEPTED", "DATA_GATE_PARTIAL_REVIEW_REQUIRED"}


def test_reconcile_control_and_missing_not_pass() -> None:
    one_row = (
        [{"security_id": "S", "session_date": "2020-01-02", "return": 0.01, "volume": 100}],
        [{"security_id": "S", "session_date": "2020-01-02", "return": 0.01, "volume": 100}],
    )
    # One aligned row is never a pass under the registered minimum sample.
    thin = reconcile_aligned_returns(*one_row)
    assert thin["status"] == "INSUFFICIENT"
    assert thin["insufficient_reason"] is not None
    ok = reconcile_aligned_returns(*one_row, min_return_coverage=1, min_securities=1)
    assert ok["status"] == "PASS"
    assert ok["aligned_n"] == 1
    missing = reconcile_aligned_returns(
        [{"security_id": "S", "session_date": "2020-01-02", "return": None, "volume": None}],
        [{"security_id": "S", "session_date": "2020-01-02", "return": None, "volume": None}],
    )
    assert missing["status"] == "INSUFFICIENT"
