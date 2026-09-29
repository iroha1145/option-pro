"""After the radar replay stops (guard: disk, memory or 14:30 UTC): slim T1 copies, Drive sync, evaluation
with the a20c5769 code, per-segment completed days, bundles for killed segments (time-boxed), final sync
and unmount. Queued behind the replay cell so the kernel stays busy (2026-09-29)."""
import glob
import json
import os
import signal
import subprocess
import tarfile
import time
from datetime import datetime, timezone

LOG = "/content/logs/post_stop.log"
PAT = "replay_v1/scripts/[r]eplay\\.py"
CODE = "/content/option-pro-eval"
PY = "/content/venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{CODE}/backend"}
DRIVE = "/content/drive/MyDrive/option-pro-data/radar_replay_2026-09-29"
VARIANTS = ["baseline", "confirm3", "chase15", "orb15", "orb60", "disc5", "adv25", "basemin15"]
EVAL_OUT = "/content/eval/full_partial"
summary = {"started": datetime.now(timezone.utc).isoformat()}
t0 = time.time()


def utc(hour, minute):
    return datetime(2026, 9, 29, hour, minute, tzinfo=timezone.utc).timestamp()


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - t0:6.0f}s {text}"
    print(line, flush=True)
    with open(LOG, "a") as handle:
        handle.write(line + "\n")


def sh(cmd, timeout=None):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return out.returncode, (out.stdout + out.stderr).strip()


def run_logged(cmd, log, timeout, extra_env=None):
    with open(log, "w") as handle:
        proc = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, env={**ENV, **(extra_env or {})}, start_new_session=True)
        try:
            return proc.wait(timeout=max(60, timeout))
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            return "timeout"


def step(name, fn):
    try:
        result = fn()
        summary[name] = {"ok": True, "result": result}
        say(f"{name}: ok {json.dumps(result, default=str)[:1500]}")
    except Exception as exc:  # keep going: the Drive steps must still run
        summary[name] = {"ok": False, "error": repr(exc)[-1500:]}
        say(f"{name}: FAILED {exc!r}"[:2000])


def ensure_stopped():
    code, out = sh(f"pgrep -f '{PAT}'")
    if code == 0:
        replay = "/content/option-pro/research/breakout_radar/replay_v1/scripts/replay.py"
        if os.path.exists(replay):
            os.rename(replay, replay + ".stopped")
        sh(f"kill -TERM {' '.join(out.split())}")
        for _ in range(60):
            if sh(f"pgrep -f '{PAT}'")[0] != 0:
                break
            time.sleep(5)
        sh(f"pkill -KILL -f '{PAT}'")
        with open("/content/STOPPED", "a") as handle:
            handle.write(f"{datetime.now(timezone.utc):%H:%M:%SZ} stopped by post_stop ({len(out.split())} processes)\n")
    for _ in range(12):
        if os.path.exists("/content/STOPPED"):
            break
        time.sleep(5)
    sh("pkill -f 'bash /content/g4_[g]uard.sh'")
    return open("/content/STOPPED").read().strip() if os.path.exists("/content/STOPPED") else "no STOPPED file"


def stop_sync_loop():
    sh("pkill -f '[w]hile true; do rsync'")
    for _ in range(120):
        if sh("pgrep -f '[r]sync -a /content/replay/full/'")[0] != 0:
            break
        time.sleep(5)
    return sh("pgrep -fa '[r]sync'")[1] or "no rsync running"


def export(mode, deadline):
    log = f"/content/logs/export_{mode}.log"
    code = run_logged([PY, "/content/export_t1.py", mode], log, deadline - time.time(),
                      {"BUNDLE_DEADLINE": str(deadline - 600), "DELETE_AFTER": "1"})
    return {"exit": code, "tail": open(log).read()[-600:]}


def drive_sync():
    if not os.path.ismount("/content/drive"):
        raise RuntimeError("Drive is not mounted; nothing synced")
    results = {}
    for src, dst in (("/content/replay/full/", "full/"), ("/content/db_t1/", "db_t1/"), ("/content/eval/", "eval/"),
                     ("/content/logs/", "logs/"), ("/content/bundles_killed/", "bundles_killed/")):
        if not os.path.isdir(src):
            continue
        os.makedirs(f"{DRIVE}/{dst}", exist_ok=True)
        code, out = sh(f"rsync -a {src} {DRIVE}/{dst}", timeout=5400)
        _c, pending = sh(f"rsync -an --itemize-changes {src} {DRIVE}/{dst} | head -3", timeout=1800)
        results[src] = {"exit": code, "error": out[-300:], "pending": pending[:300]}
    if os.path.exists("/content/radar_eval_small.tar.gz"):
        sh(f"cp /content/radar_eval_small.tar.gz {DRIVE}/radar_eval_small.tar.gz")
    return results


