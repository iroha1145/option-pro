"""Round-2 G4 setup (2026-10-04), run inside the kernel: code 617032d8, Python env, replay cache,
minute store (with SPY) from the Drive archives, round-1 inputs. Same sources and checks as the
2026-09-28 setup (ops/2026-09-29/g4_setup.py, g4_setup3.py); minute_raw is removed after the build."""
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
INPUTS = f"{DRIVE}/radar_replay_2026-09-29/inputs"
CODE_SHA = "b7530d866b87fe23758bdda6d28c7609049b8c289ec0a4354807a01a10476797"
BUNDLE_SHA = "9e20ac818ee4af6e77a4679d910536cf333461dba539d7652d325e61888e2ef4"
REPO = "/content/option-pro"
PACK = f"{REPO}/research/breakout_radar/replay_v1"
started = time.time()
os.makedirs("/content/logs", exist_ok=True)


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


if not os.path.ismount("/content/drive"):
    raise SystemExit("Drive is not mounted")
if sha("/content/upload/radar_code4.tar.gz") != CODE_SHA:
    raise SystemExit("code tarball sha mismatch")
shutil.rmtree(REPO, ignore_errors=True)
os.makedirs(REPO)
run(f"tar -xzf /content/upload/radar_code4.tar.gz -C {REPO}")
say("code 617032d8 unpacked (sha ok)")

run("uv venv -q --python 3.12.13 /content/venv")
run(f"uv pip install -q --python /content/venv/bin/python --require-hashes -r {REPO}/backend/requirements-ci.txt")
run("uv pip install -q --python /content/venv/bin/python pyarrow==25.0.1")
say("python env ready: " + run("/content/venv/bin/python -c 'import sys, pyarrow, pandas; print(sys.version.split()[0], pyarrow.__version__, pandas.__version__)'").strip())

shutil.copyfile(f"{ARC}/replay_bundle.tar.gz", "/content/replay_bundle.tar.gz")
if sha("/content/replay_bundle.tar.gz") != BUNDLE_SHA:
    raise SystemExit("replay bundle sha mismatch")
run("tar -xzf /content/replay_bundle.tar.gz -C /content && rm /content/replay_bundle.tar.gz")
say("replay cache restored (sha ok): " + run("ls /content/data").replace("\n", " "))

sums = {}
for line in Path(f"{INPUTS}/SHA256SUMS").read_text().splitlines():
    digest, name = line.split(maxsplit=1)
    sums[name.strip().lstrip("./")] = digest
os.makedirs("/content/data/fred", exist_ok=True)
for name, target in (("pit_shares.jsonl.gz", "/content/data/pit_shares.jsonl.gz"),
                     ("minute_manifest.jsonl", "/content/minute_manifest.jsonl"),
                     ("minute_spy.tar", "/content/minute_spy.tar"),
                     ("fred/VIXCLS.csv", "/content/data/fred/VIXCLS.csv"),
                     ("fred/DGS10.csv", "/content/data/fred/DGS10.csv")):
    shutil.copyfile(f"{INPUTS}/{name}", target)
    if sha(target) != sums[name]:
        raise SystemExit(f"{name} sha mismatch")
say("inputs placed (sha ok against inputs/SHA256SUMS)")

STORE_TAR = f"{DRIVE}/radar_replay_2026-10-04/minute_store.tar"
restored = False
if os.path.exists(STORE_TAR) and os.path.exists(STORE_TAR + ".sha256"):
    shutil.copyfile(STORE_TAR, "/content/minute_store.tar")
    if sha("/content/minute_store.tar") == Path(STORE_TAR + ".sha256").read_text().split()[0]:
        run("tar -xf /content/minute_store.tar -C /content")
        restored = True
        say("minute store restored from Drive (sha ok): " + run("du -sh /content/minute_store").strip())
    else:
        say("minute store archive sha mismatch; building from the raw pages")
    os.remove("/content/minute_store.tar")
if not restored:
    # The full manifest also lists the smoke pages (2026-09-28 setup, g4_setup2.py), so minute_smoke is needed too.
    for name in ("minute_smoke", "minute_2026", "minute_2025", "minute_2024", "minute_2023", "minute_2022", "minute_2021"):
        recorded = Path(f"{MIN}/{name}.tar.sha256").read_text().split()[0]
        shutil.copyfile(f"{MIN}/{name}.tar", f"/content/{name}.tar")
        if sha(f"/content/{name}.tar") != recorded:
            raise SystemExit(f"{name}.tar sha mismatch")
        with tarfile.open(f"/content/{name}.tar") as archive:
            archive.extractall("/content", filter="data")
        os.remove(f"/content/{name}.tar")
        say(f"{name}: sha ok, extracted")
    with tarfile.open("/content/minute_spy.tar") as archive:
        archive.extractall("/content", filter="data")
    os.remove("/content/minute_spy.tar")
    spy_lines = sum(1 for line in open("/content/minute_manifest.jsonl") if '"SPY"' in line)
    say(f"SPY pages extracted; manifest lines naming SPY: {spy_lines}")

    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": f"{REPO}/backend"}
    run(f"/content/venv/bin/python {PACK}/scripts/build_minute_store.py --manifest /content/minute_manifest.jsonl "
        f"--raw-root /content/minute_raw --out /content/minute_store --chunk-months 3", env=env)
    say("minute store built: " + run("du -sh /content/minute_store; ls -la /content/minute_store | head -5").replace("\n", " "))
    shutil.rmtree("/content/minute_raw")
    say("minute_raw removed; disk: " + run("df -h /content | tail -1").strip())
    if os.environ.get("RADAR_ARCHIVE_STORE") == "1":
        os.makedirs(os.path.dirname(STORE_TAR), exist_ok=True)
        run(f"tar -cf {STORE_TAR}.partial -C /content minute_store")
        digest = sha(f"{STORE_TAR}.partial")
        os.replace(f"{STORE_TAR}.partial", STORE_TAR)
        Path(STORE_TAR + ".sha256").write_text(f"{digest}  minute_store.tar\n")
        say(f"minute store archived to Drive (sha {digest[:16]}...)")
say("DONE g4c_setup")
