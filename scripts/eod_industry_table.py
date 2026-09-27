#!/usr/bin/env python3
"""Maintain the SIC industry table the v1.7 EOD candidates read (DATA_DIR/eod-limited-v1/industry-sic-v1.json.gz).

    eod_industry_table.py import --source ticker_sic.json.gz   # seed/merge from the frozen research table
    eod_industry_table.py refresh --budget 400                  # look up unseen stock tickers of the live directory
    eod_industry_table.py show                                  # counts

``import`` needs no network. ``refresh`` fetches the Massive directory and asks
``/v3/reference/tickers/{ticker}`` for at most ``--budget`` tickers the table has never
seen, through the same client the worker uses (four concurrent requests, retries on
429). Both write the table atomically. Nothing here changes what production scores:
the table is read only when ``live_config.LIVE_CONFIG`` turns an industry mode on.
"""
from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.eod_limited.industry import SicTable, load_table, refresh_missing, table_path  # noqa: E402
from app.services.eod_limited.universe import STOCK_PROVIDER_TYPES  # noqa: E402


def _read_records(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:  # type: ignore[operator]
        payload = json.load(handle)
    return payload["records"] if isinstance(payload, dict) else payload


def _show(table: SicTable) -> None:
    with_sic = [record for record in table.records if record["sic_code"]]
    groups = Counter(record["sic_code"][:3] for record in with_sic)
    print(f"records {len(table)}, with sic {len(with_sic)}, sic3 groups {len(groups)} "
          f"(>=5 members: {sum(1 for count in groups.values() if count >= 5)})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", help="DATA_DIR override; defaults to the configured data root")
    sub = parser.add_subparsers(dest="command", required=True)
    importer = sub.add_parser("import", help="merge records from a frozen table file")
    importer.add_argument("--source", required=True, type=Path)
    refresher = sub.add_parser("refresh", help="look up unseen stock tickers of the live directory")
    refresher.add_argument("--budget", type=int, default=400)
    sub.add_parser("show", help="print table counts")
    args = parser.parse_args()

    path = table_path(args.root)
    table = load_table(args.root)
    if args.command == "import":
        added = table.extend(_read_records(args.source))
        table.save(path)
        print(f"merged {added} records from {args.source} into {path}")
    elif args.command == "refresh":
        from app.services.eod_limited.market_data import _fetch_directory

        directory = _fetch_directory()
        stocks = [row["ticker"] for row in directory if str(row.get("type") or "").upper() in STOCK_PROVIDER_TYPES]
        counts = refresh_missing(table, stocks, budget=args.budget)
        if counts.get("looked_up"):
            table.save(path)
        print(f"directory stocks {len(stocks)}, refresh {counts}, table {path}")
    _show(table)


if __name__ == "__main__":
    main()
