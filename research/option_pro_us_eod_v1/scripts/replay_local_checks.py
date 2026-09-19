"""Replay the attached local_checks.json cases against shipped modules.

The review recorded 6/7; the missing case was a verified proof that named a
different permaticker. This script uses the real action_value_evidence path.
No credential is read and no network call is made.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/replay_local_checks.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar_identity import (  # noqa: E402
    _numeric_value,
    action_value_evidence,
)
from app.services.research_eod_v1.data.sharadar_schema import ACTIONS_FIELDS  # noqa: E402

OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3" / "local_checks.json"
REVIEW_HEAD = "3de4bdf5ecb211c3faff9ed1652bdf5df80c1795"


def _action_row(ticker, date, action, value, **overrides) -> dict:
    row = {field: None for field in ACTIONS_FIELDS}
    row.update({"date": date, "action": action, "ticker": ticker, "value": value})
    row.update(overrides)
    return row


def _cash_row(*, ticker="EXAMPLE"):
    return _action_row(ticker, "2023-06-05", "acquisitioncash", "12.0")


def main() -> int:
    security_1001 = {"ticker": "EXAMPLE", "permaticker": "1001", "security_id": "sharadar:1001"}
    cases = []

    rejected = [_numeric_value({"value": raw}) for raw in (None, "", "nan", "inf", "Infinity", "1e309", -1, 0, True)]
    cases.append({
        "name": "non_finite_non_positive_and_boolean_rejected",
        "expected": "all value None",
        "actual": rejected,
        "satisfied": all(item["value"] is None and item["rejected_reason"] == "no_positive_finite_value" for item in rejected),
    })

    matched = action_value_evidence(_cash_row(), security=security_1001)
    cases.append({
        "name": "matched_positive_cash_control",
        "expected": True,
        "actual": matched,
        "satisfied": matched["accepted_as_cash_consideration"] is True and matched["value"] == 12.0,
    })

    merger = action_value_evidence(
        _action_row("EXAMPLE", "2023-06-05", "merger", "0.4"),
        security=security_1001,
    )
    cases.append({
        "name": "generic_merger_not_cash",
        "expected": False,
        "actual": merger,
        "satisfied": merger["accepted_as_cash_consideration"] is False
        and merger["rejected_reason"] == "value_unit_not_documented_for_this_action",
    })

    missing = action_value_evidence(_cash_row(ticker=None), security=security_1001)
    cases.append({
        "name": "missing_ticker_without_proof_rejected",
        "expected": False,
        "actual": missing,
        "satisfied": missing["accepted_as_cash_consideration"] is False
        and missing["rejected_reason"] == "share_basis_unverified_without_caller_proof",
    })

    proven = action_value_evidence(
        _cash_row(ticker=None),
        security=security_1001,
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1001"},
    )
    cases.append({
        "name": "matching_proof_control",
        "expected": True,
        "actual": proven,
        "satisfied": proven["accepted_as_cash_consideration"] is True
        and proven["share_basis_proof"]["permaticker"] == "1001"
        and proven["share_basis_proof"]["bound_to_security_permaticker"] == "1001",
    })

    wrong = action_value_evidence(
        _cash_row(ticker=None),
        security=security_1001,
        verified_share_basis_proof={"proof": "verified_permaticker", "permaticker": "1002"},
    )
    cases.append({
        "name": "proof_for_different_security_must_not_authorize_target",
        "expected": False,
        "actual": wrong,
        "satisfied": wrong["accepted_as_cash_consideration"] is False
        and wrong["rejected_reason"] == "share_basis_proof_target_mismatch"
        and wrong["share_basis_proof"] is None,
    })

    invalid_kind = action_value_evidence(
        _cash_row(ticker=None),
        security=security_1001,
        verified_share_basis_proof={"proof": "no_contradiction_found", "permaticker": "1001"},
    )
    cases.append({
        "name": "invalid_proof_kind_rejected",
        "expected": False,
        "actual": invalid_kind,
        "satisfied": invalid_kind["accepted_as_cash_consideration"] is False
        and invalid_kind["rejected_reason"] == "share_basis_unverified_without_caller_proof",
    })

    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip() or None
    payload = {
        "head": REVIEW_HEAD,
        "replay_head": head,
        "scope": "source-excerpt functions; shipped action_value_evidence; no network",
        "groups": len(cases),
        "satisfied": sum(1 for item in cases if item["satisfied"]),
        "not_satisfied": sum(1 for item in cases if not item["satisfied"]),
        "previously_unsatisfied": ["proof_for_different_security_must_not_authorize_target"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cases": cases,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({
        "groups": payload["groups"],
        "satisfied": payload["satisfied"],
        "not_satisfied": payload["not_satisfied"],
        "failed": [item["name"] for item in cases if not item["satisfied"]],
        "out": str(OUT.relative_to(ROOT)),
    }, indent=2))
    return 0 if payload["not_satisfied"] == 0 else 3


if __name__ == "__main__":
    raise SystemExit(main())
