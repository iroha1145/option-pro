#!/usr/bin/env python3
"""Summarize a soak JSONL: early vs late latency, errors, bounded growth."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def _load(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--in", dest="src", type=Path, default=Path("/opt/cursor/artifacts/perf/soak-2h.jsonl"))
    parser.add_argument("--rss", type=Path, default=Path("/opt/cursor/artifacts/perf/backend-rss.jsonl"))
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/soak-2h.analysis.json"))
    args = parser.parse_args()
    raw = _load(args.src)
    rows = []
    prev = None
    for row in raw:
        at = row.get("at")
        gap = None
        if prev and at:
            try:
                from datetime import datetime
                gap = (
                    datetime.fromisoformat(at.replace("Z", "+00:00"))
                    - datetime.fromisoformat(prev.replace("Z", "+00:00"))
                ).total_seconds()
            except ValueError:
                gap = None
        # End-of-run spin: leftover sleep was skipped, cycles fire every few ms.
        if gap is not None and gap < 1.0:
            continue
        rows.append(row)
        prev = at
    spin_dropped = len(raw) - len(rows)
    p95 = [float(row["summary"]["p95_ms"]) for row in rows if row.get("summary")]
    errors = [int(row["summary"].get("errors") or 0) for row in rows if row.get("summary")]
    ok = [int(row["summary"].get("ok") or 0) for row in rows if row.get("summary")]
    split = max(1, len(rows) // 3)
    early, late = p95[:split], p95[-split:]
    rss_rows = _load(args.rss) if args.rss.exists() else []
    rss = [int(row["VmRSS"]) for row in rss_rows if "VmRSS" in row]
    report = {
        "cycles": len(rows),
        "spin_cycles_dropped": spin_dropped,
        "first_at": rows[0]["at"] if rows else None,
        "last_at": rows[-1]["at"] if rows else None,
        "ok_sum": sum(ok),
        "error_sum": sum(errors),
        "p95_overall": _percentile(p95, 0.95),
        "p95_early_p50": _percentile(early, 0.5),
        "p95_late_p50": _percentile(late, 0.5),
        "p95_late_minus_early": (
            None if not early or not late or early[-1] is None else (sorted(late)[len(late) // 2] - sorted(early)[len(early) // 2])
        ),
        "rss_n": len(rss),
        "rss_kb_first": rss[0] if rss else None,
        "rss_kb_last": rss[-1] if rss else None,
        "rss_kb_delta": (rss[-1] - rss[0]) if rss else None,
        "bounded_growth_note": (
            "late p50 p95 within 2x early and rss delta < 256MiB looks bounded; otherwise inspect"
            if rows
            else "no soak rows"
        ),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
