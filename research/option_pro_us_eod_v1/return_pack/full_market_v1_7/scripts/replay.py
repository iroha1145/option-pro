"""Replay the production all-market screener with the v1.7 candidate switches on.

Every replay date T runs the production path exactly as ``full_market_v1_6/scripts/replay.py``
did: universe selection on the point-in-time directory of T's week, ``_required_sessions``,
``_session_manifest``, ``_load_panel``, ``prepare_limited_panel``, ``precompute_all_horizon_inputs``,
``score_eod_session``, ``_compact_variant`` and ``project_strength_payload``; lists are then filtered
and ordered like ``/api/strength/scan`` (price >= $5, sort_score desc, ticker).

Candidates are the production switches, not re-implementations (see PREREGISTRATION.md):

* ``industry`` - ``ScoringOptions(industry_mode="g_only" | "full")`` with SIC tags from the frozen
  table (``--industry-table``) joined to the directory of T's week by (ticker, CIK);
* ``tilt`` - ``load_market_registry(extra_tilt_multipliers=...)`` on balanced and aggressive;
* ``conservative`` - ``conservative_v17_policy`` plus the conservative registry tilt of ``live_config``;
* ``fund_scope`` - ``select_all_market_universe(fund_scope="benchmarks")``;
* ``residual`` - the family-D residual window swap of v1.6 stage 2 (506-session panel), which
  now honours the industry tags of the variant it is combined with;
* ``live`` - production as configured in ``live_config.LIVE_CONFIG`` (registry tilts and
  scoring options come from that object); cannot be composed with other parts.

Variants are composed with ``+`` (``g3+cons17``, ``full3+d12m1``): specs merge, profiles unite.
Precomputed inputs are shared by every variant with the same panel and industry tagging, and
each distinct input set gets its own cache; input sets are processed one after another so a
worker holds one at a time. One gzipped JSON per date; an interrupted run resumes.

    python replay.py --db /content/data/replay.sqlite \\
        --directory /content/data/massive_directory_2026-09-27 \\
        --industry-table /content/data/industry/ticker_sic.json.gz \\
        --out /content/replay/v17_stage1 --start 2023-03-17 --end 2026-09-18 --every 5 \\
        --variants v16,g3,g3x2,g4,full3,full4,cons17,nofund --workers 40
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import multiprocessing as mp
import sqlite3
import sys
import time
from collections import Counter
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
ALL_PROFILES = ("conservative", "balanced", "aggressive")
TILTED_PROFILES = ("balanced", "aggressive")
LONG_HISTORY = 506  # 12-1 residual: 231 summed sessions, 21 skipped, 252-session fit, one return lag
DIAGNOSTIC_SIC_LEVEL = 3  # the industry_id recorded on every listed row, whatever the variant uses

# Base specs. ``profiles`` are the views a variant scores; everything else is a production switch.
VARIANTS: dict[str, dict] = {
    "v16": {"profiles": ALL_PROFILES},
    "g3": {"industry": "g_only", "sic_level": 3, "profiles": TILTED_PROFILES},
    "g3x2": {"industry": "g_only", "sic_level": 3, "tilt": {"G": 2.0}, "profiles": TILTED_PROFILES},
    "g4": {"industry": "g_only", "sic_level": 4, "profiles": TILTED_PROFILES},
    "full3": {"industry": "full", "sic_level": 3, "profiles": ALL_PROFILES},
    "full4": {"industry": "full", "sic_level": 4, "profiles": ALL_PROFILES},
    "cons17": {"conservative": 2.0, "profiles": ("conservative",)},
    # V2 (review of 2026-09-28): cons17 with the v1.4 conservative ATR multiple, to separate
    # how much of the cons17 gain comes from relaxing the volatility gate. One value, no sweep.
    "cons17_atr125": {"conservative": 1.25, "profiles": ("conservative",)},
    "nofund": {"fund_scope": "benchmarks", "profiles": ALL_PROFILES},
    "d12m1": {"residual": (251, 21), "profiles": TILTED_PROFILES},
    # Production as configured in live_config.LIVE_CONFIG: registry tilts and scoring
    # options come from that object, so the adopted set can be replayed and compared
    # with the explicit candidate it was evaluated as (compare_records --map).
    "live": {"live": True, "profiles": ALL_PROFILES},
}

_state: dict = {}


def live_spec() -> dict:
    """The switches ``LIVE_CONFIG`` implies, in the shape of a base spec (for input sharing)."""
    from app.services.eod_limited.live_config import CONSERVATIVE_V17, LIVE_CONFIG

    spec: dict = {"live": True, "profiles": ALL_PROFILES}
    if LIVE_CONFIG.wants_industry:
        spec.update(industry=LIVE_CONFIG.industry_mode, sic_level=LIVE_CONFIG.sic_level)
    if LIVE_CONFIG.g_tilt is not None:
        spec["tilt"] = {"G": LIVE_CONFIG.g_tilt}
    if LIVE_CONFIG.conservative_policy == CONSERVATIVE_V17:
        spec["conservative"] = LIVE_CONFIG.conservative_atr_multiplier
    if LIVE_CONFIG.fund_scope != "all":
        spec["fund_scope"] = LIVE_CONFIG.fund_scope
    return spec


def compose(name: str) -> dict:
    """Merge ``a+b+c`` base specs; a switch may be set by one part only."""
    if name == "live":
        return live_spec()
    if "live" in name.split("+"):
        raise ValueError("live is production as configured and cannot be composed with other parts")
    spec: dict = {"profiles": ()}
    for part in name.split("+"):
        if part not in VARIANTS:
            raise ValueError(f"unknown variant part {part!r}; base variants: {sorted(VARIANTS)}")
        for key, value in VARIANTS[part].items():
            if key == "profiles":
                spec["profiles"] = tuple(dict.fromkeys([*spec["profiles"], *value]))
            elif key in spec and spec[key] != value:
                raise ValueError(f"{name}: {key} set twice ({spec[key]!r} and {value!r})")
            else:
                spec[key] = value
    spec["profiles"] = tuple(profile for profile in ALL_PROFILES if profile in spec["profiles"])
    return spec


def input_key(spec: dict) -> tuple:
    """Variants with the same key share one precompute."""
    return (spec.get("industry") if spec.get("industry") == "full" else None,
            spec.get("sic_level") if spec.get("industry") == "full" else None,
            spec.get("fund_scope", "all"))


def registry_for(spec: dict) -> dict:
    from app.services.eod_limited.live_config import CONSERVATIVE_V17_TILT_MULTIPLIERS, LIVE_CONFIG
    from app.services.eod_limited.market_registry import load_market_registry

    if spec.get("live"):
        return load_market_registry(extra_tilt_multipliers=LIVE_CONFIG.tilt_multipliers())
    tilts: dict[str, dict[str, float]] = {}
    for factor, multiplier in (spec.get("tilt") or {}).items():
        for profile in TILTED_PROFILES:
            tilts.setdefault(profile, {})[factor] = multiplier
    if spec.get("conservative") is not None:
        tilts["conservative"] = dict(CONSERVATIVE_V17_TILT_MULTIPLIERS)
    return load_market_registry(extra_tilt_multipliers=tilts or None)


def options_for(name: str, spec: dict, tags_by_level: dict[int, dict]):
    from app.services.eod_limited.full_market_tuning import DEFAULT_POLICY, conservative_v17_policy
    from app.services.eod_limited.live_config import LIVE_CONFIG
    from app.services.eod_limited.options import ScoringOptions

    if spec.get("live"):
        tags = tags_by_level[LIVE_CONFIG.sic_level] if LIVE_CONFIG.wants_industry else None
        return LIVE_CONFIG.scoring_options(tags)
    policy = DEFAULT_POLICY if spec.get("conservative") is None else conservative_v17_policy(spec["conservative"])
    mode = spec.get("industry") or "off"
    industry = tags_by_level[spec["sic_level"]] if mode != "off" else {}
    if mode == "off" and policy is DEFAULT_POLICY:
        return None
    return ScoringOptions(industry_mode=mode, industry=industry, tuning=policy, label=name)


def directory_labels(folder: Path) -> list[str]:
    return sorted(path.name[:-8] for path in folder.glob("20*.json.gz"))


def init_worker(db: str, directory: str, directory_mode: str, industry_table: str | None, variants: list[str]) -> None:
    from app.services.eod_limited.industry import SicTable

    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    specs = {name: compose(name) for name in variants}
    _state.update(
        connection=connection,
        directory=Path(directory),
        directory_mode=directory_mode,
        labels=directory_labels(Path(directory)),
        specs=specs,
        registries={name: registry_for(spec) for name, spec in specs.items()},
        table=SicTable.load(industry_table) if industry_table else SicTable(),
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


def long_window_residuals(connection, directory: list[dict], session: date, sum_window: tuple[int, int],
                          securities, *, fund_scope: str, industry) -> dict | None:
    """Family-D residuals from a LONG_HISTORY panel; None when the window predates the freeze.

    ``industry`` (a tag mapping or None) makes the long panel carry the same industries as the
    variant's inputs, so the 12-1 residual is two-factor exactly when the variant is ``full``.
    """
    from app.services.eod_limited import market_data as md
    from app.services.eod_limited.panel import prepare_limited_panel
    from app.services.eod_limited.universe import select_all_market_universe
    from app.services.market_calendar import prior_trading_sessions
    from app.services.research_eod_v1.residual import ResidualMomentum, residual_raw_momentum

    sessions = [*prior_trading_sessions(session, LONG_HISTORY - 1), session]
    if sessions[0] < FREEZE_START:
        return None
    md._session_manifest(connection, sessions)
    members, coverage = select_all_market_universe(directory, fund_scope=fund_scope)
    long_panel = prepare_limited_panel(md._load_panel(connection, members, coverage, sessions)[0], industry=industry)
    market = long_panel.get("SPY")
    sum_start, sum_end = sum_window
    basket_cache: dict = {}
    out = {}
    for sid in securities:
        series = long_panel.get(sid)
        if series is None:
            out[sid] = ResidualMomentum(None, "LONG_WINDOW_UNAVAILABLE")
            continue
        out[sid] = residual_raw_momentum(series, market or series, long_panel, spy_residual_allowed=True,
                                         sum_start=sum_start, sum_end=sum_end,
                                         history_min=sum_start + 252 + 2, basket_cache=basket_cache)
    return out


def with_residuals(inputs: dict, residuals: dict) -> dict:
    from dataclasses import replace

    def swap(values: dict) -> dict:
        return {sid: replace(raw, residual=residuals[sid]) if sid in residuals else raw
                for sid, raw in values.items()}

    return {horizon: (swap(raws), clipped, {theme: swap(values) for theme, values in themed.items()})
            for horizon, (raws, clipped, themed) in inputs.items()}


def replay_date(job: tuple[str, str]) -> dict:
    from app.services.eod_limited import LIST_KIND_OBSERVATION, PURPOSE_LIVE
    from app.services.eod_limited import market_data as md
    from app.services.eod_limited.inference import precompute_all_horizon_inputs, score_eod_session
    from app.services.eod_limited.options import ScoringOptions
    from app.services.eod_limited.panel import prepare_limited_panel
    from app.services.eod_limited.project import project_strength_payload
    from app.services.eod_limited.universe import select_all_market_universe
    from app.services.eod_limited.worker import _compact_variant
    from app.services.research_eod_v1.constants import HORIZONS

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
        specs = _state["specs"]
        levels = sorted({spec["sic_level"] for spec in specs.values() if spec.get("industry")} | {DIAGNOSTIC_SIC_LEVEL})
        tags_by_level = {level: _state["table"].classify(directory, level=level, tickers=panel) for level in levels}
        diagnostic_tags = tags_by_level[DIAGNOSTIC_SIC_LEVEL]
        record["industry"] = {f"sic{level}": {"classified": len(tags), "groups": len({t.industry_id for t in tags.values()})}
                              for level, tags in tags_by_level.items()}
        lists: dict = {}
        record["unavailable_variants"] = {}
        record["variant_options"] = {}
        groups: dict[tuple, list[str]] = {}
        for name, spec in specs.items():
            groups.setdefault(input_key(spec), []).append(name)
        for (full_mode, level, fund_scope), names in groups.items():
            scope_panel = panel
            if fund_scope != "all":
                scoped, _ = select_all_market_universe(directory, fund_scope=fund_scope)
                scope_panel = {sid: series for sid, series in panel.items() if sid in scoped}
            precompute_options = None
            if full_mode:
                precompute_options = ScoringOptions(industry_mode="full", industry=tags_by_level[level],
                                                    label=f"precompute:full:sic{level}")
            inputs = precompute_all_horizon_inputs(
                scope_panel, session, registry=_state["registries"][names[0]], horizons=list(HORIZONS),
                geometry_workers=1, **({} if precompute_options is None else {"options": precompute_options}))
            clock[f"precomputed:{full_mode or 'base'}:{level or ''}:{fund_scope}"] = time.perf_counter()
            for name in names:
                spec = specs[name]
                options = options_for(name, spec, tags_by_level)
                record["variant_options"][name] = None if options is None else options.describe()
                variant_inputs = inputs
                if spec.get("residual"):
                    residuals = long_window_residuals(
                        _state["connection"], directory, session, tuple(spec["residual"]), list(scope_panel),
                        fund_scope=fund_scope, industry=None if options is None else options.panel_industry())
                    if residuals is None:
                        record["unavailable_variants"][name] = "long_window_before_freeze"
                        continue
                    record.setdefault("residual_status", {})[name] = dict(
                        Counter(value.status for value in residuals.values()))
                    variant_inputs = with_residuals(inputs, residuals)
                cache: dict = {}
                extra = {} if options is None else {"options": options}
                for horizon in HORIZONS:
                    raws, clipped, themed = variant_inputs[horizon]
                    for profile in spec["profiles"]:
                        scored = score_eod_session(
                            scope_panel, session, registry=_state["registries"][name], profile=profile,
                            horizon=horizon, purpose=PURPOSE_LIVE, precomputed_raws=raws, clipped_panel=clipped,
                            precomputed_theme_raws=themed, compact=True, snapshot_cache=cache, **extra)
                        if int(scored.get("scored_security_count") or 0) != len(scope_panel):
                            raise RuntimeError(f"{session}: {name} scored {scored.get('scored_security_count')} "
                                               f"of {len(scope_panel)}")
                        payload = project_strength_payload(_compact_variant(scored),
                                                           parameters={"profile": profile, "timeframe": horizon},
                                                           list_kind=LIST_KIND_OBSERVATION)
                        rows = [row for row in payload.get("observation_rows") or []
                                if not row.get("price_unknown") and float(row.get("price") or 0) >= MIN_PRICE]
                        rows.sort(key=lambda row: (-float(row["sort_score"]) if row.get("sort_score") is not None
                                                   else math.inf, str(row.get("ticker") or "")))
                        kept = []
                        for row in rows[:KEEP_ROWS]:
                            sources = by_upper.get(str(row.get("ticker") or ""), [])
                            tag = diagnostic_tags.get(sources[0]) if len(sources) == 1 else None
                            kept.append({**compact_row(row), "source_tickers": sources,
                                         "industry_id": None if tag is None else tag.industry_id})
                        lists[f"{name}/{profile}/{horizon}"] = {"n": len(rows), "watch_n": payload.get("watch_n"),
                                                                 "rows": kept}
                clock[f"scored_{name}"] = time.perf_counter()
                variant_inputs = raws = clipped = themed = None
            del inputs
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
    parser.add_argument("--industry-table", help="frozen SIC table (ticker, cik, sic_code); required for industry variants")
    parser.add_argument("--out", required=True)
    parser.add_argument("--dates", help="comma-separated sessions; overrides --start/--end/--every")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--every", type=int, default=5)
    parser.add_argument("--directory-mode", choices=("pit", "current"), default="pit")
    parser.add_argument("--variants", default="v16")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()

    variants = [name.strip() for name in args.variants.split(",") if name.strip()]
    specs = {name: compose(name) for name in variants}
    if "v16" not in variants:
        raise SystemExit("variants must include the v16 baseline")
    if any(spec.get("industry") for spec in specs.values()) and not args.industry_table:
        raise SystemExit("industry variants need --industry-table")
    if args.dates:
        dates = [value.strip() for value in args.dates.split(",") if value.strip()]
    else:
        dates = replay_dates(date.fromisoformat(args.start), date.fromisoformat(args.end), args.every)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    live_config = None
    if "live" in specs:
        from app.services.eod_limited.live_config import LIVE_CONFIG

        live_config = LIVE_CONFIG.describe()
    (out / "run.json").write_text(json.dumps({
        "variants": {name: {**spec, "profiles": list(spec["profiles"])} for name, spec in specs.items()},
        "live_config": live_config,
        "dates": dates, "directory_mode": args.directory_mode, "db": args.db, "industry_table": args.industry_table,
        "min_price": MIN_PRICE, "keep_rows": KEEP_ROWS, "diagnostic_sic_level": DIAGNOSTIC_SIC_LEVEL,
    }, indent=1, default=list))
    print(f"replaying {len(dates)} dates {dates[0]}..{dates[-1]} variants {variants} workers {args.workers}",
          flush=True)
    started = time.time()
    context = mp.get_context("spawn")
    with context.Pool(args.workers, initializer=init_worker,
                      initargs=(args.db, args.directory, args.directory_mode, args.industry_table, variants)) as pool:
        for done, result in enumerate(pool.imap_unordered(replay_date, [(day, str(out)) for day in dates]), 1):
            print(f"{done}/{len(dates)} {json.dumps(result)} total {time.time() - started:.0f}s", flush=True)
    print("DONE replay", flush=True)


if __name__ == "__main__":
    main()
