#!/usr/bin/env python3
"""Exploratory mixed-URL load against the isolated backend.

Not a production demand model. Uses an arrival-rate loop so a slow system
does not hide itself by issuing fewer requests. Do not point at production
or paid upstreams. Keep this out of ordinary pytest.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PATHS = (
    "/ready",
    "/api/catalysts/status",
    "/api/catalysts/feed?window_hours=72&limit=12&include_unanalyzed=true&include_neutral=true",
    "/api/catalysts/feed?window_hours=24&limit=50&include_unanalyzed=true&include_neutral=true",
    "/api/catalysts/hotspots?limit=8",
    "/api/market/status",
    "/api/market/indices",
    "/api/access/status",
    "/watchlist",
    "/screener",
)


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def _one(base: str, path: str, timeout: float) -> dict[str, Any]:
    url = base.rstrip("/") + path
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read()
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "bytes": len(body),
                "path": path,
                "error": None,
            }
    except Exception as error:  # noqa: BLE001
        status = getattr(error, "code", None)
        return {
            "ok": False,
            "status": status,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "bytes": 0,
            "path": path,
            "error": f"{type(error).__name__}: {error}",
        }


def run_rate(base: str, rps: float, duration_s: float, timeout: float) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    lock = threading.Lock()
    stop_at = time.perf_counter() + duration_s
    interval = 1.0 / rps if rps > 0 else 1.0

    def worker(path: str) -> None:
        row = _one(base, path, timeout)
        with lock:
            samples.append(row)

    next_at = time.perf_counter()
    while time.perf_counter() < stop_at:
        path = random.choice(PATHS)
        threading.Thread(target=worker, args=(path,), daemon=True).start()
        next_at += interval
        sleep_for = next_at - time.perf_counter()
        if sleep_for > 0:
            time.sleep(sleep_for)
    time.sleep(timeout)
    return samples


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [float(row["elapsed_ms"]) for row in samples]
    ok = [row for row in samples if row.get("ok")]
    by_path: dict[str, list[float]] = {}
    for row in samples:
        by_path.setdefault(row["path"], []).append(float(row["elapsed_ms"]))
    return {
        "n": len(samples),
        "ok": len(ok),
        "errors": len(samples) - len(ok),
        "p50_ms": _percentile(latencies, 0.5),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99) if len(latencies) >= 20 else None,
        "by_path": {
            path: {
                "n": len(values),
                "p50_ms": _percentile(values, 0.5),
                "p95_ms": _percentile(values, 0.95),
            }
            for path, values in sorted(by_path.items())
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:2000")
    parser.add_argument("--rps", type=float, default=5)
    parser.add_argument("--duration", type=float, default=60)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/load-explore.json"))
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    samples = run_rate(args.base, args.rps, args.duration, args.timeout)
    report = {
        "label": "exploratory",
        "not_production_demand": True,
        "measured_at": started,
        "rps_target": args.rps,
        "duration_s": args.duration,
        "summary": summarize(samples),
        "samples": samples,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], indent=2))
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
