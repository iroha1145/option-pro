#!/usr/bin/env python3
"""Offline synthetic feed comparison; never contacts a model/provider.

Run in an environment whitelist. --reference disables only the two read
optimizations introduced with this script, keeping data and business rules
identical. Output includes response size/row counts, not just latency.
"""
from __future__ import annotations

import argparse
import cProfile
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
import tracemalloc

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from seed_catalyst_news import seed
from app.access import request_owner_access_context
from app.services.catalysts import personal_service as facade
from app.services.catalysts.config import CatalystSettings
from app.services.catalysts import local_intelligence as local


def bench_jobs(args):
    """100k/300k narrow synthetic history; latest 10% is in retention window."""
    import gc
    import sqlite3
    from app.services.ai_jobs.repository import AIJobRepository, _PAYLOAD_LOOKUP_INDEX_SQL

    args.data_dir.mkdir(parents=True, exist_ok=True)
    path = args.data_dir / "ai-jobs.db"
    if path.exists():
        raise RuntimeError("Use a fresh data-dir; this fixture never overwrites a database")
    repository = AIJobRepository(path)
    repository.initialize()
    count = args.jobs_count
    with sqlite3.connect(path) as connection:
        for index in ("idx_ai_jobs_news_identity", "idx_ai_jobs_focus_cycle", "idx_ai_jobs_report_identity"):
            connection.execute(f"DROP INDEX {index}")
        def rows():
            for number in range(count):
                job_type = ("earnings_impact" if number % 10 == 0 else "market_focus" if number % 10 == 1 else "news_impact")
                created = "2026-10-12T00:00:00Z" if number >= count * 9 // 10 else "2026-08-01T00:00:00Z"
                payload = json.dumps({"news_id": number, "cycle_id": f"cycle-{number}", "report_id": f"report-{number}", "analysis_stage": "post_release_final", "body": "x" * 128})
                yield (f"job-{number}", job_type, str(number), payload,
                       "pending" if number >= count - 100 else "completed", "fixture-model", "max", "background",
                       "fixture-prompt", "fixture-schema", "a" * 64, created, created,
                       '{"result":"' + 'x' * 128 + '"}', '{"receipt":"' + 'x' * 128 + '"}')
        connection.executemany("INSERT INTO ai_jobs(job_id,job_type,request_hash,payload_json,status,model,reasoning,execution_mode,prompt_version,schema_version,schema_sha256,created_at,updated_at,result_json,provider_result_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows())
        connection.commit()
    target_news = count - 2
    target_report = count - 10
    queries = {
        "news": ("SELECT j.* FROM ai_jobs j WHERE j.job_type='news_impact' AND json_extract(j.payload_json,'$.news_id')=?", (target_news,)),
        "report": ("SELECT EXISTS(SELECT 1 FROM ai_jobs WHERE job_type='earnings_impact' AND status IN ('pending','queued','in_progress') AND json_extract(payload_json,'$.report_id')=? AND COALESCE(json_extract(payload_json,'$.analysis_stage'),json_extract(payload_json,'$.analysis_phase'))='post_release_final')", (f"report-{target_report}",)),
        "cycle": ("SELECT j.* FROM ai_jobs j WHERE j.job_type='market_focus' AND json_extract(j.payload_json,'$.cycle_id')=?", (f"cycle-{count-9}",)),
        "retention": ("SELECT job_id,json_extract(payload_json,'$.news_id') FROM ai_jobs WHERE status NOT IN ('completed','failed','cancelled','insufficient_context','budget_blocked')", ()),
    }
    def measure_queries():
        result = {}
        with sqlite3.connect(path) as connection:
            for name, (sql, params) in queries.items():
                plan = [row[3] for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params)]
                elapsed = []
                for _ in range(6):
                    started = time.perf_counter()
                    output = connection.execute(sql, params).fetchall()
                    elapsed.append((time.perf_counter()-started)*1000)
                result[name] = {"warm_median_ms": statistics.median(elapsed[1:]), "rows": len(output), "plan": plan}
        return result
    before = measure_queries()
    with sqlite3.connect(path) as connection:
        started = time.perf_counter()
        connection.executescript(_PAYLOAD_LOOKUP_INDEX_SQL)
        migration_ms = (time.perf_counter()-started)*1000
    queries["retention"] = ("SELECT job_id,json_extract(payload_json,'$.news_id') FROM ai_jobs WHERE status IN ('pending','queued','in_progress')", ())
    after = measure_queries()
    intelligence = local.LocalCatalystIntelligence(args.data_dir / "catalyst-cache.db", repository, mode="manual", canonical_tickers=())
    floor = datetime(2026, 10, 2, tzinfo=timezone.utc)
    def reference_snapshot():
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(local._FULL_JOB_SNAPSHOT_SQL.format(created_filter="AND j.created_at>=?"), (local._iso(floor),)).fetchall()
        rows.sort(key=lambda row: row["created_at"] or "", reverse=True)
        return {str(row["job_id"]): dict(row) for row in rows}
    def snapshot_measure(fn):
        gc.collect()
        started = time.perf_counter()
        snapshot = fn()
        elapsed = (time.perf_counter()-started)*1000
        import hashlib
        digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
        length = len(snapshot)
        del snapshot
        gc.collect()
        tracemalloc.start()
        snapshot = fn()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        del snapshot
        return {"ms": elapsed, "peak_bytes": peak, "rows": length, "sha256": digest}
    old_snapshot = snapshot_measure(reference_snapshot)
    new_snapshot = snapshot_measure(lambda: intelligence._ai_job_snapshot(created_since=floor))
    assert old_snapshot["sha256"] == new_snapshot["sha256"]
    print(json.dumps({"jobs": count, "db_bytes": path.stat().st_size, "index_migration_ms": migration_ms,
        "queries_before": before, "queries_after": after, "snapshot_before": old_snapshot, "snapshot_after": new_snapshot}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=1000)
    parser.add_argument("--jobs-count", type=int, default=0)
    parser.add_argument("--hidden-newest", type=int, default=0)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--reference", action="store_true")
    parser.add_argument("--profile", type=Path)
    args = parser.parse_args()
    os.environ["DATA_DIR"] = str(args.data_dir)
    if any(os.environ.get(k) for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_BASE_URL")):
        raise RuntimeError("Use a clean environment without provider credentials/configuration")
    if args.jobs_count:
        bench_jobs(args)
        return
    manifest = seed(args.data_dir, args.count, 20261012,
                    hidden_newest=args.hidden_newest, clock=datetime.now(timezone.utc))
    facade._valid_zh_text_cached.cache_clear()
    if args.reference:
        facade._valid_zh_text_cached = facade._validate_zh_text
        local._REVISION_BUILD_LOCKS = tuple(nullcontext() for _ in range(32))
    service = facade.PersonalCatalystService(CatalystSettings(cache_db_path=args.data_dir / "catalyst-cache.db"))
    counts = {"published": 0, "projected": 0, "validation": 0}
    guard = threading.Lock()
    def instrument(obj, name, key):
        original = getattr(obj, name)
        def counted(*a, **kw):
            with guard:
                counts[key] += 1
            return original(*a, **kw)
        setattr(obj, name, counted)
    instrument(service.intelligence, "_published_item", "published")
    instrument(service, "_project_news_item", "projected")
    instrument(facade, "validate_simplified_chinese_text", "validation")
    def read(as_of=None):
        with request_owner_access_context(False):
            return service.feed(window_hours=72, limit=50, page_mode="visible", as_of=as_of)
    local._reset_revision_cache()
    barrier = threading.Barrier(10)
    concurrent_as_of = datetime.now(timezone.utc)
    def concurrent(_):
        barrier.wait()
        return read(concurrent_as_of)
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=10) as executor:
        responses = list(executor.map(concurrent, range(10)))
    cold = {"ms": (time.perf_counter()-start)*1000, "calls": dict(counts),
            "returned_counts": [len(r["items"]) for r in responses]}
    counts.update(dict.fromkeys(counts, 0))
    times = []
    profiler = cProfile.Profile()
    profiler.enable()
    for _ in range(args.repeats):
        start = time.perf_counter()
        response = read()
        times.append((time.perf_counter()-start)*1000)
    profiler.disable()
    if args.profile:
        profiler.dump_stats(str(args.profile))
    tracemalloc.start()
    read()
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(json.dumps({"reference": args.reference, "manifest": manifest, "concurrent_cold": cold,
        "warm_median_ms_profiled": statistics.median(times), "warm_calls_including_memory_sample": counts,
        "returned": len(response["items"]), "response_bytes": len(json.dumps(response).encode()),
        "warm_peak_tracemalloc_bytes": peak}, ensure_ascii=False))


if __name__ == "__main__":
    main()
