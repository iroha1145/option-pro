"""Offline research CLI. Fetch, replay, and report stay in separate commands."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.research.dataset import load_dataset, research_universe
from app.services.research.labels import attach_event_labels, attach_screener_labels, last_usable_signal_date
from app.services.research.metrics import date_clustered_mean, spearman_rank_ic, summarize_daily_ics, top_k_mean
from app.services.research.portfolio import simulate_long_only
from app.services.research.protocol import (
    DEFAULT_SCAN_PARAMETERS,
    FROZEN_PROTOCOL,
    FROZEN_SPLITS,
    PRIMARY_HORIZON,
    PRIMARY_TOP_K,
    SplitName,
    assert_split_access,
    iter_split_dates,
    protocol_hash,
)
from app.services.research.radar import (
    first_trigger_by_pivot,
    platform_evolution_groups,
    reconstruct_daily_base_events,
    reconstruct_ticker_dates,
)
from app.services.research.replay_store import ReplayStore
from app.services.research.registry import append_trial
from app.services.research.run_identity import RunIdentityError, build_run_identity
from app.services.research.screener import compact_screener_row, replay_screener_day

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


def _radar_ticker_job(ticker: str) -> tuple[str, dict[str, Any]]:
    payload = reconstruct_ticker_dates(
        _RADAR_JOB["dataset"],
        ticker,
        _RADAR_JOB["dates"],
        allow_sealed=_RADAR_JOB["allow_sealed"],
        frame=_RADAR_JOB["frames"].get(ticker),
    )
    return ticker, payload

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
    compact = [
        compact_screener_row(row, signal_date=session, dataset=_SCREENER_JOB["dataset"])
        for row in labeled
    ]
    slim = {
        "screened_count": payload.get("screened_count"),
        "as_of": payload.get("as_of"),
        "universe_version": payload.get("universe_version"),
        "skipped": payload.get("skipped"),
    }
    return session_iso, slim, compact


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
        "min_price": float(getattr(args, "min_price", DEFAULT_SCAN_PARAMETERS["min_price"])),
        "min_avg_dollar_volume": float(
            getattr(
                args,
                "min_avg_dollar_volume",
                DEFAULT_SCAN_PARAMETERS["min_avg_dollar_volume"],
            )
        ),
    }

    out = Path(args.out)
    identity = build_run_identity(
        command="screener-replay",
        dataset=dataset,
        split=split,
        dates=dates,
        step=args.step,
        limit=args.limit,
        allow_sealed=allow_sealed,
        parameters=parameters,
        timeframe=args.timeframe,
        profile=args.profile,
        top=args.top,
        min_price=parameters["min_price"],
        min_avg_dollar_volume=parameters["min_avg_dollar_volume"],
    )
    store = ReplayStore(out)
    resume = bool(getattr(args, "resume", False))
    try:
        if resume:
            store.require_resume(identity)
        else:
            store.initialize(identity)
    except RunIdentityError as exc:
        raise SystemExit(str(exc)) from exc
    done_keys = store.completed_keys()
    pending = [day for day in dates if day.isoformat() not in done_keys]
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
        f"screener-replay starting {len(pending)}/{len(dates)} days "
        f"resume={int(len(done_keys))} workers={workers}",
        flush=True,
    )
    session_keys = [day.isoformat() for day in pending]
    if workers == 1:
        for index, key in enumerate(session_keys, start=1):
            session_iso, payload, labeled = _screener_day_job(key)
            _persist_screener_day(store, session_iso, payload, labeled)
            print(f"screener-replay {index}/{len(session_keys)} {key}", flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_screener_job,
            initargs=(dict(_SCREENER_JOB),),
        ) as pool:
            futures = {pool.submit(_screener_day_job, key): key for key in session_keys}
            done = 0
            for future in as_completed(futures):
                session_iso, payload, labeled = future.result()
                _persist_screener_day(store, session_iso, payload, labeled)
                done += 1
                print(f"screener-replay {done}/{len(session_keys)} {session_iso}", flush=True)
    daily = store.assemble_markers()
    daily.sort(key=lambda item: str(item.get("signal_date") or ""))
    event_rows = store.assemble_rows()
    event_rows = _unique_screener_rows(event_rows)
    summary = {
        "status": "active",
        "split": split,
        "step": args.step,
        "limit": args.limit,
        "protocol_hash": protocol_hash(),
        "run_identity": identity,
        "parameters": parameters,
        "ic_summary": summarize_daily_ics(daily),
        "days": daily,
        "notes": [
            "这是开发区/验证区点时重建，不是当年真实发布快照。",
            "封存区未包含在内，除非显式 --unblind-sealed。",
            "Rank IC 与股票池均值使用当日全部合格 view_rows，不是 Top-N 截断样本。",
            "逐日压缩行含 short/mid/long 与 profile 重算所需字段，可用同一份 dump 报告六种预登记模式。",
            "Top-K 先按事前分数冻结再挂标签；CI 使用期限长度的块 bootstrap，不是普通日期分块 SE。",
        ],
    }
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
            "parameters": parameters,
        },
    )
    print(json.dumps({"wrote": str(out), "days": len(daily)}, ensure_ascii=True))
    return 0


def _unique_screener_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row.get("signal_date") or ""), str(row.get("ticker") or ""))
        merged[key] = row
    return list(merged.values())


def _persist_screener_day(
    store: ReplayStore,
    session_iso: str,
    payload: dict[str, Any],
    labeled: list[dict[str, Any]],
) -> dict[str, Any]:
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
    day = {
        "signal_date": session_iso,
        "screened_count": payload.get("screened_count"),
        "ic": ic,
        "top_k": tops,
        "as_of": payload.get("as_of"),
        "universe_version": payload.get("universe_version"),
        "skipped": payload.get("skipped"),
    }
    store.commit(session_iso, marker=day, rows=labeled)
    return day


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
    out = Path(args.out)
    identity = build_run_identity(
        command="radar-replay",
        dataset=dataset,
        split=split,
        dates=dates,
        step=args.step,
        limit=args.limit,
        allow_sealed=allow_sealed,
        universe=FROZEN_PROTOCOL.get("universe"),
    )
    store = ReplayStore(out)
    resume = bool(getattr(args, "resume", False))
    try:
        if resume:
            store.require_resume(identity)
        else:
            store.initialize(identity)
    except RunIdentityError as exc:
        raise SystemExit(str(exc)) from exc
    done_keys = store.completed_keys()
    pending = [ticker for ticker in symbols if ticker not in done_keys]
    workers = max(1, int(getattr(args, "workers", 1)))
    _RADAR_JOB.update(
        {
            "dataset": dataset,
            "allow_sealed": allow_sealed,
            "frames": frames,
            "dates": dates,
        }
    )
    print(
        f"radar-replay starting {len(pending)}/{len(symbols)} tickers "
        f"{len(dates)} sessions resume={int(len(done_keys))} workers={workers}",
        flush=True,
    )
    if workers == 1:
        for index, ticker in enumerate(pending, start=1):
            name, payload = _radar_ticker_job(ticker)
            _persist_radar_ticker(store, name, payload)
            print(f"radar-replay {index}/{len(pending)} {name}", flush=True)
    else:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        with ProcessPoolExecutor(
            max_workers=workers,
            initializer=_init_radar_job,
            initargs=(dict(_RADAR_JOB),),
        ) as pool:
            futures = {pool.submit(_radar_ticker_job, ticker): ticker for ticker in pending}
            done = 0
            for future in as_completed(futures):
                name, payload = future.result()
                _persist_radar_ticker(store, name, payload)
                done += 1
                print(f"radar-replay {done}/{len(pending)} {name}", flush=True)
    events = store.assemble_rows()
    pivot_dedupe = first_trigger_by_pivot(events)
    events = list(pivot_dedupe["events"])
    labeled_events = attach_event_labels(
        events,
        dataset,
        horizon=PRIMARY_HORIZON,
        universe_tickers=symbols,
        allow_sealed=allow_sealed,
    )
    universe_pairs = [
        (item["trading_date"], item["excess_vs_universe_20d"])
        for item in labeled_events
        if item.get("excess_vs_universe_20d") is not None
    ]
    spy_pairs = [
        (item["trading_date"], item["excess_vs_spy_20d"])
        for item in labeled_events
        if item.get("excess_vs_spy_20d") is not None
    ]
    platforms = platform_evolution_groups(labeled_events)
    summary = {
        "status": "active",
        "split": split,
        "protocol_hash": protocol_hash(),
        "run_identity": identity,
        "event_count": len(labeled_events),
        "duplicate_triggers_dropped": pivot_dedupe["duplicate_triggers_dropped"],
        "triggered_20d_excess_vs_universe": date_clustered_mean(
            universe_pairs, horizon_days=PRIMARY_HORIZON
        ),
        "triggered_20d_excess_vs_spy": date_clustered_mean(
            spy_pairs, horizon_days=PRIMARY_HORIZON
        ),
        "platform_evolution": {
            "evolving_platform_count": platforms["evolving_platform_count"],
            "events_in_evolving_platforms": platforms["events_in_evolving_platforms"],
            "extra_events_if_only_pivot_id_deduped": platforms["extra_events_if_only_pivot_id_deduped"],
            "note": platforms["note"],
        },
        "unverifiable": FROZEN_PROTOCOL["unverifiable_without_intraday"],
        "notes": [
            "日线平台重建，无 Discovery、无盘中确认。Grade C。",
            "主指标是 triggered_20d_excess_vs_universe；相对 SPY 只作补充。",
            "ticker+pivot_id 去重不等于首次经济事件；见 platform_evolution。",
            "hold_bars 是日线收盘计数，不能等同分钟确认。",
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


def _persist_radar_ticker(
    store: ReplayStore,
    ticker: str,
    payload: dict[str, Any],
) -> None:
    events = list(payload.get("events") or [])
    store.commit(
        ticker,
        marker={
            "ticker": ticker,
            "event_count": len(events),
            "skipped": payload.get("skipped"),
        },
        rows=events,
    )


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
    screener.add_argument("--resume", action="store_true")
    screener.add_argument("--min-price", type=float, default=DEFAULT_SCAN_PARAMETERS["min_price"])
    screener.add_argument(
        "--min-avg-dollar-volume",
        type=float,
        default=DEFAULT_SCAN_PARAMETERS["min_avg_dollar_volume"],
    )
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
    radar.add_argument("--resume", action="store_true")
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
