#!/usr/bin/env python3
"""Method corrections M1-M5 plus C1 and T1-priority. Reuses existing dumps."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.algorithm_protocol import (
    DESIGN_SPLIT,
    PARENT_EXECUTION_SHA,
    PRIMARY_COST_BPS,
    RISK_SIGNAL_PROTOCOL,
    RISK_SIGNAL_ROUND_ID,
)
from app.services.research.candidate_signals import original_top_signals
from app.services.research.compare import compare_screener_candidates, top10_identity
from app.services.research.dataset import load_dataset
from app.services.research.portfolio import simulate_c1_sector_budget, simulate_long_only, summarize_ledger
from app.services.research.protocol import split_for_date
from app.services.research.radar import first_trigger_by_pivot
from app.services.research.radar_compare import compare_radar_candidates
from app.services.research.t1_priority import compare_t1_priority
from app.services.research.t2_wait import compare_t2_wait_paths


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slim_pair(block: dict) -> dict:
    out = {}
    for key, value in block.items():
        if key == "c0_audit_head":
            continue
        if key.endswith("_vs_original") and isinstance(value, dict):
            out[key] = {
                inner: {k: v for k, v in item.items() if k != "daily"}
                for inner, item in value.items()
            }
        else:
            out[key] = value
    return out


def _ledger_slim(result: dict) -> dict:
    summary = summarize_ledger(result) if "total_return" not in result else {
        key: result.get(key)
        for key in (
            "initial_cash",
            "final_equity",
            "total_return",
            "max_drawdown",
            "drawdown_start",
            "drawdown_trough",
            "drawdown_recover",
            "trade_count",
            "rejected_count",
            "reject_reasons",
            "average_positions",
            "average_occupancy",
            "average_gross_exposure",
            "time_in_market",
            "worst_name_pnls",
            "sector_pnl",
        )
        if key in result or key in summarize_ledger(result)
    }
    if "reject_reasons" not in summary:
        summary = summarize_ledger(result)
    summary["overrun_day_share"] = result.get("overrun_day_share")
    summary["sector_overrun_n"] = len(result.get("sector_overruns") or [])
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows")
    parser.add_argument("--events")
    parser.add_argument("--dataset")
    parser.add_argument("--prior-a0-signals")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--split", default=DESIGN_SPLIT)
    parser.add_argument("--skip-radar", action="store_true")
    parser.add_argument("--skip-ledgers", action="store_true")
    args = parser.parse_args()
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write(out / "risk-signal-protocol.json", RISK_SIGNAL_PROTOCOL)

    summary = {
        "round_id": RISK_SIGNAL_ROUND_ID,
        "parent_execution_sha": PARENT_EXECUTION_SHA,
        "split": args.split,
        "inputs": {},
        "screener": None,
        "radar": None,
        "t2_wait": None,
        "t1_priority": None,
        "ledgers": None,
    }

    if args.rows:
        rows_path = Path(args.rows)
        summary["inputs"]["rows"] = {"path": str(rows_path), "sha256": _sha256(rows_path)}
        rows = _load_json(rows_path)
        if any(split_for_date(row.get("signal_date")) == "sealed" for row in rows):
            raise SystemExit("refusing sealed screener rows")
        compare = compare_screener_candidates(rows, split=args.split)
        identity = {
            session: tickers
            for session, tickers in top10_identity(
                {
                    session: [
                        row
                        for row in compare["a0_vs_original"]["10"]["daily"]
                        if False
                    ]
                }
            )
        }
        a0_tickers = [
            (row["signal_date"], tuple(row["candidate"]["tickers"]))
            for row in compare["a0_vs_original"]["10"]["daily"]
        ]
        identity_hash = hashlib.sha256(
            json.dumps(a0_tickers, ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        compare["a0_top10_identity_sha256"] = identity_hash
        if args.prior_a0_signals:
            prior = _load_json(Path(args.prior_a0_signals))
            prior_pairs = []
            by_date: dict[str, list[str]] = {}
            for item in prior:
                by_date.setdefault(str(item["signal_date"]), []).append(str(item["ticker"]))
            for session, tickers in sorted(by_date.items()):
                prior_pairs.append((session, tuple(tickers)))
            compare["a0_top10_identity_unchanged"] = prior_pairs == a0_tickers
            compare["prior_a0_signals"] = str(args.prior_a0_signals)
        _write(out / "screener-compare.json", compare)
        summary["screener"] = {
            "ics": {name: block.get("mean_ic") for name, block in compare["ics"].items()},
            "named_tails": compare.get("named_tails"),
            "a0_top10_identity_sha256": identity_hash,
            "a0_top10_identity_unchanged": compare.get("a0_top10_identity_unchanged"),
            "top10": {
                key: {
                    "original_mean": compare[f"{key}_vs_original"]["10"]["original_mean"],
                    "candidate_mean": compare[f"{key}_vs_original"]["10"]["candidate_mean"],
                    "paired": compare[f"{key}_vs_original"]["10"]["paired"],
                    "paired_ci": compare[f"{key}_vs_original"]["10"]["paired_ci"],
                }
                for key in ("a0", "c0", "momentum63")
                if f"{key}_vs_original" in compare
            },
        }
        _ = identity
        if args.dataset and not args.skip_ledgers:
            dataset = load_dataset(args.dataset)
            signals = original_top_signals(rows)
            _write(out / "signals-original-top10.json", signals)
            ledgers = {}
            original = simulate_long_only(dataset, signals, cost_bps=float(PRIMARY_COST_BPS))
            _write(out / "ledger-original-10bps.json", {**original, "trades": original["trades"][:20], "rejected": original["rejected"][:20], "equity_curve": original["equity_curve"]})
            ledgers["original"] = _ledger_slim(original)
            from app.services.research.candidate_signals import c0_top_signals

            c0_signals = c0_top_signals(rows)
            _write(out / "signals-c0-top10.json", c0_signals)
            c0 = simulate_long_only(dataset, c0_signals, cost_bps=float(PRIMARY_COST_BPS))
            _write(out / "ledger-c0-10bps.json", {**c0, "trades": c0["trades"][:20], "rejected": c0["rejected"][:20], "equity_curve": c0["equity_curve"]})
            ledgers["c0"] = _ledger_slim(c0)
            c1 = simulate_c1_sector_budget(dataset, signals, cost_bps=float(PRIMARY_COST_BPS))
            _write(
                out / "ledger-c1-10bps.json",
                {
                    **c1,
                    "trades": c1["trades"][:20],
                    "rejected": c1["rejected"][:40],
                    "equity_curve": c1["equity_curve"],
                    "sector_overruns": c1.get("sector_overruns"),
                },
            )
            ledgers["c1"] = _ledger_slim(c1)
            gross80 = simulate_long_only(
                dataset,
                signals,
                cost_bps=float(PRIMARY_COST_BPS),
                max_gross_exposure=0.80,
            )
            _write(
                out / "ledger-gross80-10bps.json",
                {**gross80, "trades": gross80["trades"][:20], "rejected": gross80["rejected"][:20], "equity_curve": gross80["equity_curve"]},
            )
            ledgers["gross_cap_80"] = _ledger_slim(gross80)
            summary["ledgers"] = ledgers

    if args.events and args.dataset and not args.skip_radar:
        events_path = Path(args.events)
        summary["inputs"]["events"] = {"path": str(events_path), "sha256": _sha256(events_path)}
        events = _load_json(events_path)
        if isinstance(events, dict):
            events = events.get("events") or []
        if any(split_for_date(event.get("trading_date") or event.get("signal_date")) == "sealed" for event in events):
            raise SystemExit("refusing sealed radar events")
        events = [
            event
            for event in events
            if split_for_date(event.get("trading_date") or event.get("signal_date")) == args.split
        ]
        if events and not (events[0].get("t1") and events[0].get("t2")):
            events = first_trigger_by_pivot(events)["events"]
        dataset = load_dataset(args.dataset)
        if events and events[0].get("t1") and events[0].get("t2"):
            labeled = list(events)
            t2 = compare_t2_wait_paths(dataset, labeled)
            t1p = compare_t1_priority(dataset, labeled)
            radar = {
                "event_count": len(labeled),
                "reused_attached_t1_t2": True,
                "t2_wait": t2,
                "t1_priority": t1p,
            }
        else:
            radar_full = compare_radar_candidates(dataset, events)
            labeled = radar_full["events"]
            t2 = compare_t2_wait_paths(dataset, labeled)
            t1p = compare_t1_priority(dataset, labeled)
            radar = {key: value for key, value in radar_full.items() if key != "events"}
            radar["t2_wait"] = t2
            radar["t1_priority"] = t1p
        _write(out / "radar-corrections.json", radar)
        summary["radar"] = {
            "event_count": radar.get("event_count"),
            "paired_vs_raw": radar.get("paired_vs_raw"),
            "t1_selection_contribution": (
                (radar.get("t1") or {}).get("selection_contribution_on_original_opportunity_set_excess_vs_universe")
            ),
        }
        summary["t2_wait"] = {
            "confirmed": t2.get("confirmed"),
            "t2_open_over_t1_open": t2.get("t2_open_over_t1_open"),
            "own_20d_clock": t2.get("own_20d_clock"),
            "common_exit_clock": t2.get("common_exit_clock"),
            "mae_mfe": t2.get("mae_mfe"),
            "failure_definitions": t2.get("failure_definitions"),
        }
        summary["t1_priority"] = {
            "rank_proxy": t1p.get("rank_proxy"),
            "by_k": {
                key: {
                    inner: value
                    for inner, value in block.items()
                    if inner
                    not in {
                        "raw_top_k",
                        "t1_priority_top_k",
                    }
                }
                | {
                    "raw_daily_ew_excess": (block.get("raw_top_k") or {}).get("daily_ew_excess"),
                    "priority_daily_ew_excess": (block.get("t1_priority_top_k") or {}).get("daily_ew_excess"),
                    "paired": block.get("paired_daily_excess_priority_minus_raw"),
                    "t1_share_raw": block.get("t1_share_raw"),
                    "t1_share_priority": block.get("t1_share_priority"),
                }
                for key, block in (t1p.get("by_k") or {}).items()
            },
        }

    _write(out / "summary.json", summary)
    print(json.dumps({"wrote": str(out / "summary.json"), "split": args.split}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
