"""Compare a smoke-window replay with the production export, layer by layer.

    python smoke_compare.py --export radar_export.jsonl.gz --replay runs/smoke \
        --variant hybrid_otc --out results/smoke

Layers (DATA_SPEC 7.2, thresholds in PREREGISTRATION.md):
1. discovery: per matched scan, the listed candidate sets, the listed names inside the
   top 60 by change, and value differences on common tickers (change, relative volume,
   market cap); the relative-volume ratio is the calibration statistic;
2. daily stage: pivot_id equality on tickers both sides detected a base for;
3. events: production first TRIGGERED transitions against replay first triggered scans,
   matched on (ticker, trading date, origin setup); state at the day's last scan;
4. intraday features: rvol_time_of_day relative error and comparison_sessions on scans
   whose production bars came from Massive;
5. T1: current status per event id.
Only completed production scans are compared; replay scans at times production failed
are listed separately.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NY = ZoneInfo("America/New_York")
TRIGGERED_STATES = {"TRIGGERED", "CONFIRMED", "HOLDING", "RETESTING", "RETEST_HELD", "REACCELERATING", "EXTENDED"}


def read_export(path: Path) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    columns: dict[str, list[str]] = {}
    current = None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if isinstance(obj, dict) and "table" in obj:
                current = obj["table"]
                columns[current] = obj["columns"]
            elif isinstance(obj, dict) and obj.get("end"):
                break
            else:
                tables[current].append(dict(zip(columns[current], obj)))
    return tables


def ts(value: Any) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def read_ledgers(folder: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in sorted(folder.glob("*.jsonl.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            records.extend(json.loads(line) for line in handle if line.strip())
    return records


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "p10": None, "p50": None, "p90": None}
    ordered = sorted(values)

    def q(p: float) -> float:
        return ordered[min(len(ordered) - 1, int(p * (len(ordered) - 1)))]

    return {"n": len(values), "p10": q(0.1), "p50": q(0.5), "p90": q(0.9)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True, help="replay output directory (holds <variant>/ledger)")
    parser.add_argument("--variant", default="hybrid_otc")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    tables = read_export(args.export)
    runs = {r["scan_run_id"]: r for r in tables["breakout_scan_runs"]}
    completed = {k: v for k, v in runs.items() if v["status"] == "completed"}
    by_as_of = {ts(r["scheduled_at"]): k for k, r in completed.items()}
    ledgers = [r for r in read_ledgers(args.replay / args.variant / "ledger") if r["kind"] != "t1"]
    replay_by_as_of = {ts(r["as_of"]): r for r in ledgers}

    # ---- production tables reshaped
    prod_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["breakout_candidates"]:
        if row["scan_run_id"] in completed:
            body = json.loads(row["candidate_json"])
            body["ticker"] = str(row["ticker"]).upper()
            prod_candidates[row["scan_run_id"]].append(body)
    prod_structures: dict[str, dict[str, str]] = defaultdict(dict)
    for row in tables["breakout_structures"]:
        prod_structures[row["scan_run_id"]][str(row["ticker"]).upper()] = row["pivot_id"]
    prod_snapshots: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    prod_first: dict[str, dict[str, Any]] = {}
    for row in tables["breakout_scan_events"]:
        if row["scan_run_id"] not in completed:
            continue
        body = json.loads(row["event_snapshot_json"])
        prod_snapshots[row["scan_run_id"]][row["event_id"]] = body
        first = prod_first.get(row["event_id"])
        if first is None or ts(completed[row["scan_run_id"]]["scheduled_at"]) < first["_as_of"]:
            body["_as_of"] = ts(completed[row["scan_run_id"]]["scheduled_at"])
            prod_first[row["event_id"]] = body
    prod_trigger: dict[str, datetime] = {}
    for row in tables["breakout_transitions"]:
        if row["to_state"] == "TRIGGERED":
            stamp = ts(row["evidence_at"])
            if row["event_id"] not in prod_trigger or stamp < prod_trigger[row["event_id"]]:
                prod_trigger[row["event_id"]] = stamp
    prod_t1 = {row["event_id"]: row["status"] for row in tables.get("breakout_t1_current", [])}

    # ---- layer 1: discovery
    matched = sorted(set(by_as_of) & set(replay_by_as_of))
    unmatched_replay = sorted(set(replay_by_as_of) - set(by_as_of))
    discovery_rows = []
    change_diff: list[float] = []
    relvol_ratio: list[float] = []
    cap_ratio: list[float] = []
    missing_bars: Counter[str] = Counter()
    for as_of in matched:
        scan_id = by_as_of[as_of]
        replay = replay_by_as_of[as_of]
        session = completed[scan_id]["session"]
        prod_rows = prod_candidates.get(scan_id, [])
        prod_listed = {c["ticker"]: c for c in prod_rows if str(c.get("exchange") or "").upper() != "OTC"}
        rep_listed = {c["ticker"]: c for c in replay["candidates"] if str(c.get("exchange") or "").upper() != "OTC"}
        prod_top60 = {c["ticker"] for c in sorted(prod_rows, key=lambda c: (-(c.get("provider_change_pct") or 0), c["ticker"]))[:60]
                      if str(c.get("exchange") or "").upper() != "OTC"}
        rep_top60 = {c["ticker"] for c in sorted(replay["candidates"], key=lambda c: (-(c.get("change") or 0), c["ticker"]))[:60]
                     if str(c.get("exchange") or "").upper() != "OTC"}
        union = set(prod_listed) | set(rep_listed)
        common = set(prod_listed) & set(rep_listed)
        for ticker in common:
            p, r = prod_listed[ticker], rep_listed[ticker]
            if p.get("provider_change_pct") is not None and r.get("change") is not None:
                change_diff.append(float(r["change"]) - float(p["provider_change_pct"]))
            if p.get("provider_relative_volume") and r.get("relvol"):
                relvol_ratio.append(float(p["provider_relative_volume"]) / float(r["relvol"]))
            if p.get("provider_market_cap") and r.get("market_cap"):
                cap_ratio.append(float(r["market_cap"]) / float(p["provider_market_cap"]))
        for ticker in set(prod_listed) - set(rep_listed):
            missing_bars[ticker] += 1
        discovery_rows.append(
            {
                "as_of": as_of.isoformat(), "session": session, "prod_listed": len(prod_listed), "replay_listed": len(rep_listed),
                "jaccard": round(len(common) / len(union), 4) if union else None,
                "top60_prod_listed": len(prod_top60), "top60_replay_listed": len(rep_top60),
                "top60_overlap": round(len(prod_top60 & rep_top60) / len(prod_top60), 4) if prod_top60 else None,
                "replay_prefilter": replay.get("prefilter_count"),
            }
        )
    with open(args.out / "discovery.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(discovery_rows[0]) if discovery_rows else ["as_of"])
        writer.writeheader()
        writer.writerows(discovery_rows)

    # ---- layer 2: structures
    pivot_equal = pivot_total = 0
    for as_of in matched:
        prod = prod_structures.get(by_as_of[as_of], {})
        rep = {s["ticker"]: s["pivot_id"] for s in replay_by_as_of[as_of]["structures"]}
        for ticker in set(prod) & set(rep):
            pivot_total += 1
            pivot_equal += prod[ticker] == rep[ticker]

    # ---- layer 3: events
    def key_of(event: dict[str, Any]) -> tuple:
        origin = event.get("origin_setup_type") or (event.get("features") or {}).get("origin_setup_type") or event.get("setup_type")
        return (str(event.get("ticker")).upper(), str(event.get("trading_date")), str(getattr(origin, "value", origin)))

    prod_events = {}
    for event_id, first in prod_first.items():
        trigger = prod_trigger.get(event_id)
        if trigger is None:
            continue
        trigger_scan = [k for k, r in completed.items() if ts(r["scheduled_at"]) == trigger]
        price = None
        if trigger_scan and event_id in prod_snapshots.get(trigger_scan[0], {}):
            price = prod_snapshots[trigger_scan[0]][event_id].get("event_price")
        prod_events[key_of(first)] = {"event_id": event_id, "triggered_at": trigger, "price": price}
    replay_events: dict[tuple, dict[str, Any]] = {}
    replay_last_state: dict[tuple, str] = {}
    prod_last_state: dict[tuple, str] = {}
    for record in sorted(ledgers, key=lambda r: r["as_of"]):
        for event in record["events"]:
            key = (event["ticker"], event["trading_date"], event["origin_setup_type"] or event["setup_type"])
            replay_last_state[key] = event["lifecycle_state"]
            if event["lifecycle_state"] in TRIGGERED_STATES and key not in replay_events:
                replay_events[key] = {"event_id": event["event_id"], "triggered_at": ts(record["as_of"]), "price": event["event_price"]}
    for scan_id in sorted(completed, key=lambda k: completed[k]["scheduled_at"]):
        for event_id, body in prod_snapshots.get(scan_id, {}).items():
            prod_last_state[key_of(body)] = str(body.get("lifecycle_state"))
    recall_rows = []
    hits = 0
    for key, prod in prod_events.items():
        rep = replay_events.get(key)
        row = {"ticker": key[0], "trading_date": key[1], "origin": key[2], "prod_triggered_at": prod["triggered_at"].isoformat(),
               "replay_triggered_at": rep["triggered_at"].isoformat() if rep else None,
               "minutes_apart": round((rep["triggered_at"] - prod["triggered_at"]).total_seconds() / 60, 1) if rep else None,
               "price_diff_pct": round((float(rep["price"]) / float(prod["price"]) - 1) * 100, 3) if rep and rep.get("price") and prod.get("price") else None,
               "prod_last_state": prod_last_state.get(key), "replay_last_state": replay_last_state.get(key)}
        hits += rep is not None
        recall_rows.append(row)
    extra = [{"ticker": k[0], "trading_date": k[1], "origin": k[2], "replay_triggered_at": v["triggered_at"].isoformat()}
             for k, v in replay_events.items() if k not in prod_events]
    with open(args.out / "events.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(recall_rows[0]) if recall_rows else ["ticker"])
        writer.writeheader()
        writer.writerows(recall_rows)
    with open(args.out / "events_replay_only.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["ticker", "trading_date", "origin", "replay_triggered_at"])
        writer.writeheader()
        writer.writerows(extra)

    # ---- layer 4: rvol on Massive-sourced production scans
    rvol_errors: list[float] = []
    prod_sessions: Counter[int] = Counter()
    replay_sessions: Counter[int] = Counter()
    for as_of in matched:
        scan_id = by_as_of[as_of]
        rep_events = {e["ticker"]: e for e in replay_by_as_of[as_of]["events"]}
        for event_id, body in prod_snapshots.get(scan_id, {}).items():
            features = body.get("features") or {}
            source = ((features.get("price_data_provenance") or {}).get("intraday") or {}).get("source")
            if source != "Massive" or features.get("rvol_time_of_day") is None:
                continue
            rep = rep_events.get(str(body.get("ticker")).upper())
            if rep is None or rep["features"].get("rvol_time_of_day") is None:
                continue
            rvol_errors.append(abs(rep["features"]["rvol_time_of_day"] / features["rvol_time_of_day"] - 1))
            if features.get("comparison_sessions") is not None:
                prod_sessions[int(features["comparison_sessions"])] += 1
            if rep["features"].get("comparison_sessions") is not None:
                replay_sessions[int(rep["features"]["comparison_sessions"])] += 1

    # ---- layer 5: T1
    bundle_path = args.replay / args.variant / "research_bundle.json.gz"
    t1_agree = t1_total = 0
    if bundle_path.exists():
        with gzip.open(bundle_path, "rt", encoding="utf-8") as handle:
            bundle = json.load(handle)
        for row in bundle.get("t1_current", []):
            if row["event_id"] in prod_t1:
                t1_total += 1
                t1_agree += prod_t1[row["event_id"]] == row["status"]

    summary = {
        "variant": args.variant,
        "matched_scans": len(matched),
        "production_completed_scans": len(completed),
        "replay_scans_without_production_match": len(unmatched_replay),
        "discovery": {
            "jaccard_listed": quantiles([r["jaccard"] for r in discovery_rows if r["jaccard"] is not None]),
            "top60_overlap_listed": quantiles([r["top60_overlap"] for r in discovery_rows if r["top60_overlap"] is not None]),
            "change_diff_pct_points": quantiles(change_diff),
            "relvol_ratio_prod_over_replay": quantiles(relvol_ratio),
            "market_cap_ratio_replay_over_prod": quantiles(cap_ratio),
            "production_listed_candidates_missing_in_replay": sum(missing_bars.values()),
            "distinct_missing_tickers": len(missing_bars),
            "missing_examples": [t for t, _ in missing_bars.most_common(15)],
        },
        "structures": {"common": pivot_total, "pivot_id_equal": pivot_equal,
                       "equal_rate": round(pivot_equal / pivot_total, 4) if pivot_total else None},
        "events": {
            "production_triggered": len(prod_events), "matched": hits,
            "recall": round(hits / len(prod_events), 4) if prod_events else None,
            "replay_only": len(extra),
            "minutes_apart": quantiles([r["minutes_apart"] for r in recall_rows if r["minutes_apart"] is not None]),
            "abs_price_diff_pct": quantiles([abs(r["price_diff_pct"]) for r in recall_rows if r["price_diff_pct"] is not None]),
            "last_state_agreement": round(
                sum(1 for r in recall_rows if r["replay_last_state"] and r["prod_last_state"] == r["replay_last_state"])
                / max(1, sum(1 for r in recall_rows if r["replay_last_state"])), 4),
        },
        "rvol": {"relative_error": quantiles(rvol_errors), "prod_comparison_sessions": dict(prod_sessions.most_common(6)),
                 "replay_comparison_sessions": dict(replay_sessions.most_common(6))},
        "t1": {"common": t1_total, "agree": t1_agree},
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    print("DONE smoke_compare")


if __name__ == "__main__":
    main()
