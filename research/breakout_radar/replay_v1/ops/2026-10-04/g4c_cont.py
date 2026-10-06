"""Round-2 continuation on one G4 (2026-10-04, PREREGISTRATION 修订 6, RUN_SPEC section 7), run inside the kernel.

RADAR_MACHINE (1 or 2) picks segments_<n>.txt from continuation_segments.py --split 2 (both machines compute the
same split). Beside the run: a guard that touches STOP in unfinished sub-segments when disk runs low or the
24-hour session nears its end (SIGTERM as the last resort), and an hourly rsync of the outputs to Drive.
"""
import os
import subprocess
import time

MACHINE = int(os.environ["RADAR_MACHINE"])
SESSION_START = float(os.environ["RADAR_SESSION_START"])  # epoch seconds when this G4 was created
P = "/content/option-pro/research/breakout_radar/replay_v1"
PY = "/content/venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
DRIVE_OUT = f"/content/drive/MyDrive/option-pro-data/radar_replay_2026-10-04/cont_{MACHINE}"
FULL = ["--daily-db", "/content/data/replay.sqlite", "--minute-store", "/content/minute_store", "--fred", "/content/data/fred",
        "--directory", "/content/data/massive_directory_2026-09-27", "--sic", "/content/data/industry/ticker_sic.json.gz",
        "--shares", "/content/data/pit_shares.jsonl.gz", "--metadata", "directory", "--market-cap", "shares",
        "--warmup", "1", "--on-degraded", "continue",
        "--variants", "baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15"]
started = time.time()
os.makedirs("/content/logs/cont", exist_ok=True)


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:7.0f}s {text}"
    print(line, flush=True)
    with open(f"/content/logs/cont_{MACHINE}.log", "a") as handle:
        handle.write(line + "\n")


subprocess.run([PY, f"{P}/scripts/continuation_segments.py", "--completed",
                f"{P}/results/full_partial_2026-09-29/completed_days_by_segment.json",
                "--length", "10", "--slow-length", "6", "--split", "2", "--parallel", "40", "--out", "/content/continuation"],
               env=ENV, check=True, stdout=open("/content/logs/continuation_segments.log", "w"), stderr=subprocess.STDOUT)
segments = f"/content/continuation/segments_{MACHINE}.txt"
lines = [line for line in open(segments).read().splitlines() if line.strip()]
say(f"machine {MACHINE}: {len(lines)} sub-segments from {segments}; first {lines[0]!r}, last {lines[-1]!r}")

guard = f"""
LOG=/content/logs/cont_guard_{MACHINE}.log
PAT='replay_v1/scripts/[r]eplay\\.py'
stop_unfinished() {{ for d in /content/replay/cont/seg_*; do [ -f "$d/run.json" ] || touch "$d/STOP"; done; }}
i=0; stopped=""
while true; do
  avail=$(df -B1 --output=avail /content | tail -1 | tr -d ' ')
  age=$(( $(date +%s) - {int(SESSION_START)} ))
  n=$(pgrep -f "$PAT" | wc -l)
  if [ -z "$stopped" ] && {{ [ "$avail" -lt 15000000000 ] || [ "$age" -ge 75600 ]; }}; then
    stopped=1; echo "$(date -u +%H:%M:%SZ) STOP files touched (avail=${{avail}}B age=${{age}}s)" >> $LOG
  fi
  # Once stopping, keep touching STOP so sub-segments that start later stop before their first day.
  [ -n "$stopped" ] && stop_unfinished
  if [ "$avail" -lt 6000000000 ] || [ "$age" -ge 82800 ]; then
    pids=$(pgrep -f "$PAT"); [ -n "$pids" ] && kill -TERM $pids
    echo "$(date -u +%H:%M:%SZ) SIGTERM ($(echo $pids | wc -w) processes; avail=${{avail}}B age=${{age}}s)" >> $LOG
  fi
  [ $((i % 10)) -eq 0 ] && echo "$(date -u +%H:%M:%SZ) avail=$((avail/1000000000))GB procs=$n age=$((age/3600))h" >> $LOG
  i=$((i+1)); sleep 60
done
"""
open(f"/content/cont_guard_{MACHINE}.sh", "w").write(guard)
sync = (f"mkdir -p {DRIVE_OUT}; while true; do sleep 3600; rsync -a /content/replay/cont/ {DRIVE_OUT}/cont/ && "
        f"rsync -a /content/logs/ {DRIVE_OUT}/logs/ && date -u +%H:%M:%SZ >> /content/logs/drive_sync_{MACHINE}.log; done")
subprocess.Popen(["setsid", "bash", f"/content/cont_guard_{MACHINE}.sh"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
subprocess.Popen(["setsid", "bash", "-c", sync], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
say("guard and hourly Drive sync started")

code = subprocess.run([PY, f"{P}/scripts/run_segments.py", "--segments", segments, "--parallel", "40", "--delete-db-after",
                       "--out-root", "/content/replay/cont", "--db-root", "/content/db/cont", "--log-root", "/content/logs/cont",
                       "--", *FULL], env=ENV, stdout=open(f"/content/logs/run_segments_{MACHINE}.log", "w"),
                      stderr=subprocess.STDOUT).returncode
say(f"run_segments exit {code}: " + open(f"/content/logs/run_segments_{MACHINE}.log").read()[-600:])
subprocess.run("pkill -f 'while true; do sleep 3600; [r]sync'", shell=True)
for _ in range(120):  # let an hourly rsync that is already running finish first
    if subprocess.run("pgrep -f '[r]sync -a /content/replay/cont/'", shell=True, capture_output=True).returncode != 0:
        break
    time.sleep(5)
for src, dst in (("/content/replay/cont/", "cont/"), ("/content/logs/", "logs/")):
    out = subprocess.run(f"rsync -a {src} {DRIVE_OUT}/{dst}", shell=True, capture_output=True, text=True)
    say(f"final rsync {src}: exit {out.returncode} {out.stderr[-200:]}")
open("/content/CONT_DONE", "w").write(time.strftime("%H:%M:%SZ") + f" run_segments exit {code}\n")
say("DONE g4c_cont")
