"""G4 setup for the radar replay, run inside the kernel (a detached process lets Colab reclaim the machine)."""
import gzip
import hashlib
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path

DRIVE = "/content/drive/MyDrive/option-pro-data"
ARC = f"{DRIVE}/replay_archive_2026-09-28"
MIN = f"{DRIVE}/massive_minute_frozen_2026-09-28"
UP = "/content/upload"
REPO = "/content/option-pro"
PACK = f"{REPO}/research/breakout_radar/replay_v1"
EXPECTED = {
    f"{UP}/radar_code.tar.gz": "88c21086649ffdacf430ae12f38a699e7914c40370c7998a0ff954bded811cab",
    f"{UP}/replay_smoke_subset.sqlite.gz": "ab508c581606991bb881d5eb12f65ee477e0555410cb169c04ebae3ee93b62af",
    f"{UP}/radar_export_2026-09-08_2026-09-25.jsonl.gz": "2082450502dbe26965dd63d755cc246853cad2c39b11558f344ed961177c4112",
}
PIT_RAW_SHA = "55073eda699d1804ee6b9283c356e30286f6cf962edb803a1e5d0debf8b799af"
BUNDLE_SHA = "9e20ac818ee4af6e77a4679d910536cf333461dba539d7652d325e61888e2ef4"
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


for path, digest in EXPECTED.items():
    if sha(path) != digest:
        raise SystemExit(f"upload sha mismatch: {path}")
os.makedirs(REPO, exist_ok=True)
run(f"tar -xzf {UP}/radar_code.tar.gz -C {REPO}")
say("code unpacked; uploads verified")

run("uv venv -q --python 3.12.13 /content/venv")
run(f"uv pip install -q --python /content/venv/bin/python --require-hashes -r {REPO}/backend/requirements-ci.txt")
run("uv pip install -q --python /content/venv/bin/python pyarrow==25.0.1")
say("python env ready: " + run("/content/venv/bin/python -c 'import sys, pyarrow, pandas; print(sys.version.split()[0], pyarrow.__version__, pandas.__version__)'").strip())

os.makedirs("/content/data/fred", exist_ok=True)
shutil.copyfile(f"{ARC}/replay_bundle.tar.gz", "/content/replay_bundle.tar.gz")
if sha("/content/replay_bundle.tar.gz") != BUNDLE_SHA:
    raise SystemExit("replay bundle sha mismatch")
run("tar -xzf /content/replay_bundle.tar.gz -C /content && rm /content/replay_bundle.tar.gz")
say("replay cache restored (sha ok): " + run("ls /content/data").replace("\n", " "))

for name in ("minute_smoke", "minute_2026", "minute_2025", "minute_2024", "minute_2023", "minute_2022", "minute_2021"):
    recorded = Path(f"{MIN}/{name}.tar.sha256").read_text().split()[0]
    shutil.copyfile(f"{MIN}/{name}.tar", f"/content/{name}.tar")
    if sha(f"/content/{name}.tar") != recorded:
        raise SystemExit(f"{name}.tar sha mismatch")
    if name == "minute_smoke":
        shutil.move(f"/content/{name}.tar", "/content/data/smoke_minute.tar")
    else:
        with tarfile.open(f"/content/{name}.tar") as archive:
            archive.extractall("/content", filter="data")
        os.remove(f"/content/{name}.tar")
    say(f"{name}: sha ok")
shutil.copyfile(f"{MIN}/minute_manifest.jsonl", "/content/minute_manifest.jsonl")

with gzip.open(f"{UP}/pit_shares.jsonl.gz", "rb") as src, open("/content/data/pit_shares.jsonl", "wb") as dst:
    shutil.copyfileobj(src, dst)
if sha("/content/data/pit_shares.jsonl") != PIT_RAW_SHA:
    raise SystemExit("pit shares sha mismatch")
with gzip.open(f"{UP}/replay_smoke_subset.sqlite.gz", "rb") as src, open("/content/data/replay_smoke_subset.sqlite", "wb") as dst:
    shutil.copyfileobj(src, dst)
shutil.copyfile(f"{UP}/radar_export_2026-09-08_2026-09-25.jsonl.gz", "/content/data/radar_export_2026-09-08_2026-09-25.jsonl.gz")
for name in ("VIXCLS.csv", "DGS10.csv"):
    shutil.copyfile(f"{UP}/fred/{name}", f"/content/data/fred/{name}")
say("local data placed (pit shares sha ok)")

env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{REPO}/backend"}
run(f"/content/venv/bin/python {PACK}/scripts/build_minute_store.py --tar /content/data/smoke_minute.tar --out /content/minute_store_smoke", env=env)
say("smoke minute store built")
run(f"/content/venv/bin/python {PACK}/scripts/build_minute_store.py --manifest /content/minute_manifest.jsonl "
    f"--raw-root /content/minute_raw --out /content/minute_store --chunk-months 3", env=env)
say("full minute store built: " + run("du -sh /content/minute_store /content/minute_raw").replace("\n", " "))
say("DONE g4_setup")
