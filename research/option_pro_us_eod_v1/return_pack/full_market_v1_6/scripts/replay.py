"""Replay the production all-market screener on historical sessions from the frozen cache.

Every replay date T goes through the production path the worker runs for its nine
views: universe selection on the point-in-time directory of T's week, the 370-session
window ending at T (``_required_sessions``), ``_session_manifest``, ``_load_panel``
(splits executed inside that window only), ``prepare_limited_panel``,
``precompute_all_horizon_inputs``, ``score_eod_session`` with the v1.5 tuning hooks,
``_compact_variant`` and ``project_strength_payload``; the list is then filtered and
ordered the way ``/api/strength/scan`` serves it (price >= $5, sort_score desc, ticker).

Variants only swap the registry profile tilts (see PREREGISTRATION.md). They reuse T's
precomputed inputs, which do not read the tilts, and each gets its own snapshot cache.
One gzipped JSON per date is written, so an interrupted run resumes where it stopped.

    python replay.py --db /content/data/replay.sqlite \
        --directory /content/data/massive_directory_2026-09-27 --out /content/replay/run \
        --start 2023-03-17 --end 2026-06-26 --every 5 --variants v15,tilt_a --workers 7
"""
from __future__ import annotations

import argparse
import copy
import gzip
import json
import math
import multiprocessing as mp
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "backend"))

FREEZE_START = date(2021, 9, 28)
MIN_PRICE = 5.0
KEEP_ROWS = 60
ROW_FIELDS = ("ticker", "name", "price", "sort_score", "algorithm_id", "stock_or_etf_track", "entry_state",
              "status", "rejection_reasons", "factors", "effective_weights", "observation_family_scores",
              "avg_dollar_volume_20d")
FACTOR_INDEX = {"T": 0, "M": 1, "R": 6}
# Multipliers on the balanced and aggressive factor_tilt; conservative stays v1.4 as the control.
VARIANTS = {
    "v15": {},
    "tilt_a": {"T": 0.6, "M": 1.6},
    "tilt_b": {"T": 0.5, "M": 2.0},
    "r0": {"R": 0.0},
    "tilt_a_r0": {"T": 0.6, "M": 1.6, "R": 0.0},
}
TILTED_PROFILES = ("balanced", "aggressive")

_state: dict = {}


def tilted_registry(base: dict, multipliers: dict[str, float]) -> dict:
    registry = copy.deepcopy(base)
    for profile in TILTED_PROFILES:
        tilt = registry["profiles"][profile]["factor_tilt"]
        for factor, multiplier in multipliers.items():
            tilt[FACTOR_INDEX[factor]] = tilt[FACTOR_INDEX[factor]] * multiplier
    return registry


def directory_labels(folder: Path) -> list[str]:
    return sorted(path.name[:-8] for path in folder.glob("20*.json.gz"))


def init_worker(db: str, directory: str, directory_mode: str, variants: list[str]) -> None:
    from app.services.eod_limited.market_registry import load_market_registry

    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    base = load_market_registry()
    _state.update(
        connection=connection,
        directory=Path(directory),
        directory_mode=directory_mode,
        labels=directory_labels(Path(directory)),
        base=base,
        registries={name: tilted_registry(base, VARIANTS[name]) for name in variants},
    )


def load_directory(session: date) -> tuple[list[dict], str]:
    if _state["directory_mode"] == "current":
        label = "current_active"
    else:
        eligible = [label for label in _state["labels"] if label <= session.isoformat()]
        if not eligible:
            raise RuntimeError(f"no directory snapshot on or before {session}")
        label = eligible[-1]
    payload = json.loads(gzip.open(_state["directory"] / f"{label}.json.gz").read())
    return payload["results"], label


def compact_row(row: dict) -> dict:
    return {key: row.get(key) for key in ROW_FIELDS}


