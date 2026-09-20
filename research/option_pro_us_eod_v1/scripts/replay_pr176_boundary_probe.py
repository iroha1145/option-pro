"""Replay the reviewer's 16 PR #176 probe cases against the shipped repository code.

The review ran transcribed source functions with filesystem/SQLite doubles and
reported 14 satisfied, 2 not. This script runs the same 16 cases through the
real modules -- real bulk ingest, real store, real classifier -- so the two
that failed can be shown fixed without re-transcribing anything.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/replay_pr176_boundary_probe.py

No credential is read and no network call is made.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar_acceptance import (  # noqa: E402
    evaluate_delist_fixture,
    warmup_hit_date,
)
from app.services.research_eod_v1.data.sharadar_bulk import ingest_bulk_archive, verify_ingested_state  # noqa: E402
from app.services.research_eod_v1.data.sharadar_identity import identity_from_ticker_row  # noqa: E402
from app.services.research_eod_v1.data.sharadar_pipeline import _event_bounds, _quote_bounds, summarize_stages  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ACTIONS_FIELDS,
    REQUIRED_GATE_STAGES,
    STOCKS_FIELDS,
    TICKERS_FIELDS,
)
from app.services.research_eod_v1.data.sharadar_store import (  # noqa: E402
    checkpoint_path,
    load_checkpoint,
    merged_path,
    page_path,
    read_jsonl,
    save_checkpoint,
)

OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3" / "runtime_first_probe_replay.json"
REVIEWED_HEAD = "50495405ce085e898bf260c784aff3da91bb5b5f"
FIXTURE = {"ticker": "EXAMPLE", "year": 2023, "kind": "acquisition", "expected_terminal": "acquisition_cash", "note": "toy"}
IDENTITY = {"security_id": "sharadar:toy", "permaticker": "toy", "ticker": "EXAMPLE"}


def _price_row(ticker: str, session: str, close: float = 10.0) -> dict:
    row = {field: "" for field in STOCKS_FIELDS}
    row.update({
        "ticker": ticker, "date": session, "open": close, "high": close + 0.5, "low": close - 0.5,
        "close": close, "volume": 1_000_000, "closeadj": close, "closeunadj": close, "lastupdated": session,
    })
    return row


def _action_row(ticker: str, session: str, action: str, value) -> dict:
    row = {field: "" for field in ACTIONS_FIELDS}
    row.update({"date": session, "action": action, "ticker": ticker, "name": ticker, "value": value})
    return row


def _ticker_row(ticker: str, permaticker: str, first: str, last: str) -> dict:
    row = {field: "" for field in TICKERS_FIELDS}
    row.update({
        "permaticker": permaticker, "ticker": ticker, "name": ticker, "exchange": "NASDAQ", "isdelisted": "Y",
        "category": "Domestic Common Stock", "currency": "USD", "firstpricedate": first, "lastpricedate": last,
        "table": "SEP",
    })
    return row


def _archive(path: Path, rows: list[dict]) -> Path:
    header = list(STOCKS_FIELDS)
    lines = [",".join(header)] + [",".join(str(row.get(field, "")) for field in header) for row in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("stocks.csv", "\n".join(lines) + "\n")
    path.write_bytes(buffer.getvalue())
    return path


def _sample(tmp: Path, name: str) -> tuple[Path, Path, list[dict]]:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    store = tmp / name
    archive = _archive(tmp / f"{name}.zip", rows)
    assert ingest_bulk_archive(store, "stocks", archive, years="full")["status"] == "READ_OK"
    return store, archive, rows


def _slim(verification: dict) -> dict:
    return {key: verification[key] for key in (
        "status", "problems", "expected_rows", "merged_rows", "key_index_rows",
        "committed_pages", "pages_verified", "source_archive", "repairable_from_pages",
    )}


def _case_summary(case: dict) -> dict:
    return {
        "label": case["observed_terminal"]["label"],
        "reason": case["observed_terminal"]["reason"],
        "value": case["observed_terminal"]["value"],
        "actions_seen": case["observed_terminal"]["actions_seen"],
        "live_status": case["live_status"],
        "observed_last_quote": case["observed_last_quote"],
        "settlement_evidence": case["settlement_evidence"],
        "settlement_evidence_source": case["settlement_evidence_source"],
        "unpriced_actions": case["unpriced_actions"],
        "determinate_terminal": case["determinate_terminal"],
        "concrete_terminal": case["concrete_terminal"],
        "economic_settlement_blocked_only": case["economic_settlement_blocked_only"],
        "rule_version": case["rule_version"],
    }


def _bulk_cases(tmp: Path) -> list[dict]:
    results: list[dict] = []

    store, archive, _rows = _sample(tmp, "intact")
    observed = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    results.append({
        "name": "bulk_intact_control",
        "contract_satisfied": observed["status"] == "READ_OK" and not observed["problems"],
        "observed": observed,
        "expected": "READ_OK for intact source/page/merged/index",
    })

    store, archive, _rows = _sample(tmp, "no_merged")
    merged_path(store, "stocks").unlink()
    observed = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    results.append({
        "name": "bulk_missing_merged_detected",
        "contract_satisfied": observed["status"] != "READ_OK" and observed["repairable_from_pages"],
        "observed": observed,
        "expected": "not READ_OK; repairable from intact pages",
    })

    store, archive, _rows = _sample(tmp, "changed_page")
    page = page_path(store, "stocks", 0)
    page.write_text(page.read_text(encoding="utf-8").replace("MSFT", "TAMPER"), encoding="utf-8")
    observed = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    results.append({
        "name": "bulk_changed_page_detected",
        "contract_satisfied": observed["status"] == "CORRUPT",
        "observed": observed,
        "expected": "CORRUPT",
    })

    # The case the review could not satisfy: rows and keys still line up, one
    # price does not.
    store, archive, rows = _sample(tmp, "changed_merged")
    source_close = read_jsonl(page_path(store, "stocks", 0))[0]["close"]
    merged = merged_path(store, "stocks")
    lines = merged.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["close"] = "99.0"
    lines[0] = json.dumps(row)
    merged.write_text("\n".join(lines) + "\n", encoding="utf-8")
    verification = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    repaired = ingest_bulk_archive(store, "stocks", archive, years="full")
    consumer_close = read_jsonl(merged)[0]["close"]
    results.append({
        "name": "bulk_same_count_changed_merged_rejected",
        "contract_satisfied": verification["status"] != "READ_OK" and str(consumer_close) == str(source_close),
        "observed": {
            "verification": verification,
            "source_page_close": source_close,
            "merged_consumer_close_before_reuse": "99.0",
            "merged_consumer_close_after_reuse": consumer_close,
            "repair": repaired.get("verification", {}).get("repaired"),
        },
        "expected": "not READ_OK or rebuild verified merged content; row count alone insufficient",
    })

    store, archive, _rows = _sample(tmp, "legacy_no_digest")
    stale = load_checkpoint(store, "stocks")
    stale.pop("merged_content", None)
    save_checkpoint(store, stale)
    observed = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    results.append({
        "name": "bulk_merged_without_digest_not_self_certified",
        "contract_satisfied": observed["status"] != "READ_OK" and "merged_content_unrecorded" in observed["problems"],
        "observed": observed,
        "expected": "an artifact with no recorded digest is rebuilt, not trusted",
    })

    store, archive, _rows = _sample(tmp, "checkpoint_only")
    keep = checkpoint_path(store, "stocks").read_text(encoding="utf-8")
    for path in list(store.iterdir()):
        if path.name == "checkpoints":
            continue
        shutil.rmtree(path) if path.is_dir() else path.unlink()
    archive.unlink()
    checkpoint_path(store, "stocks").write_text(keep, encoding="utf-8")
    observed = _slim(verify_ingested_state(store, "stocks", load_checkpoint(store, "stocks"), archive_path=archive))
    results.append({
        "name": "bulk_checkpoint_without_payload_rejected",
        "contract_satisfied": observed["status"] != "READ_OK",
        "observed": observed,
        "expected": "not READ_OK",
    })
    return results


def _whitelist_cases() -> list[dict]:
    all_pass = {name: {"status": "PASS", "evidence": {}} for name in REQUIRED_GATE_STAGES}
    summary = summarize_stages(all_pass)
    good = {
        "name": "whitelist_all_pass",
        "contract_satisfied": summary["accepted"] is True,
        "observed": {key: summary[key] for key in ("required", "optional", "accepted", "observed_status", "not_pass", "missing", "failed", "unknown")},
        "expected": "accepted",
    }
    observed: dict[str, bool] = {}
    for bad in ("FAIL", "SKIPPED", "PENDING", "whatever", "pass", "", None):
        stages = dict(all_pass)
        stages["RECONCILE"] = {"status": bad, "evidence": {}}
        observed[str(bad)] = summarize_stages(stages)["accepted"]
    no_status = dict(all_pass)
    no_status["HISTORY"] = {"evidence": {}}
    observed["missing_status"] = summarize_stages(no_status)["accepted"]
    observed["missing_stage"] = summarize_stages({k: v for k, v in all_pass.items() if k != "IDENTITY"})["accepted"]
    bad_case = {
        "name": "whitelist_rejects_unknown_or_missing",
        "contract_satisfied": not any(observed.values()),
        "observed": observed,
        "expected": "all false",
    }
    return [good, bad_case]


def _window_cases() -> list[dict]:
    identity = identity_from_ticker_row(_ticker_row("EXAMPLE1", "9001", "2010-01-04", "2023-06-02"))
    quote = _quote_bounds(identity, 2023)
    event = _event_bounds(identity, 2023, [identity])
    retained = {
        "name": "event_after_last_quote_retained",
        "contract_satisfied": quote[1] == date(2023, 6, 2) and event[0] <= date(2023, 6, 5) <= event[1],
        "observed": {"quote": [item.isoformat() for item in quote], "event": [item.isoformat() for item in event]},
        "expected": "price end June2; June5 event permitted",
    }
    successor = identity_from_ticker_row(_ticker_row("EXAMPLE", "9002", "2024-01-03", "2024-06-28"))
    bounded = _event_bounds(identity, 2023, [identity, successor])
    return [retained, {
        "name": "event_successor_and_holdout_bound",
        "contract_satisfied": bounded[1] == date(2024, 1, 2),
        "observed": [item.isoformat() for item in bounded],
        "expected": "end before successor, never use sealed 2025 event",
    }]


def _settlement_cases() -> list[dict]:
    bankruptcy = evaluate_delist_fixture(
        {"ticker": "EXAMPLE", "year": 2023, "kind": "bankruptcy", "expected_terminal": "bankruptcy_last_trade", "note": "toy"},
        [_action_row("EXAMPLE", "2023-05-01", "bankruptcy", None)],
        7.0,
        identity=IDENTITY,
    )
    cash = evaluate_delist_fixture(FIXTURE, [_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "12.0")], 11.5, identity=IDENTITY)
    mixed = evaluate_delist_fixture(
        FIXTURE,
        [_action_row("EXAMPLE", "2023-06-05", "acquisitioncash", "6.0"), _action_row("EXAMPLE", "2023-06-05", "acquisitionstock", "0.4")],
        11.5,
        identity=IDENTITY,
    )
    generic = evaluate_delist_fixture(FIXTURE, [_action_row("EXAMPLE", "2023-06-05", "merger", "0.4")], 11.5, identity=IDENTITY)
    election = evaluate_delist_fixture(FIXTURE, [_action_row("EXAMPLE", "2023-06-05", "acquisitionelectcash", "9.0")], 11.5, identity=IDENTITY)
    return [
        {
            "name": "bankruptcy_quote_is_not_settlement",
            "contract_satisfied": bankruptcy["determinate_terminal"] and not bankruptcy["concrete_terminal"],
            "observed": _case_summary(bankruptcy),
            "expected": "identity label permitted; economic gate blocked",
        },
        {
            "name": "explicit_cash_control",
            "contract_satisfied": bankruptcy is not None and cash["concrete_terminal"] and cash["live_status"] == "PASS",
            "observed": _case_summary(cash),
            "expected": "explicit cash accepted",
        },
        {
            "name": "mixed_consideration_blocked",
            "contract_satisfied": not mixed["concrete_terminal"] and mixed["live_status"] == "UNSUPPORTED",
            "observed": _case_summary(mixed),
            "expected": "mixed without complete valuation stays blocked",
        },
        {
            "name": "generic_merger_numeric_not_cash",
            "contract_satisfied": not generic["concrete_terminal"] and generic["economic_settlement_blocked_only"],
            "observed": _case_summary(generic),
            "expected": "generic numeric merger has no cash-unit proof; must not release economic gate",
        },
        {
            "name": "election_leg_is_not_complete_consideration",
            "contract_satisfied": not election["concrete_terminal"],
            "observed": _case_summary(election),
            "expected": "an election leg alone does not settle the position",
        },
    ]


def _warmup_cases() -> list[dict]:
    sparse = warmup_hit_date([date(2023, 1, 3), date(2023, 1, 6), date(2023, 1, 11)], 3)
    contiguous = warmup_hit_date([date(2023, 1, 3), date(2023, 1, 4), date(2023, 1, 5)], 3)
    summary_only = warmup_hit_date({"first": date(2023, 1, 3), "last": date(2023, 1, 11), "n": 3}, 3)
    return [
        {
            "name": "warmup_sparse_dates",
            "contract_satisfied": sparse == (date(2023, 1, 11), True),
            "observed": [sparse[0].isoformat() if sparse[0] else None, sparse[1]],
            "expected": ["2023-01-11", True],
        },
        {
            "name": "warmup_continuous_dates",
            "contract_satisfied": contiguous == (date(2023, 1, 5), True),
            "observed": [contiguous[0].isoformat() if contiguous[0] else None, contiguous[1]],
            "expected": ["2023-01-05", True],
        },
        {
            "name": "warmup_summary_without_dates",
            "contract_satisfied": summary_only == (None, False),
            "observed": [summary_only[0], summary_only[1]],
            "expected": [None, False],
        },
    ]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pr176-replay-"))
    try:
        results = _bulk_cases(tmp) + _whitelist_cases() + _window_cases() + _settlement_cases() + _warmup_cases()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
    payload = {
        "reviewed_head": REVIEWED_HEAD,
        "replay_head": head,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "shipped repository modules, real bulk ingest / store / classifier; no transcription",
        "market_data_replayed": False,
        "real_supplier_requests": 0,
        "secret_used": False,
        "cases": len(results),
        "satisfied": sum(1 for item in results if item["contract_satisfied"]),
        "unsatisfied": sum(1 for item in results if not item["contract_satisfied"]),
        "previously_unsatisfied": ["bulk_same_count_changed_merged_rejected", "generic_merger_numeric_not_cash"],
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "cases": payload["cases"],
        "satisfied": payload["satisfied"],
        "unsatisfied": payload["unsatisfied"],
        "failed": [item["name"] for item in results if not item["contract_satisfied"]],
        "out": str(OUT.relative_to(ROOT)),
    }, indent=2))
    return 0 if payload["unsatisfied"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