def evaluate():
    cmd = [PY, f"{CODE}/research/breakout_radar/replay_v1/scripts/evaluate.py", "--db", "/content/data/replay.sqlite",
           "--replay", "/content/replay/full/seg_*", "--variants", ",".join(VARIANTS), "--baseline", "baseline",
           "--directory", "/content/data/massive_directory_2026-09-27", "--db-dir", "/content/db_t1/full",
           "--workers", "8", "--out", EVAL_OUT]
    code = run_logged(cmd, "/content/logs/eval.log", min(6000, utc(15, 50) - time.time()))
    return {"exit": code, "tail": open("/content/logs/eval.log").read()[-1500:]}


def completed_by_segment():
    _code, bad = sh("find /content/replay/full -name '*.jsonl.gz' -print0 | xargs -0 -P 16 -n 64 gzip -t 2>&1", timeout=3600)
    corrupt = {line.split(":")[1].strip() for line in bad.splitlines() if line.startswith("gzip:") and ":" in line[5:]}
    pack_path = f"{EVAL_OUT}/result_pack.json"
    evaluated = json.load(open(pack_path))["completed_days"] if os.path.exists(pack_path) else None
    segs = sorted(glob.glob("/content/replay/full/seg_*"))
    starts = [os.path.basename(s)[4:] for s in segs]
    out = {}
    for i, seg in enumerate(segs):
        lo, hi = starts[i], (starts[i + 1] if i + 1 < len(segs) else "9999")
        per_variant = []
        for variant in VARIANTS:
            files = glob.glob(f"{seg}/{variant}/ledger/*.jsonl.gz")
            per_variant.append({os.path.basename(f)[:10] for f in files if f not in corrupt and lo <= os.path.basename(f)[:10] < hi})
        days = sorted(set.intersection(*per_variant))
        row = {"start": lo, "days_all_variants": len(days), "first": days[0] if days else None, "last": days[-1] if days else None,
               "finished": os.path.exists(f"{seg}/run.json"), "days": days}
        if evaluated:
            row["evaluated"] = len([d for d in evaluated["days"] if lo <= d < hi])
            row["dropped_degraded"] = [d for d in evaluated["dropped_degraded"] if lo <= d < hi]
        out[os.path.basename(seg)] = row
    with open("/content/eval/completed_days_by_segment.json", "w") as handle:
        json.dump({"corrupt_files": sorted(corrupt), "segments": out}, handle, indent=1)
    return {"segments": len(out), "days_all_variants": sum(r["days_all_variants"] for r in out.values()), "corrupt_files": len(corrupt),
            "evaluated": None if not evaluated else len(evaluated["days"]), "per_year": None if not evaluated else evaluated["per_year"]}


def pack_small():
    names = [f"{EVAL_OUT}/result_pack.json", f"{EVAL_OUT}/README_tables.md", f"{EVAL_OUT}/decision.json",
             "/content/eval/completed_days_by_segment.json", "/content/logs/post_stop.log", "/content/logs/guard.log",
             "/content/logs/eval.log", "/content/logs/run_full.log", "/content/logs/export_t1.log", "/content/logs/export_t1.json",
             "/content/replay/full/segments_summary.json", "/content/STOPPED"]
    with tarfile.open("/content/radar_eval_small.tar.gz", "w:gz") as tar:
        for name in names:
            if os.path.exists(name):
                tar.add(name, arcname=name.replace("/content/", ""))
    return os.path.getsize("/content/radar_eval_small.tar.gz")


def unmount():
    from google.colab import drive
    drive.flush_and_unmount()
    return {"still_mounted": os.path.ismount("/content/drive")}


os.makedirs("/content/eval", exist_ok=True)
say("post_stop start")
step("ensure_stopped", ensure_stopped)
step("stop_sync_loop", stop_sync_loop)
step("export_t1", lambda: export("t1", utc(15, 30)))
step("drive_sync_1", drive_sync)
step("evaluate", evaluate)
step("completed_by_segment", completed_by_segment)
step("pack_small", pack_small)
step("drive_sync_2", drive_sync)
step("export_bundles", lambda: export("bundles", min(time.time() + 3 * 3600, utc(15, 45))))
step("drive_sync_3", drive_sync)
step("unmount", unmount)
summary["finished"] = datetime.now(timezone.utc).isoformat()
with open("/content/PIPELINE_DONE", "w") as handle:
    json.dump(summary, handle, indent=1, default=str)
say("DONE post_stop")
print(json.dumps({k: (v if not isinstance(v, dict) else {kk: str(vv)[:300] for kk, vv in v.items()}) for k, v in summary.items()}, default=str)[:4000])
