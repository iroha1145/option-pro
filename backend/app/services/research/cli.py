"""Offline research CLI. Fetch, replay, and report stay in separate commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.research.dataset import load_dataset, research_universe
from app.services.research.labels import attach_screener_labels, last_usable_signal_date
from app.services.research.metrics import date_clustered_mean, spearman_rank_ic, summarize_daily_ics, top_k_mean
from app.services.research.portfolio import simulate_long_only
from app.services.research.protocol import (
    FROZEN_PROTOCOL,
    FROZEN_SPLITS,
    PRIMARY_HORIZON,
    PRIMARY_TOP_K,
    SplitName,
    assert_split_access,
    iter_split_dates,
    protocol_hash,
)
from app.services.research.radar import reconstruct_daily_base_events

_RADAR_JOB: dict[str, Any] = {}


def _init_radar_job(state: dict[str, Any]) -> None:
    _RADAR_JOB.clear()
    _RADAR_JOB.update(state)


def _radar_day_job(session_iso: str) -> tuple[str, dict[str, Any]]:
    from datetime import date

    session = date.fromisoformat(session_iso)
    payload = reconstruct_daily_base_events(
        _RADAR_JOB["dataset"],
        session,
        allow_sealed=_RADAR_JOB["allow_sealed"],
        frames=_RADAR_JOB["frames"],
    )
    return session_iso, payload
from app.services.research.registry import append_trial
from app.services.research.screener import momentum_baseline_ranks, replay_screener_day

_SCREENER_JOB: dict[str, Any] = {}


def _init_screener_job(state: dict[str, Any]) -> None:
    _SCREENER_JOB.clear()
    _SCREENER_JOB.update(state)


def _screener_day_job(session_iso: str) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    from datetime import date

    session = date.fromisoformat(session_iso)
    payload = replay_screener_day(
        _SCREENER_JOB["dataset"],
        session,
        parameters=_SCREENER_JOB["parameters"],
        allow_sealed=_SCREENER_JOB["allow_sealed"],
        panel=_SCREENER_JOB["panel"],
    )
    labeled = attach_screener_labels(
        payload.get("view_rows") or payload.get("rows") or [],
        _SCREENER_JOB["dataset"],
        signal_date=session,
        allow_sealed=_SCREENER_JOB["allow_sealed"],
    )
    return session_iso, payload, labeled


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def cmd_protocol(_args: argparse.Namespace) -> int:
    print(json.dumps(FROZEN_PROTOCOL, indent=2, ensure_ascii=True))
    return 0


def cmd_audit_data(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    coverage = dataset.coverage()
    _write_json(Path(args.out), coverage)
    print(json.dumps({"wrote": args.out, "tickers": coverage["manifest"]["ticker_count"], "bars": coverage["manifest"]["bar_count"]}, ensure_ascii=True))
    return 0


def cmd_screener_replay(args: argparse.Namespace) -> int:
    split: SplitName = args.split
    allow_sealed = bool(args.unblind_sealed)
    if split == "sealed":
        assert_split_access(FROZEN_PROTOCOL["splits"]["sealed"]["start"], allow_sealed=allow_sealed)
    dataset = load_dataset(args.dataset)
    dates = iter_split_dates(split, allow_sealed=allow_sealed)
    last = last_usable_signal_date(split, PRIMARY_HORIZON, embargo_sessions=1)
    dates = [day for day in dates if day <= last]
    if args.step > 1:
        dates = dates[:: args.step]
    if args.limit:
        dates = dates[: args.limit]
    panel_through = (
        FROZEN_SPLITS["sealed"]["end"]
        if split == "sealed" and allow_sealed
        else FROZEN_SPLITS["validation"]["end"]
    )
    panel = dataset.adjusted_panel(through=panel_through, allow_sealed=allow_sealed)
    parameters = {
        "timeframe": args.timeframe,
        "profile": args.profile,
        "top": args.top,
    }

    daily: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    workers = max(1, int(args.workers))
    _SCREENER_JOB.update(
        {
            "dataset": dataset,
            "panel": panel,
            "parameters": parameters,
            "allow_sealed": allow_sealed,
        }
    )
    print(
        f"screener-replay starting {len(dates)} days workers={workers}",
        flush=True,
    )
    session_keys = [day.isoformat() for day in dates]
    if workers == 1:
        sequenced = []
        for index, key in enumerate(session_keys, start=1):
            item = _screener_day_job(key)
            sequenced.append(item)
            print(f"screener-replay {index}/{len(dates)} {key}", flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        ordered = {key: None for key in session_keys}
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_screener_job,
            initargs=(dict(_SCREENER_JOB),),
        ) as pool:
            futures = {pool.submit(_screener_day_job, key): key for key in session_keys}
            done = 0
            for future in as_completed(futures):
                session_iso, payload, labeled = future.result()
                ordered[session_iso] = (session_iso, payload, labeled)
                done += 1
                print(f"screener-replay {done}/{len(dates)} {session_iso}", flush=True)
        sequenced = [item for item in ordered.values() if item is not None]
    for session_iso, payload, labeled in sequenced:
        ic = spearman_rank_ic(
            [row.get("ranking_score") for row in labeled],
            [
                ((row.get("excess") or {}).get(str(PRIMARY_HORIZON)) or {}).get("excess_vs_universe")
                for row in labeled
            ],
        )
        tops = {
            str(k): top_k_mean(
                labeled,
                score_key="ranking_score",
                outcome_key=("excess", str(PRIMARY_HORIZON), "excess_vs_universe"),
                k=k,
            )
            for k in PRIMARY_TOP_K
        }
        daily.append(
            {
                "signal_date": session_iso,
                "screened_count": payload.get("screened_count"),
                "ic": ic,
                "top_k": tops,
                "as_of": payload.get("as_of"),
                "universe_version": payload.get("universe_version"),
            }
        )
        for row in labeled:
            event_rows.append(
                {
                    "signal_date": session_iso,
                    "ticker": row.get("ticker"),
                    "selected_view_rank": row.get("selected_view_rank"),
                    "ranking_score": row.get("ranking_score"),
                    "intrinsic_score": row.get("intrinsic_score"),
                    "return_63d": row.get("return_63d"),
                    "labels": row.get("labels"),
                    "excess": row.get("excess"),
                }
            )
    summary = {
        "status": "active",
        "split": split,
        "step": args.step,
        "limit": args.limit,
        "protocol_hash": protocol_hash(),
        "ic_summary": summarize_daily_ics(item["ic"] for item in daily),
        "days": daily,
        "notes": [
            "这是开发区/验证区点时重建，不是当年真实发布快照。",
            "封存区未包含在内，除非显式 --unblind-sealed。",
            "Rank IC 与股票池均值使用当日全部合格 view_rows，不是 Top-N 截断样本。",
        ],
    }
    out = Path(args.out)
    _write_json(out, summary)
    _write_json(out.with_name(out.stem + "-rows.json"), event_rows)
    append_trial(
        Path(args.registry),
        {
            "trial_id": args.trial_id,
            "layer": "original",
            "family": "screener",
            "split": split,
            "command": "screener-replay",
            "output": str(out),
            "day_count": len(daily),
        },
    )
    print(json.dumps({"wrote": str(out), "days": len(daily)}, ensure_ascii=True))
    return 0


def cmd_radar_replay(args: argparse.Namespace) -> int:
    split: SplitName = args.split
    allow_sealed = bool(args.unblind_sealed)
    dataset = load_dataset(args.dataset)
    dates = iter_split_dates(split, allow_sealed=allow_sealed)
    last = last_usable_signal_date(split, PRIMARY_HORIZON, embargo_sessions=1)
    dates = [day for day in dates if day <= last]
    if args.step > 1:
        dates = dates[:: args.step]
    if args.limit:
        dates = dates[: args.limit]
    events: list[dict[str, Any]] = []
    from app.services.strength.scanner import _theme_universe

    symbols, _meta = _theme_universe()
    panel_through = (
        FROZEN_SPLITS["sealed"]["end"]
        if split == "sealed" and allow_sealed
        else FROZEN_SPLITS["validation"]["end"]
    )
    frames = {
        ticker: dataset.frame(ticker, through=panel_through, allow_sealed=allow_sealed)
        for ticker in symbols
    }
    workers = max(1, int(getattr(args, "workers", 1)))
    _RADAR_JOB.update(
        {"dataset": dataset, "allow_sealed": allow_sealed, "frames": frames}
    )
    print(f"radar-replay starting {len(dates)} days workers={workers}", flush=True)
    session_keys = [day.isoformat() for day in dates]
    if workers == 1:
        day_payloads = [_radar_day_job(key) for key in session_keys]
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        ordered = {key: None for key in session_keys}
        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_radar_job,
            initargs=(dict(_RADAR_JOB),),
        ) as pool:
            futures = {pool.submit(_radar_day_job, key): key for key in session_keys}
            done = 0
            for future in as_completed(futures):
                session_iso, payload = future.result()
                ordered[session_iso] = (session_iso, payload)
                done += 1
                print(f"radar-replay {done}/{len(dates)} {session_iso}", flush=True)
        day_payloads = [item for item in ordered.values() if item is not None]
    for _session_iso, payload in day_payloads:
        events.extend(payload.get("events") or [])
    first_hits: dict[tuple[str, str], dict[str, Any]] = {}
    duplicate_triggers = 0
    for event in events:
        key = (str(event.get("ticker")), str(event.get("pivot_id")))
        if key in first_hits:
            duplicate_triggers += 1
            continue
        first_hits[key] = event
    events = list(first_hits.values())
    pairs = []
    labeled_events = []
    from app.services.research.labels import forward_close_return

    for event in events:
        label = forward_close_return(
            dataset,
            str(event["ticker"]),
            parse_date(event["trading_date"]),
            PRIMARY_HORIZON,
            allow_sealed_prices=allow_sealed,
        )
        spy = forward_close_return(
            dataset,
            "SPY",
            parse_date(event["trading_date"]),
            PRIMARY_HORIZON,
            allow_sealed_prices=allow_sealed,
        )
        raw = label.get("forward_return")
        bench = spy.get("forward_return")
        excess = None if raw is None or bench is None else raw - bench
        item = {**event, "label_20d": label, "spy_20d": spy, "excess_vs_spy_20d": excess}
        labeled_events.append(item)
        if excess is not None:
            pairs.append((event["trading_date"], excess))
    summary = {
        "status": "active",
        "split": split,
        "protocol_hash": protocol_hash(),
        "event_count": len(labeled_events),
        "duplicate_triggers_dropped": duplicate_triggers,
        "clustered_excess_vs_spy": date_clustered_mean(pairs),
        "unverifiable": FROZEN_PROTOCOL["unverifiable_without_intraday"],
        "notes": [
            "日线平台重建，无 Discovery、无盘中确认。",
            "同一 ticker+pivot_id 只保留首次触发，后续续扫不重复计成功。",
        ],
    }
    out = Path(args.out)
    _write_json(out, summary)
    _write_json(out.with_name(out.stem + "-events.json"), labeled_events)
    append_trial(
        Path(args.registry),
        {
            "trial_id": args.trial_id,
            "layer": "original",
            "family": "radar",
            "split": split,
            "command": "radar-replay",
            "output": str(out),
            "event_count": len(labeled_events),
        },
    )
    print(json.dumps({"wrote": str(out), "events": len(labeled_events)}, ensure_ascii=True))
    return 0


def parse_date(value: str):
    from datetime import date

    return date.fromisoformat(value)


def cmd_portfolio(args: argparse.Namespace) -> int:
    dataset = load_dataset(args.dataset)
    rows = json.loads(Path(args.signals).read_text(encoding="utf-8"))
    result = simulate_long_only(
        dataset,
        rows,
        cost_bps=args.cost_bps,
        hold_days=args.hold_days,
    )
    _write_json(Path(args.out), result)
    append_trial(
        Path(args.registry),
        {
            "trial_id": args.trial_id,
            "layer": "original",
            "family": "portfolio",
            "command": "portfolio",
            "output": args.out,
            "cost_bps": args.cost_bps,
            "trade_count": result.get("trade_count"),
        },
    )
    print(json.dumps({"wrote": args.out, "final_equity": result.get("final_equity")}, ensure_ascii=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.services.research.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("protocol").set_defaults(func=cmd_protocol)

    audit = sub.add_parser("audit-data")
    audit.add_argument("--dataset", required=True)
    audit.add_argument("--out", required=True)
    audit.set_defaults(func=cmd_audit_data)

    screener = sub.add_parser("screener-replay")
    screener.add_argument("--dataset", required=True)
    screener.add_argument("--out", required=True)
    screener.add_argument("--registry", required=True)
    screener.add_argument("--split", default="development", choices=["warmup", "development", "validation", "sealed"])
    screener.add_argument("--timeframe", default="all")
    screener.add_argument("--profile", default="balanced")
    screener.add_argument("--top", type=int, default=20)
    screener.add_argument("--step", type=int, default=1)
    screener.add_argument("--limit", type=int, default=0)
    screener.add_argument("--trial-id", default="original-screener-balanced-all")
    screener.add_argument("--unblind-sealed", action="store_true")
    screener.add_argument("--workers", type=int, default=1)
    screener.set_defaults(func=cmd_screener_replay)

    radar = sub.add_parser("radar-replay")
    radar.add_argument("--dataset", required=True)
    radar.add_argument("--out", required=True)
    radar.add_argument("--registry", required=True)
    radar.add_argument("--split", default="development", choices=["warmup", "development", "validation", "sealed"])
    radar.add_argument("--step", type=int, default=1)
    radar.add_argument("--limit", type=int, default=0)
    radar.add_argument("--trial-id", default="original-radar-daily-base-theme-universe")
    radar.add_argument("--unblind-sealed", action="store_true")
    radar.add_argument("--workers", type=int, default=1)
    radar.set_defaults(func=cmd_radar_replay)

    portfolio = sub.add_parser("portfolio")
    portfolio.add_argument("--dataset", required=True)
    portfolio.add_argument("--signals", required=True)
    portfolio.add_argument("--out", required=True)
    portfolio.add_argument("--registry", required=True)
    portfolio.add_argument("--cost-bps", type=float, default=10.0)
    portfolio.add_argument("--hold-days", type=int, default=20)
    portfolio.add_argument("--trial-id", default="original-portfolio-10bps")
    portfolio.set_defaults(func=cmd_portfolio)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
