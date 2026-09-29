"""Radar 5-year replay (RUN_SPEC section 3) with 40 parallel segment processes, run inside the kernel.

RUN_SPEC's xargs line passes a bash array into `bash -c`, where it is undefined, so the segments are
driven from here instead. A segment that fails is retried once (RUN_SPEC: a minute-store read error is
fixed by rerunning the segment). MODE is "full" (8 configurations, production data visibility) or
"realtime" (baseline, rvol2, lookback10 with zero bar and TradingView delays).
"""
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

MODE = os.environ.get("RADAR_MODE", "full")
P = "/content/option-pro/research/breakout_radar/replay_v1"
PY = "/content/venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
COMMON = ["--daily-db", "/content/data/replay.sqlite", "--minute-store", "/content/minute_store", "--fred", "/content/data/fred",
          "--directory", "/content/data/massive_directory_2026-09-27", "--sic", "/content/data/industry/ticker_sic.json.gz",
          "--shares", "/content/data/pit_shares.jsonl.gz", "--metadata", "directory", "--market-cap", "shares",
          "--warmup", "1", "--on-degraded", "continue"]
if MODE == "full":
    EXTRA = ["--variants", "baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15"]
else:
    EXTRA = ["--variants", "baseline,rvol2,lookback10", "--bar-delay-seconds", "0", "--tv-delay-minutes", "0"]
OUT = f"/content/replay/{MODE}"
LOGDIR = f"/content/logs/{MODE}"
os.makedirs(OUT, exist_ok=True)
os.makedirs(LOGDIR, exist_ok=True)
started = time.time()


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:7.0f}s {text}"
    print(line, flush=True)
    with open(f"/content/logs/run_{MODE}.log", "a") as handle:
        handle.write(line + "\n")


segments = subprocess.run([PY, "-c", """
import sys
sys.path.insert(0, "/content/option-pro/backend")
from datetime import date, timedelta
from app.services.market_calendar import is_trading_day
days = [d for d in (date(2021, 10, 4) + timedelta(n) for n in range(0, 1900)) if d <= date(2026, 9, 25) and is_trading_day(d)]
for i in range(0, len(days), 32):
    chunk = days[i:i + 32]
    print(chunk[0], chunk[-1])
"""], capture_output=True, text=True, env=ENV, check=True).stdout.split("\n")
segments = [line.split() for line in segments if line.strip()]
say(f"{MODE}: {len(segments)} segments, {segments[0][0]} .. {segments[-1][1]}")


def run_segment(start, end):
    for attempt in (1, 2):
        cmd = [PY, f"{P}/scripts/replay.py", *COMMON, *EXTRA, "--start", start, "--end", end,
               "--out", f"{OUT}/seg_{start}", "--db-dir", f"/content/db/{MODE}/seg_{start}", "--label", f"seg_{start}"]
        seg_started = time.time()
        with open(f"{LOGDIR}/seg_{start}.attempt{attempt}.log", "w") as handle:
            code = subprocess.run(cmd, env=ENV, stdout=handle, stderr=subprocess.STDOUT).returncode
        if code == 0:
            return start, end, attempt, code, time.time() - seg_started
    return start, end, attempt, code, time.time() - seg_started


results = []
with ThreadPoolExecutor(max_workers=int(os.environ.get("RADAR_PROCS", "40"))) as pool:
    futures = [pool.submit(run_segment, start, end) for start, end in segments]
    for future in as_completed(futures):
        start, end, attempt, code, seconds = future.result()
        results.append({"start": start, "end": end, "attempt": attempt, "exit": code, "seconds": round(seconds)})
        say(f"segment {start}..{end}: exit {code} (attempt {attempt}, {seconds / 60:.1f} min); {len(results)}/{len(segments)} done")
failed = [r for r in results if r["exit"] != 0]
with open(f"{OUT}/segments_summary.json", "w") as handle:
    json.dump({"mode": MODE, "segments": sorted(results, key=lambda r: r["start"]), "failed": failed,
               "elapsed_s": round(time.time() - started)}, handle, indent=2)
say(f"DONE {MODE}: {len(results) - len(failed)} ok, {len(failed)} failed")
