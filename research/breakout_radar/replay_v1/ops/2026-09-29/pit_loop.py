"""Point-in-time shares rounds (RUN_SPEC section 2), run inside the kernel so the machine is not reclaimed.

Each round: pit_shares_requests.py writes (ticker, date) rows; they are fetched from
/v3/reference/tickers/{T}?date=D (key in the Authorization header only) and appended to
/content/data/pit_shares.jsonl as {"ticker", "date", "http", "status", "results"}. Repeat until a round is empty.
"""
import csv
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

SCRIPT = "/content/radar/pit_shares_requests.py"
SAMPLES = "/content/data/pit_shares.jsonl"
LOG = "/content/logs/pit.log"
os.makedirs("/content/logs", exist_ok=True)
os.chmod("/content/.massive.env", 0o600)
token = next(line.split("=", 1)[1].strip().strip('"').strip("'") for line in open("/content/.massive.env")
             if line.startswith("MASSIVE_API_KEY="))


def log(text):
    with open(LOG, "a") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")
    print(text, flush=True)


def get(ticker, day):
    url = f"https://api.massive.com/v3/reference/tickers/{urllib.parse.quote(ticker, safe='.')}?date={day}"
    delay = 1.0
    for attempt in range(7):
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code in (429, 500, 502, 503, 504) and attempt < 6:
                time.sleep(delay)
                delay *= 2
                continue
            try:
                return exc.code, json.loads(body)
            except ValueError:
                return exc.code, {}
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError):
            if attempt < 6:
                time.sleep(delay)
                delay *= 2
                continue
            return 0, {}
    return 0, {}


lock = threading.Lock()
for round_no in range(1, 30):
    out = f"/content/data/pit_round{round_no}.csv"
    args = ["python3", SCRIPT, "--db", "/content/data/replay.sqlite", "--directory", "/content/data/massive_directory_2026-09-27",
            "--start", "2021-10-04", "--end", "2026-09-25", "--out", out]
    if os.path.exists(SAMPLES):
        args += ["--samples", SAMPLES]
    subprocess.run(args, check=True, capture_output=True)
    rows = list(csv.DictReader(open(out)))
    log(f"round {round_no}: {len(rows)} requests")
    if not rows:
        break
    started, errors = time.time(), 0

    def one(row):
        global errors
        status, payload = get(row["ticker"], row["date"])
        results = payload.get("results") if isinstance(payload, dict) else None
        record = {"ticker": row["ticker"], "date": row["date"], "reason": row.get("reason"), "http": status,
                  "status": payload.get("status") if isinstance(payload, dict) else None,
                  "results": results if isinstance(results, dict) else {}}
        with lock:
            with open(SAMPLES, "a") as handle:
                handle.write(json.dumps(record) + "\n")
            if status != 200:
                errors += 1

    with ThreadPoolExecutor(max_workers=24) as pool:
        list(pool.map(one, rows))
    log(f"round {round_no}: done in {time.time() - started:.0f}s, non-200 {errors}")
lines = sum(1 for _ in open(SAMPLES))
log(f"DONE pit rounds; {lines} samples in {SAMPLES}")
