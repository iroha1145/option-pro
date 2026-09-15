#!/usr/bin/env python3
"""Run the same cash ledger on original, momentum, A0 and C0 signals."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.algorithm_protocol import COST_SCENARIOS_BPS, PRIMARY_COST_BPS
from app.services.research.dataset import load_dataset
from app.services.research.portfolio import simulate_long_only


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--signals-dir", required=True)
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    src = Path(args.signals_dir)
    dest = Path(args.out_dir)
    dest.mkdir(parents=True, exist_ok=True)
    names = {
        "original": "signals-original-top10.json",
        "momentum63": "signals-momentum63-top10.json",
        "a0": "signals-a0-top10.json",
        "c0": "signals-c0-top10.json",
    }
    summary = {}
    for name, filename in names.items():
        path = src / filename
        if not path.is_file():
            summary[name] = {"status": "missing_signals"}
            continue
        signals = json.loads(path.read_text(encoding="utf-8"))
        summary[name] = {}
        for bps in COST_SCENARIOS_BPS:
            result = simulate_long_only(dataset, signals, cost_bps=float(bps))
            slim = {
                "status": result.get("status"),
                "cost_bps": bps,
                "final_equity": result.get("final_equity"),
                "total_return": result.get("total_return"),
                "max_drawdown": result.get("max_drawdown"),
                "trade_count": result.get("trade_count"),
                "rejected_count": result.get("rejected_count"),
                "average_positions": result.get("average_positions"),
                "average_occupancy": result.get("average_occupancy"),
                "time_in_market": result.get("time_in_market"),
                "open_positions_at_end": result.get("open_positions_at_end"),
            }
            (dest / f"{name}-portfolio-{int(bps)}bps.json").write_text(
                json.dumps(result, indent=2, ensure_ascii=True, default=str) + "\n"
            )
            summary[name][f"{int(bps)}bps"] = slim
        summary[name]["primary_cost_bps"] = PRIMARY_COST_BPS
    (dest / "portfolio-summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=True, default=str) + "\n"
    )
    print(json.dumps({"wrote": str(dest / "portfolio-summary.json")}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
