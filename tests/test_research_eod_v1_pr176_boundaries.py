"""The last two acceptance boundaries: merged content is verified, and a bare number is not cash.

Both were reproduced against the shipped interfaces before being fixed here.
The first one let a merged file whose rows and primary keys still lined up hand
a consumer an edited price. The second read the number on a generic merger row
as dollars per share and opened the economic gate on it.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.services.research_eod_v1.data.sharadar import (
    SharadarClient,
    explain_access_class,
    redacted_vendor_error,
)
from app.services.research_eod_v1.data.sharadar_acceptance import reconcile_aligned_returns
from app.services.research_eod_v1.data.sharadar_acceptance import evaluate_delist_fixture
from app.services.research_eod_v1.data.sharadar_bulk import ingest_bulk_archive, verify_ingested_state
from app.services.research_eod_v1.data.sharadar_identity import action_value_evidence, classify_terminal
from app.services.research_eod_v1.data.sharadar_pipeline import execute_data_gate
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTION_VALUE_SEMANTICS_VERSION,
    ACTIONS_FIELDS,
    ENV_KEY_NAME,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import (
    load_checkpoint,
    merged_content_digest,
    merged_path,
    page_path,
    read_jsonl,
    save_checkpoint,
    verify_merged_content,
)

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
        "table": "SEP",
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


def _bulk_zip(rows: list[dict], name: str = "stocks.csv") -> bytes:
    header = list(STOCKS_FIELDS)
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(field, "")) for field in header))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


def _ingest_sample(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path]:
    store = tmp_path / "store"
    archive = tmp_path / "stocks_full.zip"
    archive.write_bytes(_bulk_zip(rows))
    result = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert result["status"] == "READ_OK"
    return store, archive


def _edit_one_price(store: Path, table: str, close: str) -> None:
    """Rewrite one merged row in place. Row count and primary keys are untouched."""

    path = merged_path(store, table)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["close"] = close
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ------------------------------------------------- A. merged content, not merged row count


def test_same_row_count_and_keys_with_changed_content_is_not_read_ok(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive = _ingest_sample(tmp_path, rows)
    source_close = read_jsonl(page_path(store, "stocks", 0))[0]["close"]

    _edit_one_price(store, "stocks", "99.0")
    assert read_jsonl(merged_path(store, "stocks"))[0]["close"] == "99.0"

    checkpoint = load_checkpoint(store, "stocks")
    verification = verify_ingested_state(store, "stocks", checkpoint, archive_path=archive)
    assert verification["status"] != "READ_OK"
    assert verification["status"] == "CORRUPT"
    assert "merged_content_mismatch" in verification["problems"]
    # The counts that used to carry the whole check still agree with each other.
    assert verification["merged_rows"] == verification["expected_rows"] == len(rows)
    assert verification["key_index_rows"] == len(rows)
    assert verification["pages_verified"] is True
    assert verification["repairable_from_pages"] is True

    repaired = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert repaired["status"] == "READ_OK"
    assert repaired["verification"]["repaired"]["merged_content_rebound"] is True
    assert read_jsonl(merged_path(store, "stocks"))[0]["close"] == source_close


def test_merged_file_without_a_recorded_digest_cannot_vouch_for_itself(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive = _ingest_sample(tmp_path, rows)

    stale = load_checkpoint(store, "stocks")
    stale.pop("merged_content", None)
    save_checkpoint(store, stale)
    _edit_one_price(store, "stocks", "99.0")

    verification = verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive)
    assert verification["status"] != "READ_OK"
    assert "merged_content_unrecorded" in verification["problems"]
    assert verification["repairable_from_pages"] is True

    repaired = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert repaired["status"] == "READ_OK"
    assert read_jsonl(merged_path(store, "stocks"))[0]["close"] == "10.0"
    bound = load_checkpoint(store, "stocks")["merged_content"]
    assert bound["chain_sha256"] == merged_content_digest(store, "stocks")["chain_sha256"]


def test_digest_tracks_content_where_a_row_count_cannot(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, _archive = _ingest_sample(tmp_path, rows)
    checkpoint = load_checkpoint(store, "stocks")
    assert verify_merged_content(store, "stocks", checkpoint)["status"] == "MATCH"
    original = merged_content_digest(store, "stocks")

    path = merged_path(store, "stocks")
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    reordered = merged_content_digest(store, "stocks")
    assert reordered["rows"] == original["rows"]
    assert reordered["chain_sha256"] != original["chain_sha256"]
    assert verify_merged_content(store, "stocks", checkpoint)["status"] == "MISMATCH"

    path.unlink()
    absent = verify_merged_content(store, "stocks", checkpoint)
    assert absent["status"] == "MISSING"
    assert absent["rows"] is None


def test_paged_download_also_rebuilds_an_edited_merged_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        return 200, json.dumps(rows if skip == 0 else []).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    first = client.fetch_all("stocks", store=tmp_path, include_rows=False)
    assert first["status"] == "READ_OK"
    assert load_checkpoint(tmp_path, "stocks")["merged_content"]["chain_sha256"]

    _edit_one_price(tmp_path, "stocks", "99.0")
    again = client.fetch_all("stocks", store=tmp_path, include_rows=True)
    assert again["status"] == "READ_OK"
    assert float(again["rows"][0]["close"]) == 10.0
    assert load_checkpoint(tmp_path, "stocks")["merged_content_rebuilt_because"] == "MISMATCH"


# ------------------------------------------- B. a number is not a currency until something says so

_FIXTURE = {"ticker": "EXAMPLE", "year": 2023, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "toy"}
_IDENTITY = {"security_id": "sharadar:9001", "permaticker": "9001", "ticker": "EXAMPLE"}


def _case(actions: list[dict], last_trade: float | None = 11.5, fixture: dict | None = None) -> dict:
    return evaluate_delist_fixture(fixture or _FIXTURE, actions, last_trade, identity=_IDENTITY)


def test_generic_merger_number_is_not_a_cash_settlement() -> None:
    case = _case([_action_row("EXAMPLE", "2023-06-05", "merger", "0.4")])
    assert case["observed_terminal"]["label"] == "TERMINAL_UNKNOWN"
    assert case["observed_terminal"]["value"] is None
    assert case["observed_terminal"]["reason"] == "acquisition_value_unit_unverified"
    assert case["live_status"] == "UNSUPPORTED"
    assert case["concrete_terminal"] is False
    assert case["economic_settlement_blocked_only"] is True
    assert case["settlement_evidence"] is None
    assert case["settlement_evidence_source"] is None

    # The number is kept as observed, read as neither dollars nor a ratio.
    evidence = case["unpriced_actions"][0]
    assert evidence["action"] == "merger"
    assert evidence["raw_value"] == "0.4"
    assert evidence["value"] == 0.4
    assert evidence["unit"] == "unverified"
    assert evidence["not_assumed_usd"] is True
    assert evidence["not_assumed_exchange_ratio"] is True
    assert evidence["rejected_reason"] == "value_unit_not_documented_for_this_action"
    assert evidence["semantics_version"] == ACTION_VALUE_SEMANTICS_VERSION
    assert case["rule_version"] == "delist-terminal-rule-v3"


def test_undocumented_acquisition_codes_are_treated_the_same_way() -> None:
    for action in ("acquisitionby", "acquired", "takeprivate", "mergerfrom", "acquisitionsomethingnew"):
        case = _case([_action_row("EXAMPLE", "2023-06-05", action, "13.75")])
        assert case["observed_terminal"]["label"] == "TERMINAL_UNKNOWN", action
        assert case["concrete_terminal"] is False, action
        assert case["economic_settlement_blocked_only"] is True, action
        assert case["unpriced_actions"][0]["value"] == 13.75, action


def test_a_documented_cash_action_still_settles() -> None:
    case = _case([_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "12.0")])
    assert case["observed_terminal"]["label"] == "acquisition_cash"
    assert case["observed_terminal"]["value"] == 12.0
    assert case["observed_terminal"]["value_unit"] == "usd_per_share"
    assert case["live_status"] == "PASS"
    assert case["concrete_terminal"] is True
    assert case["economic_settlement_blocked_only"] is False
    source = case["settlement_evidence_source"]
    assert source["action"] == "acquisitioncash"
    assert source["date"] == "2023-06-05"
    assert source["share_basis"] == "matched"
    assert source["semantics_version"] == ACTION_VALUE_SEMANTICS_VERSION


def test_cash_and_stock_legs_and_bare_last_quotes_stay_blocked() -> None:
    mixed = _case([
        _action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "6.0"),
        _action_row("EXAMPLE", "2023-06-05", "acquisitionstock", "0.4"),
    ])
    assert mixed["observed_terminal"]["reason"] == "acquisition_stock_or_mixed"
    assert mixed["concrete_terminal"] is False

    bankruptcy = evaluate_delist_fixture(
        {"ticker": "EXAMPLE", "year": 2023, "kind": "bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "toy"},
        [_action_row("EXAMPLE", "2023-05-01", "bankruptcy", None, name="Chapter 11 bankruptcy")],
        7.0,
        identity=_IDENTITY,
    )
    assert bankruptcy["observed_terminal"]["label"] == "bankruptcy_last_trade"
    assert bankruptcy["observed_terminal"]["value_unit"] == "usd_per_share_observed_quote"
    assert bankruptcy["concrete_terminal"] is False
    assert bankruptcy["economic_settlement_blocked_only"] is True


def test_an_election_or_contingent_leg_is_not_the_whole_consideration() -> None:
    election = _case([_action_row("EXAMPLE", "2023-06-05", "acquisitionelectcash", "9.0")])
    assert election["observed_terminal"]["reason"] == "acquisition_consideration_incomplete"
    assert election["concrete_terminal"] is False

    contingent = _case([
        _action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "6.0"),
        _action_row("EXAMPLE", "2023-06-05", "cvr", "1.0"),
    ])
    assert contingent["observed_terminal"]["reason"] == "acquisition_consideration_incomplete"
    assert contingent["concrete_terminal"] is False


def test_a_cash_row_carried_by_another_security_is_not_this_settlement() -> None:
    case = _case([_action_row("OTHER", "2023-06-05", "acquisitioncash", "12.0")])
    assert case["observed_terminal"]["reason"] == "acquisition_action_on_another_security"
    assert case["concrete_terminal"] is False
    assert case["unpriced_actions"][0]["share_basis"] == "mismatched"


def test_unknown_and_non_finite_values_follow_the_existing_contract() -> None:
    for raw in ("", None, "n/a", "nan", "-3.0", "0"):
        evidence = action_value_evidence(_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", raw))
        assert evidence["value"] is None, raw
        assert evidence["accepted_as_cash_consideration"] is False, raw
        assert evidence["rejected_reason"] == "no_positive_finite_value", raw
    empty = classify_terminal([_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "")])
    assert empty["label"] == "TERMINAL_UNKNOWN"
    assert empty["value"] is None
    assert empty["reason"] == "acquisition_without_cash"


def test_positive_infinity_and_overflow_are_rejected() -> None:
    security = {"ticker": "SYNTH"}
    for raw in ("inf", "Infinity", "1e309"):
        evidence = action_value_evidence(
            _action_row("SYNTH", "2023-06-05", "acquisitioncash", raw),
            security=security,
        )
        assert evidence["raw_value"] == raw
        assert evidence["value"] is None
        assert evidence["accepted_as_cash_consideration"] is False
        assert evidence["rejected_reason"] == "no_positive_finite_value"
        assert evidence["share_basis"] == "matched"
    terminal = classify_terminal(
        [_action_row("SYNTH", "2023-06-05", "acquisitioncash", "inf")],
        security=security,
    )
    assert terminal["label"] == "TERMINAL_UNKNOWN"
    assert terminal["value"] is None
    assert terminal["cash_consideration"] is None


def test_unverified_share_basis_is_not_cash_evidence_without_proof() -> None:
    row = _action_row("", "2023-06-05", "acquisitioncash", "12.0")
    security = {"ticker": "SYNTH"}
    evidence = action_value_evidence(row, security=security)
    assert evidence["share_basis"] == "unverified"
    assert evidence["value"] == 12.0
    assert evidence["raw_value"] == "12.0"
    assert evidence["accepted_as_cash_consideration"] is False
    assert evidence["rejected_reason"] == "share_basis_unverified_without_caller_proof"
    assert evidence["share_basis_proof"] is None

    # "no contradiction found" is not a substitute for an explicit proof.
    case = _case([row], fixture={**_FIXTURE, "ticker": "SYNTH"})
    assert case["concrete_terminal"] is False
    assert case["settlement_evidence"] is None
    assert case["unpriced_actions"][0]["share_basis"] == "unverified"
    assert case["observed_terminal"]["reason"] == "acquisition_share_basis_unverified"

    proven = action_value_evidence(
        row,
        security={"ticker": "SYNTH", "permaticker": "9001", "security_id": "sharadar:9001"},
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "9001"},
    )
    assert proven["accepted_as_cash_consideration"] is True
    assert proven["share_basis"] == "unverified"
    assert proven["share_basis_proof"]["proof"] == "verified_permaticker"
    assert proven["share_basis_proof"]["permaticker"] == "9001"
    assert proven["share_basis_proof"]["not_inferred_from_missing_contradiction"] is True


def test_proof_for_a_different_permaticker_does_not_authorize_the_target() -> None:
    """A verified proof is only cash evidence when it names this security."""

    row = _action_row("", "2023-06-05", "acquisitioncash", "12.0")
    security = {"ticker": "SYNTH", "permaticker": "1001", "security_id": "sharadar:1001"}
    mismatched = action_value_evidence(
        row,
        security=security,
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1002"},
    )
    assert mismatched["share_basis"] == "unverified"
    assert mismatched["value"] == 12.0
    assert mismatched["accepted_as_cash_consideration"] is False
    assert mismatched["rejected_reason"] == "share_basis_proof_target_mismatch"
    assert mismatched["share_basis_proof"] is None

    missing_target = action_value_evidence(
        row,
        security={"ticker": "SYNTH"},
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1001"},
    )
    assert missing_target["accepted_as_cash_consideration"] is False
    assert missing_target["rejected_reason"] == "share_basis_proof_target_missing"

    conflicted = action_value_evidence(
        row,
        security={"ticker": "SYNTH", "permaticker": "1001", "security_id": "sharadar:1999"},
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1001"},
    )
    assert conflicted["accepted_as_cash_consideration"] is False
    assert conflicted["rejected_reason"] == "share_basis_proof_target_conflict"

    matched = action_value_evidence(
        row,
        security=security,
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1001"},
    )
    assert matched["accepted_as_cash_consideration"] is True
    assert matched["share_basis_proof"]["permaticker"] == "1001"
    assert matched["share_basis_proof"]["bound_to_security_permaticker"] == "1001"
    rejected_empty_proof = action_value_evidence(
        row,
        security=security,
        verified_share_basis_proof={"proof": "no_contradiction_found", "permaticker": "9001"},
    )
    assert rejected_empty_proof["accepted_as_cash_consideration"] is False


def test_http_401_and_403_stay_distinct_on_actions_pages(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)

    def opener_401(url: str, follow_redirects: bool = False):
        return 401, b'{"error":{"code":"invalid_api_key","message":"Invalid API key"}}', url

    def opener_403(url: str, follow_redirects: bool = False):
        return 403, b'{"error":{"code":"not_subscribed","message":"Not subscribed to this dataset"}}', url

    page_401 = SharadarClient(allow_network=True, opener=opener_401, sleep=lambda _s: None).fetch_page("actions")
    page_403 = SharadarClient(allow_network=True, opener=opener_403, sleep=lambda _s: None).fetch_page("actions")
    assert page_401.http_status == 401
    assert page_403.http_status == 403
    assert page_401.http_status != page_403.http_status
    assert page_401.vendor_error["vendor_code"] == "invalid_api_key"
    assert page_403.vendor_error["vendor_code"] == "not_subscribed"
    assert page_401.vendor_error["not_collapsed_to_upgrade_sku"] is True
    assert page_403.vendor_error["not_collapsed_to_upgrade_sku"] is True
    excerpt = redacted_vendor_error(b'{"error":"no key dummy-not-a-real-secret here"}', 401)
    assert SECRET not in json.dumps(excerpt)


def test_auth_failed_wrapper_explains_403_without_calling_it_credential_loss(monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)

    def opener_tier(url: str, follow_redirects: bool = False):
        return 403, b'{"error":{"message":"Exceeds free tier"}}', url

    def opener_forbidden(url: str, follow_redirects: bool = False):
        return 403, b'{"error":{"message":"Forbidden"}}', url

    page_tier = SharadarClient(allow_network=True, opener=opener_tier, sleep=lambda _s: None).fetch_page("actions")
    page_forbidden = SharadarClient(allow_network=True, opener=opener_forbidden, sleep=lambda _s: None).fetch_page("actions")
    assert page_tier.status == "AUTH_FAILED"
    assert page_tier.http_status == 403
    assert page_tier.vendor_error["access_class"] == "observed_access_or_quota_limit"
    assert page_tier.vendor_error["not_inferred_as_unsubscribed"] is True
    assert page_tier.vendor_error["page_status_wrapper"] == "AUTH_FAILED"
    assert page_forbidden.status == "AUTH_FAILED"
    assert page_forbidden.vendor_error["access_class"] == "forbidden_reason_unknown"
    assert page_forbidden.vendor_error["not_inferred_as_unsubscribed"] is True
    key_401 = explain_access_class(401, "Invalid API key")
    assert key_401["access_class"] == "credential_invalid_or_unauthorized"
    assert key_401["access_class"] != page_tier.vendor_error["access_class"]


def test_schema_format_json_400_is_not_subscription_evidence() -> None:
    explained = explain_access_class(
        400,
        "Bad request",
        endpoint="https://api.sharadar.com/v1.0/schema/actions",
        query_format="json",
        page_status="HTTP_ERROR",
    )
    assert explained["access_class"] == "unsupported_schema_format"
    assert explained["not_subscription_evidence"] is True
    assert explained["schema_format_json_is_not_subscription_evidence"] is True
    assert "json" not in explained["official_schema_formats"]
    recorded = redacted_vendor_error(
        b'{"error":{"message":"Bad request"}}',
        400,
        endpoint="/v1.0/schema/actions",
        query_format="json",
        page_status="HTTP_ERROR",
    )
    assert recorded["access_class"] == "unsupported_schema_format"
    assert recorded["http_status"] == 400


def test_data_endpoint_json_400_is_bad_request_not_schema_format() -> None:
    """format=json is valid on /data; only a known schema path + json is format evidence."""

    data_actions = explain_access_class(
        400,
        "Invalid date",
        endpoint="/v1.0/data/actions",
        query_format="json",
    )
    assert data_actions["access_class"] == "bad_request"
    assert data_actions["schema_format_json_is_not_subscription_evidence"] is False

    data_stocks = explain_access_class(
        400,
        "Unknown field",
        endpoint="/v1.0/data/stocks",
        query_format="json",
    )
    assert data_stocks["access_class"] == "bad_request"

    schema_sqlite = explain_access_class(
        400,
        "Bad request",
        endpoint="/v1.0/schema/actions",
        query_format="sqlite",
    )
    assert schema_sqlite["access_class"] == "bad_request"

    unknown_endpoint = explain_access_class(400, "Bad request", endpoint=None, query_format="json")
    assert unknown_endpoint["access_class"] == "bad_request"

    missing_format = explain_access_class(400, "Bad request", endpoint="/v1.0/schema/actions")
    assert missing_format["access_class"] == "bad_request"

    data_body = redacted_vendor_error(
        b'{"error":{"message":"Invalid date"}}',
        400,
        endpoint="/v1.0/data/actions",
        query_format="json",
    )
    assert data_body["access_class"] == "bad_request"
    stocks_body = redacted_vendor_error(
        b'{"error":{"message":"Unknown field"}}',
        400,
        endpoint="/v1.0/data/stocks",
        query_format="json",
    )
    assert stocks_body["access_class"] == "bad_request"


def test_reconcile_names_massive_and_keeps_other_fields_unknown() -> None:
    result = reconcile_aligned_returns(
        [
            {"security_id": "sharadar:1", "session_date": "2024-06-24", "return": 0.01, "volume": 100},
            {"security_id": "sharadar:2", "session_date": "2024-06-24", "return": 0.02, "volume": 100},
        ],
        [
            {"security_id": "sharadar:1", "session_date": "2024-06-24", "return": 0.01, "volume": 100},
            {"security_id": "sharadar:2", "session_date": "2024-06-24", "return": 0.02, "volume": 100},
        ],
        source={
            "kind": "massive_unadjusted_daily",
            "available": True,
            "price_basis": "sharadar_closeunadj_vs_massive_adjusted_false",
        },
        min_return_coverage=1,
        min_securities=1,
    )
    assert result["comparison_source"]["kind"] == "massive_unadjusted_daily"
    assert result["comparison_source"]["yahoo_label_not_used"] is True
    assert result["control_n"] == 2
    assert result["yahoo_n_is_legacy_alias"] is True
    assert result["field_status"]["unadjusted_simple_return"] == "PASS"
    assert result["field_status"]["split_adjusted_geometric_price"] == "UNKNOWN"
    assert result["field_status"]["total_return_with_dividends"] == "UNKNOWN"
    assert result["field_status"]["share_basis_volume_and_turnover"] == "UNKNOWN"
    assert result["field_status"]["economic_ledger"] == "UNKNOWN"
    assert result["field_status"]["overall_status_does_not_imply_other_fields"] is True


def test_unit_unverified_merger_blocks_execution_without_blocking_the_raw_tape(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {
        "tickers": [
            _ticker_row("MSFT", "101"),
            _ticker_row("XLNX", "9002", isdelisted="Y", firstpricedate="2010-01-04", lastpricedate="2022-02-14"),
        ],
        "stocks": [
            _price_row("MSFT", "2010-01-04", 30.0),
            _price_row("XLNX", "2022-02-14", 194.0),
        ],
        "funds": [],
        "actions": [_action_row("XLNX", "2022-02-14", "merger", "1.7234", contraticker="AMD", contraname="AMD Inc")],
    }

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        return 200, json.dumps(tables.get(table, []) if skip == 0 else []).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    case = next(item for item in gate["delist"] if item["ticker"] == "XLNX")
    assert case["identity_resolved"] is True
    assert case["identity_resolution"]["security_id"] == "sharadar:9002"
    assert case["concrete_terminal"] is False
    assert case["unpriced_actions"][0]["value"] == 1.7234

    # Identity resolves and the tape is stored; only the economic layer is shut.
    assert gate["stages"]["EXECUTION"]["status"] == "UNSUPPORTED"
    assert "XLNX" in gate["stages"]["EXECUTION"]["evidence"]["settlement_blocked_cases"]
    assert gate["stages"]["EXECUTION"]["evidence"]["raw_history_retained"] is True
    assert gate["isolated_row_counts"]["stocks"] == 2
    assert gate["transform"]["converted_rows"] == 2
    assert gate["transform"]["skipped_n"] == 0
    assert gate["action_vocabulary"]["value_semantics_version"] == ACTION_VALUE_SEMANTICS_VERSION
    assert "merger" in gate["action_vocabulary"]["assumed_by_terminal_classifier"]["acquisition_value_unit_unverified"]
    assert "merger" not in gate["action_vocabulary"]["assumed_by_terminal_classifier"]["cash_consideration_usd_per_share"]
