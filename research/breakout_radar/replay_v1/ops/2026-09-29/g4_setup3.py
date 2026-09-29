"""G4 setup, step 3: code 3aaa04d1, SPY pages into the full store, rebuild it, then run RUN_SPEC 1c."""
import hashlib, json, os, shutil, subprocess, tarfile, time
started = time.time()
def say(text):
    line = f"{time.strftime('%H:%M:%S')} +{time.time() - started:6.0f}s {text}"
    print(line, flush=True)
    with open("/content/logs/setup.log", "a") as handle:
        handle.write(line + "\n")
def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()
def run(cmd, **kwargs):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, **kwargs)
    if out.returncode:
        say(f"FAILED: {cmd}\n{out.stdout[-3000:]}\n{out.stderr[-3000:]}")
        raise SystemExit(out.returncode)
    return out.stdout
assert sha("/content/upload/radar_code2.tar.gz") == "339744fe2576dba5207825276ebd748e139bad14eac1f2b8d34fb7da19b1ca19"
assert sha("/content/upload/minute_spy.tar") == "c2346525102ac0ec4e93f465d715af4818b81e68075bd83d960b40ab4192a9b0"
shutil.rmtree("/content/option-pro")
os.makedirs("/content/option-pro")
run("tar -xzf /content/upload/radar_code2.tar.gz -C /content/option-pro")
shutil.copyfile("/content/upload/pit_shares.jsonl.gz", "/content/data/pit_shares.jsonl.gz")
with tarfile.open("/content/upload/minute_spy.tar") as archive:
    archive.extractall("/content", filter="data")
spy_lines = open("/content/pack_manifest_spy.jsonl").read()
manifest = open("/content/minute_manifest.jsonl").read()
if '"ticker": "SPY"' not in manifest:
    with open("/content/minute_manifest.jsonl", "a") as handle:
        handle.write(spy_lines)
say(f"code 3aaa04d1 in place; SPY pages added ({spy_lines.count(chr(10))} lines)")
shutil.rmtree("/content/minute_store", ignore_errors=True)
env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/content/option-pro/backend"}
run("/content/venv/bin/python /content/option-pro/research/breakout_radar/replay_v1/scripts/build_minute_store.py "
    "--manifest /content/minute_manifest.jsonl --raw-root /content/minute_raw --out /content/minute_store --chunk-months 3", env=env)
say("full minute store built: " + run("du -sh /content/minute_store; ls /content/minute_store/SPY.parquet; df -h /content | tail -1").replace("\n", " "))
exec(open("/content/smoke_1c.py").read())
say("DONE g4_setup3")
