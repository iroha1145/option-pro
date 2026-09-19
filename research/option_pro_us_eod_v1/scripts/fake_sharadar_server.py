"""Local stand-in for api.sharadar.com to exercise run_sharadar_data_gate.py offline.

Usage (two terminals, from the repo root):

    PYTHONPATH=backend python research/option_pro_us_eod_v1/scripts/fake_sharadar_server.py 18765 /tmp/ready.json /tmp/cache/daily_bars.parquet
    SHARADAR_API_KEY=dummy-not-a-real-secret PYTHONPATH=backend \
        python research/option_pro_us_eod_v1/scripts/run_sharadar_data_gate.py \
        --base-url http://127.0.0.1:18765/v1.0/data --store /tmp/store --pack-dir /tmp/pack \
        --reconcile-cache /tmp/cache/daily_bars.parquet

Expected: exit 0 / DATA_GATE_ACCEPTED, bulk for stocks/funds/tickers and paged for
actions; `--mode paged` exercises the paged path for every table. The dummy key is
never sent: the client only attaches a key to the official https origin. This is a
plumbing check, not a substitute for the live run against the real vendor. Keep
--store and --pack-dir outside the repo so no tracked pack file is rewritten.

Serves the four tables with skip/limit/from/to/ticker paging and explicit sort,
bulk status metadata + zip for stocks/funds/tickers, and refuses bulk for actions
so the paged fallback and the per-day recount run too. Writes a reconcile parquet
cache aligned to the same synthetic prices.
"""

from __future__ import annotations

import csv
import io
import json
import random
import sys
import zipfile
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.data.sharadar_acceptance import DELIST_FIXTURES, trading_calendar_sessions  # noqa: E402
from app.services.research_eod_v1.data.sharadar_schema import ACTIONS_FIELDS, STOCKS_FIELDS, TICKERS_FIELDS  # noqa: E402
PORT = int(sys.argv[1])
READY = Path(sys.argv[2])
CACHE = Path(sys.argv[3])

SESSIONS = trading_calendar_sessions(date(2010, 1, 1), date(2024, 6, 28))
ISO = [s.isoformat() for s in SESSIONS]

ACTIVE = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "INTC", "ORCL", "CSCO", "ADBE"]
ACTIVE_FIRST = {"META": "2012-05-18", "TSLA": "2010-06-29"}
ETFS = ["SPY", "QQQ"]


def ticker_row(**kw):
    row = {f: "" for f in TICKERS_FIELDS}
    row["table"] = "SEP"
    row.update(kw)
    return row


def series(seed: str, first: str, last: str, base: float, vol: float):
    rng = random.Random(seed)
    price = base
    out = []
    for day in ISO:
        if day < first or day > last:
            continue
        price = max(1.0, price * (1.0 + rng.uniform(-0.02, 0.021)))
        volume = float(int(vol * rng.uniform(0.6, 1.6)))
        c = round(price, 2)
        out.append({
            "ticker": None, "date": day, "open": c, "high": round(c * 1.01, 2), "low": round(c * 0.99, 2),
            "close": c, "volume": volume, "closeadj": c, "closeunadj": c, "lastupdated": day,
        })
    return out


TICKERS, STOCKS, FUNDS, ACTIONS = [], [], [], []
PRICES = {}
perma = 100000
for tk in ACTIVE:
    perma += 1
    first = ACTIVE_FIRST.get(tk, "2010-01-04")
    TICKERS.append(ticker_row(permaticker=str(perma), ticker=tk, name=f"{tk} Inc", exchange="NASDAQ", isdelisted="N",
                              category="Domestic Common Stock", currency="USD", firstpricedate=first,
                              lastpricedate="2024-06-28", scalemarketcap="6 - Mega", lastupdated="2024-06-28"))
    rows = series(tk, first, "2024-06-28", 60.0, 4_000_000)
    for r in rows:
        r["ticker"] = tk
    STOCKS.extend(rows)
    PRICES[tk] = rows
    if tk == "AAPL":
        for day in ISO[::63]:
            ACTIONS.append({"date": day, "action": "dividend", "ticker": tk, "name": f"{tk} Inc", "value": 0.22, "contraticker": "", "contraname": ""})

# reused ticker: the live DELL row does not cover 2013; DELL1 is the 2013 company
perma = 200000
for fx in DELIST_FIXTURES:
    perma += 1
    tk = fx["ticker"]
    year = int(fx["year"])
    stored = "DELL1" if tk == "DELL" else tk
    year_sessions = [d for d in ISO if d.startswith(str(year))]
    last = year_sessions[min(len(year_sessions) - 1, 120)]
    first = max("2010-01-04", f"{year - 2}-01-04")
    first = next(d for d in ISO if d >= first)
    TICKERS.append(ticker_row(permaticker=str(perma), ticker=stored, name=f"{tk} Corp", exchange="NYSE", isdelisted="Y",
                              category="Domestic Common Stock", currency="USD", firstpricedate=first,
                              lastpricedate=last, scalemarketcap="4 - Mid", lastupdated=last,
                              relatedtickers="DELL" if tk == "DELL" else ""))
    rows = series(stored, first, last, 25.0, 1_500_000)
    for r in rows:
        r["ticker"] = stored
    STOCKS.extend(rows)
    last_close = rows[-1]["close"]
    ACTIONS.append({"date": last, "action": "delisted", "ticker": stored, "name": f"{tk} Corp", "value": "", "contraticker": "", "contraname": ""})
    if fx["expected_terminal"] == "bankruptcy_last_trade":
        ACTIONS.append({"date": last, "action": "bankruptcyliquidation", "ticker": stored, "name": f"{tk} Corp", "value": "", "contraticker": "", "contraname": ""})
    else:
        # `acquisitioncash` for the fixtures the list calls cash deals; the one
        # `acquisition_or_unknown` fixture keeps `acquisitionby`, whose number
        # the vendor never assigns a unit, so the offline chain also exercises
        # the path where identity resolves and the economic gate stays shut.
        action = "acquisitionby" if fx["expected_terminal"] == "acquisition_or_unknown" else "acquisitioncash"
        ACTIONS.append({"date": last, "action": action, "ticker": stored, "name": f"{tk} Corp", "value": round(last_close * 1.1, 2), "contraticker": "ACQ", "contraname": "Acquirer Inc"})
