"""Disk-aware throttle for the round-2 continuation (2026-10-04); replaces cont_guard_<n>.sh.

In 2025-2026 a sub-segment's replay databases grow about 0.7-1 GB per day (round 1 averaged 0.28 over all years),
so 40 recent sub-segments in parallel would need about 240 GB at their peak against about 150 GB free. A paused
process keeps its work, and a finished sub-segment's databases are deleted (run_segments --delete-db-after),
which frees space. So: when free space falls below 20 GB, pause (SIGSTOP) the running replay with the most days
left; when it rises above 40 GB, resume (SIGCONT) the paused one with the fewest days left; keep at least 6
running. Below 8 GB keep only the 2 closest to finishing. The old guard's session-age stops stay: STOP files at
21 h (paused processes resumed so they can finish the day), SIGTERM at 23 h.
"""
import glob
import os
import signal
import time
from datetime import date, timedelta

MACHINE = int(os.environ["RADAR_MACHINE"])
SESSION_START = float(os.environ["RADAR_SESSION_START"])
LOG = f"/content/logs/throttle_{MACHINE}.log"
LOW, HIGH, EMERGENCY = 20e9, 40e9, 8e9
MIN_RUNNING, EMERGENCY_RUNNING = 6, 2


def log(text):
    with open(LOG, "a") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def free_bytes():
    stat = os.statvfs("/content")
    return stat.f_bavail * stat.f_frsize


def weekdays(start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    return sum(1 for n in range((last - first).days + 1) if (first + timedelta(n)).weekday() < 5)


def replays():
    found = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            argv = [part.decode(errors="replace") for part in open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")]
            state = open(f"/proc/{pid}/stat").read().rsplit(") ", 1)[1].split()[0]
        except OSError:
            continue
        if not any(arg.endswith("replay_v1/scripts/replay.py") for arg in argv):
            continue
        try:
            out, start, end = (argv[argv.index(flag) + 1] for flag in ("--out", "--start", "--end"))
        except ValueError:
            continue
        done = len(glob.glob(f"{out}/baseline/ledger/*.jsonl.gz"))
        # +1 for the warm-up day, whose ledger file is written too
        found.append({"pid": int(pid), "out": out, "paused": state == "T", "left": weekdays(start, end) + 1 - done})
    return found


def signal_all(rows, sig):
    for row in rows:
        try:
            os.kill(row["pid"], sig)
        except ProcessLookupError:
            pass


if __name__ == "__main__":
    import sys
    if "--once" in sys.argv:  # dry run: what the throttle sees, no signals
        rows = replays()
        print(f"free {free_bytes() / 1e9:.1f} GB, replays {len(rows)}, paused {sum(r['paused'] for r in rows)}, "
              f"days left min/median/max {sorted(r['left'] for r in rows)[0]}/{sorted(r['left'] for r in rows)[len(rows) // 2]}/"
              f"{sorted(r['left'] for r in rows)[-1]}" if rows else "no replays")
        raise SystemExit
    log(f"throttle started (machine {MACHINE})")
    tick = 0
    while True:
        age = time.time() - SESSION_START
        rows = replays()
        running = sorted((r for r in rows if not r["paused"]), key=lambda r: r["left"])
        paused = sorted((r for r in rows if r["paused"]), key=lambda r: r["left"])
        free = free_bytes()
        if age >= 23 * 3600:
            signal_all(paused, signal.SIGCONT)
            signal_all(rows, signal.SIGTERM)
            log(f"SIGTERM {len(rows)} processes (age {age / 3600:.1f} h)")
        elif age >= 21 * 3600:
            for folder in glob.glob("/content/replay/cont/seg_*"):
                if not os.path.exists(f"{folder}/run.json"):
                    open(f"{folder}/STOP", "a").close()
            if paused:
                signal_all(paused, signal.SIGCONT)
            if tick % 10 == 0:
                log(f"STOP files touched, {len(paused)} resumed (age {age / 3600:.1f} h)")
        elif free < EMERGENCY and len(running) > EMERGENCY_RUNNING:
            victims = running[EMERGENCY_RUNNING:]
            signal_all(victims, signal.SIGSTOP)
            log(f"EMERGENCY free {free / 1e9:.1f} GB: paused {len(victims)}, {EMERGENCY_RUNNING} left running")
        elif free < LOW and len(running) > MIN_RUNNING:
            victim = running[-1]
            signal_all([victim], signal.SIGSTOP)
            log(f"paused pid {victim['pid']} ({os.path.basename(victim['out'])}, {victim['left']} days left); free {free / 1e9:.1f} GB")
        elif free > HIGH and paused:
            lucky = paused[0]
            signal_all([lucky], signal.SIGCONT)
            log(f"resumed pid {lucky['pid']} ({os.path.basename(lucky['out'])}, {lucky['left']} days left); free {free / 1e9:.1f} GB")
        if tick % 10 == 0:
            log(f"free {free / 1e9:.0f} GB, running {len(running)}, paused {len(paused)}, age {age / 3600:.1f} h")
        tick += 1
        time.sleep(60)
