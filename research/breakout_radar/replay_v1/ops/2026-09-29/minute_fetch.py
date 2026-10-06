"""Freeze Massive 5-minute bars for the radar replay (research/breakout_radar/replay_v1/DATA_SPEC.md 16).

  python minute_fetch.py plan    # writes /content/minute_plan_smoke.jsonl and /content/minute_plan_main.jsonl
  python minute_fetch.py fetch <plan.jsonl> [threads]

Pages: one request per ticker and page of at most 52 sessions (5-minute bars, extended hours, adjusted=false).
The main plan covers, per ticker, the whole span from 20 sessions before its first needed day to its last needed
day under the rule A1 relaxed ∪ A3n on eligible point-in-time directory types (census v2), newest pages first.
Raw responses are stored gzip-compressed as received, one file per request, with a JSONL manifest (sha256 of the
raw bytes). The API key is read from /content/.massive.env and only sent in the Authorization header.
"""
import bisect
import gzip
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://api.massive.com"
ROOT = Path("/content/minute_raw")
MANIFEST = Path("/content/minute_manifest.jsonl")
LOG = Path("/content/logs/minute_fetch.log")
MINUTE_START = "2021-10-04"
LOOKBACK = 20
PAGE_DAYS = 52
KEEP_TYPES = {"CS", "OS", "ADRC", "ADRP", "GDR", "ETF", "ETV", "ETS"}
ETF_TYPES = {"ETF", "ETV", "ETS"}
SMOKE_FROM, SMOKE_TO = "2026-08-03", "2026-09-25"


def log(text):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        handle.write(f"{time.strftime('%H:%M:%S')} {text}\n")


def plan():
    spec = importlib.util.spec_from_file_location("asset_policy", "/content/asset_policy.py")
    asset_policy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(asset_policy)
    directory = Path("/content/data/massive_directory_2026-09-27")
    labels = sorted(path.name[:-8] for path in directory.glob("20*.json.gz"))
    snapshots = {}
    for label in labels:
        rows = json.loads(gzip.open(directory / f"{label}.json.gz").read())["results"]
        snapshots[label] = {row["ticker"]: (row.get("type"), row.get("name") or "") for row in rows if row.get("ticker")}

    def eligible(ticker, day):
        i = bisect.bisect_right(labels, day) - 1
        found = snapshots[labels[i]].get(ticker) if i >= 0 else None
        if found is None or found[0] not in KEEP_TYPES:
            return False
        return not (found[0] in ETF_TYPES and asset_policy.is_leveraged_etf(found[0], found[1]))

    conn = sqlite3.connect("/content/census.sqlite")
    conn.execute("ATTACH 'file:/content/data/replay.sqlite?mode=ro' AS src")
    sessions = [row[0] for row in conn.execute(
        "SELECT session_date FROM src.market_sessions WHERE session_date >= ? ORDER BY session_date", (MINUTE_START,))]
    index = {day: i for i, day in enumerate(sessions)}
    base = "n_prior = 10 AND prev_close_adj > 0 AND high >= 2.0"
    where = (f"{base} AND ((high >= 1.025 * prev_close_adj AND volume >= 1.4 * avg_vol_10_adj) OR "
             f"((open >= 1.02 * prev_close_adj OR high >= 1.05 * prev_close_adj) AND "
             f"(adv_20 >= 10000000 OR open >= 1.05 * prev_close_adj)))")
    lo, hi = {}, {}
    for ticker, day in conn.execute(f"SELECT ticker, session_date FROM feat WHERE session_date >= ? AND {where}", (MINUTE_START,)):
        if eligible(ticker, day):
            i = index[day]
            lo[ticker] = min(lo.get(ticker, i), i)
            hi[ticker] = max(hi.get(ticker, i), i)
    pages = []
    for ticker in lo:
        start = max(0, lo[ticker] - LOOKBACK)
        for page_start in range(start, hi[ticker] + 1, PAGE_DAYS):
            page_end = min(page_start + PAGE_DAYS - 1, hi[ticker])
            pages.append({"ticker": ticker, "from": sessions[page_start], "to": sessions[page_end]})
    pages.sort(key=lambda page: (page["to"], page["ticker"]), reverse=True)
    with open("/content/minute_plan_main.jsonl", "w") as handle:
        for page in pages:
            handle.write(json.dumps(page) + "\n")
    smoke = json.load(open("/content/smoke_tickers.json"))
    with open("/content/minute_plan_smoke.jsonl", "w") as handle:
        for ticker in smoke:
            handle.write(json.dumps({"ticker": ticker, "from": SMOKE_FROM, "to": SMOKE_TO}) + "\n")
    print(json.dumps({"main_pages": len(pages), "main_tickers": len(lo), "smoke_pages": len(smoke),
                      "newest_to": pages[0]["to"], "oldest_from": min(page["from"] for page in pages)}))