TICKERS.append(ticker_row(permaticker="200099", ticker="DELL", name="Dell Technologies", exchange="NYSE", isdelisted="N",
                          category="Domestic Common Stock", currency="USD", firstpricedate="2018-12-28",
                          lastpricedate="2024-06-28", scalemarketcap="6 - Mega", lastupdated="2024-06-28", relatedtickers="DELL1"))
rows = series("DELL-live", "2018-12-28", "2024-06-28", 45.0, 3_000_000)
for r in rows:
    r["ticker"] = "DELL"
STOCKS.extend(rows)

perma = 300000
for tk in ETFS:
    perma += 1
    TICKERS.append(ticker_row(permaticker=str(perma), ticker=tk, name=f"{tk} Trust", exchange="NYSEARCA", isdelisted="N",
                              category="ETF", currency="USD", firstpricedate="2010-01-04", lastpricedate="2024-06-28",
                              lastupdated="2024-06-28", table="SFP"))
    rows = series(tk, "2010-01-04", "2024-06-28", 150.0, 50_000_000)
    for r in rows:
        r["ticker"] = tk
    FUNDS.extend(rows)

for table_rows in (STOCKS, FUNDS, ACTIONS):
    table_rows.sort(key=lambda r: (r["date"], r["ticker"]))

TABLES = {"tickers": TICKERS, "stocks": STOCKS, "funds": FUNDS, "actions": ACTIONS}
FIELDS = {"tickers": TICKERS_FIELDS + ("table",), "stocks": STOCKS_FIELDS, "funds": STOCKS_FIELDS, "actions": ACTIONS_FIELDS}
CSV_NAME = {"tickers": "SHARADAR_TICKERS.csv", "stocks": "SHARADAR_SEP.csv", "funds": "SHARADAR_SFP.csv"}


def zip_bytes(table: str) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(FIELDS[table]))
    writer.writeheader()
    for row in TABLES[table]:
        writer.writerow({k: row.get(k, "") for k in FIELDS[table]})
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(CSV_NAME[table], buf.getvalue())
    return out.getvalue()


ZIPS = {t: zip_bytes(t) for t in CSV_NAME}


def write_cache(path: Path) -> None:
    import pandas as pd

    records = []
    for tk in ACTIVE:
        for r in PRICES[tk]:
            if r["date"].startswith("2023"):
                records.append({"symbol": tk, "date": r["date"], "tri": r["closeadj"], "close": r["close"], "raw_close": r["closeunadj"], "volume": r["volume"]})
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(records).to_parquet(path, index=False)


class Handler(BaseHTTPRequestHandler):
    hits: dict[str, int] = {}

    def log_message(self, *_a) -> None:
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        q = parse_qs(parts.query)
        table = parts.path.rstrip("/").rsplit("/", 1)[-1]
        if "api_key" in q:
            Handler.hits["KEY_ATTACHED_TO_LOCAL_ORIGIN"] = Handler.hits.get("KEY_ATTACHED_TO_LOCAL_ORIGIN", 0) + 1
        if table not in TABLES:
            self._send(404, b'{"error":"unknown table"}')
            return
        Handler.hits[table] = Handler.hits.get(table, 0) + 1
        if "years" in q:
            years = q["years"][0]
            if table == "actions" or years not in {"full", "10", "5"}:
                self._send(200, json.dumps({"error": {"code": "QEPx05", "message": "bulk export not available"}}).encode())
                return
            if q.get("status", [""])[0].lower() == "true":
                meta = {"table": table.upper(), "name": CSV_NAME[table].replace(".csv", ".zip"), "size": len(ZIPS[table]), "sizeLabel": f"{len(ZIPS[table]) // 1024} KB", "modified": "2024-06-28"}
                self._send(200, json.dumps(meta).encode())
                return
            self._send(200, ZIPS[table], "application/zip")
            return
        rows = TABLES[table]
        if table != "tickers":
            lo = q.get("from", [""])[0]
            hi = q.get("to", ["9999"])[0]
            rows = [r for r in rows if r["date"] >= lo and r["date"] <= hi]
            sort = q.get("sort", ["date.desc"])[0]
            if sort.endswith("desc"):
                rows = list(reversed(rows))
        if "ticker" in q:
            wanted = set(q["ticker"][0].split(","))
            rows = [r for r in rows if r["ticker"] in wanted]
        skip = int(q.get("skip", ["0"])[0])
        limit = int(q.get("limit", ["10000"])[0])
        page = rows[skip:skip + limit]
        self._send(200, json.dumps(page).encode())


if __name__ == "__main__":
    write_cache(CACHE)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    READY.write_text(json.dumps({"port": PORT, "rows": {t: len(r) for t, r in TABLES.items()}}))
    try:
        server.serve_forever()
    finally:
        Path(str(READY) + ".hits").write_text(json.dumps(Handler.hits))
