"""Rebuild production's all-market bar cache from the frozen Massive responses.

The replay reads the schema production writes (``market_data._connect``) and every
row passes production's own validators (``_raw_bar`` for bars, ``_split_row`` for
splits), so the panel builder and the split adjustment used later are production
code, not a reimplementation. Rows a validator rejects are counted per day and
reported; production would have refused those days.

    python build_replay_db.py --freeze /content/data/massive_frozen_2026-09-27 \
        --db /content/data/replay.sqlite
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "backend"))

from app.services import massive  # noqa: E402
from app.services.eod_limited import market_data as md  # noqa: E402
from app.services.market_calendar import is_trading_day  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--freeze", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()
    if args.db.exists():
        raise SystemExit(f"{args.db} already exists; refusing to append to a replay cache")

    started = time.time()
    manifest = json.loads((args.freeze / "manifest.json").read_text())
    days = sorted(manifest["days"], key=lambda entry: entry["date"])
    connection = md._connect(args.db)
    connection.execute("PRAGMA synchronous=OFF")
    rejected: dict[str, Counter] = {}
    sessions = bars = 0
    holiday_errors = []
    for entry in days:
        day = date.fromisoformat(entry["date"])
        if not entry.get("rows"):
            if is_trading_day(day):
                holiday_errors.append(entry["date"])
            continue
        body = gzip.open(args.freeze / "grouped_daily_raw" / str(day.year) / f"{day.isoformat()}.json.gz").read()
        payload = json.loads(body)
        results = payload.get("results") or []
        if payload.get("status") != "OK" or payload.get("adjusted") is not False \
                or payload.get("resultsCount") != len(results):
            raise SystemExit(f"{day}: frozen envelope does not match what production accepts")
        rows = []
        for raw in results:
            try:
                rows.append(md._raw_bar(day, raw))
            except massive.MassiveError as error:
                rejected.setdefault(day.isoformat(), Counter())[str(error)] += 1
        with connection:
            connection.execute(
                "INSERT INTO market_sessions (session_date, fetched_at, result_count, content_sha256, provider_status) "
                "VALUES (?, ?, ?, ?, ?)",
                (day.isoformat(), manifest["frozen_at_utc"], len(rows), entry["sha256"], "OK"),
            )
            connection.executemany(
                "INSERT INTO raw_daily_bars (ticker, session_date, timestamp_ms, open, high, low, close, "
                "volume, vwap, transactions) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        sessions += 1
        bars += len(rows)

    first, last = date.fromisoformat(days[0]["date"]), date.fromisoformat("2026-09-27")
    split_rows = json.loads(gzip.open(args.freeze / "splits.json.gz").read())
    parsed, invalid = [], Counter()
    for raw in split_rows:
        try:
            parsed.append(md._split_row(raw, first, last))
        except massive.MassiveError as error:
            invalid[str(error)] += 1
    with connection:
        connection.executemany(
            "INSERT OR IGNORE INTO splits (ticker, execution_date, split_from, split_to, provider_id) "
            "VALUES (?, ?, ?, ?, ?)",
            parsed,
        )
    split_count = connection.execute("SELECT COUNT(*) FROM splits").fetchone()[0]
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()

    print(f"sessions {sessions}, bars {bars}, holidays with rows or sessions without rows: {holiday_errors}")
    print(f"rejected bar rows: {sum(sum(c.values()) for c in rejected.values())} on {len(rejected)} days")
    for day, counter in sorted(rejected.items())[:20]:
        print(f"  {day}: {dict(counter)}")
    print(f"splits: {len(split_rows)} frozen, {len(parsed)} valid, {split_count} stored after de-duplication, "
          f"invalid {dict(invalid)}")
    print(f"db {args.db.stat().st_size / 1e9:.2f} GB, {time.time() - started:.0f}s")
    print("DONE build_replay_db")


if __name__ == "__main__":
    main()
