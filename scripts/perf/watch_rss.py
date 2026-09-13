#!/usr/bin/env python3
"""Sample one process RSS/CPU while a soak or bench runs.

Does not restart or signal the target. Default target is the isolated uvicorn
on :2000. Write JSONL for leak vs bounded-growth review.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


def _read_status(pid: int) -> dict[str, int | str]:
    status = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
    fields: dict[str, int | str] = {"pid": pid}
    for line in status.splitlines():
        key, _, raw = line.partition(":")
        value = raw.strip()
        if key in {"VmRSS", "VmSize", "VmSwap"}:
            fields[key] = int(value.split()[0])
        elif key == "Threads":
            fields[key] = int(value)
        elif key == "Name":
            fields[key] = value
    stat = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").split()
    # utime + stime in clock ticks
    fields["cpu_ticks"] = int(stat[13]) + int(stat[14])
    return fields


def _find_uvicorn(port: int) -> int:
    explicit = os.environ.get("OPTIX_PERF_BACKEND_PID")
    if explicit:
        return int(explicit)
    for path in Path("/proc").iterdir():
        if not path.name.isdigit():
            continue
        try:
            cmd = (path / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if "uvicorn app.main:app" in cmd and f"--port {port}" in cmd:
            return int(path.name)
    raise SystemExit(f"no uvicorn on port {port}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=2000)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--interval", type=float, default=15.0)
    parser.add_argument("--hours", type=float, default=0.0, help="0 = until the process exits")
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/backend-rss.jsonl"))
    args = parser.parse_args()
    pid = args.pid or _find_uvicorn(args.port)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    stop_at = time.time() + args.hours * 3600 if args.hours > 0 else None
    with args.out.open("a", encoding="utf-8") as handle:
        while True:
            if not Path(f"/proc/{pid}").exists():
                handle.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "pid": pid, "exited": True}) + "\n")
                handle.flush()
                break
            row = {
                "at": datetime.now(timezone.utc).isoformat(),
                **_read_status(pid),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"pid={pid} rss_kb={row.get('VmRSS')} threads={row.get('Threads')}", flush=True)
            if stop_at is not None and time.time() >= stop_at:
                break
            time.sleep(args.interval)


if __name__ == "__main__":
    main()
