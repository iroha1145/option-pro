"""Closeout contracts: verified bulk payload, PASS whitelist, settlement evidence, real warmup dates."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from app.services.research_eod_v1.data.sharadar_acceptance import (
    evaluate_delist_fixture,
    history_budget,
    trading_calendar_sessions,
    warmup_hit_date,
)
from app.services.research_eod_v1.data.sharadar_bulk import ingest_bulk_archive, verify_ingested_state
from app.services.research_eod_v1.data.sharadar_pipeline import (
    _event_bounds,
    _quote_bounds,
    execute_data_gate,
    summarize_stages,
)
from app.services.research_eod_v1.data.sharadar_schema import (
    ACTIONS_FIELDS,
    ALLOWED_END,
    ENV_KEY_NAME,
    REQUIRED_GATE_STAGES,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import (
    checkpoint_path,
    load_checkpoint,
    merged_path,
    page_path,
    read_jsonl,
)
from app.services.research_eod_v1.data.sharadar import SharadarClient

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


def _identity(ticker: str, permaticker: str, first: str, last: str):
    from app.services.research_eod_v1.data.sharadar_identity import identity_from_ticker_row

    return identity_from_ticker_row(_ticker_row(ticker, permaticker, firstpricedate=first, lastpricedate=last))


def _bulk_zip(rows: list[dict], name: str = "stocks.csv") -> bytes:
    header = list(STOCKS_FIELDS)
    lines = [",".join(header)]
    for row in rows:
        lines.append(",".join(str(row.get(field, "")) for field in header))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(name, "\n".join(lines) + "\n")
    return buffer.getvalue()


def _stage(status: str) -> dict:
    return {"status": status, "evidence": {}}


# --------------------------------------------------------------------- A. bulk payload


def _ingest_sample(tmp_path: Path, rows: list[dict]) -> tuple[Path, Path, dict]:
    store = tmp_path / "store"
    archive = tmp_path / "stocks_full.zip"
    archive.write_bytes(_bulk_zip(rows))
    result = ingest_bulk_archive(store, "stocks", archive, years="full")
    return store, archive, result


def test_bulk_first_ingest_records_a_verifiable_payload(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive, result = _ingest_sample(tmp_path, rows)
    assert result["status"] == "READ_OK"
    assert result["row_count"] == len(rows)
    checkpoint = load_checkpoint(store, "stocks")
    assert checkpoint is not None
    assert checkpoint["source_archive"]["sha256"]
    assert checkpoint["source_archive"]["same_name_is_not_same_dataset"] is True

    again = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert again["status"] == "READ_OK"
    assert again["already_ingested"] is True
    assert again["verification"]["status"] == "READ_OK"
    assert again["verification"]["problems"] == []


def test_checkpoint_alone_is_not_a_payload(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive, first = _ingest_sample(tmp_path, rows)
    assert first["status"] == "READ_OK"

    keep = checkpoint_path(store, "stocks").read_text(encoding="utf-8")
    for path in store.iterdir():
        if path.name != "checkpoints":
            if path.is_dir():
                import shutil

                shutil.rmtree(path)
            else:
                path.unlink()
    archive.unlink()
    checkpoint_path(store, "stocks").write_text(keep, encoding="utf-8")

    result = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert result["status"] in {"MISSING", "CORRUPT", "PARTIAL"}
    assert result["status"] != "READ_OK"
    assert result["complete"] is False
    assert result["row_count"] == 0
    assert result["reason"] == "completed_checkpoint_without_verifiable_payload"


def test_missing_merged_file_rebuilds_from_pages(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive, _first = _ingest_sample(tmp_path, rows)
    merged_path(store, "stocks").unlink()

    checkpoint = load_checkpoint(store, "stocks")
    broken = verify_ingested_state(store, "stocks", checkpoint, archive_path=archive)
    assert broken["status"] != "READ_OK"
    assert "missing_merged_file" in broken["problems"]
    assert broken["repairable_from_pages"] is True

    repaired = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert repaired["status"] == "READ_OK"
    assert repaired["verification"]["repaired"]["rebuilt_from"] == "committed_pages"
    assert len(read_jsonl(merged_path(store, "stocks"))) == len(rows)


def test_deleted_or_altered_page_is_not_read_ok(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive, _first = _ingest_sample(tmp_path, rows)
    checkpoint = load_checkpoint(store, "stocks")
    page = page_path(store, "stocks", 0)

    original = page.read_text(encoding="utf-8")
    page.unlink()
    missing = verify_ingested_state(store, "stocks", checkpoint, archive_path=archive)
    assert missing["status"] == "MISSING"
    assert any(item.startswith("missing_page") for item in missing["problems"])

    page.write_text(original.replace("MSFT", "TAMPER"), encoding="utf-8")
    altered = verify_ingested_state(store, "stocks", checkpoint, archive_path=archive)
    assert altered["status"] == "CORRUPT"
    assert any(item.startswith("hash_mismatch") for item in altered["problems"])

    # The archive still verifies, so a re-ingest is allowed to rebuild from the source.
    page.write_text(original, encoding="utf-8")
    recovered = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert recovered["status"] == "READ_OK"


def test_changed_archive_is_not_assumed_to_be_the_same_dataset(tmp_path: Path) -> None:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store, archive, _first = _ingest_sample(tmp_path, rows)
    checkpoint = load_checkpoint(store, "stocks")

    archive.write_bytes(_bulk_zip(rows + [_price_row("MSFT", "2010-01-11")]))
    changed = verify_ingested_state(store, "stocks", checkpoint, archive_path=archive)
    assert changed["source_archive"] == "changed"
    assert changed["status"] == "CORRUPT"

    reingested = ingest_bulk_archive(store, "stocks", archive, years="full")
    assert reingested["status"] == "READ_OK"
    assert reingested["row_count"] == len(rows) + 1
    assert reingested["already_ingested"] is False


# ------------------------------------------------------------------ B. PASS whitelist


def test_acceptance_needs_an_explicit_pass_on_every_required_stage() -> None:
    all_pass = {name: _stage("PASS") for name in REQUIRED_GATE_STAGES}
    assert summarize_stages(all_pass)["accepted"] is True

    for bad in ("SKIPPED", "PENDING", "ok", "pass", ""):
        stages = dict(all_pass)
        stages["RECONCILE"] = _stage(bad)
        summary = summarize_stages(stages)
        assert summary["accepted"] is False, bad
        assert "RECONCILE" in summary["unknown"], bad
        assert summary["not_pass"][0]["status"] == bad

    no_status = dict(all_pass)
    no_status["HISTORY"] = {"evidence": {}}
    summary = summarize_stages(no_status)
    assert summary["accepted"] is False
    assert summary["observed_status"]["HISTORY"] is None

    none_status = dict(all_pass)
    none_status["HISTORY"] = _stage(None)  # type: ignore[arg-type]
    assert summarize_stages(none_status)["accepted"] is False

    absent = {name: _stage("PASS") for name in REQUIRED_GATE_STAGES if name != "IDENTITY"}
    summary = summarize_stages(absent)
    assert summary["accepted"] is False
    assert summary["missing"] == ["IDENTITY"]

    known_fail = dict(all_pass)
    known_fail["RECONCILE"] = _stage("FAIL")
    summary = summarize_stages(known_fail)
    assert summary["accepted"] is False
    assert summary["failed"] == ["RECONCILE"]


# -------------------------------------------------- C. quote vs event vs settlement


def test_settlement_event_after_the_final_quote_is_kept() -> None:
    identity = _identity("EXAMPLE1", "9001", "2010-01-04", "2023-06-02")
    event_start, event_end = _event_bounds(identity, 2023, [identity])
    quote_start, quote_end = _quote_bounds(identity, 2023)
    assert quote_end == date(2023, 6, 2)
    assert event_end == ALLOWED_END
    assert event_start == date(2023, 1, 1)
    assert quote_start == date(2023, 1, 1)
    assert event_start <= date(2023, 6, 5) <= event_end

    successor = _identity("EXAMPLE", "9002", "2024-01-03", "2024-06-28")
    _start, bounded_end = _event_bounds(identity, 2023, [identity, successor])
    assert bounded_end == date(2024, 1, 2)
    assert bounded_end < date(2024, 1, 3)
    assert date(2025, 8, 1) > ALLOWED_END


def test_observed_last_quote_does_not_release_the_economic_gate() -> None:
    fixture = {"ticker": "EXAMPLE", "year": 2023, "kind": "bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "toy"}
    bankrupt = evaluate_delist_fixture(
        fixture,
        [_action_row("EXAMPLE", "2023-05-01", "bankruptcy", None, name="Chapter 11 bankruptcy")],
        7.0,
        identity={"security_id": "sharadar:9001", "permaticker": "9001", "ticker": "EXAMPLE"},
    )
    assert bankrupt["observed_terminal"]["label"] == "bankruptcy_last_trade"
    assert bankrupt["observed_terminal"]["value"] == 7.0
    assert bankrupt["observed_last_quote"] == 7.0
    assert bankrupt["settlement_evidence"] is None
    assert bankrupt["concrete_terminal"] is False
    assert bankrupt["economic_settlement_blocked_only"] is True
    assert bankrupt["last_trade_is_not_liquidation_value"] is True

    cash = evaluate_delist_fixture(
        {"ticker": "EXAMPLE", "year": 2023, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "toy"},
        [_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "12.0")],
        11.5,
        identity={"security_id": "sharadar:9001", "permaticker": "9001", "ticker": "EXAMPLE"},
    )
    assert cash["observed_terminal"]["value"] == 12.0
    assert cash["settlement_evidence"] == "actions.cash_consideration"
    assert cash["concrete_terminal"] is True
    assert cash["economic_settlement_blocked_only"] is False

    mixed = evaluate_delist_fixture(
        {"ticker": "EXAMPLE", "year": 2023, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "toy"},
        [
            _action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "6.0"),
            _action_row("EXAMPLE", "2023-06-05", "acquisitionstock", "0.4"),
        ],
        11.5,
        identity={"security_id": "sharadar:9001", "permaticker": "9001", "ticker": "EXAMPLE"},
    )
    assert mixed["observed_terminal"]["value"] is None
    assert mixed["concrete_terminal"] is False
    assert mixed["economic_settlement_blocked_only"] is True


def test_cash_event_after_final_quote_survives_the_full_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    tables = {
        "tickers": [
            _ticker_row("BBBY", "2023", isdelisted="Y", firstpricedate="2010-01-04", lastpricedate="2023-06-02"),
            _ticker_row("MSFT", "101"),
        ],
        "stocks": [
            _price_row("BBBY", "2023-06-02", 3.5),
            _price_row("MSFT", "2010-01-04", 30.0),
        ],
        "funds": [],
        "actions": [
            _action_row("BBBY", "2023-06-05", "acquisitioncash", "4.25"),
            _action_row("BBBY", "2025-08-01", "acquisitioncash", "99.0"),
        ],
    }

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        rows = tables.get(table, []) if skip == 0 else []
        return 200, json.dumps(rows).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    case = next(item for item in gate["delist"] if item["ticker"] == "BBBY")
    window = case["identity_resolution"]["event_window"]
    assert window["quote_end"] == "2023-06-02"
    assert window["end"] == ALLOWED_END.isoformat()
    assert case["observed_terminal"]["value"] == 4.25
    assert case["settlement_evidence"] == "actions.cash_consideration"
    assert case["concrete_terminal"] is True
    # The sealed 2025 action never enters the window, so it cannot overwrite the value.
    assert case["observed_terminal"]["value"] != 99.0
    assert gate["isolated_row_counts"]["actions"] == 1


# -------------------------------------------------------------- D. real warmup dates


def test_warmup_date_counts_valid_observations_not_calendar_offset() -> None:
    gapped = [date(2023, 1, 3), date(2023, 1, 6), date(2023, 1, 11)]
    hit, known = warmup_hit_date(gapped, 3)
    assert known is True
    assert hit == date(2023, 1, 11)

    contiguous = [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)]
    assert warmup_hit_date(contiguous, 3) == (date(2023, 1, 5), True)

    summary_only = {"first": date(2023, 1, 3), "last": date(2023, 1, 11), "n": 3}
    assert warmup_hit_date(summary_only, 3) == (None, False)

    with_hits = {"first": date(2023, 1, 3), "last": date(2023, 1, 11), "n": 3, "warmup_hits": {"3": "2023-01-11"}}
    assert warmup_hit_date(with_hits, 3) == (date(2023, 1, 11), True)


def test_gapped_history_budget_does_not_report_a_contiguous_first_score_day() -> None:
    calendar = trading_calendar_sessions(date(2023, 1, 3), date(2023, 1, 31))
    gapped = history_budget(
        earliest=date(2023, 1, 3),
        entitlement_status="READ_OK",
        calendar=calendar,
        warmup={"toy": 3},
        security_sessions={"sharadar:1": [date(2023, 1, 3), date(2023, 1, 6), date(2023, 1, 11)]},
    )
    dist = gapped["per_security"]["first_score_day_distribution"]["toy"]
    assert dist["status"] == "COMPUTED"
    assert dist["earliest"] == "2023-01-11"
    assert dist["earliest"] != "2023-01-05"

    contiguous = history_budget(
        earliest=date(2023, 1, 3),
        entitlement_status="READ_OK",
        calendar=calendar,
        warmup={"toy": 3},
        security_sessions={"sharadar:1": [date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)]},
    )
    assert contiguous["per_security"]["first_score_day_distribution"]["toy"]["earliest"] == "2023-01-05"
    assert contiguous["per_security"]["first_score_day_is_per_security"] is True


def test_pipeline_records_the_date_each_warmup_count_was_reached(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv(ENV_KEY_NAME, SECRET)
    sessions = trading_calendar_sessions(date(2010, 1, 4), date(2024, 6, 28))
    dense = sessions[:300]
    sparse = [sessions[index] for index in range(0, 600, 2)][:300]
    tables = {
        "tickers": [_ticker_row("DENSE", "1"), _ticker_row("SPARSE", "2")],
        "stocks": (
            [_price_row("DENSE", item.isoformat(), 20.0) for item in dense]
            + [_price_row("SPARSE", item.isoformat(), 20.0) for item in sparse]
        ),
        "funds": [],
        "actions": [],
    }

    def opener(url: str, follow_redirects: bool = False):
        parts = urlsplit(url)
        table = parts.path.rsplit("/", 1)[-1]
        skip = int(parse_qs(parts.query).get("skip", ["0"])[0])
        rows = tables.get(table, []) if skip == 0 else []
        return 200, json.dumps(rows).encode(), url.split("?")[0]

    client = SharadarClient(allow_network=True, opener=opener, sleep=lambda _s: None)
    gate = execute_data_gate(client=client, store=tmp_path, yahoo_rows=[], allow_network=True)

    dist = gate["history_budget"]["per_security"]["first_score_day_distribution"]["A_trend_quality"]
    assert dist["status"] == "COMPUTED"
    assert dist["securities_with_first_score"] == 2
    assert dist["earliest"] == dense[251].isoformat()
    assert dist["latest"] == sparse[251].isoformat()
    # The sparse name needs twice the calendar span to reach the same 252 bars.
    assert dist["latest"] > dist["earliest"]
    assert gate["history_budget"]["per_security"]["securities_with_observation_gaps"] == 1
    assert gate["history_budget"]["per_security"]["pool_earliest_is_not_every_security_ready"] is True
