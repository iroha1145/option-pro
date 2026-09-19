"""Bounded official-channel Sharadar samples. Never starts the full-market gate.

Same-process credential check first. If SHARADAR_API_KEY is absent, no vendor
request is made. Dates stay inside the allowed region ending 2024-06-28.

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/run_authorized_sharadar_samples.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar import (  # noqa: E402
    SharadarClient,
    credential_present,
)
from app.services.research_eod_v1.data.sharadar_schema import (  # noqa: E402
    ALLOWED_END,
    ENV_KEY_NAME,
    OFFICIAL_CHANNEL,
    OFFICIAL_HTTPS_HOST,
    PROBE_SAMPLES,
)

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "sharadar_v3"
DEFAULT_SAMPLE_DIR = Path.home() / "optix-data" / "authorized_sharadar_samples"
SAMPLE_WINDOW = {"from": "2024-06-24", "to": str(ALLOWED_END)}
HISTORY_IDENTITY = PROBE_SAMPLES["delisted_example"]  # BBBY; permanent identity via tickers table


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _head() -> str | None:
    result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return result.stdout.strip() or None


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _summarize_page(page, *, date_field: str | None = "date") -> dict:
    rows = list(page.rows)
    dates = sorted({str(row.get(date_field) or "")[:10] for row in rows if date_field and row.get(date_field)})
    return {
        "status": page.status,
        "row_count": page.row_count,
        "non_empty": bool(rows),
        "first_date": dates[0] if dates else None,
        "last_date": dates[-1] if dates else None,
        "schema_ok": page.status == "READ_OK",
        "complete": page.complete,
        "body_kind": page.body_kind,
        "redacted_url_host_ok": OFFICIAL_HTTPS_HOST in (page.redacted_url or ""),
        "empty_is_not_forged": True,
    }


def _store_rows(sample_dir: Path, name: str, rows: list[dict]) -> dict:
    path = sample_dir / f"{name}.jsonl"
    text = "".join(json.dumps(row, ensure_ascii=False, default=str) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")
    return {
        "path": str(path),
        "sha256": _sha256_file(path),
        "rows": len(rows),
        "bytes": path.stat().st_size,
    }


def main() -> int:
    present = bool((os.environ.get(ENV_KEY_NAME) or "").strip())
    assert present is credential_present()
    print({"credential_present": present})
    checked_at = _now()
    sample_dir = Path(os.environ.get("AUTHORIZED_SHARADAR_SAMPLE_DIR") or DEFAULT_SAMPLE_DIR)
    report = {
        "generated_at": checked_at,
        "code_sha": _head(),
        "credential_present": present,
        "source_channel_required": OFFICIAL_CHANNEL,
        "official_https_host": OFFICIAL_HTTPS_HOST,
        "sample_window": SAMPLE_WINDOW,
        "allowed_end": str(ALLOWED_END),
        "authorized_restore_dir": str(sample_dir),
        "does_not_overwrite_mock_or_full_store": True,
        "full_market_bulk_started": False,
        "yahoo_fallback": False,
        "factor_or_weight_search": False,
        "purchase_attempted": False,
        "real_supplier_requests": 0,
        "mock_requests": 0,
        "cache_reads": 0,
        "tables": {},
        "artifacts": {},
        "blocking_field_or_entitlement": None,
        "terminal_status": "AUTH_REQUIRED",
    }
    if not present:
        report["blocking_field_or_entitlement"] = (
            f"process environment has no nonempty {ENV_KEY_NAME}; "
            "Runtime Secret must be injected at agent start. Real network tasks stopped."
        )
        report["tables"] = {
            "stocks": {"ticker": PROBE_SAMPLES["non_free_example"], "status": "AUTH_REQUIRED", "non_empty_rows": 0},
            "funds": {"ticker": PROBE_SAMPLES["fund_example"], "status": "AUTH_REQUIRED", "non_empty_rows": 0},
            "tickers": {"tickers": [PROBE_SAMPLES["non_free_example"], PROBE_SAMPLES["fund_example"], HISTORY_IDENTITY], "status": "AUTH_REQUIRED", "non_empty_rows": 0, "date_params_sent": False},
            "actions": {"window": SAMPLE_WINDOW, "status": "AUTH_REQUIRED", "non_empty_rows": 0, "empty_may_be_legitimate": True},
            "historical_identity": {"ticker": HISTORY_IDENTITY, "status": "AUTH_REQUIRED", "not_pretended_all_delists_passed": True},
        }
        _write(PACK / "authorized_sample_report.json", report)
        print(json.dumps({"terminal_status": report["terminal_status"], "real_supplier_requests": 0, "out": "sharadar_v3/authorized_sample_report.json"}, indent=2))
        return 2

    sample_dir.mkdir(parents=True, exist_ok=True)
    client = SharadarClient(allow_network=True)
    stocks = client.fetch_page(
        "stocks",
        extra={"ticker": PROBE_SAMPLES["non_free_example"], **SAMPLE_WINDOW},
    )
    funds = client.fetch_page(
        "funds",
        extra={"ticker": PROBE_SAMPLES["fund_example"], **SAMPLE_WINDOW},
    )
    tickers = client.fetch_page(
        "tickers",
        extra={"ticker": ",".join([PROBE_SAMPLES["non_free_example"], PROBE_SAMPLES["fund_example"], HISTORY_IDENTITY])},
    )
    actions = client.fetch_page(
        "actions",
        extra={"ticker": ",".join([PROBE_SAMPLES["non_free_example"], PROBE_SAMPLES["fund_example"], HISTORY_IDENTITY]), **SAMPLE_WINDOW},
    )
    history_id = client.fetch_page("tickers", extra={"ticker": HISTORY_IDENTITY})
    report["real_supplier_requests"] = client.live_request_count
    report["source_channel_observed"] = (
        "official_https_origin_api.sharadar.com" if client.channel_confirmed() else "unconfirmed_origin"
    )
    report["tables"] = {
        "stocks": {"ticker": PROBE_SAMPLES["non_free_example"], **_summarize_page(stocks)},
        "funds": {"ticker": PROBE_SAMPLES["fund_example"], **_summarize_page(funds)},
        "tickers": {"date_params_sent": False, **_summarize_page(tickers, date_field=None)},
        "actions": {
            **_summarize_page(actions),
            "empty_access_ok_is_not_nonempty_action_evidence": actions.row_count == 0 and actions.status == "READ_OK",
        },
        "historical_identity": {
            "ticker": HISTORY_IDENTITY,
            **_summarize_page(history_id, date_field=None),
            "permaticker": (history_id.rows[0].get("permaticker") if history_id.rows else None),
            "isdelisted": (history_id.rows[0].get("isdelisted") if history_id.rows else None),
            "not_pretended_all_delists_passed": True,
        },
    }
    if client.live_request_count and not client.channel_confirmed():
        report["blocking_field_or_entitlement"] = "responses were not from the confirmed official api.sharadar.com origin; no other origin was retried"
        report["terminal_status"] = "CHANNEL_UNCONFIRMED"
        _write(PACK / "authorized_sample_report.json", report)
        return 3
    report["artifacts"] = {
        "stocks": _store_rows(sample_dir, "stocks_msft", list(stocks.rows)),
        "funds": _store_rows(sample_dir, "funds_spy", list(funds.rows)),
        "tickers": _store_rows(sample_dir, "tickers", list(tickers.rows)),
        "actions": _store_rows(sample_dir, "actions", list(actions.rows)),
        "historical_identity": _store_rows(sample_dir, "historical_identity_bbby", list(history_id.rows)),
    }
    nonempty = [name for name, item in report["tables"].items() if item.get("non_empty") or item.get("row_count")]
    report["terminal_status"] = "SAMPLE_STORED" if nonempty else "ACCESS_VERIFIED_OR_EMPTY"
    if stocks.status not in {"READ_OK"}:
        report["blocking_field_or_entitlement"] = f"stocks sample status={stocks.status}"
    _write(PACK / "authorized_sample_report.json", report)
    print(json.dumps({
        "terminal_status": report["terminal_status"],
        "real_supplier_requests": report["real_supplier_requests"],
        "authorized_restore_dir": str(sample_dir),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
