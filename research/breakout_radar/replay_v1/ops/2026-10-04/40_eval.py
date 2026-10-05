"""Round-2 final evaluation (2026-10-05), run as a queue step on the machine that finishes last.

Code 987c2894 (evaluation with the 修订 6 SPY and entry backfills) goes to /content/option-pro-eval. Inputs:
round-1 ledgers from Drive plus the round-1 bundles exported for killed segments (copied into each segment's
variant folder), this machine's continuation, and the other machine's continuation from Drive. The 5 round-1
days that not every variant finished (修订 5 appendix) were re-run whole by the continuation; their partial
round-1 files are moved aside so the evaluator's first-directory rule cannot mix the two runs for one day.
"""
import glob
import json
import os
import shutil
import subprocess
import tarfile
import time

OTHER = int(os.environ["RADAR_OTHER_MACHINE"])
DRIVE = "/content/drive/MyDrive/option-pro-data"
ROUND1 = f"{DRIVE}/radar_replay_2026-09-29"
ROUND2 = f"{DRIVE}/radar_replay_2026-10-04"
CODE = "/content/option-pro-eval"
PY = "/content/venv/bin/python"
ENV = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{CODE}/backend"}
DROPPED = ("2021-11-03", "2021-12-02", "2022-09-12", "2024-11-01", "2025-02-07")
VARIANTS = "baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15"
started = time.time()


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:6.0f}s {text}"
    print(line, flush=True)
    with open("/content/logs/eval_final.log", "a") as handle:
        handle.write(line + "\n")


def sh(cmd, timeout=None):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    if out.returncode:
        raise RuntimeError(f"{cmd}: {out.stdout[-1500:]} {out.stderr[-1500:]}")
    return out.stdout


shutil.rmtree(CODE, ignore_errors=True)
os.makedirs(CODE)
with tarfile.open("/content/upload/radar_code5.tar.gz") as archive:
    archive.extractall(CODE, filter="data")
say("evaluation code 987c2894 in place")

os.makedirs("/content/replay/full", exist_ok=True)
sh(f"rsync -a {ROUND1}/full/ /content/replay/full/", timeout=7200)
copied = 0
for bundle in glob.glob(f"{ROUND1}/bundles_killed/full/seg_*/*/research_bundle.json.gz"):
    seg, variant = bundle.split("/")[-3], bundle.split("/")[-2]
    target = f"/content/replay/full/{seg}/{variant}/research_bundle.json.gz"
    if not os.path.exists(target):
        shutil.copyfile(bundle, target)
        copied += 1
say(f"round-1 ledgers restored; {copied} killed-segment bundles placed")

moved = 0
os.makedirs("/content/replay/round1_partial_days", exist_ok=True)
for day in DROPPED:
    for path in glob.glob(f"/content/replay/full/seg_*/*/ledger/{day}.jsonl.gz"):
        parts = path.split("/")
        target = f"/content/replay/round1_partial_days/{parts[-4]}_{parts[-3]}_{day}.jsonl.gz"
        shutil.move(path, target)
        moved += 1
say(f"moved aside {moved} partial round-1 day files for {len(DROPPED)} days")

sh(f"rsync -a {ROUND2}/cont_{OTHER}/cont/ /content/replay/cont/", timeout=7200)
segs = sorted(glob.glob("/content/replay/cont/seg_*"))
finished = sum(os.path.exists(f"{s}/run.json") for s in segs)
say(f"continuation outputs: {len(segs)} sub-segments here after merging machine {OTHER} ({finished} with run.json)")

with open("/content/logs/eval_full_all.log", "w") as handle:
    code = subprocess.run(
        [PY, f"{CODE}/research/breakout_radar/replay_v1/scripts/evaluate.py", "--db", "/content/data/replay.sqlite",
         "--replay", "/content/replay/full/seg_*", "--replay", "/content/replay/cont/seg_*",
         "--variants", VARIANTS, "--baseline", "baseline", "--directory", "/content/data/massive_directory_2026-09-27",
         "--minute-store", "/content/minute_store", "--workers", "8", "--out", "/content/eval/full_all"],
        env=ENV, stdout=handle, stderr=subprocess.STDOUT).returncode
say(f"evaluate exit {code}: " + open("/content/logs/eval_full_all.log").read()[-1500:])

names = ["/content/eval/full_all/result_pack.json", "/content/eval/full_all/README_tables.md",
         "/content/eval/full_all/decision.json", "/content/logs/eval_final.log", "/content/logs/eval_full_all.log"]
with tarfile.open("/content/radar_eval_round2.tar.gz", "w:gz") as tar:
    for name in names:
        if os.path.exists(name):
            tar.add(name, arcname=name.replace("/content/", ""))
os.makedirs(f"{ROUND2}/eval", exist_ok=True)
sh(f"rsync -a /content/eval/ {ROUND2}/eval/ && cp /content/radar_eval_round2.tar.gz {ROUND2}/eval/", timeout=3600)
say("evaluation synced to Drive radar_replay_2026-10-04/eval/")
open("/content/EVAL_DONE", "w").write(time.strftime("%H:%M:%SZ") + f" evaluate exit {code}\n")
say("DONE 40_eval")
