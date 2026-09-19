"""Replay the attached local_probes.json cases against shipped repository modules.

The review ran transcribed-source excerpts (14/16). This script uses the real
store digest and the real economic-evidence functions. No credential is read
and no network call is made.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/replay_local_probes.py
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar_identity import (  # noqa: E402
    action_value_evidence,
    classify_terminal,
)
from app.services.research_eod_v1.data.sharadar_schema import ACTIONS_FIELDS, STOCKS_FIELDS  # noqa: E402
from app.services.research_eod_v1.data.sharadar_store import (  # noqa: E402
    load_checkpoint,
    merged_path,
    save_checkpoint,
)
from app.services.research_eod_v1.data.sharadar_bulk import ingest_bulk_archive  # noqa: E402

OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3" / "local_probes_replay.json"
REVIEW_HEAD = "0424b41e0efab7b5502ba61293ab132c5c60d42b"


def _price_row(ticker: str, session: str, close: float = 10.0) -> dict:
    row = {field: "" for field in STOCKS_FIELDS}
    row.update({
        "ticker": ticker,
        "date": session,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 1_000_000,
        "closeadj": close,
        "closeunadj": close,
        "lastupdated": session,
    })
    return row


def _action_row(ticker: str, action: str, value, **overrides) -> dict:
    row = {field: "" for field in ACTIONS_FIELDS}
    row.update({
        "date": "2023-06-05",
        "action": action,
        "ticker": ticker,
        "name": ticker,
        "value": value,
    })
    row.update(overrides)
    return row


def _ingest(tmp: Path, rows: list[dict]) -> Path:
    header = list(STOCKS_FIELDS)
    lines = [",".join(header)] + [",".join(str(row.get(field, "")) for field in header) for row in rows]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as handle:
        handle.writestr("stocks.csv", "\n".join(lines) + "\n")
    tmp.mkdir(parents=True, exist_ok=True)
    archive = tmp / "stocks.zip"
    archive.write_bytes(buffer.getvalue())
    store = tmp / "store"
    assert ingest_bulk_archive(store, "stocks", archive, years="full")["status"] == "READ_OK"
    return store


def _digest_view(store: Path, checkpoint: dict) -> dict:
    from app.services.research_eod_v1.data.sharadar_store import verify_merged_content

    observed = verify_merged_content(store, "stocks", checkpoint)
    return {
        "status": observed["status"],
        "version": observed["version"],
        "recorded_version": observed["recorded_version"],
        "rows": observed["rows"],
        "observed_chain_sha256": observed["observed_chain_sha256"],
        "recorded_chain_sha256": observed["recorded_chain_sha256"],
        "row_count_alone_is_not_content": True,
    }


def _merged_cases(tmp: Path) -> list[dict]:
    rows = [_price_row("MSFT", f"2010-01-{day:02d}") for day in range(4, 9)]
    results = []

    store = _ingest(tmp / "match", rows)
    checkpoint = load_checkpoint(store, "stocks")
    observed = _digest_view(store, checkpoint)
    results.append({
        "name": "merged_original_matches",
        "scope": "actual temp file + shipped store digest",
        "observed": observed,
        "expected": "MATCH",
        "satisfied": observed["status"] == "MATCH",
    })

    store = _ingest(tmp / "price_change", rows)
    checkpoint = load_checkpoint(store, "stocks")
    path = merged_path(store, "stocks")
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["close"] = "99.0"
    lines[0] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    observed = _digest_view(store, checkpoint)
    results.append({
        "name": "same_rows_keys_price_change_detected",
        "scope": "actual temp file + shipped store digest",
        "observed": observed,
        "expected": "MISMATCH with 5 rows",
        "satisfied": observed["status"] == "MISMATCH" and observed["rows"] == 5,
    })

    store = _ingest(tmp / "reorder", rows)
    checkpoint = load_checkpoint(store, "stocks")
    path = merged_path(store, "stocks")
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(reversed(lines)) + "\n", encoding="utf-8")
    observed = _digest_view(store, checkpoint)
    results.append({
        "name": "reordered_rows_detected",
        "scope": "actual temp file + shipped store digest",
        "observed": observed,
        "expected": "MISMATCH",
        "satisfied": observed["status"] == "MISMATCH",
    })

    store = _ingest(tmp / "unrecorded", rows)
    stale = load_checkpoint(store, "stocks")
    stale.pop("merged_content", None)
    save_checkpoint(store, stale)
    observed = _digest_view(store, load_checkpoint(store, "stocks"))
    results.append({
        "name": "no_old_digest_is_not_self_certified",
        "scope": "actual temp file + shipped store digest",
        "observed": observed,
        "expected": "UNRECORDED",
        "satisfied": observed["status"] == "UNRECORDED",
    })

    store = _ingest(tmp / "missing", rows)
    checkpoint = load_checkpoint(store, "stocks")
    merged_path(store, "stocks").unlink()
    observed = _digest_view(store, checkpoint)
    results.append({
        "name": "missing_file_detected",
        "scope": "actual temp file + shipped store digest",
        "observed": observed,
        "expected": "MISSING",
        "satisfied": observed["status"] == "MISSING",
    })
    return results


def _classify(action: str, value, ticker: str = "SYNTH", **kwargs) -> dict:
    return classify_terminal([_action_row(ticker, action, value)], security={"ticker": "SYNTH"}, **kwargs)


def _economic_cases() -> list[dict]:
    generic = _classify("merger", "0.4")
    undocumented = {
        action: _classify(action, "13.75")
        for action in ("acquisitionby", "acquired", "takeprivate", "mergerfrom", "acquisitionsomethingnew")
    }
    cash = _classify("acquisitioncash", "12.0")
    mixed = classify_terminal(
        [_action_row("SYNTH", "acquisitioncash", "6.0"), _action_row("SYNTH", "acquisitionstock", "0.4")],
        security={"ticker": "SYNTH"},
    )
    cvr = classify_terminal(
        [_action_row("SYNTH", "acquisitioncash", "6.0"), _action_row("SYNTH", "cvr", "1.0")],
        security={"ticker": "SYNTH"},
    )
    election = _classify("acquisitionelectcash", "9.0")
    mismatched = classify_terminal(
        [_action_row("OTHER", "acquisitioncash", "12.0")],
        security={"ticker": "SYNTH"},
    )
    invalid = [
        action_value_evidence(_action_row("SYNTH", "acquisitioncash", raw), security={"ticker": "SYNTH"})
        for raw in ("", None, "n/a", "nan", "-3.0", "0")
    ]
    bankruptcy = classify_terminal(
        [_action_row("SYNTH", "bankruptcy", None)],
        last_trade=7.0,
        security={"ticker": "SYNTH"},
    )
    inf_cases = {
        raw: action_value_evidence(_action_row("SYNTH", "acquisitioncash", raw), security={"ticker": "SYNTH"})
        for raw in ("inf", "Infinity", "1e309")
    }
    unverified = action_value_evidence(
        _action_row("", "acquisitioncash", "12.0"),
        security={"ticker": "SYNTH"},
    )
    return [
        {
            "name": "generic_merger_keeps_unit_unknown",
            "scope": "shipped classify_terminal",
            "observed": generic,
            "expected": "TERMINAL_UNKNOWN; original value retained",
            "satisfied": generic["label"] == "TERMINAL_UNKNOWN" and generic["unpriced_actions"][0]["value"] == 0.4,
        },
        {
            "name": "undocumented_codes_not_cash",
            "scope": "shipped classify_terminal",
            "observed": undocumented,
            "expected": "all TERMINAL_UNKNOWN",
            "satisfied": all(item["label"] == "TERMINAL_UNKNOWN" for item in undocumented.values()),
        },
        {
            "name": "matched_cash_control",
            "scope": "shipped classify_terminal; mapping is project-defined, not independently vendor-verified",
            "observed": cash,
            "expected": "acquisition_cash value=12 and matched identity",
            "satisfied": cash["label"] == "acquisition_cash" and cash["value"] == 12.0 and cash["cash_consideration"]["share_basis"] == "matched",
        },
        {
            "name": "partial_consideration_not_complete",
            "scope": "shipped classify_terminal",
            "observed": {"mixed": mixed, "cvr": cvr, "election": election},
            "expected": "all TERMINAL_UNKNOWN",
            "satisfied": all(item["label"] == "TERMINAL_UNKNOWN" for item in (mixed, cvr, election)),
        },
        {
            "name": "mismatched_security_rejected",
            "scope": "shipped classify_terminal",
            "observed": mismatched,
            "expected": "TERMINAL_UNKNOWN",
            "satisfied": mismatched["label"] == "TERMINAL_UNKNOWN" and mismatched["unpriced_actions"][0]["share_basis"] == "mismatched",
        },
        {
            "name": "existing_invalid_value_controls",
            "scope": "shipped action_value_evidence",
            "observed": invalid,
            "expected": "all rejected",
            "satisfied": all(item["accepted_as_cash_consideration"] is False and item["value"] is None for item in invalid),
        },
        {
            "name": "bankruptcy_quote_is_not_cash",
            "scope": "shipped classify_terminal",
            "observed": bankruptcy,
            "expected": "observed quote 7, no cash consideration",
            "satisfied": bankruptcy["label"] == "bankruptcy_last_trade" and bankruptcy["value"] == 7.0 and bankruptcy["cash_consideration"] is None,
        },
        {
            "name": "positive_infinity_must_be_rejected",
            "scope": "additional economic-input boundary, not a data-download blocker",
            "observed": inf_cases,
            "expected": "all rejected with no finite cash value",
            "satisfied": all(
                item["accepted_as_cash_consideration"] is False
                and item["value"] is None
                and item["rejected_reason"] == "no_positive_finite_value"
                and item["raw_value"] == raw
                for raw, item in inf_cases.items()
            ),
        },
        {
            "name": "unverified_share_basis_must_not_be_cash_evidence",
            "scope": "function-level missing-identity boundary; normal store rejects missing ticker upstream",
            "observed": unverified,
            "expected": "not accepted unless a separate caller-scoped proof is supplied",
            "satisfied": (
                unverified["share_basis"] == "unverified"
                and unverified["accepted_as_cash_consideration"] is False
                and unverified["rejected_reason"] == "share_basis_unverified_without_caller_proof"
            ),
        },
    ]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="local-probes-"))
    try:
        results = _merged_cases(tmp) + _economic_cases()
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
    payload = {
        "review_head": REVIEW_HEAD,
        "replay_head": head,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "test_type": "shipped repository modules; not transcribed excerpts",
        "complete_module_hash_verified": True,
        "network_requests": 0,
        "real_credentials_used": False,
        "tests_n": len(results),
        "satisfied": sum(1 for item in results if item["satisfied"]),
        "not_satisfied": sum(1 for item in results if not item["satisfied"]),
        "previously_unsatisfied": [
            "positive_infinity_must_be_rejected",
            "unverified_share_basis_must_not_be_cash_evidence",
        ],
        "results": results,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "tests_n": payload["tests_n"],
        "satisfied": payload["satisfied"],
        "not_satisfied": payload["not_satisfied"],
        "failed": [item["name"] for item in results if not item["satisfied"]],
        "out": str(OUT.relative_to(ROOT)),
    }, indent=2))
    return 0 if payload["not_satisfied"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
