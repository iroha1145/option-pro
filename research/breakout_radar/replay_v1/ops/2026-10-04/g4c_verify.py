"""Round-2 checks on radarc1 after setup (2026-10-04): SPY gap diagnosis on the round-1 triggers,
the 2-day verification replay (RUN_SPEC section 7) and its evaluation, then a compact report."""
import glob
import gzip
import json
import os
import shutil
import subprocess
import time

P = "/content/option-pro/research/breakout_radar/replay_v1"
PY = "/content/venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
ROUND1 = "/content/drive/MyDrive/option-pro-data/radar_replay_2026-09-29"
FULL = ["--daily-db", "/content/data/replay.sqlite", "--minute-store", "/content/minute_store", "--fred", "/content/data/fred",
        "--directory", "/content/data/massive_directory_2026-09-27", "--sic", "/content/data/industry/ticker_sic.json.gz",
        "--shares", "/content/data/pit_shares.jsonl.gz", "--metadata", "directory", "--market-cap", "shares",
        "--warmup", "1", "--on-degraded", "continue",
        "--variants", "baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15"]
started = time.time()


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:6.0f}s {text}"
    print(line, flush=True)
    with open("/content/logs/verify.log", "a") as handle:
        handle.write(line + "\n")


def run(cmd, log):
    with open(log, "w") as handle:
        code = subprocess.run(cmd, env=ENV, stdout=handle, stderr=subprocess.STDOUT).returncode
    tail = open(log).read().splitlines()[-4:]
    say(f"exit {code}: {os.path.basename(log)}\n    " + "\n    ".join(line[:300] for line in tail))
    return code


os.makedirs("/content/eval/full_partial", exist_ok=True)
shutil.copyfile(f"{ROUND1}/eval/full_partial/events_h20.csv", "/content/eval/full_partial/events_h20.csv")
say("round-1 events_h20.csv copied from Drive")
run([PY, f"{P}/scripts/diagnose_spy_gaps.py", "--events", "/content/eval/full_partial/events_h20.csv",
     "--minute-store", "/content/minute_store", "--out", "/content/eval/spy_gaps.json"], "/content/logs/spy_gaps.log")
if os.path.exists("/content/eval/spy_gaps.json"):
    gaps = json.load(open("/content/eval/spy_gaps.json"))
    say("spy_gaps: " + json.dumps(gaps, default=str)[:2500])

code = run([PY, f"{P}/scripts/replay.py", *FULL, "--start", "2026-09-24", "--end", "2026-09-25", "--warmup", "1",
            "--out", "/content/replay/verify/seg_2026-09-24", "--db-dir", "/content/db/verify/seg_2026-09-24", "--label", "verify"],
           "/content/logs/verify_replay.log")
if code == 0:
    run([PY, f"{P}/scripts/evaluate.py", "--db", "/content/data/replay.sqlite", "--replay", "/content/replay/verify/seg_2026-09-24",
         "--variants", "baseline,confirm3,chase15", "--baseline", "baseline", "--minute-store", "/content/minute_store",
         "--out", "/content/eval/verify"], "/content/logs/verify_eval.log")

report = {}
run_json = "/content/replay/verify/seg_2026-09-24/run.json"
if os.path.exists(run_json):
    info = json.load(open(run_json))
    report["degraded"] = len(info.get("degraded") or [])
    report["proof_hash"] = (info.get("variants", {}).get("baseline", {}) or {}).get("production_field_hash")
    report["full_hash"] = (info.get("variants", {}).get("baseline", {}) or {}).get("full_hash")
records = []
for path in sorted(glob.glob("/content/replay/verify/seg_2026-09-24/baseline/ledger/*.jsonl.gz")):
    with gzip.open(path, "rt") as handle:
        records += [json.loads(line) for line in handle if line.strip()]
entries = [e for r in records for e in (r.get("events") or []) if "next_bar_delay_slots" in e]
report["scans"] = len(records)
report["entry_events_with_delay_field"] = len(entries)
report["entry_delay_slots"] = sorted({e.get("next_bar_delay_slots") for e in entries}, key=str)
report["scans_with_benchmark_delay_field"] = sum(1 for r in records if "benchmark_next_bar_delay_slots" in r)
report["benchmark_missing_when_entries"] = sum(1 for r in records if r.get("benchmark_next_bar_delay_slots") is None and any(
    "next_bar_delay_slots" in e for e in (r.get("events") or [])))
pack = "/content/eval/verify/result_pack.json"
if os.path.exists(pack):
    data = json.load(open(pack))
    variants = data.get("decisions", {}).get("variants", {})
    report["metric_views"] = {name: v.get("metric_view") for name, v in variants.items()}
    report["confirmed_share"] = variants.get("confirm3", {}).get("confirmed_share")
    report["extended_share"] = variants.get("chase15", {}).get("extended_share")
    report["triggers_confirmed"] = data.get("coverage", {}).get("baseline", {}).get("triggers_confirmed")
    report["benchmark_close_fallback"] = {n: c.get("benchmark_close_fallback") for n, c in data.get("coverage", {}).items()}
json.dump(report, open("/content/logs/verify_report.json", "w"), indent=1, default=str)
say("REPORT " + json.dumps(report, default=str))
say("DONE g4c_verify")
