#!/usr/bin/env python3
"""HTTP + in-process API timing against a real catalyst-cache.db.

Does not mock /api/catalysts. Errors, empty pages, and non-200s stay in the
sample. Writes raw JSON under /opt/cursor/artifacts/perf/ unless --out is set.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))
    return ordered[index]


def _summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [row for row in samples if row.get("ok")]
    latencies = [float(row["elapsed_ms"]) for row in samples]
    return {
        "n": len(samples),
        "ok": len(ok),
        "errors": len(samples) - len(ok),
        "p50_ms": _percentile(latencies, 0.50),
        "p75_ms": _percentile(latencies, 0.75),
        "p95_ms": _percentile(latencies, 0.95),
        "p99_ms": _percentile(latencies, 0.99) if len(latencies) >= 20 else None,
        "min_ms": min(latencies) if latencies else None,
        "max_ms": max(latencies) if latencies else None,
        "stdev_ms": statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        "status_codes": sorted({row.get("status") for row in samples}),
        "first_ok_item_count": next((row.get("item_count") for row in ok), None),
        "first_ok_total": next((row.get("total") for row in ok), None),
        "first_ok_news_ids": next((row.get("news_ids") for row in ok), None),
    }


def _http_get(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000
            payload = json.loads(body.decode("utf-8"))
            items = payload.get("items") if isinstance(payload, dict) else None
            summary = payload.get("summary") if isinstance(payload, dict) else {}
            return {
                "ok": 200 <= response.status < 300,
                "status": response.status,
                "elapsed_ms": elapsed_ms,
                "bytes": len(body),
                "item_count": len(items) if isinstance(items, list) else None,
                "total": (summary or {}).get("count") if isinstance(summary, dict) else None,
                "has_more": payload.get("has_more") if isinstance(payload, dict) else None,
                "next_cursor": payload.get("next_cursor") if isinstance(payload, dict) else None,
                "hidden_unanalyzed": payload.get("hidden_unanalyzed") if isinstance(payload, dict) else None,
                "news_ids": [
                    item.get("news_id")
                    for item in items[:12]
                    if isinstance(item, dict)
                ]
                if isinstance(items, list)
                else None,
                "as_of": payload.get("as_of") if isinstance(payload, dict) else None,
                "error": None,
            }
    except urllib.error.HTTPError as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "status": error.code,
            "elapsed_ms": elapsed_ms,
            "bytes": 0,
            "error": str(error),
        }
    except Exception as error:  # noqa: BLE001 — keep every failure in the sample
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "ok": False,
            "status": None,
            "elapsed_ms": elapsed_ms,
            "bytes": 0,
            "error": f"{type(error).__name__}: {error}",
        }


def measure_http(base: str, path: str, repeats: int, timeout: float) -> dict[str, Any]:
    url = base.rstrip("/") + path
    samples = [_http_get(url, timeout) for _ in range(repeats)]
    return {"url": url, "samples": samples, "summary": _summarize(samples)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:2000")
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/api-baseline.json"))
    parser.add_argument("--label", default="baseline")
    args = parser.parse_args()
    paths = [
        "/health",
        "/ready",
        "/api/catalysts/status",
        "/api/catalysts/hotspots/status",
        "/api/catalysts/hotspots?limit=8",
        "/api/catalysts/feed?window_hours=72&limit=12&include_unanalyzed=true&include_neutral=true",
        "/api/catalysts/feed?window_hours=24&limit=50&include_unanalyzed=true&include_neutral=true",
        "/api/market/status",
        "/api/market/indices",
        "/api/access/status",
    ]
    results = {
        "label": args.label,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "repeats": args.repeats,
        "endpoints": {},
    }
    for path in paths:
        results["endpoints"][path] = measure_http(args.base, path, args.repeats, args.timeout)
        summary = results["endpoints"][path]["summary"]
        print(
            f"{path} n={summary['n']} ok={summary['ok']} "
            f"p50={summary['p50_ms']:.1f} p75={summary['p75_ms']:.1f} "
            f"p95={summary['p95_ms']:.1f} errors={summary['errors']}"
            if summary["p50_ms"] is not None
            else f"{path} n={summary['n']} no samples"
        )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
