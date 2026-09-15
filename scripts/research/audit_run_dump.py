#!/usr/bin/env python3
"""Audit uniqueness and completion of an existing screener or radar dump."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.replay_store import read_jsonl


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_keys(rows: list[dict], fields: tuple[str, str]) -> dict:
    keys = []
    missing = 0
    for row in rows:
        left = row.get(fields[0])
        right = row.get(fields[1])
        if not left or not right:
            missing += 1
            continue
        keys.append((str(left), str(right)))
    counts = Counter(keys)
    dupes = {f"{left}|{right}": n for (left, right), n in counts.items() if n > 1}
    return {
        "row_count": len(rows),
        "unique_keys": len(counts),
        "duplicate_key_count": len(dupes),
        "duplicate_examples": dict(list(dupes.items())[:20]),
        "missing_key_count": missing,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", help="screener compact rows JSON")
    parser.add_argument("--days", help="screener or radar day/ticker marker JSONL or JSON")
    parser.add_argument("--events", help="radar events JSON")
    parser.add_argument("--partial-days", help="legacy partial-days.jsonl")
    parser.add_argument("--partial-rows", help="legacy partial-rows.jsonl")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload: dict = {
        "review_status": "pending_review_of_pre_repair_dump",
        "files": {},
        "notes": [
            "Final day_count == 988 does not prove the detail file is unique or complete.",
            "Legacy partials are not an accepted resume identity.",
        ],
    }
    if args.rows:
        path = Path(args.rows)
        rows = json.loads(path.read_text(encoding="utf-8"))
        payload["screener_rows"] = _count_keys(rows, ("signal_date", "ticker"))
        payload["files"][str(path)] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
        by_day = Counter(str(row.get("signal_date")) for row in rows)
        payload["screener_rows"]["day_count"] = len(by_day)
        payload["screener_rows"]["min_rows_per_day"] = min(by_day.values()) if by_day else 0
        payload["screener_rows"]["max_rows_per_day"] = max(by_day.values()) if by_day else 0
    if args.events:
        path = Path(args.events)
        events = json.loads(path.read_text(encoding="utf-8"))
        payload["radar_events"] = _count_keys(events, ("trading_date", "ticker"))
        payload["radar_events"]["pivot_keys"] = _count_keys(events, ("ticker", "pivot_id"))
        payload["files"][str(path)] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    if args.days:
        path = Path(args.days)
        if path.suffix == ".jsonl":
            days = read_jsonl(path)
        else:
            raw = json.loads(path.read_text(encoding="utf-8"))
            days = raw.get("days") if isinstance(raw, dict) else raw
        payload["day_markers"] = {
            "count": len(days),
            "unique_signal_dates": len({str(item.get("signal_date")) for item in days if item.get("signal_date")}),
            "unique_tickers": len({str(item.get("ticker")) for item in days if item.get("ticker")}),
        }
        payload["files"][str(path)] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    if args.partial_days or args.partial_rows:
        days = read_jsonl(Path(args.partial_days)) if args.partial_days else []
        rows = read_jsonl(Path(args.partial_rows)) if args.partial_rows else []
        payload["legacy_partials"] = {
            "completed_markers": len(days),
            "partial_rows": _count_keys(rows, ("signal_date", "ticker")) if rows and "signal_date" in (rows[0] or {}) else _count_keys(rows, ("trading_date", "ticker")),
            "resume_without_manifest": "refused",
        }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({key: payload[key] for key in payload if key != "files"}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
