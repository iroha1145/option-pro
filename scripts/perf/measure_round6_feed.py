#!/usr/bin/env python3
"""Round-6 in-process feed timings: guest cache hits, visible pages, fingerprints.

Uses an isolated DATA_DIR copy. Does not talk to production.
"""

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
from app.services.catalysts import local_intelligence as local_module  # noqa: E402
from app.services.catalysts.config import CatalystSettings  # noqa: E402
from app.services.catalysts.local_intelligence import _reset_revision_cache  # noqa: E402
from app.services.catalysts.personal_service import PersonalCatalystService  # noqa: E402


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q)))]


def spread(values: list[float]) -> dict[str, float | None]:
    if len(values) < 2:
        return {"stdev": None, "iqr": None}
    ordered = sorted(values)
    mid = len(ordered) // 2
    return {
        "stdev": statistics.stdev(values),
        "iqr": ordered[min(len(ordered) - 1, int(len(ordered) * 0.75))]
        - ordered[int(len(ordered) * 0.25)],
    }


def hop_until_visible(service: PersonalCatalystService, **kwargs) -> dict:
    hidden = 0
    hops = 0
    cursor = None
    started = time.perf_counter()
    first_ids: list[int] = []
    while hops < 9:
        payload = service.feed(cursor=cursor, **kwargs)
        hops += 1
        hidden += int(payload.get("hidden_unanalyzed") or 0)
        items = payload.get("items") or []
        if items:
            first_ids = [int(item["news_id"]) for item in items[:3]]
            return {
                "elapsed_ms": (time.perf_counter() - started) * 1000,
                "hops": hops,
                "hidden": hidden,
                "item_count": len(items),
                "first_ids": first_ids,
                "status": payload.get("status"),
                "has_more": payload.get("has_more"),
                "total": (payload.get("summary") or {}).get("count"),
            }
        cursor = payload.get("next_cursor")
        if not cursor:
            break
    return {
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "hops": hops,
        "hidden": hidden,
        "item_count": 0,
        "first_ids": [],
        "status": "empty",
        "has_more": False,
        "total": None,
    }


def timed_feed(service: PersonalCatalystService, **kwargs) -> dict:
    started = time.perf_counter()
    payload = service.feed(**kwargs)
    items = payload.get("items") or []
    return {
        "elapsed_ms": (time.perf_counter() - started) * 1000,
        "item_count": len(items),
        "hidden": payload.get("hidden_unanalyzed"),
        "first_ids": [int(item["news_id"]) for item in items[:3]],
        "status": payload.get("status"),
        "has_more": payload.get("has_more"),
        "total": (payload.get("summary") or {}).get("count"),
    }


def count_store(data_dir: Path) -> dict[str, int]:
    import sqlite3

    path = data_dir / "catalyst-cache.db"
    with sqlite3.connect(path) as connection:
        links = connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_analysis_links"
        ).fetchone()[0]
        audits = connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_analysis_result_audit"
        ).fetchone()[0]
        revisions = connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions"
        ).fetchone()[0]
    return {
        "analysis_links": int(links),
        "result_audits": int(audits),
        "news_revisions": int(revisions),
    }


def run_case(service: PersonalCatalystService, repeats: int, owner: bool, **kwargs) -> dict:
    samples = []
    fingerprint_counts = []
    original = local_module._revision_store_cursor

    def counted(connection):
        counted.n += 1  # type: ignore[attr-defined]
        return original(connection)

    counted.n = 0  # type: ignore[attr-defined]
    local_module._revision_store_cursor = counted  # type: ignore[method-assign]
    try:
        context = request_owner_access_context(owner)
        with context:
            for index in range(repeats):
                if index == 0:
                    _reset_revision_cache()
                    counted.n = 0  # type: ignore[attr-defined]
                before = counted.n  # type: ignore[attr-defined]
                hop = kwargs.get("hop_empty")
                call = {k: v for k, v in kwargs.items() if k != "hop_empty"}
                row = hop_until_visible(service, **call) if hop else timed_feed(service, **call)
                row["cold"] = index == 0
                row["fingerprints"] = counted.n - before  # type: ignore[attr-defined]
                samples.append(row)
                fingerprint_counts.append(row["fingerprints"])
    finally:
        local_module._revision_store_cursor = original  # type: ignore[method-assign]
    latencies = [row["elapsed_ms"] for row in samples]
    warm = latencies[1:]
    return {
        "n": len(samples),
        "cold_ms": samples[0]["elapsed_ms"] if samples else None,
        "p50_ms": percentile(latencies, 0.5),
        "p75_ms": percentile(latencies, 0.75),
        "warm_p50_ms": percentile(warm, 0.5),
        "warm_p75_ms": percentile(warm, 0.75),
        "spread": spread(warm),
        "cold_fingerprints": samples[0]["fingerprints"] if samples else None,
        "warm_fingerprints_p50": percentile([float(v) for v in fingerprint_counts[1:]], 0.5),
        "first_ids": samples[0]["first_ids"] if samples else [],
        "hops": samples[0].get("hops"),
        "item_count": samples[0]["item_count"] if samples else None,
        "total": samples[0]["total"] if samples else None,
        "samples": samples,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=12)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("/opt/cursor/artifacts/perf/round6-feed-inprocess.json"),
    )
    args = parser.parse_args()
    data_dir = args.data_dir
    if not data_dir.is_absolute() or not (data_dir / "catalyst-cache.db").exists():
        raise SystemExit("data-dir must be an absolute directory with catalyst-cache.db")
    os.environ["DATA_DIR"] = str(data_dir)
    _reset_revision_cache()
    settings = CatalystSettings(cache_db_path=data_dir / "catalyst-cache.db")
    service = PersonalCatalystService(settings)
    base = {
        "window_hours": 72,
        "limit": 12,
        "include_unanalyzed": True,
        "include_neutral": True,
    }
    report = {
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "data_dir": str(data_dir),
        "store": count_store(data_dir),
        "cases": {},
    }
    report["cases"]["guest_legacy_hop"] = run_case(
        service, args.repeats, False, hop_empty=True, **base
    )
    report["cases"]["guest_visible"] = run_case(
        service, args.repeats, False, page_mode="visible", **base
    )
    report["cases"]["guest_legacy_one_page"] = run_case(
        service, args.repeats, False, **base
    )
    report["cases"]["owner_visible"] = run_case(
        service, args.repeats, True, page_mode="visible", **base
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "samples"} for k, v in report["cases"].items()}, ensure_ascii=False, indent=2))
    print(f"store={report['store']}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