def replay_date(job: tuple[str, str]) -> dict:
    from app.services.eod_limited import LIST_KIND_OBSERVATION, PURPOSE_LIVE
    from app.services.eod_limited import market_data as md
    from app.services.eod_limited.inference import precompute_all_horizon_inputs, score_eod_session
    from app.services.eod_limited.panel import prepare_limited_panel
    from app.services.eod_limited.project import project_strength_payload
    from app.services.eod_limited.universe import select_all_market_universe
    from app.services.eod_limited.worker import _compact_variant
    from app.services.research_eod_v1.constants import HORIZONS, PROFILES

    session_text, out_dir = job
    target = Path(out_dir) / f"{session_text}.json.gz"
    if target.exists():
        return {"session": session_text, "status": "cached"}
    session = date.fromisoformat(session_text)
    clock = {"start": time.perf_counter()}
    directory, label = load_directory(session)
    members, coverage = select_all_market_universe(directory)
    sessions = md._required_sessions(session)
    record = {"session": session_text, "directory": label, "window_start": sessions[0].isoformat(),
              "eligible": len(members), "directory_rows": len(directory)}
    if sessions[0] < FREEZE_START:
        record["status"] = "window_before_freeze"
    else:
        md._session_manifest(_state["connection"], sessions)
        panel, coverage, missing, short, residual_short = md._load_panel(
            _state["connection"], members, coverage, sessions)
        record.update(complete=len(panel), missing_target=missing, short_history=short,
                      residual_short_history=residual_short, gate_ok=len(panel) >= 0.90 * len(members))
        by_upper: dict[str, list[str]] = {}
        for ticker in panel:
            by_upper.setdefault(ticker.upper(), []).append(ticker)
        record["case_collisions"] = {key: value for key, value in by_upper.items() if len(value) > 1}
        panel = prepare_limited_panel(panel)
        clock["loaded"] = time.perf_counter()
        inputs = precompute_all_horizon_inputs(panel, session, registry=_state["base"],
                                               horizons=list(HORIZONS), geometry_workers=1)
        clock["precomputed"] = time.perf_counter()
        lists = {}
        for name, registry in _state["registries"].items():
            cache: dict = {}
            for horizon in HORIZONS:
                raws, clipped, themed = inputs[horizon]
                for profile in PROFILES:
                    if name != "v15" and profile not in TILTED_PROFILES:
                        continue
                    scored = score_eod_session(
                        panel, session, registry=registry, profile=profile, horizon=horizon,
                        purpose=PURPOSE_LIVE, precomputed_raws=raws, clipped_panel=clipped,
                        precomputed_theme_raws=themed, compact=True, snapshot_cache=cache)
                    if int(scored.get("scored_security_count") or 0) != len(panel):
                        raise RuntimeError(f"{session}: scored {scored.get('scored_security_count')} of {len(panel)}")
                    payload = project_strength_payload(_compact_variant(scored),
                                                       parameters={"profile": profile, "timeframe": horizon},
                                                       list_kind=LIST_KIND_OBSERVATION)
                    rows = [row for row in payload.get("observation_rows") or []
                            if not row.get("price_unknown") and float(row.get("price") or 0) >= MIN_PRICE]
                    rows.sort(key=lambda row: (-float(row["sort_score"]) if row.get("sort_score") is not None
                                               else math.inf, str(row.get("ticker") or "")))
                    for row in rows[:KEEP_ROWS]:
                        row["source_tickers"] = by_upper.get(str(row.get("ticker") or ""), [])
                    lists[f"{name}/{profile}/{horizon}"] = {
                        "n": len(rows), "watch_n": payload.get("watch_n"),
                        "rows": [{**compact_row(row), "source_tickers": row["source_tickers"]}
                                 for row in rows[:KEEP_ROWS]],
                    }
            clock[f"scored_{name}"] = time.perf_counter()
        record.update(status="scored", lists=lists)
    marks = list(clock.items())
    record["timing_s"] = {name: round(value - marks[index - 1][1], 1)
                          for index, (name, value) in enumerate(marks) if index}
    record["elapsed_s"] = round(time.perf_counter() - clock["start"], 1)
    temporary = target.with_suffix(".tmp")
    with gzip.open(temporary, "wt") as handle:
        json.dump(record, handle, sort_keys=True)
    temporary.rename(target)
    return {"session": session_text, "status": record["status"], "elapsed_s": record["elapsed_s"],
            "timing_s": record["timing_s"]}


def replay_dates(start: date, end: date, every: int) -> list[str]:
    from app.services.market_calendar import is_trading_day

    days, day = [], start
    while day <= end:
        if is_trading_day(day):
            days.append(day)
        day = date.fromordinal(day.toordinal() + 1)
    return [day.isoformat() for day in days[::every]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--dates", help="comma-separated sessions; overrides --start/--end/--every")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--every", type=int, default=5)
    parser.add_argument("--directory-mode", choices=("pit", "current"), default="pit")
    parser.add_argument("--variants", default="v15")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    variants = [name.strip() for name in args.variants.split(",") if name.strip()]
    unknown = [name for name in variants if name not in VARIANTS]
    if unknown or "v15" not in variants:
        raise SystemExit(f"variants must include v15 and come from {sorted(VARIANTS)}; got {variants}")
    if args.dates:
        dates = [value.strip() for value in args.dates.split(",") if value.strip()]
    else:
        dates = replay_dates(date.fromisoformat(args.start), date.fromisoformat(args.end), args.every)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.json").write_text(json.dumps({"variants": {name: VARIANTS[name] for name in variants},
                                              "dates": dates, "directory_mode": args.directory_mode,
                                              "db": args.db, "min_price": MIN_PRICE, "keep_rows": KEEP_ROWS},
                                             indent=1))
    print(f"replaying {len(dates)} dates {dates[0]}..{dates[-1]} variants {variants} workers {args.workers}",
          flush=True)
    started = time.time()
    context = mp.get_context("spawn")
    with context.Pool(args.workers, initializer=init_worker,
                      initargs=(args.db, args.directory, args.directory_mode, variants)) as pool:
        for done, result in enumerate(pool.imap_unordered(replay_date, [(day, str(out)) for day in dates]), 1):
            print(f"{done}/{len(dates)} {json.dumps(result)} total {time.time() - started:.0f}s", flush=True)
    print("DONE replay", flush=True)


if __name__ == "__main__":
    main()
