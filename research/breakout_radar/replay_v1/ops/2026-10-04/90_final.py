"""Round-2 wrap-up on one G4 (2026-10-05): stop the hourly sync, rsync the continuation outputs and logs to Drive,
check nothing is pending (an itemized dry run that lists only content changes), count files on both sides, then
flush and unmount Drive so every upload has landed before the machine is stopped."""
import os
import subprocess
import time

M = int(os.environ["RADAR_MACHINE"])
DRIVE_OUT = f"/content/drive/MyDrive/option-pro-data/radar_replay_2026-10-04/cont_{M}"


def sh(cmd):
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return out.returncode, (out.stdout + out.stderr).strip()


def log(text):
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    with open("/content/logs/final.log", "a") as handle:
        handle.write(line + "\n")


sh("pkill -f 'while true; do sleep 3600; [r]sync'")
for _ in range(120):
    if sh("pgrep -f '[r]sync -a /content/replay/cont/'")[0] != 0:
        break
    time.sleep(5)
for src, dst in (("/content/replay/cont/", "cont/"), ("/content/logs/", "logs/")):
    code, out = sh(f"rsync -a {src} {DRIVE_OUT}/{dst}")
    log(f"rsync {src}: exit {code} {out[-200:]}")
code, pending = sh(f"rsync -an --itemize-changes /content/replay/cont/ {DRIVE_OUT}/cont/ | grep -v '^[.]d' | grep -v '^[.]f[.][.][.]p' | head -5")
local = sh("find /content/replay/cont -type f | wc -l")[1]
remote = sh(f"find {DRIVE_OUT}/cont -type f | wc -l")[1]
log(f"pending content changes: {pending or 'none'}; files local {local}, Drive {remote}")
from google.colab import drive  # noqa: E402

drive.flush_and_unmount()
log(f"Drive flushed and unmounted; still mounted: {os.path.ismount('/content/drive')}")
open("/content/FINAL_DONE", "w").write(f"files local {local} Drive {remote}; pending {pending or 'none'}\n")
log("DONE 90_final")
