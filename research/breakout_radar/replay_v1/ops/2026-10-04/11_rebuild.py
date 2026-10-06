"""Round-2 setup fix (2026-10-04). The minute manifest's 21 SPY lines cover only 2022-02-08..2026-04-08; the dedicated
25-page SPY fetch (2021-10-04..2026-09-25, pack_manifest_spy.jsonl) was never appended because the 2026-09-28 setup
skipped it when SPY was already present. That left about 16% of the days without SPY bars (the round-1 benchmark
close fallback). Replace the SPY lines with the 25 pages, rebuild the store, drop minute_raw, archive the store."""
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

INPUTS = "/content/drive/MyDrive/option-pro-data/radar_replay_2026-09-29/inputs"
STORE_TAR = "/content/drive/MyDrive/option-pro-data/radar_replay_2026-10-04/minute_store.tar"
PACK = "/content/option-pro/research/breakout_radar/replay_v1"
started = time.time()


def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:6.0f}s {text}"
    print(line, flush=True)
    with open("/content/logs/setup.log", "a") as handle:
        handle.write(line + "\n")


def sha(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(cmd, **kwargs):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if out.returncode:
        say(f"FAILED: {cmd}\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
        raise SystemExit(out.returncode)
    return out.stdout


rows = [json.loads(line) for line in open("/content/minute_manifest.jsonl") if line.strip()]
spy = [json.loads(line) for line in open("/content/pack_manifest_spy.jsonl") if line.strip()]
kept = [row for row in rows if row.get("ticker") != "SPY"]
missing = [row["file"] for row in spy if not os.path.exists(f"/content/minute_raw/{row['file']}")]
if missing:
    raise SystemExit(f"SPY raw pages missing: {missing[:3]}")
if not os.path.exists("/content/minute_manifest.orig.jsonl"):
    shutil.copyfile("/content/minute_manifest.jsonl", "/content/minute_manifest.orig.jsonl")
with open("/content/minute_manifest.jsonl", "w") as handle:
    for row in kept + spy:
        handle.write(json.dumps(row) + "\n")
say(f"manifest: {len(rows) - len(kept)} SPY lines replaced by the {len(spy)} dedicated pages "
    f"({min(r['from'] for r in spy)}..{max(r['to'] for r in spy)}); {len(kept) + len(spy)} lines")
shutil.copyfile("/content/minute_manifest.jsonl", f"{INPUTS}/minute_manifest_spyfix.jsonl")
Path(f"{INPUTS}/minute_manifest_spyfix.jsonl.sha256").write_text(f"{sha('/content/minute_manifest.jsonl')}  minute_manifest_spyfix.jsonl\n")
say("fixed manifest saved to Drive inputs/minute_manifest_spyfix.jsonl")

env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
shutil.rmtree("/content/minute_store", ignore_errors=True)
run(f"/content/venv/bin/python {PACK}/scripts/build_minute_store.py --manifest /content/minute_manifest.jsonl "
    f"--raw-root /content/minute_raw --out /content/minute_store --chunk-months 3", env=env)
say("minute store rebuilt: " + run("du -sh /content/minute_store").strip())
shutil.rmtree("/content/minute_raw")
say("minute_raw removed; disk: " + run("df -h /content | tail -1").strip())
os.makedirs(os.path.dirname(STORE_TAR), exist_ok=True)
run(f"tar -cf {STORE_TAR}.partial -C /content minute_store")
digest = sha(f"{STORE_TAR}.partial")
os.replace(f"{STORE_TAR}.partial", STORE_TAR)
Path(STORE_TAR + ".sha256").write_text(f"{digest}  minute_store.tar\n")
say(f"minute store archived to Drive (sha {digest[:16]}...)")
say("DONE 11_rebuild")
