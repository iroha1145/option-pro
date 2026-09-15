#!/usr/bin/env python3
"""Compare original, momentum, A0, C0, T1 and T2 on one frozen dump."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.algorithm_protocol import (
    ALGORITHM_ROUND_ID,
    ALGORITHM_ROUND_PROTOCOL,
    DESIGN_SPLIT,
)
from app.services.research.candidate_signals import (
    a0_top_signals,
    c0_top_signals,
    momentum_top_signals,
    original_top_signals,
)
from app.services.research.compare import compare_screener_candidates, filter_split, group_by_date, yearly_top10_means
from app.services.research.followups import compare_followups
from app.services.research.dataset import load_dataset
from app.services.research.diagnostics import (
    concentration_report,
    radar_repeat_report,
    saturation_report,
    tail_failures,
)
from app.services.research.protocol import split_for_date
from app.services.research.radar import first_trigger_by_pivot
from app.services.research.radar_compare import compare_radar_candidates
from app.services.research.registry import append_trial


def _load_json(path: Path) -> list | dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", help="screener compact rows JSON")
    parser.add_argument("--events", help="radar events JSON")
    parser.add_argument("--dataset", help="offline OHLCV for T1/T2 and optional ledgers")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--registry")
    parser.add_argument("--split", default=DESIGN_SPLIT)
    parser.add_argument("--first-triggers-only", action="store_true", default=True)
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "algorithm-protocol.json", ALGORITHM_ROUND_PROTOCOL)

    summary: dict = {
        "round_id": ALGORITHM_ROUND_ID,
        "split": args.split,
        "screener": None,
        "radar": None,
        "followups": None,
        "diagnostics": {},
    }

    if args.rows:
        rows = _load_json(Path(args.rows))
        if not isinstance(rows, list):
            raise SystemExit("rows must be a JSON list")
        if any(split_for_date(row.get("signal_date")) == "sealed" for row in rows):
            raise SystemExit("refusing sealed screener rows")
        compare = compare_screener_candidates(rows, split=args.split)
        compare["yearly_a0_top10"] = yearly_top10_means(compare["a0_vs_original"]["10"])
        compare["yearly_c0_top10"] = yearly_top10_means(compare["c0_vs_original"]["10"])
        compare["yearly_momentum_top10"] = yearly_top10_means(compare["momentum63_vs_original"]["10"])
        def _slim_pair(block: dict) -> dict:
            return {
                key: (
                    {inner: {k: v for k, v in item.items() if k != "daily"} for inner, item in value.items()}
                    if key.endswith("_vs_original") and isinstance(value, dict)
                    else value
                )
                for key, value in block.items()
                if key != "c0_audit_head"
            }

        summary["screener"] = _slim_pair(compare)
        summary["diagnostics"]["saturation"] = saturation_report(
            [row for row in rows if split_for_date(row.get("signal_date")) == args.split]
        )
        summary["diagnostics"]["concentration"] = concentration_report(
            [row for row in rows if split_for_date(row.get("signal_date")) == args.split]
        )
        summary["diagnostics"]["tail_failures"] = tail_failures(
            [row for row in rows if split_for_date(row.get("signal_date")) == args.split]
        )
        _write(out / "screener-compare.json", compare)
        _write(out / "signals-original-top10.json", original_top_signals(rows))
        _write(out / "signals-a0-top10.json", a0_top_signals(rows))
        _write(out / "signals-c0-top10.json", c0_top_signals(rows))
        _write(out / "signals-momentum63-top10.json", momentum_top_signals(rows))
        follow = compare_followups(group_by_date(filter_split(rows, args.split)))
        follow["yearly_f1_top10"] = yearly_top10_means(follow["F1"]["vs_original_top10"])
        follow["yearly_f2_top10"] = yearly_top10_means(follow["F2"]["vs_original_top10"])
        follow["yearly_f2_vs_a0_top10"] = yearly_top10_means(follow["F2"]["vs_a0_top10"])
        summary["followups"] = {
            "F1": {
                key: value
                for key, value in follow["F1"].items()
                if key != "vs_original_top10"
            }
            | {
                "vs_original_top10": {
                    k: v
                    for k, v in follow["F1"]["vs_original_top10"].items()
                    if k != "daily"
                }
            },
            "F2": {
                key: value
                for key, value in follow["F2"].items()
                if key not in {"vs_original_top10", "vs_a0_top10"}
            }
            | {
                "vs_original_top10": {
                    k: v
                    for k, v in follow["F2"]["vs_original_top10"].items()
                    if k != "daily"
                },
                "vs_a0_top10": {
                    k: v
                    for k, v in follow["F2"]["vs_a0_top10"].items()
                    if k != "daily"
                },
            },
            "yearly_f1_top10": follow["yearly_f1_top10"],
            "yearly_f2_top10": follow["yearly_f2_top10"],
            "yearly_f2_vs_a0_top10": follow["yearly_f2_vs_a0_top10"],
        }
        _write(out / "followup-compare.json", follow)

    if args.events:
        events = _load_json(Path(args.events))
        if isinstance(events, dict):
            events = events.get("events") or []
        if any(split_for_date(event.get("trading_date") or event.get("signal_date")) == "sealed" for event in events):
            raise SystemExit("refusing sealed radar events")
        events = [
            event
            for event in events
            if split_for_date(event.get("trading_date") or event.get("signal_date")) == args.split
        ]
        if args.first_triggers_only:
            events = first_trigger_by_pivot(events)["events"]
        summary["diagnostics"]["radar_repeats"] = radar_repeat_report(events)
        if not args.dataset:
            summary["radar"] = {
                "status": "not_evaluated",
                "reason": "dataset_required_for_t1_t2",
                "event_count": len(events),
            }
        else:
            dataset = load_dataset(args.dataset)
            radar = compare_radar_candidates(dataset, events)
            slim = {key: value for key, value in radar.items() if key != "events"}
            summary["radar"] = slim
            _write(out / "radar-compare.json", slim)
            _write(out / "radar-events-with-t1-t2.json", radar["events"])

    _write(out / "summary.json", summary)
    if args.registry:
        append_trial(
            args.registry,
            {
                "trial_id": f"{ALGORITHM_ROUND_ID}-batch1",
                "layer": "candidate",
                "family": "algorithm-round",
                "split": args.split,
                "command": "analyze_algorithm_round",
                "output": str(out),
                "candidates": ["A0", "C0", "T1", "T2"],
            },
        )
    print(json.dumps({"wrote": str(out / "summary.json"), "split": args.split}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
