#!/usr/bin/env python3
"""Restart one isolated backend and assert feed count/title still match.

Do not aim this at the soak process on :2000 while soak is running.
Default is the unoptimized contrast instance on :2001.
Does not call paid upstreams or production.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def _feed(base: str, timeout: float) -> dict:
    url = (
        base.rstrip("/")
        + "/api/catalysts/feed?window_hours=72&include_unanalyzed=true&include_neutral=true&limit=12"
    )
    with urllib.request.urlopen(url, timeout=timeout) as response:
        status = response.status
        body = json.loads(response.read().decode("utf-8"))
    items = body.get("items") or []
    title = ""
    if items:
        title = items[0].get("title_zh") or items[0].get("titleZh") or items[0].get("title") or ""
    summary = body.get("summary") or {}
    return {
        "status": status,
        "count": summary.get("count") if summary.get("count") is not None else body.get("count") or body.get("total"),
        "item_n": len(items),
        "first_title": title,
        "as_of": body.get("as_of"),
        "data_through": body.get("data_through"),
    }


def _wait_ready(base: str, timeout: float) -> None:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base.rstrip("/") + "/ready", timeout=5) as response:
                if 200 <= response.status < 300:
                    return
        except Exception as error:  # noqa: BLE001
            last = error
        time.sleep(0.4)
    raise SystemExit(f"backend not ready: {last}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:2001")
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--start-cmd", default="")
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/restart-recovery.json"))
    args = parser.parse_args()
    before = _feed(args.base, args.timeout)
    restarted = False
    if args.pid:
        os.kill(args.pid, signal.SIGTERM)
        time.sleep(1.0)
        if args.start_cmd:
            subprocess.Popen(args.start_cmd, shell=True, start_new_session=True)
            restarted = True
            _wait_ready(args.base, args.timeout)
        else:
            raise SystemExit("pid given but no --start-cmd to bring the process back")
    elif args.start_cmd:
        raise SystemExit("refusing to start a second copy without --pid of the one to stop")
    after = _feed(args.base, args.timeout)
    report = {
        "lab": True,
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "restarted": restarted,
        "before": before,
        "after": after,
        "count_match": before["count"] == after["count"],
        "title_match": before["first_title"] == after["first_title"],
        "ok": before["count"] == after["count"] and before["first_title"] == after["first_title"] and after["item_n"] == 12,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if not report["ok"]:
        raise SystemExit("restart recovery mismatch")


if __name__ == "__main__":
    main()
