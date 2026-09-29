"""Continue the G4 setup: unpack the smoke pages next to the main pages, then build both minute stores."""
import os, subprocess, tarfile, time
started = time.time()
def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:6.0f}s {text}"
    print(line, flush=True)
    with open("/content/logs/setup.log", "a") as handle:
        handle.write(line + "\n")
def run(cmd, **kwargs):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if out.returncode:
        say(f"FAILED: {cmd}\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
        raise SystemExit(out.returncode)
    return out.stdout
with tarfile.open("/content/data/smoke_minute.tar") as archive:
    archive.extractall("/content", filter="data")
say("smoke pages unpacked: " + run("ls /content/pack_manifest_smoke.jsonl && ls /content/minute_raw/2026-09 | grep -c _2026-08-03_2026-09-25_").replace("\n", " "))
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
P = "/content/option-pro/research/breakout_radar/replay_v1"
run(f"/content/venv/bin/python {P}/scripts/build_minute_store.py --manifest /content/pack_manifest_smoke.jsonl "
    f"--raw-root /content/minute_raw --out /content/minute_store_smoke", env=env)
say("smoke minute store built")
run(f"/content/venv/bin/python {P}/scripts/build_minute_store.py --manifest /content/minute_manifest.jsonl "
    f"--raw-root /content/minute_raw --out /content/minute_store --chunk-months 3", env=env)
say("full minute store built: " + run("du -sh /content/minute_store /content/minute_raw; df -h /content | tail -1").replace("\n", " "))
say("DONE g4_setup2")
