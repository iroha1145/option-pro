"""Kernel-side agent (2026-10-04): runs /content/queue/*.py one at a time in name order inside this kernel and
waits for more. A failing step is renamed *.failed and the loop goes on; /content/queue/EXIT ends it. The kernel
stays busy (Colab reclaims a G4 left without activity), and new steps can be added by upload."""
import glob
import os
import time
import traceback

os.makedirs("/content/queue", exist_ok=True)
os.makedirs("/content/logs", exist_ok=True)


def agent_log(text):
    line = f"{time.strftime('%H:%M:%S')} {text}"
    print(line, flush=True)
    with open("/content/logs/agent.log", "a") as handle:
        handle.write(line + "\n")


agent_log("agent started")
while not os.path.exists("/content/queue/EXIT"):
    todo = sorted(glob.glob("/content/queue/*.py"))
    if not todo:
        time.sleep(15)
        continue
    path = todo[0]
    running = path + ".running"
    os.rename(path, running)
    agent_log(f"run {os.path.basename(path)}")
    try:
        exec(compile(open(running).read(), running, "exec"), {"__name__": "__main__"})
        os.rename(running, path + ".done")
        agent_log(f"done {os.path.basename(path)}")
    except BaseException as exc:  # a step's SystemExit included: log it and keep the kernel busy
        os.rename(running, path + ".failed")
        agent_log(f"FAILED {os.path.basename(path)}: {exc!r}\n{traceback.format_exc()[-2000:]}")
agent_log("agent exit")
