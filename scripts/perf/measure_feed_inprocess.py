#!/usr/bin/env python3
"""In-process feed timing across seed sizes. Same code path as HTTP, no mocks."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.access import request_owner_access_context  # noqa: E402
from app.services.catalysts.local_intelligence import _reset_revision_cache  # noqa: E402
from app.services.catalysts.personal_service import PersonalCatalystService  # noqa: E402


def percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def bench(data_dir: Path, repeats: int, window: int, limit: int) -> dict:
    os.environ["DATA_DIR"] = str(data_dir)
    _reset_revision_cache()
    from app.services.catalysts.config import CatalystSettings

    settings = CatalystSettings(cache_db_path=data_dir / "catalyst-cache.db")
    service = PersonalCatalystService(settings)
    samples = []
    with request_owner_access_context(True):
        for i in range(repeats):
            if i == 0:
                _reset_revision_cache()
            started = time.perf_counter()
            payload = service.feed(
                window_hours=window,
                limit=limit,
                include_unanalyzed=True,
                include_neutral=True,
            )
            elapsed = (time.perf_counter() - started) * 1000
            items = payload.get("items") or []
            samples.append(
                {
                    "elapsed_ms": elapsed,
                    "item_count": len(items),
                    "total": (payload.get("summary") or {}).get("count"),
                    "status": payload.get("status"),
                    "cold_revision_cache": i == 0,
                }
            )
    latencies = [row["elapsed_ms"] for row in samples]
    return {
        "data_dir": str(data_dir),
        "window_hours": window,
        "limit": limit,
        "n": len(samples),
        "p50_ms": percentile(latencies, 0.5),
        "p75_ms": percentile(latencies, 0.75),
        "p95_ms": percentile(latencies, 0.95),
        "cold_ms": samples[0]["elapsed_ms"] if samples else None,
        "warm_p50_ms": percentile(latencies[1:], 0.5) if len(latencies) > 1 else None,
        "first_total": samples[0]["total"] if samples else None,
        "first_ids": None,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/feed-inprocess.json"))
    args = parser.parse_args()
    root = Path.home() / "optix-perf-data"
    report = {"measured_at": datetime.now(timezone.utc).isoformat(), "cases": []}
    for count in (100, 1000, 10000):
        data_dir = root / f"n{count}"
        if not (data_dir / "catalyst-cache.db").exists():
            continue
        for window, limit in ((72, 12), (24, 50)):
            result = bench(data_dir, args.repeats, window, limit)
            report["cases"].append(result)
            print(
                f"n={count} window={window} limit={limit} "
                f"cold={result['cold_ms']:.1f} warm_p50={result['warm_p50_ms']:.1f} "
                f"p95={result['p95_ms']:.1f} total={result['first_total']}"
            )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