def key():
    for line in open("/content/.massive.env"):
        if line.startswith("MASSIVE_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("MASSIVE_API_KEY missing")


def get(url, token):
    delay = 1.0
    for attempt in range(7):
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            if exc.code in (429, 500, 502, 503, 504) and attempt < 6:
                time.sleep(delay)
                delay *= 2
                continue
            return exc.code, body
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt < 6:
                time.sleep(delay)
                delay *= 2
                continue
            return 0, b""
    return 0, b""


def fetch(plan_path, threads):
    token = key()
    done = set()
    if MANIFEST.exists():
        for line in MANIFEST.open():
            record = json.loads(line)
            if record.get("complete"):
                done.add((record["ticker"], record["from"], record["to"]))
    todo = [page for page in map(json.loads, open(plan_path)) if (page["ticker"], page["from"], page["to"]) not in done]
    log(f"plan {plan_path}: {len(todo)} pages to fetch, {len(done)} already complete, {threads} threads")
    lock = threading.Lock()
    counters = {"pages": 0, "bytes": 0, "errors": 0, "bars": 0}
    started = time.time()

    def one(page):
        ticker = page["ticker"]
        url = (f"{BASE}/v2/aggs/ticker/{urllib.parse.quote(ticker, safe='.')}/range/5/minute/"
               f"{page['from']}/{page['to']}?adjusted=false&sort=asc&limit=50000")
        part, records, complete = 0, [], True
        while url:
            status, body = get(url, token)
            folder = ROOT / page["to"][:7]
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{ticker}_{page['from']}_{page['to']}_p{part}.json.gz"
            with gzip.open(path, "wb", compresslevel=6) as handle:
                handle.write(body)
            try:
                payload = json.loads(body) if body else {}
            except ValueError:
                payload = {}
            count = payload.get("resultsCount") if isinstance(payload, dict) else None
            ok = status == 200 and isinstance(payload, dict) and payload.get("status") in ("OK", "DELAYED")
            complete = complete and ok
            records.append({"ticker": ticker, "from": page["from"], "to": page["to"], "part": part, "http": status,
                            "status": payload.get("status") if isinstance(payload, dict) else None, "count": count,
                            "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest(), "file": str(path.relative_to(ROOT))})
            next_url = payload.get("next_url") if ok else None
            if next_url:
                # Follow the cursor with the header only; drop any key the server might echo into the URL.
                parts = urllib.parse.urlsplit(next_url)
                query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query) if k.lower() != "apikey"]
                next_url = urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))
            url = next_url
            part += 1
        with lock:
            with MANIFEST.open("a") as handle:
                for record in records:
                    handle.write(json.dumps({**record, "complete": complete and record is records[-1]}) + "\n")
            counters["pages"] += 1
            counters["bytes"] += sum(record["bytes"] for record in records)
            counters["bars"] += sum(record["count"] or 0 for record in records)
            counters["errors"] += 0 if complete else 1
            if counters["pages"] % 500 == 0:
                rate = counters["pages"] / max(1.0, time.time() - started)
                log(f"{counters['pages']}/{len(todo)} pages, {counters['bytes'] / 1e9:.2f} GB, "
                    f"{counters['bars']} bars, {counters['errors']} incomplete, {rate:.1f} pages/s")

    with ThreadPoolExecutor(max_workers=threads) as pool:
        list(pool.map(one, todo))
    log(f"DONE {plan_path}: {counters['pages']} pages, {counters['bytes'] / 1e9:.2f} GB, {counters['bars']} bars, "
        f"{counters['errors']} incomplete, {time.time() - started:.0f}s")


if __name__ == "__main__":
    if sys.argv[1] == "plan":
        plan()
    elif sys.argv[1] == "fetch":
        fetch(sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 16)
