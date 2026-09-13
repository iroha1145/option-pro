#!/usr/bin/env python3
"""Replay the public-board burst against a running Optix process.

Default target is the Cloud Agent uvicorn on 127.0.0.1:2000. The script
records per-path latency and refuses 5xx. It does not write, refresh, or
create model jobs.
"""

from __future__ import annotations

import argparse
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PATHS = (
    "/health",
    "/ready",
    "/api/market/status",
    "/api/market/indices",
    "/api/catalysts/status",
    "/api/catalysts/feed?window_hours=24&limit=1",
    "/api/catalysts/hotspots?limit=8",
    "/api/catalysts/hotspots/status",
    "/api/earnings/upcoming",
    "/api/sectors",
    "/api/breakouts/status",
    "/api/breakouts/current",
    "/api/catalysts/calendar",
    "/api/strength/market",
    "/api/signals/market",
    "/api/macro/conditions",
)


def fetch(base: str, path: str, timeout: float) -> tuple[str, int, float]:
    started = time.perf_counter()
    request = Request(base.rstrip("/") + path, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            response.read()
    except HTTPError as error:
        status = int(error.code)
        error.read()
    except URLError:
        status = 599
    return path, status, time.perf_counter() - started


def main() -> int:
    parser = argparse.ArgumentParser(description="Optix public-read stress")
    parser.add_argument("--base", default="http://127.0.0.1:2000")
    parser.add_argument("--repeat", type=int, default=6)
    parser.add_argument("--workers", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    jobs = PATHS * max(1, args.repeat)
    results: list[tuple[str, int, float]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch, args.base, path, args.timeout) for path in jobs]
        for future in as_completed(futures):
            results.append(future.result())
    by_path: dict[str, list[tuple[int, float]]] = {}
    for path, status, elapsed in results:
        by_path.setdefault(path, []).append((status, elapsed))
    failures = [item for item in results if item[1] >= 500]
    for path, samples in sorted(by_path.items()):
        latencies = [item[1] * 1000 for item in samples]
        codes = sorted({item[0] for item in samples})
        p50 = statistics.median(latencies)
        p95 = statistics.quantiles(latencies, n=20)[18] if len(latencies) >= 20 else max(latencies)
        print(
            f"{path:48} n={len(samples):3} codes={codes} "
            f"p50={p50:7.1f}ms p95={p95:7.1f}ms"
        )
    if failures:
        print(f"FAIL {len(failures)} responses were 5xx/timeout")
        return 1
    print(f"OK {len(results)} reads, no 5xx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
