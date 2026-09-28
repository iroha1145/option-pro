"""Explain the production-listed candidates the replay proxy did not list, and the reverse.

    python discovery_misses.py --export radar_export.jsonl.gz --replay runs/smoke \
        --variant hybrid_otc --daily-db replay_smoke.sqlite --minute-store minute_store \
        --out results/smoke/misses [--exclude-current-leveraged]

For every completed production scan on the replayed days, every listed (non-OTC)
production candidate missing from the replay's candidate list is re-derived from the
proxy's own arrays at that scan (``ReplayDiscoveryProvider.prefilter_arrays``) and given
one reason, the first that applies:

    no_metadata            production never listed the ticker outside OTC (no TradingView row to copy)
    no_bars_today          the minute store has no bar for the ticker that day
    no_bar_yet             bars exist that day but none had completed at the scan
    no_previous_close      the daily store has no prior close (change undefined)
    price_filter           last close below the minimum price
    change_filter          change below the profile's minimum
    relvol_filter          relative volume below the minimum (regular session)
    relvol_unavailable     relative volume undefined (no 10-day average volume)
    no_premarket_volume    pre-market profile, no volume yet
    cut_by_150_window      passed the filters, ranked at or beyond row 150 by change
    removed_by_normalizer  passed the filters and the window; production's own
                           normalizer or deduplication dropped it

Production's values (price, change, relative volume) sit beside the replay's in
misses.csv; summary.json aggregates by reason and quantifies the value gaps. The
reverse list (replay-listed tickers production did not list) is summarised as well.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter, defaultdict
from datetime import timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

PACK = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(PACK))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from smoke_compare import quantiles, read_export, read_ledgers, ts  # noqa: E402

from app.services.breakouts.models import MarketSession  # noqa: E402
from harness.discovery import ReplayDiscoveryProvider  # noqa: E402
from harness.settings import build_settings, variant_spec  # noqa: E402
from harness.stores import DailyStore, MinuteStore, ProductionCandidateMetadata  # noqa: E402

NY = ZoneInfo("America/New_York")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--replay", type=Path, required=True)
    parser.add_argument("--variant", default="hybrid_otc")
    parser.add_argument("--daily-db", type=Path, required=True)
    parser.add_argument("--minute-store", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--relvol-scale", type=float, default=1.0)
    parser.add_argument("--exclude-current-leveraged", action="store_true",
                        help="ignore production candidates that today's asset_policy.is_leveraged_etf flags")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    if args.exclude_current_leveraged:
        from app.services.breakouts.asset_policy import is_leveraged_etf
    else:
        def is_leveraged_etf(*_args: Any, **_kwargs: Any) -> bool:
            return False

    tables = read_export(args.export)
    ledgers = [r for r in read_ledgers(args.replay / args.variant / "ledger") if r["kind"] != "t1" and not r.get("warmup")]
    replay_by_as_of = {ts(r["as_of"]): r for r in ledgers}
    replay_days = {stamp.astimezone(NY).date().isoformat() for stamp in replay_by_as_of}
    completed = {
        r["scan_run_id"]: r for r in tables["breakout_scan_runs"]
        if r["status"] == "completed" and ts(r["scheduled_at"]).astimezone(NY).date().isoformat() in replay_days
    }
    prod_candidates: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for row in tables["breakout_candidates"]:
        if row["scan_run_id"] not in completed:
            continue
        body = json.loads(row["candidate_json"])
        if str(body.get("exchange") or "").upper() == "OTC":
            continue
        if is_leveraged_etf(body.get("asset_type"), body.get("name"), {}):
            continue
        prod_candidates[row["scan_run_id"]][str(row["ticker"]).upper()] = body

    spec = variant_spec(args.variant)
    settings = build_settings(args.variant, args.out / "unused.sqlite")
    metadata = ProductionCandidateMetadata(args.export)
    minute_store = MinuteStore(args.minute_store)
    daily_store = DailyStore(args.daily_db)
    provider = ReplayDiscoveryProvider(
        settings, minute_store=minute_store, daily_store=daily_store, metadata=metadata,
        market_cap_source="production", relvol_scale=args.relvol_scale,
        inject_production_otc=bool(spec.get("BREAKOUT_ALLOW_OTC", False)),
    )
    limit = settings.provider_result_limit

    rows: list[dict[str, Any]] = []
    reasons: Counter[str] = Counter()
    reasons_by_session: dict[str, Counter[str]] = defaultdict(Counter)
    gaps: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    extra_rows: list[dict[str, Any]] = []
    extra_values: dict[str, list[float]] = defaultdict(list)
    scans = 0
    for scan_id, run in sorted(completed.items(), key=lambda item: item[1]["scheduled_at"]):
        as_of = ts(run["scheduled_at"])
        replay = replay_by_as_of.get(as_of)
        if replay is None:
            continue
        scans += 1
        session = MarketSession.PREMARKET if run["session"] == "premarket" else MarketSession.REGULAR
        if run["session"] not in ("premarket", "regular"):
            continue
        replay_tickers = {str(c["ticker"]).upper() for c in replay["candidates"]}
        arrays = provider.prefilter_arrays(session, as_of)
        context = arrays["context"] if arrays else None
        index_of = {ticker: i for i, ticker in enumerate(context.tickers)} if context else {}
        # Row 150's change is the cut for the sort by change; OTC rows take slots when injected.
        cut_change = None
        if arrays is not None:
            passing_changes = [float(v) for v in arrays["change"][arrays["passing"]]]
            passing_changes.extend(change for change, _ticker, _row in provider._otc_rows(session, as_of))
            passing_changes.sort(reverse=True)
            if len(passing_changes) >= limit:
                cut_change = passing_changes[limit - 1]
        day = as_of.astimezone(NY).date()
        minute = as_of.astimezone(NY).hour * 60 + as_of.astimezone(NY).minute
        for ticker, body in prod_candidates.get(scan_id, {}).items():
            if ticker in replay_tickers:
                continue
            record = {
                "as_of": as_of.isoformat(), "session": run["session"], "minute": minute, "ticker": ticker,
                "prod_price": body.get("price"), "prod_change": body.get("provider_change_pct"),
                "prod_relvol": body.get("provider_relative_volume"), "prod_asset_type": body.get("asset_type"),
                "replay_price": None, "replay_change": None, "replay_relvol": None, "replay_cumulative_volume": None,
            }
            if metadata.meta(ticker, day) is None:
                reason = "no_metadata"
            elif ticker not in index_of:
                reason = "no_bars_today" if minute_store.day_slots(ticker, day) is None else "no_bar_yet"
            else:
                i = index_of[ticker]
                price = float(arrays["last_close"][i])
                change = float(arrays["change"][i])
                relvol = float(arrays["relvol"][i])
                cumulative = float(arrays["cumulative"][i])
                record.update(
                    replay_price=None if not np.isfinite(price) else round(price, 4),
                    replay_change=None if not np.isfinite(change) else round(change, 4),
                    replay_relvol=None if not np.isfinite(relvol) else round(relvol, 4),
                    replay_cumulative_volume=None if not np.isfinite(cumulative) else cumulative,
                )
                if not np.isfinite(price):
                    reason = "no_bar_yet"
                elif not np.isfinite(context.previous_close[i]):
                    reason = "no_previous_close"
                elif price < settings.min_price:
                    reason = "price_filter"
                elif session is MarketSession.PREMARKET and change < settings.premarket_min_change_pct:
                    reason = "change_filter"
                elif session is MarketSession.REGULAR and change < settings.regular_min_change_pct:
                    reason = "change_filter"
                elif session is MarketSession.PREMARKET and not cumulative > 0:
                    reason = "no_premarket_volume"
                elif session is MarketSession.REGULAR and not np.isfinite(relvol):
                    reason = "relvol_unavailable"
                elif session is MarketSession.REGULAR and relvol < settings.regular_min_relative_volume:
                    reason = "relvol_filter"
                elif cut_change is not None and change < cut_change:
                    reason = "cut_by_150_window"
                else:
                    reason = "removed_by_normalizer"
                if reason == "change_filter" and body.get("provider_change_pct") is not None:
                    gaps["change_filter"]["prod_change"].append(float(body["provider_change_pct"]))
                    gaps["change_filter"]["replay_change"].append(change)
                    gaps["change_filter"]["replay_minus_prod"].append(change - float(body["provider_change_pct"]))
                if reason == "relvol_filter" and body.get("provider_relative_volume"):
                    gaps["relvol_filter"]["prod_relvol"].append(float(body["provider_relative_volume"]))
                    gaps["relvol_filter"]["replay_relvol"].append(relvol)
                    gaps["relvol_filter"]["replay_over_prod"].append(relvol / float(body["provider_relative_volume"]))
                if reason == "cut_by_150_window" and cut_change is not None:
                    gaps["cut_by_150_window"]["change_below_cut"].append(cut_change - change)
            record["reason"] = reason
            reasons[reason] += 1
            reasons_by_session[run["session"]][reason] += 1
            rows.append(record)
        # The reverse direction: listed replay candidates production did not list.
        prod_tickers = set(prod_candidates.get(scan_id, {}))
        for candidate in replay["candidates"]:
            ticker = str(candidate["ticker"]).upper()
            if ticker in prod_tickers or str(candidate.get("exchange") or "").upper() == "OTC":
                continue
            extra_rows.append({
                "as_of": as_of.isoformat(), "session": run["session"], "minute": minute, "ticker": ticker,
                "replay_change": candidate.get("change"), "replay_relvol": candidate.get("relvol"),
                "replay_price": candidate.get("price"), "asset_type": candidate.get("asset_type"),
            })
            if candidate.get("change") is not None:
                extra_values["change"].append(float(candidate["change"]))
            if candidate.get("relvol") is not None:
                extra_values["relvol"].append(float(candidate["relvol"]))

    fields = ["as_of", "session", "minute", "ticker", "reason", "prod_price", "prod_change", "prod_relvol",
              "prod_asset_type", "replay_price", "replay_change", "replay_relvol", "replay_cumulative_volume"]
    with open(args.out / "misses.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    with open(args.out / "replay_only.csv", "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["as_of", "session", "minute", "ticker", "replay_change",
                                                    "replay_relvol", "replay_price", "asset_type"])
        writer.writeheader()
        writer.writerows(extra_rows)
    distinct_by_reason = {reason: len({r["ticker"] for r in rows if r["reason"] == reason}) for reason in reasons}
    summary = {
        "variant": args.variant,
        "exclude_current_leveraged": args.exclude_current_leveraged,
        "replay_days": sorted(replay_days),
        "matched_scans": scans,
        "production_listed_missing_in_replay": len(rows),
        "by_reason": dict(reasons.most_common()),
        "distinct_tickers_by_reason": distinct_by_reason,
        "by_session": {session: dict(counter.most_common()) for session, counter in sorted(reasons_by_session.items())},
        "value_gaps": {reason: {name: quantiles(values) for name, values in inner.items()} for reason, inner in gaps.items()},
        "top_missing_tickers": [
            {"ticker": ticker, "count": count, "reasons": dict(Counter(r["reason"] for r in rows if r["ticker"] == ticker))}
            for ticker, count in Counter(r["ticker"] for r in rows).most_common(20)
        ],
        "replay_listed_not_in_production": {
            "rows": len(extra_rows), "distinct_tickers": len({r["ticker"] for r in extra_rows}),
            "change": quantiles(extra_values["change"]), "relvol": quantiles(extra_values["relvol"]),
            "top_tickers": [t for t, _ in Counter(r["ticker"] for r in extra_rows).most_common(15)],
        },
    }
    (args.out / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps(summary, indent=1, default=str))
    print("DONE discovery_misses")


if __name__ == "__main__":
    main()
