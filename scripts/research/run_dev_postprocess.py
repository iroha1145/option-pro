#!/usr/bin/env python3
"""Run development-split analyses after a screener dump exists. Never unblinds sealed."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = sys.executable


def _run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.check_call(args, cwd=ROOT)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="/opt/cursor/research/screener-radar-data/yahoo-daily")
    parser.add_argument("--runs", default="/opt/cursor/research/screener-radar-data/runs")
    parser.add_argument("--registry", default="/opt/cursor/research/screener-radar-data")
    parser.add_argument("--screener-run", default="dev-screener-balanced-all")
    parser.add_argument("--radar-run", default="dev-radar-daily-base")
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-radar", action="store_true")
    args = parser.parse_args()
    runs = Path(args.runs)
    screener = runs / f"{args.screener_run}.json"
    rows = runs / f"{args.screener_run}-rows.json"
    if not screener.is_file() or not rows.is_file():
        raise SystemExit(f"missing screener artifacts: {screener} {rows}")

    _run(
        [
            PYTHON,
            "scripts/research/summarize_screener_run.py",
            "--run",
            str(screener),
            "--out",
            str(runs / f"{args.screener_run}.summary.json"),
        ]
    )
    _run(
        [
            PYTHON,
            "scripts/research/analyze_screener_rows.py",
            "--rows",
            str(rows),
            "--out",
            str(runs / f"{args.screener_run}.rows-analysis.json"),
        ]
    )
    _run(
        [
            PYTHON,
            "scripts/research/analyze_screener_modes.py",
            "--rows",
            str(rows),
            "--out",
            str(runs / f"{args.screener_run}.modes.json"),
        ]
    )
    signals = runs / f"{args.screener_run}.top10-signals.json"
    _run(
        [
            PYTHON,
            "scripts/research/extract_portfolio_signals.py",
            "--rows",
            str(rows),
            "--out",
            str(signals),
            "--max-rank",
            "10",
        ]
    )
    if not args.skip_portfolio:
        for cost in (5, 10, 25):
            _run(
                [
                    PYTHON,
                    "-m",
                    "app.services.research.cli",
                    "portfolio",
                    "--dataset",
                    args.dataset,
                    "--signals",
                    str(signals),
                    "--out",
                    str(runs / f"{args.screener_run}.portfolio-{cost}bps.json"),
                    "--registry",
                    args.registry,
                    "--cost-bps",
                    str(cost),
                    "--trial-id",
                    f"original-portfolio-screener-top10-{cost}bps",
                ]
            )

    radar_events = runs / f"{args.radar_run}-events.json"
    if args.skip_radar or not radar_events.is_file():
        print("radar artifacts not ready; skip combo/radar summaries", flush=True)
        return 0
    _run(
        [
            PYTHON,
            "scripts/research/analyze_radar.py",
            "--events",
            str(radar_events),
            "--out",
            str(runs / f"{args.radar_run}.summary.json"),
        ]
    )
    _run(
        [
            PYTHON,
            "scripts/research/analyze_combo.py",
            "--events",
            str(radar_events),
            "--screener-rows",
            str(rows),
            "--out",
            str(runs / "dev-combo-prior-screener.json"),
        ]
    )
    radar_signals = runs / f"{args.radar_run}.signals.json"
    _run(
        [
            PYTHON,
            "scripts/research/extract_radar_portfolio_signals.py",
            "--events",
            str(radar_events),
            "--out",
            str(radar_signals),
        ]
    )
    if not args.skip_portfolio:
        _run(
            [
                PYTHON,
                "-m",
                "app.services.research.cli",
                "portfolio",
                "--dataset",
                args.dataset,
                "--signals",
                str(radar_signals),
                "--out",
                str(runs / f"{args.radar_run}.portfolio-10bps.json"),
                "--registry",
                args.registry,
                "--cost-bps",
                "10",
                "--trial-id",
                "original-portfolio-radar-10bps",
            ]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
