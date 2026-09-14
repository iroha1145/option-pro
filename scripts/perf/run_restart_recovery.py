#!/usr/bin/env python3
"""Restart the unoptimized backend (:2001 by default) and assert feed identity.

Does not touch the optimized process on :2000. Snapshots /catalysts/feed first
title + summary.count, SIGTERM the uvicorn PID, starts it again with the same
cwd/DATA_DIR, then polls /ready and compares the feed.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def fetch_json(url: str, timeout: float = 120.0) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def snapshot_feed(origin: str) -> dict:
    payload = fetch_json(f"{origin}/api/catalysts/feed?window_hours=72&limit=12&include_unanalyzed=true&include_neutral=true")
    items = payload.get("items") or []
    first = items[0] if items else {}
    return {
        "count": (payload.get("summary") or {}).get("count"),
        "as_of": (payload.get("summary") or {}).get("as_of"),
        "first_title": first.get("title_zh") or first.get("title"),
        "item_count": len(items),
    }


def wait_ready(origin: str, timeout_s: float) -> dict:
    deadline = time.monotonic() + timeout_s
    last_error = None
    while time.monotonic() < deadline:
        try:
            payload = fetch_json(f"{origin}/ready", timeout=3)
            if payload.get("ok") is True or payload.get("status") == "ready":
                return payload
            last_error = payload
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = str(exc)
        time.sleep(0.4)
    raise RuntimeError(f"/ready did not recover within {timeout_s}s: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", default=os.environ.get("OPTIX_PERF_UNOPT_ORIGIN", "http://127.0.0.1:2001"))
    parser.add_argument("--pid", type=int, default=int(os.environ.get("OPTIX_PERF_UNOPT_PID", "0")))
    parser.add_argument(
        "--root",
        default=os.environ.get("OPTIX_PERF_UNOPT_ROOT", str(Path.home() / "option-pro-unoptimized")),
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("OPTIX_PERF_UNOPT_DATA", str(Path.home() / "optix-perf-data/n10000-unopt")),
    )
    parser.add_argument("--ready-timeout", type=float, default=90.0)
    parser.add_argument("--out", default="/opt/cursor/artifacts/perf/restart-recovery.json")
    args = parser.parse_args()

    origin = args.origin.rstrip("/")
    pid = args.pid
    if pid <= 0:
        raise SystemExit("OPTIX_PERF_UNOPT_PID / --pid is required so we do not kill the wrong process")

    result: dict = {"ok": False, "origin": origin, "old_pid": pid}

    def write_result() -> None:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    try:
        before = snapshot_feed(origin)
    except Exception as exc:
        result["error"] = f"snapshot before restart failed: {exc}"
        write_result()
        return 1
    result["before"] = before
    t0 = time.perf_counter()

    os.kill(pid, signal.SIGTERM)
    for _ in range(50):
        try:
            os.kill(pid, 0)
            time.sleep(0.1)
        except ProcessLookupError:
            break
    else:
        os.kill(pid, signal.SIGKILL)
        time.sleep(0.2)

    env = os.environ.copy()
    env["DATA_DIR"] = args.data_dir
    env["OPTIX_HTTP_HOST"] = "127.0.0.1"
    env["OPTIX_HTTP_PORT"] = origin.rsplit(":", 1)[-1]
    log_path = Path("/tmp/optix-unopt-restart.log")
    log = log_path.open("a")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            env["OPTIX_HTTP_PORT"],
            "--log-level",
            "warning",
        ],
        cwd=str(Path(args.root) / "backend"),
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    try:
        ready = wait_ready(origin, args.ready_timeout)
        after = snapshot_feed(origin)
    except Exception as exc:
        result["new_pid"] = proc.pid
        result["error"] = f"ready/feed after restart failed: {exc}"
        result["ready_ms"] = (time.perf_counter() - t0) * 1000
        write_result()
        return 1
    elapsed_ms = (time.perf_counter() - t0) * 1000
    ok = (
        after["first_title"] == before["first_title"]
        and after["item_count"] == before["item_count"]
        and after["count"] == before["count"]
    )
    result.update({
        "ok": ok,
        "new_pid": proc.pid,
        "ready_ms": elapsed_ms,
        "after": after,
        "ready": ready,
    })
    write_result()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
