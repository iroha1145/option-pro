"""Slim copies of breakout_t1_current (evaluate.py --db-dir) and research bundles for killed segments.

A segment killed before its end writes no research_bundle.json.gz; its variant databases are too big
to keep, so the rows the evaluation and later research read are copied out here (2026-09-29).
"""
import gzip
import json
import os
import shutil
import sqlite3
import sys
import time
from multiprocessing import Pool
from pathlib import Path

CODE = "/content/option-pro-eval"
sys.path.insert(0, f"{CODE}/research/breakout_radar/replay_v1")
sys.path.insert(0, f"{CODE}/backend")
from harness.runner import _dumps, _has_table  # noqa: E402
from app.services.breakouts.research import _load_completed_events_connection, _load_completed_shadows_connection  # noqa: E402

SRC, SLIM, BUNDLES = Path("/content/db/full"), Path("/content/db_t1/full"), Path("/content/bundles_killed/full")
REPLAY = Path("/content/replay/full")
MIN_FREE = 8_000_000_000


def one(job: tuple) -> dict:
    path, mode = Path(job[0]), job[1]
    if mode == "bundles" and time.time() > float(os.environ.get("BUNDLE_DEADLINE", "1e12")):
        return {"segment": path.parent.name, "variant": path.stem, "bundle": "skipped: deadline"}
    seg, variant = path.parent.name, path.stem
    row = {"segment": seg, "variant": variant}
    connection = sqlite3.connect(str(path), timeout=60)  # read-write, so a killed writer's WAL is recovered
    connection.row_factory = sqlite3.Row
    try:
        slim = SLIM / seg / f"{variant}.sqlite"
        has_t1 = _has_table(connection, "breakout_t1_current")
        if mode == "t1" and has_t1:
            slim.parent.mkdir(parents=True, exist_ok=True)
            slim.unlink(missing_ok=True)
            connection.execute("ATTACH DATABASE ? AS slim", (str(slim),))
            connection.execute("CREATE TABLE slim.breakout_t1_current AS SELECT * FROM main.breakout_t1_current")
            connection.execute("DETACH DATABASE slim")
            row["t1_rows"] = connection.execute("SELECT COUNT(*) FROM breakout_t1_current").fetchone()[0]
        finished = (REPLAY / seg / "run.json").exists() and not os.environ.get("FORCE_BUNDLE")
        if mode == "t1":
            pass
        elif finished:
            row["bundle"] = "runner wrote it"
        elif shutil.disk_usage("/content").free < MIN_FREE:
            row["bundle"] = "skipped: disk"
        else:
            bundle = {
                "events": _load_completed_events_connection(connection),
                "shadows": _load_completed_shadows_connection(connection),
                "transitions": [dict(r) for r in connection.execute("SELECT * FROM breakout_transitions ORDER BY evidence_at, rowid")],
                "heads": [dict(r) for r in connection.execute("SELECT * FROM breakout_events ORDER BY first_seen_at, event_id")],
                "t1_current": [dict(r) for r in connection.execute("SELECT * FROM breakout_t1_current")] if has_t1 else [],
            }
            target = BUNDLES / seg / variant / "research_bundle.json.gz"
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name(target.name + ".tmp")
            with gzip.open(temporary, "wt", encoding="utf-8") as handle:
                handle.write(_dumps(bundle))
            os.replace(temporary, target)
            row["bundle"] = {key: len(value) for key, value in bundle.items()}
    finally:
        connection.close()
    if mode == "bundles" and isinstance(row.get("bundle"), dict) and os.environ.get("DELETE_AFTER"):
        # The bundle holds the rows research reads (identical to the runner's own bundle, checked on
        # seg_2026-09-24); the database itself would be discarded with the machine anyway.
        for suffix in ("", "-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        row["db_deleted"] = True
    return row


def safe_one(job: tuple) -> dict:
    try:
        return one(job)
    except Exception as exc:  # one broken database must not stop the others
        return {"segment": Path(job[0]).parent.name, "variant": Path(job[0]).stem, "error": repr(exc)[-500:]}


if __name__ == "__main__":
    mode, only = sys.argv[1], set(sys.argv[2:])
    assert mode in ("t1", "bundles"), mode
    paths = sorted(str(p) for p in SRC.glob("seg_*/*.sqlite") if not only or p.parent.name in only)
    with Pool(32) as pool:
        rows = pool.map(safe_one, [(p, mode) for p in paths], chunksize=1)
    Path(f"/content/logs/export_{mode}.json").write_text(json.dumps(rows, indent=1))
    print(f"exported {len(rows)} databases; t1 rows {sum(r.get('t1_rows', 0) for r in rows)}; "
          f"bundles {sum(1 for r in rows if isinstance(r.get('bundle'), dict))}; "
          f"skipped for disk {sum(1 for r in rows if r.get('bundle') == 'skipped: disk')}; "
          f"skipped for deadline {sum(1 for r in rows if r.get('bundle') == 'skipped: deadline')}; "
          f"databases deleted {sum(1 for r in rows if r.get('db_deleted'))}; errors {sum(1 for r in rows if 'error' in r)}")
