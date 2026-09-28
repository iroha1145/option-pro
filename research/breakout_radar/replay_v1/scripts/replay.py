"""Replay the production Breakout Radar over a segment of trading days on frozen data.

Smoke weeks on production's own scan times, with TradingView's classification values
and its OTC rows as slot takers (DATA_SPEC 11.2, path 乙 check):

    python replay.py --daily-db replay_smoke.sqlite --minute-store minute_store --fred fred \
        --export radar_export.jsonl.gz --metadata production --market-cap production \
        --grid production --start 2026-09-08 --end 2026-09-25 --warmup 0 \
        --variants baseline,hybrid_otc --full-snapshots --out runs/smoke --db-dir /tmp/smoke_db

Historical segment on the settings grid with the point-in-time directory:

    python replay.py --daily-db replay.sqlite --minute-store minute_store --fred fred \
        --directory massive_directory --sic ticker_sic.json.gz --shares pit_shares.jsonl \
        --start 2024-09-30 --end 2024-10-31 --warmup 1 \
        --variants baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15,rvol2 \
        --out runs/seg_2024_10 --db-dir /content/db/seg_2024_10

Production functions run everything after discovery; discovery is the replay proxy and
is labelled ``replay_proxy``. The baseline's settings must hash like production over
production's field set or the run refuses to start.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(PACK))

from harness.runner import RunConfig, run_segment  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--daily-db", type=Path, required=True)
    parser.add_argument("--minute-store", type=Path, required=True)
    parser.add_argument("--fred", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--db-dir", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--variants", default="baseline")
    parser.add_argument("--grid", choices=("settings", "production"), default="settings")
    parser.add_argument("--export", type=Path, help="production export (metadata=production or grid=production)")
    parser.add_argument("--directory", type=Path, help="weekly point-in-time directory snapshots")
    parser.add_argument("--sic", type=Path, help="frozen ticker_sic.json.gz")
    parser.add_argument("--shares", type=Path, help="point-in-time shares samples (JSONL)")
    parser.add_argument("--metadata", choices=("directory", "production"), default="directory")
    parser.add_argument("--market-cap", choices=("shares", "production", "none"), default="shares")
    parser.add_argument("--relvol-scale", type=float, default=1.0)
    parser.add_argument("--memo", choices=("on", "off"), default="on")
    parser.add_argument("--trim", choices=("on", "off"), default="on", help="session trimming of intraday input frames")
    parser.add_argument("--on-degraded", choices=("raise", "continue"), default="raise")
    parser.add_argument("--full-snapshots", action="store_true", help="also write every event snapshot (byte-identity checks)")
    parser.add_argument("--label", default="")
    parser.add_argument("--universe", type=Path, help="optional JSON list restricting discovery to these tickers")
    args = parser.parse_args()

    universe = None
    if args.universe is not None:
        import json

        universe = [str(item).upper() for item in json.loads(args.universe.read_text())]
    config = RunConfig(
        daily_db=args.daily_db, minute_store=args.minute_store, fred=args.fred, out=args.out, db_dir=args.db_dir,
        start=args.start, end=args.end, variants=[name.strip() for name in args.variants.split(",") if name.strip()],
        warmup_days=args.warmup, grid=args.grid, export=args.export, directory=args.directory, sic=args.sic,
        shares=args.shares, metadata_mode=args.metadata, market_cap_source=args.market_cap,
        relvol_scale=args.relvol_scale, memo=args.memo == "on", trim_sessions=args.trim == "on",
        on_degraded=args.on_degraded, full_snapshots=args.full_snapshots, label=args.label, universe=universe,
    )
    summary = run_segment(config)
    print(
        f"scans {summary['scans']} degraded {len(summary['degraded'])} "
        f"truncated_days {summary['truncated_days']} elapsed {summary['elapsed_s']}s"
    )
    print("DONE replay")


if __name__ == "__main__":
    main()
