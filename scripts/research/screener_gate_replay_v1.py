"""Restricted-pool replay for frozen gate candidates. Not a production job.

Historical bars stay in a repo-external research directory. This script
does not publish snapshots, change defaults, or unseal 2024-07-01+.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from screener_gate_adapter_v1 import (
    B0_CURRENT,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    VARIANTS,
    ranked_stocks,
    score_contexts,
    variant_rows,
)
from screener_gate_candidates_v1 import HIGH_ATR, EXTENDED

from app.services.eod_limited.bars import DOWNLOAD_PARAMS, _frame_to_bars
from app.services.eod_limited.inference import UNIVERSE_VERSION, precompute_all_horizon_inputs
from app.services.eod_limited.market_registry import ALL_MARKET_STOCKS, load_market_registry
from app.services.eod_limited.panel import bars_to_panel, prepare_limited_panel
from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.yfinance_batch import download_in_bounded_batches

LAST_OPEN_SESSION = date(2024, 6, 28)
HOLDOUT_FROM = date(2024, 7, 1)
DEFAULT_RESEARCH_DIR = Path.home() / "optix-research" / "screener-gate-v1"
UNIVERSE_CSV = Path(__file__).resolve().parent / "restricted_universe_v1.csv"
LABEL_HS = (5, 20, 63)
FEE_SCENARIOS = (0.0, 0.0010, 0.0025)
BOOTSTRAP_SEED = 174
BOOTSTRAP_REPS = 2000
EXPECTED_BARS_SHA256 = "d910d86505e1914eb14aaacea2ddabc8d61b1f097ab83c513f07750539f32d48"
EXPECTED_TAPE_SHA256 = "b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a"


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_universe() -> list[dict[str, str]]:
    rows = []
    with UNIVERSE_CSV.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            rows.append(row)
    return rows


def research_dir(root: str | Path | None = None) -> Path:
    path = Path(root) if root else Path(
        __import__("os").environ.get("SCREENER_GATE_RESEARCH_DIR") or DEFAULT_RESEARCH_DIR
    )
    path.mkdir(parents=True, exist_ok=True)
    return path


def discover_old_caches() -> dict[str, Any]:
    candidates = [
        ROOT / "research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl",
        Path.home() / "optix-research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl",
        ROOT / "research/option_pro_us_eod_v1/return_pack/measurement_factor_rows.jsonl",
        Path.home() / "optix-research/option_pro_us_eod_v1/return_pack/measurement_factor_rows.jsonl",
    ]
    found = []
    for path in candidates:
        item = {
            "path": str(path),
            "exists": path.is_file(),
            "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_file(path) if path.is_file() else None,
        }
        if "bars.pkl" in path.name:
            item["expected_sha256"] = EXPECTED_BARS_SHA256
            item["hash_ok"] = item["sha256"] == EXPECTED_BARS_SHA256 if item["sha256"] else False
        if path.name.endswith(".jsonl"):
            item["expected_sha256"] = EXPECTED_TAPE_SHA256
            item["hash_ok"] = item["sha256"] == EXPECTED_TAPE_SHA256 if item["sha256"] else False
        found.append(item)
    return {"caches": found, "any_reusable_bars": any(item.get("hash_ok") for item in found if "bars.pkl" in item["path"])}


def download_yahoo_universe(*, dest: Path, start: date, end: date) -> dict[str, Any]:
    if end >= HOLDOUT_FROM:
        end = LAST_OPEN_SESSION
    symbols = [row["provider_symbol"] for row in load_universe()]
    dest.mkdir(parents=True, exist_ok=True)

    def download(**kwargs: Any):
        import yfinance as yf

        return yf.download(**kwargs)

    started = time.perf_counter()
    frame = download_in_bounded_batches(
        download,
        tickers=symbols,
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        **DOWNLOAD_PARAMS,
    )
    bars: dict[str, list] = {}
    if frame is not None and not frame.empty:
        if hasattr(frame.columns, "get_level_values"):
            available = set(frame.columns.get_level_values(0))
            for symbol in symbols:
                bars[symbol] = _frame_to_bars(symbol, frame[symbol] if symbol in available else frame.iloc[0:0])
        elif len(symbols) == 1:
            bars[symbols[0]] = _frame_to_bars(symbols[0], frame)
    payload = {
        "provider": "yahoo-daily-auto_adjust_false",
        "price_adjustment": "split_adjusted_close_not_raw_unadjusted",
        "dividend_handling": "adj_close_used_as_tri_proxy",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "symbols": symbols,
        "bars": {
            symbol: [
                {
                    "security_id": bar.security_id,
                    "session_date": bar.session_date.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "raw_open": bar.raw_open,
                    "raw_close": bar.raw_close,
                    "volume": bar.volume,
                    "dollar_volume": bar.dollar_volume,
                    "tri": bar.tri,
                    "volume_scope": bar.volume_scope,
                }
                for bar in rows
            ]
            for symbol, rows in bars.items()
        },
        "elapsed_s": time.perf_counter() - started,
    }
    out = dest / "restricted_yahoo_bars.json"
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {
        "path": str(out),
        "sha256": sha256_file(out),
        "symbols_n": len(symbols),
        "bars_n": sum(len(rows) for rows in bars.values()),
        "elapsed_s": payload["elapsed_s"],
    }


def _bars_from_payload(payload: Mapping[str, Any]):
    from app.services.research_eod_v1.data.contract import ResearchBar

    out = {}
    for symbol, rows in (payload.get("bars") or {}).items():
        out[symbol] = [
            ResearchBar(
                security_id=row["security_id"],
                session_date=date.fromisoformat(row["session_date"]),
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                raw_open=row["raw_open"],
                raw_close=row["raw_close"],
                volume=row["volume"],
                dollar_volume=row["dollar_volume"],
                tri=row["tri"],
                volume_scope=row.get("volume_scope") or "UNKNOWN",
            )
            for row in rows
            if date.fromisoformat(row["session_date"]) <= LAST_OPEN_SESSION
        ]
    return out


def load_restricted_panel(dest: Path, *, end: date = LAST_OPEN_SESSION):
    path = dest / "restricted_yahoo_bars.json"
    if not path.is_file():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    bars = _bars_from_payload(payload)
    appearances = {}
    for row in load_universe():
        themes = [item for item in row["themes"].split("|") if item]
        if row["asset_track"] == "stock" and ALL_MARKET_STOCKS not in themes:
            themes.append(ALL_MARKET_STOCKS)
        appearances[row["security_id"]] = themes
    panel, coverage = bars_to_panel(bars, appearances, end=min(end, LAST_OPEN_SESSION))
    panel = prepare_limited_panel(panel)
    return panel, coverage, payload


def session_dates(panel: Mapping[str, Any], *, start: date, end: date) -> list[date]:
    dates = set()
    for series in panel.values():
        for session in series.dates:
            if start <= session <= end <= LAST_OPEN_SESSION:
                dates.add(session)
    return sorted(dates)


def _ids(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {str(row.get("security_id")) for row in rows}


def _mean(values: Sequence[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and value == value]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    def ranks(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            rank = 0.5 * (i + j) + 1.0
            for k in range(i, j + 1):
                out[order[k]] = rank
            i = j + 1
        return out
    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    deny = math.sqrt(sum((b - my) ** 2 for b in ry))
    if denx == 0 or deny == 0:
        return None
    return num / (denx * deny)


def label_return(series: Any, session: date, horizon: int, *, use_open: bool) -> float | None:
    if session not in series.dates:
        return None
    index = series.dates.index(session)
    end = index + horizon
    if end >= len(series.dates):
        return None
    if use_open:
        start_i = index + 1
        end_i = index + horizon
        if start_i >= len(series.dates) or end_i >= len(series.dates):
            return None
        left = float(series.open[start_i])
        right = float(series.open[end_i])
    else:
        left = float(series.close[index])
        right = float(series.close[end])
    if left <= 0 or right <= 0 or left != left or right != right:
        return None
    return right / left - 1.0


def path_stats(series: Any, session: date, horizon: int) -> dict[str, float | None]:
    if session not in series.dates:
        return {"mae": None, "mfe": None}
    index = series.dates.index(session)
    end = index + horizon
    if end >= len(series.dates):
        return {"mae": None, "mfe": None}
    entry = float(series.close[index])
    if entry <= 0:
        return {"mae": None, "mfe": None}
    highs = [float(value) for value in series.high[index + 1 : end + 1]]
    lows = [float(value) for value in series.low[index + 1 : end + 1]]
    if not highs or not lows:
        return {"mae": None, "mfe": None}
    return {"mae": min(lows) / entry - 1.0, "mfe": max(highs) / entry - 1.0}


def circular_block_bootstrap(values: Sequence[float], *, block: int, reps: int, seed: int) -> list[float]:
    if not values:
        return []
    n = len(values)
    width = max(1, min(block, n))
    rng = random.Random(seed)
    out = []
    for _ in range(reps):
        sample = []
        while len(sample) < n:
            start = rng.randrange(n)
            for step in range(width):
                sample.append(values[(start + step) % n])
                if len(sample) >= n:
                    break
        out.append(sum(sample) / n)
    return out


def ci(samples: Sequence[float]) -> list[float | None]:
    if not samples:
        return [None, None]
    ordered = sorted(samples)
    lo = ordered[int(0.025 * (len(ordered) - 1))]
    hi = ordered[int(0.975 * (len(ordered) - 1))]
    return [lo, hi]


def gate_counts(records: Sequence[Mapping[str, Any]], variant: str) -> dict[str, int]:
    high = ext = both = 0
    for item in records:
        decision = item["decisions"][variant]
        if decision.high_atr and decision.extended:
            both += 1
        elif decision.high_atr:
            high += 1
        elif decision.extended:
            ext += 1
    return {"high_atr_only": high, "extended_only": ext, "both": both}


def summarize_session(result: Mapping[str, Any], panel: Mapping[str, Any]) -> dict[str, Any]:
    session = date.fromisoformat(result["session_date"])
    summary: dict[str, Any] = {
        "session_date": result["session_date"],
        "old_reference_median": result["old_reference_median"],
        "new_reference_median": result["new_reference_median"],
        "reference_n": result["reference_n"],
        "actual_cap": result["actual_cap"],
        "reference_error": result["reference_error"],
        "complete_bar_n": result.get("complete_bar_n"),
        "family_pre_merge": result["family_pre_merge"],
        "display_best": {},
        "variants": {},
    }
    for variant in VARIANTS:
        discovery = ranked_stocks(result, variant, layer="discovery")
        technical = ranked_stocks(result, variant, layer="technical_entry")
        qualified = ranked_stocks(result, variant, layer="qualified_entry")
        counts = gate_counts(result["records"], variant)
        top = {k: [row["security_id"] for row in technical[:k]] for k in (5, 10, 20)}
        labels = {}
        for h in LABEL_HS:
            close_rets = []
            open_rets = []
            maes = []
            mfes = []
            for row in technical[:20]:
                series = panel.get(row["security_id"])
                if series is None:
                    continue
                close_rets.append(label_return(series, session, h, use_open=False))
                open_rets.append(label_return(series, session, h, use_open=True))
                path = path_stats(series, session, h)
                maes.append(path["mae"])
                mfes.append(path["mfe"])
            labels[str(h)] = {
                "close_to_close_mean": _mean(close_rets),
                "open_to_open_mean": _mean(open_rets),
                "label_coverage_close": sum(value is not None for value in close_rets),
                "label_coverage_open": sum(value is not None for value in open_rets),
                "mae_mean": _mean(maes),
                "mfe_mean": _mean(mfes),
            }
        best = discovery[0] if discovery else None
        summary["display_best"][variant] = None if best is None else {
            "security_id": best.get("security_id"),
            "algorithm_id": best.get("algorithm_id"),
            "theme": best.get("sector_context"),
            "score": best.get("score"),
        }
        summary["variants"][variant] = {
            "discovery_n": len(discovery),
            "technical_n": len(technical),
            "qualified_n": len(qualified),
            "top": top,
            "gate_hits": counts,
            "labels": labels,
        }
    return summary


def overlap(a: Sequence[str], b: Sequence[str]) -> dict[str, Any]:
    left, right = set(a), set(b)
    return {
        "left_n": len(a),
        "right_n": len(b),
        "overlap_n": len(left & right),
        "added": sorted(right - left),
        "removed": sorted(left - right),
    }


def run_one_session(
    panel: Mapping[str, Any],
    session: date,
    *,
    registry: Mapping[str, Any],
    theme_ids: Sequence[str],
    families: Sequence[str],
    profile: str,
    horizon: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    inputs = precompute_all_horizon_inputs(
        panel, session, registry=registry, horizons=[horizon], themes=list(theme_ids),
    )
    raws, clipped, themed = inputs[horizon]
    scored = score_contexts(
        registry=registry,
        session=session,
        as_of=eod_evaluation_as_of(session),
        clipped=clipped,
        raws=raws,
        themed=themed,
        themes=list(theme_ids),
        families=list(families),
        profile=profile,
        horizon=horizon,
        universe_version=UNIVERSE_VERSION,
    )
    scored["elapsed_s"] = time.perf_counter() - started
    scored["complete_bar_n"] = sum(1 for series in clipped.values() if has_complete_session_bar(series, session))
    keep = (
        "security_id",
        "session_date",
        "stock_or_etf_track",
        "algorithm_id",
        "sector_context",
        "score",
        "adv20",
        "atr",
        "rejection_reasons",
        "status",
    )
    slim = []
    for item in scored["records"]:
        row = item["row"]
        slim.append(
            {
                "row": {key: row.get(key) for key in keep},
                "facts": item["facts"],
                "decisions": item["decisions"],
                "attached": {
                    variant: {
                        "score": item["attached"][variant].get("score"),
                        "status": item["attached"][variant].get("status"),
                        "rejection_reasons": item["attached"][variant].get("rejection_reasons"),
                        "research_gate": item["attached"][variant].get("research_gate"),
                    }
                    for variant in VARIANTS
                },
            }
        )
    scored["records"] = slim
    return scored


def run_sessions(
    panel: Mapping[str, Any],
    sessions: Sequence[date],
    *,
    profile: str,
    horizon: str,
    themes: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    registry = load_market_registry()
    theme_ids = list(themes or registry["sectors"])
    families = list(ALGORITHMS)
    results = []
    for index, session in enumerate(sessions, start=1):
        scored = run_one_session(
            panel, session, registry=registry, theme_ids=theme_ids,
            families=families, profile=profile, horizon=horizon,
        )
        print(
            f"session {index}/{len(sessions)} {session.isoformat()} "
            f"{profile}/{horizon} {scored['elapsed_s']:.1f}s "
            f"ref_n={scored['reference_n']} old={scored['old_reference_median']} "
            f"new={scored['new_reference_median']}",
            flush=True,
        )
        results.append(scored)
    return results


def alignment_report(results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    compared = 0
    high_atr_mismatch = 0
    score_mismatch = 0
    examples = []
    for result in results:
        for item in result["records"]:
            if item["facts"].asset_track != "stock":
                continue
            row = item["row"]
            b0 = item["decisions"][B0_CURRENT]
            compared += 1
            production = HIGH_ATR in (row.get("rejection_reasons") or ())
            if bool(production) != bool(b0.high_atr):
                high_atr_mismatch += 1
                if len(examples) < 8:
                    examples.append(
                        {
                            "session_date": result["session_date"],
                            "security_id": row.get("security_id"),
                            "theme": row.get("sector_context"),
                            "family": row.get("algorithm_id"),
                            "production_high_atr": production,
                            "b0_high_atr": b0.high_atr,
                            "atr_pct": item["facts"].atr_pct,
                            "old_median": b0.old_reference_median,
                        }
                    )
            if row.get("score") is not None and item["attached"][B0_CURRENT].get("score") != row.get("score"):
                score_mismatch += 1
    return {
        "compared_stock_rows": compared,
        "high_atr_mismatches": high_atr_mismatch,
        "score_mutations": score_mismatch,
        "examples": examples,
        "aligned": high_atr_mismatch == 0 and score_mismatch == 0,
    }


def paired_stats(results: Sequence[Mapping[str, Any]], panel: Mapping[str, Any]) -> dict[str, Any]:
    daily = []
    prev_top: dict[str, list[str]] = {variant: [] for variant in VARIANTS}
    yearly = defaultdict(lambda: {variant: [] for variant in VARIANTS})
    for result in results:
        session = date.fromisoformat(result["session_date"])
        day = {
            "session_date": result["session_date"],
            "old_reference_median": result["old_reference_median"],
            "new_reference_median": result["new_reference_median"],
            "reference_n": result["reference_n"],
            "actual_cap": result["actual_cap"],
            "complete_bar_n": result.get("complete_bar_n"),
        }
        b0_top20 = [row["security_id"] for row in ranked_stocks(result, B0_CURRENT, layer="technical_entry")[:20]]
        for variant in VARIANTS:
            technical = ranked_stocks(result, variant, layer="technical_entry")
            discovery = ranked_stocks(result, variant, layer="discovery")
            ids20 = [row["security_id"] for row in technical[:20]]
            ov = overlap(b0_top20, ids20)
            turn = overlap(prev_top[variant], ids20)
            prev_top[variant] = ids20
            rets = {}
            for h in LABEL_HS:
                values = []
                for row in technical[:20]:
                    series = panel.get(row["security_id"])
                    values.append(None if series is None else label_return(series, session, h, use_open=False))
                rets[f"h{h}_close_mean"] = _mean(values)
                rets[f"h{h}_close_n"] = sum(value is not None for value in values)
                if h == 20:
                    yearly[session.year][variant].append(rets["h20_close_mean"])
            day[variant] = {
                "discovery_n": len(discovery),
                "technical_n": len(technical),
                "qualified_n": len(ranked_stocks(result, variant, layer="qualified_entry")),
                "top5": ids20[:5],
                "top10": ids20[:10],
                "top20": ids20,
                "overlap_vs_b0": ov,
                "turnover_vs_prev": {"overlap_n": turn["overlap_n"], "added_n": len(turn["added"]), "removed_n": len(turn["removed"])},
                **rets,
                **gate_counts(result["records"], variant),
            }
        daily.append(day)
    bootstrap = {}
    for variant in VARIANTS:
        series20 = [day[variant]["h20_close_mean"] for day in daily if day[variant]["h20_close_mean"] is not None]
        series20f = [float(value) for value in series20]
        bootstrap[variant] = {}
        for name, block in (("H", 20), ("2H", 40)):
            samples = circular_block_bootstrap(series20f, block=block, reps=BOOTSTRAP_REPS, seed=BOOTSTRAP_SEED)
            bootstrap[variant][name] = {
                "mean": _mean(series20f),
                "n_days": len(series20f),
                "ci95": ci(samples),
                "block": block,
                "reps": BOOTSTRAP_REPS,
                "seed": BOOTSTRAP_SEED,
            }
    year_out = {}
    for year, variants in yearly.items():
        year_out[str(year)] = {
            variant: _mean([value for value in values if value is not None])
            for variant, values in variants.items()
        }
    return {"daily": daily, "bootstrap_h20_close": bootstrap, "yearly_h20_close_mean": year_out}


def attribution_rows(results: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        b0_ids = _ids(ranked_stocks(result, B0_CURRENT, layer="technical_entry"))
        for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY):
            g_ids = _ids(ranked_stocks(result, variant, layer="technical_entry"))
            added = g_ids - b0_ids
            removed = b0_ids - g_ids
            reason_add = Counter()
            reason_remove = Counter()
            theme_add = Counter()
            for item in result["records"]:
                sid = item["row"].get("security_id")
                theme = item["row"].get("sector_context")
                family = item["row"].get("algorithm_id")
                if sid in added and item["decisions"][variant].technical_entry_passed:
                    for reason in item["decisions"][B0_CURRENT].research_reasons:
                        reason_add[reason] += 1
                    theme_add[f"{theme}|{family}"] += 1
                if sid in removed and item["decisions"][B0_CURRENT].technical_entry_passed:
                    for reason in item["decisions"][variant].research_reasons:
                        reason_remove[reason] += 1
            rows.append(
                {
                    "session_date": result["session_date"],
                    "profile": result["profile"],
                    "horizon": result["horizon"],
                    "variant": variant,
                    "old_reference_median": result["old_reference_median"],
                    "new_reference_median": result["new_reference_median"],
                    "reference_n": result["reference_n"],
                    "actual_cap": result["actual_cap"],
                    "added_n": len(added),
                    "removed_n": len(removed),
                    "added_reasons": dict(reason_add),
                    "removed_reasons": dict(reason_remove),
                    "added_theme_family": dict(theme_add),
                }
            )
    return rows


def example_rows(results: Sequence[Mapping[str, Any]], *, limit: int = 12) -> list[dict[str, Any]]:
    out = []
    seen: set[tuple[str, str, str]] = set()

    def push(row: Mapping[str, Any], result: Mapping[str, Any], note: str) -> None:
        key = (str(result["session_date"]), str(row.get("security_id")), note)
        if key in seen:
            return
        seen.add(key)
        gate = row.get("research_gate") or {}
        out.append(
            {
                "session_date": result["session_date"],
                "security_id": row.get("security_id"),
                "theme": row.get("sector_context"),
                "family": row.get("algorithm_id"),
                "score": row.get("score"),
                "adv20": row.get("adv20"),
                "atr": row.get("atr"),
                "reasons_b0": (row.get("rejection_reasons") or []),
                "research_reasons_g1": gate.get("research_reasons"),
                "old_median": gate.get("old_reference_median"),
                "new_median": gate.get("new_reference_median"),
                "note": note,
            }
        )

    for result in results:
        b0_rows = ranked_stocks(result, B0_CURRENT, layer="technical_entry")[:20]
        g1_rows = ranked_stocks(result, G1_STOCK_REFERENCE, layer="technical_entry")[:20]
        g2_disc = ranked_stocks(result, G2_EXTENSION_DISCOVERY, layer="discovery")[:20]
        g1_tech = ranked_stocks(result, G1_STOCK_REFERENCE, layer="technical_entry")
        b0 = {row["security_id"] for row in b0_rows}
        g1 = {row["security_id"] for row in g1_rows}
        g1_entry = {row["security_id"] for row in g1_tech}
        for row in g1_rows:
            if row["security_id"] not in b0:
                push(row, result, "g1_added_vs_b0_top20; explanatory, not a parameter-tuning seed")
        for row in b0_rows:
            if row["security_id"] not in g1:
                push(row, result, "g1_removed_vs_b0_top20; explanatory, not a parameter-tuning seed")
        for row in g2_disc:
            if row["security_id"] not in g1_entry:
                push(row, result, "g2_discovery_only_not_strict_entry; not a trading-return claim")
        if len(out) >= limit:
            return out[:limit]
    return out[:limit]


THEME_SLICE_IDS = (
    "semiconductors",
    "software",
    "ai_cloud",
    "biotech",
    "healthcare",
    "consumer_electronics",
    "automotive",
    "ev_supply",
    "finance",
    "fintech",
    "retail",
    "luxury",
    "media_streaming",
    "social_internet",
    "energy",
    "utilities",
    "defense_aero",
    "airlines",
    "real_estate",
    "crypto",
    "china_adr",
    "telecom",
    "industrials",
    "etfs",
    "all_market_stocks",
)
ADV_BUCKETS = (
    ("lt_20m", 0.0, 20_000_000.0),
    ("20m_to_100m", 20_000_000.0, 100_000_000.0),
    ("100m_to_500m", 100_000_000.0, 500_000_000.0),
    ("ge_500m", 500_000_000.0, None),
)


def universe_theme_map() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for row in load_universe():
        out[row["security_id"]] = [item for item in row["themes"].split("|") if item]
    return out


def adv20_at(series: Any, session: date) -> float | None:
    if session not in series.dates:
        return None
    index = series.dates.index(session)
    if index < 20:
        return None
    window = [float(value) for value in series.dollar_volume[index - 20 : index]]
    if not window or any(value != value or value <= 0 for value in window):
        return None
    return sum(window) / len(window)


def adv_bucket(adv20: float | None) -> str:
    if adv20 is None:
        return "adv20_missing"
    for name, lo, hi in ADV_BUCKETS:
        if adv20 >= lo and (hi is None or adv20 < hi):
            return name
    return "adv20_missing"


def fee_adjust(mean: float | None, fee: float) -> float | None:
    if mean is None:
        return None
    return float(mean) - float(fee)


def percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    if len(ordered) == 1:
        return ordered[0]
    rank = q * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def max_drawdown(returns: Sequence[float]) -> float | None:
    if not returns:
        return None
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for value in returns:
        equity *= 1.0 + float(value)
        peak = max(peak, equity)
        worst = min(worst, equity / peak - 1.0)
    return worst


def panel_track_counts(panel: Mapping[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for series in panel.values():
        track = str(getattr(series, "asset_track", "unknown") or "unknown")
        counts[track] = counts.get(track, 0) + 1
    return counts


def complete_bar_count(panel: Mapping[str, Any], session: date) -> int:
    return sum(1 for series in panel.values() if has_complete_session_bar(series, session))


def stock_ids(panel: Mapping[str, Any]) -> list[str]:
    return [sid for sid, series in panel.items() if getattr(series, "asset_track", None) == "stock"]


def etf_ids(panel: Mapping[str, Any]) -> set[str]:
    return {sid for sid, series in panel.items() if getattr(series, "asset_track", None) == "etf"}


def universe_ew_label(panel: Mapping[str, Any], session: date, horizon: int, *, use_open: bool) -> dict[str, Any]:
    values = []
    for sid in stock_ids(panel):
        values.append(label_return(panel[sid], session, horizon, use_open=use_open))
    present = [float(value) for value in values if value is not None]
    return {
        "mean": None if not present else sum(present) / len(present),
        "n": len(present),
        "coverage": None if not values else len(present) / len(values),
    }


def theme_counts_for_ids(ids: Sequence[str], theme_map: Mapping[str, Sequence[str]]) -> dict[str, int]:
    counts = {theme: 0 for theme in THEME_SLICE_IDS}
    for sid in ids:
        themes = list(theme_map.get(sid) or [])
        if "etfs" not in themes:
            themes.append("all_market_stocks")
        for theme in themes:
            if theme in counts:
                counts[theme] += 1
    return counts


def adv_counts_for_ids(ids: Sequence[str], panel: Mapping[str, Any], session: date) -> dict[str, int]:
    counts = {name: 0 for name, _lo, _hi in ADV_BUCKETS}
    counts["adv20_missing"] = 0
    for sid in ids:
        series = panel.get(sid)
        bucket = adv_bucket(None if series is None else adv20_at(series, session))
        counts[bucket] = counts.get(bucket, 0) + 1
    return counts


def enrich_paired_daily(daily: Sequence[Mapping[str, Any]], panel: Mapping[str, Any]) -> list[dict[str, Any]]:
    theme_map = universe_theme_map()
    etfs = etf_ids(panel)
    out = []
    for row in daily:
        session = date.fromisoformat(str(row["session_date"]))
        day = dict(row)
        day["complete_bar_n"] = complete_bar_count(panel, session)
        day["stock_panel_n"] = len(stock_ids(panel))
        day["etf_panel_n"] = len(etfs)
        for h in LABEL_HS:
            close_ew = universe_ew_label(panel, session, h, use_open=False)
            open_ew = universe_ew_label(panel, session, h, use_open=True)
            day[f"universe_stock_ew_h{h}_close_mean"] = close_ew["mean"]
            day[f"universe_stock_ew_h{h}_close_n"] = close_ew["n"]
            day[f"universe_stock_ew_h{h}_open_mean"] = open_ew["mean"]
            day[f"universe_stock_ew_h{h}_open_n"] = open_ew["n"]
        for variant in VARIANTS:
            block = dict(day.get(variant) or {})
            ids20 = list(block.get("top20") or [])
            block["etf_in_stock_top20_n"] = len(set(ids20) & etfs)
            block["theme_counts"] = theme_counts_for_ids(ids20, theme_map)
            block["adv_buckets"] = adv_counts_for_ids(ids20, panel, session)
            for h in LABEL_HS:
                top_mean = block.get(f"h{h}_close_mean")
                uni = day.get(f"universe_stock_ew_h{h}_close_mean")
                block[f"h{h}_close_excess_vs_stock_ew"] = None if top_mean is None or uni is None else float(top_mean) - float(uni)
                open_mean = block.get(f"h{h}_open_mean")
                uni_open = day.get(f"universe_stock_ew_h{h}_open_mean")
                block[f"h{h}_open_excess_vs_stock_ew"] = None if open_mean is None or uni_open is None else float(open_mean) - float(uni_open)
                for fee in FEE_SCENARIOS:
                    key = str(fee).replace(".", "p")
                    block[f"h{h}_close_mean_fee_{key}"] = fee_adjust(top_mean, fee)
            day[variant] = block
        out.append(day)
    return out


def compact_path_stats(values: Sequence[float | None]) -> dict[str, Any]:
    clean = [float(value) for value in values if value is not None]
    return {
        "n": len(clean),
        "missing_n": sum(1 for value in values if value is None),
        "mean": _mean(clean),
        "p05": percentile(clean, 0.05),
        "p25": percentile(clean, 0.25),
        "p50": percentile(clean, 0.50),
        "p75": percentile(clean, 0.75),
        "p95": percentile(clean, 0.95),
        "min": None if not clean else min(clean),
        "max": None if not clean else max(clean),
        "max_drawdown": max_drawdown(clean),
    }


def engineering_checks(daily: Sequence[Mapping[str, Any]], summary: Mapping[str, Any]) -> dict[str, Any]:
    entry_mismatch = 0
    etf_leaks = 0
    g1_add_days = 0
    g1_rem_days = 0
    g2_disc_gt_g1 = 0
    g3_disc_gt_g2 = 0
    qualified_nonzero = 0
    for day in daily:
        g1 = day[G1_STOCK_REFERENCE]
        g2 = day[G2_EXTENSION_DISCOVERY]
        g3 = day[G3_RISK_DISCOVERY]
        if g1.get("top20") != g2.get("top20") or g1.get("top20") != g3.get("top20") or g1.get("technical_n") != g2.get("technical_n") or g1.get("technical_n") != g3.get("technical_n"):
            entry_mismatch += 1
        for variant in VARIANTS:
            etf_leaks += int((day[variant] or {}).get("etf_in_stock_top20_n") or 0)
            qualified_nonzero += int((day[variant] or {}).get("qualified_n") or 0)
        ov = (g1.get("overlap_vs_b0") or {})
        if ov.get("added"):
            g1_add_days += 1
        if ov.get("removed"):
            g1_rem_days += 1
        if (g2.get("discovery_n") or 0) > (g1.get("discovery_n") or 0):
            g2_disc_gt_g1 += 1
        if (g3.get("discovery_n") or 0) > (g2.get("discovery_n") or 0):
            g3_disc_gt_g2 += 1
    alignment = summary.get("alignment") or {}
    return {
        "b0_aligned": bool(alignment.get("aligned")),
        "g1_g2_g3_entry_identical_days": len(daily) - entry_mismatch,
        "g1_g2_g3_entry_mismatch_days": entry_mismatch,
        "etf_in_stock_topk_total": etf_leaks,
        "g1_can_add_vs_b0_top20_days": g1_add_days,
        "g1_can_remove_vs_b0_top20_days": g1_rem_days,
        "g2_discovery_gt_g1_days": g2_disc_gt_g1,
        "g3_discovery_gt_g2_days": g3_disc_gt_g2,
        "qualified_entry_nonzero_total": qualified_nonzero,
        "unverified_not_promoted": qualified_nonzero == 0,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_reps": BOOTSTRAP_REPS,
        "passed": entry_mismatch == 0 and etf_leaks == 0 and qualified_nonzero == 0 and bool(alignment.get("aligned")),
    }


def compact_diagnostics(summary: Mapping[str, Any], daily: Sequence[Mapping[str, Any]], panel: Mapping[str, Any]) -> dict[str, Any]:
    theme_map = universe_theme_map()
    theme_days = {theme: {variant: 0 for variant in VARIANTS} for theme in THEME_SLICE_IDS}
    theme_share_sum = {theme: {variant: 0.0 for variant in VARIANTS} for theme in THEME_SLICE_IDS}
    listed = Counter(theme for themes in theme_map.values() for theme in themes)
    listed["all_market_stocks"] = sum(1 for themes in theme_map.values() if "etfs" not in themes)
    variant_stats = {}
    for variant in VARIANTS:
        close20 = [day[variant].get("h20_close_mean") for day in daily]
        excess20 = [day[variant].get("h20_close_excess_vs_stock_ew") for day in daily]
        mae = []
        mfe = []
        for sess in summary.get("sessions") or []:
            lab = ((sess.get("variants") or {}).get(variant) or {}).get("labels") or {}
            mae.append((lab.get("20") or {}).get("mae_mean"))
            mfe.append((lab.get("20") or {}).get("mfe_mean"))
        fees = {
            str(fee): {
                "h20_close_mean_minus_fee": fee_adjust(_mean([value for value in close20 if value is not None]), fee),
                "note": "sensitivity only; subtracts a one-way fee from the already-formed Top-K mean, not a live cost",
            }
            for fee in FEE_SCENARIOS
        }
        turn = [((day[variant].get("turnover_vs_prev") or {}).get("added_n")) for day in daily[1:]]
        variant_stats[variant] = {
            "h20_close_path": compact_path_stats(close20),
            "h20_close_excess_vs_restricted_stock_ew": compact_path_stats(excess20),
            "h20_mae": compact_path_stats(mae),
            "h20_mfe": compact_path_stats(mfe),
            "fee_sensitivity": fees,
            "mean_top20_turnover_added_n": _mean([float(value) for value in turn if value is not None]),
            "zero_technical_days": sum(1 for day in daily if (day[variant].get("technical_n") or 0) == 0),
            "mean_technical_n": _mean([day[variant].get("technical_n") for day in daily]),
            "mean_discovery_n": _mean([day[variant].get("discovery_n") for day in daily]),
        }
        for day in daily:
            counts = (day[variant].get("theme_counts") or {})
            n = max(1, len(day[variant].get("top20") or []))
            for theme in THEME_SLICE_IDS:
                share = float(counts.get(theme) or 0) / n
                theme_share_sum[theme][variant] += share
                if (counts.get(theme) or 0) > 0:
                    theme_days[theme][variant] += 1
    theme_slices = {}
    for theme in THEME_SLICE_IDS:
        n_listed = int(listed.get(theme) or 0)
        theme_slices[theme] = {
            "listed_n": n_listed,
            "thin_sample": n_listed < 10,
            "days_with_top20_member": theme_days[theme],
            "mean_top20_share": {
                variant: None if not daily else theme_share_sum[theme][variant] / len(daily)
                for variant in VARIANTS
            },
            "note": "coverage diagnostic only; not a per-theme champion",
        }
    return {
        "panel_tracks": panel_track_counts(panel),
        "layer": "restricted_current_membership_exploratory",
        "universe_ew_note": "equal-weight of restricted stock panel members with labels; not a full-market benchmark",
        "theme_slices": theme_slices,
        "variants": variant_stats,
        "engineering_checks": engineering_checks(daily, summary),
    }


def build_validation_md(
    *,
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
    diagnostics: Mapping[str, Any],
    caches: Mapping[str, Any],
    commands: Sequence[str],
    test_results: Mapping[str, Any] | None = None,
) -> str:
    alignment = manifest.get("run", {}).get("alignment") or summary.get("alignment") or {}
    checks = diagnostics.get("engineering_checks") or {}
    dates = manifest.get("dates") or {}
    lines = [
        "# Validation",
        "",
        "## 执行命令",
    ]
    for command in commands:
        lines.append(f"- `{command}`")
    lines.extend(
        [
            "",
            "## 基线与范围",
            f"- production HEAD target: `{manifest.get('production_anchor')}`",
            f"- layer: `{manifest.get('layer')}`",
            f"- sessions: {dates.get('start')} → {dates.get('end')} (n={dates.get('n_sessions')})",
            f"- profile/horizon: {manifest.get('run', {}).get('profile')} / {manifest.get('run', {}).get('horizon')}",
            f"- seen_or_unseen: {dates.get('seen_or_unseen')}",
            f"- holdout {dates.get('holdout_sealed_from')}+ : sealed, not used",
            f"- last open research session: {dates.get('last_open_research_session')}",
            f"- elapsed_s: {manifest.get('run', {}).get('elapsed_s')}",
            "",
            "## 旧资源复用",
            f"- old bars.pkl reusable: {caches.get('any_reusable_bars')}",
            "- expected bars.pkl sha256 `d910d86505e1914eb14aaacea2ddabc8d61b1f097ab83c513f07750539f32d48`: ABSENT, hash not verified, not claimed reused",
            "- expected 4GB tape sha256 `b490ba6d83965a0c3fe60c76afab8205fc2a0d76cc6577cdf23cb838e447f75a`: ABSENT, not claimed reused",
            f"- Yahoo re-download sha256: `{((manifest.get('data') or {}).get('yahoo_bars_sha256'))}`",
            "- algorithm_candidates.patch: not attached; candidates reconstructed from frozen CURSOR_TASK/REVIEW spec without changing weights",
            "",
            "## 通过项",
            f"- B0 alignment: {json.dumps(alignment, ensure_ascii=False)}",
            f"- G1/G2/G3 technical-entry identity mismatch days: {checks.get('g1_g2_g3_entry_mismatch_days')}",
            f"- G1 can add vs B0 Top20 days: {checks.get('g1_can_add_vs_b0_top20_days')}",
            f"- G1 can remove vs B0 Top20 days: {checks.get('g1_can_remove_vs_b0_top20_days')}",
            f"- ETF leaked into stock Top-K: {checks.get('etf_in_stock_topk_total')}",
            f"- qualified_entry nonzero total: {checks.get('qualified_entry_nonzero_total')} (DOLLAR_LIQUIDITY_UNVERIFIED / VOLUME_SESSION_UNVERIFIED not promoted)",
            f"- bootstrap seed={checks.get('bootstrap_seed')} reps={checks.get('bootstrap_reps')} blocks=H/2H",
            f"- engineering_checks.passed: {checks.get('passed')}",
            "",
            "## 跳过项与数据限制",
            "- 2024-07-01+ holdout not opened",
            "- old 214-name pool cannot be called a full-market HIGH_ATR median",
            f"- broad_market_median: {summary.get('broad_market_median')}; reason: {summary.get('broad_market_median_reason')}",
            f"- broad slices status: {(manifest.get('run') or {}).get('broad_slices', {}).get('status')}",
            "- Massive grouped_daily slices are same-day high-low/close range, NOT ATR%, NOT G1 reference",
            "- CRWV has no Yahoo bars in the open zone (IPO after 2024-06-28)",
            "- LVMUY / CFRUY venue OTC_EXCLUDED; RMS.PA NON_US_LISTING",
            "- other profile/horizon extras are 3-session verification only, not a second full 562-day matrix",
            "- overlapping 5/20/63 labels are not independent trades",
            "- fee scenarios are sensitivity only, not live costs",
            "- G2/G3 discovery expansion is not a trading-return claim",
            "",
            "## 测试",
        ]
    )
    if test_results:
        for name, payload in test_results.items():
            lines.append(f"- {name}: {json.dumps(payload, ensure_ascii=False)}")
    else:
        lines.append("- tests not re-run inside this finalize call")
    lines.extend(
        [
            "",
            "Stock and ETF tracks are reported separately; Top-K is stock-only after filtering.",
            "Synthetic 56 tests prove function behavior only; this pack is the market replay.",
            "No production defaults were changed. No deploy/merge/search.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(row[key], ensure_ascii=False) if isinstance(row[key], (dict, list)) else row[key] for key in keys})


def export_pack(
    dest: Path,
    *,
    manifest: Mapping[str, Any],
    summary: Mapping[str, Any],
    paired: Sequence[Mapping[str, Any]],
    attribution: Sequence[Mapping[str, Any]],
    examples: Sequence[Mapping[str, Any]],
    validation: str,
) -> None:
    pack = dest / "return_pack"
    pack.mkdir(parents=True, exist_ok=True)
    write_json(pack / "manifest.json", manifest)
    write_json(pack / "summary.json", summary)
    write_csv(pack / "paired_daily.csv", paired)
    write_csv(pack / "gate_attribution.csv", attribution)
    write_csv(pack / "examples.csv", examples)
    (pack / "validation.md").write_text(validation, encoding="utf-8")


def fetch_broad_slices(dest: Path, days: Sequence[str]) -> dict[str, Any]:
    """Optional Massive grouped-daily slices. Not a G1 full-market median."""

    from app.services import massive

    if not massive.configured():
        return {"status": "skipped", "reason": "massive_not_configured"}
    out_dir = dest / "broad_slices"
    out_dir.mkdir(parents=True, exist_ok=True)
    slices = []
    for day in days:
        session = date.fromisoformat(day)
        if session >= HOLDOUT_FROM:
            continue
        try:
            rows = massive.grouped_daily(day)
        except Exception as exc:
            slices.append({"session_date": day, "status": "skipped", "reason": str(exc)})
            continue
        stock_ranges = []
        etf_ranges = []
        for symbol, bar in rows.items():
            o, h, l, c = bar.get("o"), bar.get("h"), bar.get("l"), bar.get("c")
            if c is None or c <= 0 or h is None or l is None:
                continue
            rng = 100.0 * (h - l) / c
            # Same-day range proxy only; not ATR14 and not G1.
            if symbol.endswith("X") and len(symbol) <= 4:
                etf_ranges.append(rng)
            else:
                stock_ranges.append(rng)
        payload = {
            "session_date": day,
            "provider": "massive_grouped_daily_unadjusted",
            "n_symbols": len(rows),
            "same_day_range_pct_stock_n": len(stock_ranges),
            "same_day_range_pct_stock_upper_median": None if len(stock_ranges) < 30 else sorted(stock_ranges)[len(stock_ranges) // 2],
            "same_day_range_pct_note": "intraday high-low / close; NOT ATR%, NOT G1 reference, NOT full-market median",
        }
        path = out_dir / f"grouped_{day}.json"
        # Do not store raw prints in the repo; compact counts only.
        write_json(path, payload)
        slices.append(payload)
    return {"status": "ok" if slices else "empty", "slices": slices}


def cmd_discover(args: argparse.Namespace) -> None:
    payload = discover_old_caches()
    write_json(research_dir(args.research_dir) / "cache_discovery.json", payload)
    print(json.dumps(payload, indent=2))


def cmd_download(args: argparse.Namespace) -> None:
    dest = research_dir(args.research_dir)
    info = download_yahoo_universe(dest=dest, start=date.fromisoformat(args.start), end=date.fromisoformat(args.end))
    write_json(dest / "download_manifest.json", info)
    print(json.dumps(info, indent=2))


def _paired_from_summaries(summaries: Sequence[Mapping[str, Any]], panel: Mapping[str, Any]) -> dict[str, Any]:
    daily = []
    prev_top = {variant: [] for variant in VARIANTS}
    yearly: dict[int, dict[str, list]] = defaultdict(lambda: {variant: [] for variant in VARIANTS})
    for summary in summaries:
        session = date.fromisoformat(str(summary["session_date"]))
        day: dict[str, Any] = {
            "session_date": summary["session_date"],
            "old_reference_median": summary.get("old_reference_median"),
            "new_reference_median": summary.get("new_reference_median"),
            "reference_n": summary.get("reference_n"),
            "actual_cap": summary.get("actual_cap"),
            "complete_bar_n": summary.get("complete_bar_n"),
        }
        b0_top20 = list((summary.get("variants") or {}).get(B0_CURRENT, {}).get("top", {}).get(20) or [])
        for variant in VARIANTS:
            block = (summary.get("variants") or {}).get(variant) or {}
            ids20 = list((block.get("top") or {}).get(20) or [])
            ov = overlap(b0_top20, ids20)
            turn = overlap(prev_top[variant], ids20)
            prev_top[variant] = ids20
            labels = block.get("labels") or {}
            rets = {}
            for h in LABEL_HS:
                lab = labels.get(str(h)) or {}
                rets[f"h{h}_close_mean"] = lab.get("close_to_close_mean")
                rets[f"h{h}_close_n"] = lab.get("label_coverage_close")
                rets[f"h{h}_open_mean"] = lab.get("open_to_open_mean")
                rets[f"h{h}_open_n"] = lab.get("label_coverage_open")
            if rets.get("h20_close_mean") is not None:
                yearly[session.year][variant].append(rets["h20_close_mean"])
            hits = block.get("gate_hits") or {}
            day[variant] = {
                "discovery_n": block.get("discovery_n"),
                "technical_n": block.get("technical_n"),
                "qualified_n": block.get("qualified_n"),
                "top5": (block.get("top") or {}).get(5),
                "top10": (block.get("top") or {}).get(10),
                "top20": ids20,
                "overlap_vs_b0": ov,
                "turnover_vs_prev": {
                    "overlap_n": turn["overlap_n"],
                    "added_n": len(turn["added"]),
                    "removed_n": len(turn["removed"]),
                },
                **rets,
                **hits,
            }
        daily.append(day)
    bootstrap = {}
    for variant in VARIANTS:
        series20f = [float(day[variant]["h20_close_mean"]) for day in daily if day[variant].get("h20_close_mean") is not None]
        bootstrap[variant] = {}
        for name, block in (("H", 20), ("2H", 40)):
            samples = circular_block_bootstrap(series20f, block=block, reps=BOOTSTRAP_REPS, seed=BOOTSTRAP_SEED)
            bootstrap[variant][name] = {
                "mean": _mean(series20f),
                "n_days": len(series20f),
                "ci95": ci(samples),
                "block": block,
                "reps": BOOTSTRAP_REPS,
                "seed": BOOTSTRAP_SEED,
            }
    year_out = {
        str(year): {variant: _mean(values) for variant, values in variants.items()}
        for year, variants in yearly.items()
    }
    return {"daily": daily, "bootstrap_h20_close": bootstrap, "yearly_h20_close_mean": year_out}


def score_ics(result: Mapping[str, Any], panel: Mapping[str, Any], horizon_h: int = 20) -> dict[str, Any]:
    session = date.fromisoformat(result["session_date"])
    out: dict[str, Any] = {}
    common_rows = []
    for item in result["records"]:
        if item["facts"].asset_track != "stock" or item["row"].get("score") is None:
            continue
        series = panel.get(item["row"]["security_id"])
        label = None if series is None else label_return(series, session, horizon_h, use_open=False)
        if label is None:
            continue
        common_rows.append((str(item["row"]["security_id"]), float(item["row"]["score"]), float(label)))
    best: dict[str, tuple[float, float]] = {}
    for sid, score, label in common_rows:
        prev = best.get(sid)
        if prev is None or score > prev[0]:
            best[sid] = (score, label)
    xs = [pair[0] for pair in best.values()]
    ys = [pair[1] for pair in best.values()]
    out["common_sample_ic_h20"] = _spearman(xs, ys)
    out["common_sample_n"] = len(best)
    for variant in VARIANTS:
        selected = ranked_stocks(result, variant, layer="technical_entry")[:20]
        labels = []
        for row in selected:
            series = panel.get(row["security_id"])
            labels.append(None if series is None else label_return(series, session, horizon_h, use_open=False))
        out[f"{variant}_top20_h20_mean"] = _mean(labels)
        out[f"{variant}_top20_h20_n"] = sum(value is not None for value in labels)
    return out


def cmd_replay(args: argparse.Namespace) -> None:
    dest = research_dir(args.research_dir)
    started = time.perf_counter()
    caches = discover_old_caches()
    panel, coverage, yahoo_payload = load_restricted_panel(dest, end=LAST_OPEN_SESSION)
    sessions = session_dates(panel, start=date.fromisoformat(args.start), end=date.fromisoformat(args.end))
    if args.max_sessions:
        sessions = sessions[: int(args.max_sessions)]
    align_n = max(1, int(args.align_sessions))
    registry = load_market_registry()
    theme_ids = list(registry["sectors"])
    families = list(ALGORITHMS)
    alignment = {"compared_stock_rows": 0, "high_atr_mismatches": 0, "score_mutations": 0, "examples": [], "aligned": True}
    summaries: list[dict[str, Any]] = []
    attribution: list[dict[str, Any]] = []
    examples: list[dict[str, Any]] = []
    ics: list[dict[str, Any]] = []
    extras: dict[str, Any] = {}

    def consume(result: Mapping[str, Any], *, align: bool = False) -> None:
        if align:
            part = alignment_report([result])
            alignment["compared_stock_rows"] += part["compared_stock_rows"]
            alignment["high_atr_mismatches"] += part["high_atr_mismatches"]
            alignment["score_mutations"] += part["score_mutations"]
            alignment["examples"].extend(part["examples"][: max(0, 8 - len(alignment["examples"]))])
            alignment["aligned"] = alignment["high_atr_mismatches"] == 0 and alignment["score_mutations"] == 0
        summaries.append(summarize_session(result, panel))
        attribution.extend(attribution_rows([result]))
        if len(examples) < 12:
            examples.extend(example_rows([result], limit=12 - len(examples)))
        ics.append(score_ics(result, panel))

    wanted = sessions if args.align_only else sessions
    print(f"replaying {len(wanted)} sessions profile={args.profile} horizon={args.horizon}", flush=True)
    for index, session in enumerate(wanted, start=1):
        result = run_one_session(
            panel, session, registry=registry, theme_ids=theme_ids,
            families=families, profile=args.profile, horizon=args.horizon,
        )
        print(
            f"session {index}/{len(wanted)} {session.isoformat()} "
            f"{args.profile}/{args.horizon} {result['elapsed_s']:.1f}s "
            f"ref_n={result['reference_n']} old={result['old_reference_median']} "
            f"new={result['new_reference_median']}",
            flush=True,
        )
        consume(result, align=index <= align_n and args.profile == "balanced" and args.horizon == "mid")
        if args.align_only and index >= align_n:
            break
    print(f"alignment {json.dumps(alignment, ensure_ascii=False)}", flush=True)
    if args.other_variants:
        extra_sessions = sessions[: max(1, int(args.extra_sessions))]
        for profile in PROFILES:
            for horizon in HORIZONS:
                if profile == args.profile and horizon == args.horizon:
                    continue
                extras[f"{horizon}|{profile}"] = [
                    summarize_session(item, panel)
                    for item in run_sessions(panel, extra_sessions, profile=profile, horizon=horizon)
                ]
    # Rebuild paired stats from stored summaries + leftover compact fields.
    paired = _paired_from_summaries(summaries, panel)
    paired["daily"] = enrich_paired_daily(paired["daily"], panel)
    paired["common_sample_ic"] = {
        "mean": _mean([item.get("common_sample_ic_h20") for item in ics]),
        "n_days": sum(1 for item in ics if item.get("common_sample_ic_h20") is not None),
        "note": "rank IC on shared scored names, separate from each candidate Top-K set",
    }
    broad = {"status": "skipped", "reason": "not_requested"}
    if args.broad_slices:
        broad = fetch_broad_slices(dest, args.broad_slices.split(","))
    code_hash = sha256_file(Path(__file__).resolve().parent / "screener_gate_candidates_v1.py")
    adapter_hash = sha256_file(Path(__file__).resolve().parent / "screener_gate_adapter_v1.py")
    yahoo_hash = sha256_file(dest / "restricted_yahoo_bars.json")
    manifest = {
        "protocol": "us-eod-screener-gate-candidates-v1",
        "layer": "restricted_current_membership_exploratory",
        "production_anchor": "d16b25e3812dce9adf16e2ca993b4f2c38d7b1ef",
        "code_hashes": {
            "screener_gate_candidates_v1.py": code_hash,
            "screener_gate_adapter_v1.py": adapter_hash,
            "screener_gate_replay_v1.py": sha256_file(Path(__file__).resolve()),
        },
        "data": {
            "provider": yahoo_payload.get("provider"),
            "price_adjustment": yahoo_payload.get("price_adjustment"),
            "yahoo_bars_sha256": yahoo_hash,
            "old_caches": caches,
            "coverage": coverage,
        },
        "dates": {
            "start": sessions[0].isoformat() if sessions else None,
            "end": sessions[-1].isoformat() if sessions else None,
            "n_sessions": len(sessions),
            "holdout_sealed_from": HOLDOUT_FROM.isoformat(),
            "last_open_research_session": LAST_OPEN_SESSION.isoformat(),
            "seen_or_unseen": "open_development_zone_exploratory",
        },
        "universe": {
            "kind": "restricted_current_membership",
            "n_listed": len(load_universe()),
            "n_panel": len(panel),
            "tracks": panel_track_counts(panel),
            "stocks_etfs_separated": True,
        },
        "labels": {
            "primary": "T_close_to_T_plus_H_close",
            "execution_proxy": "next_session_open_to_E_plus_H_open",
            "horizons": list(LABEL_HS),
            "fee_scenarios": list(FEE_SCENARIOS),
            "fee_note": "sensitivity only, not claimed live costs",
        },
        "run": {
            "profile": args.profile,
            "horizon": args.horizon,
            "elapsed_s": time.perf_counter() - started,
            "alignment": alignment,
            "broad_slices": broad,
            "status": "ok" if alignment.get("aligned") else "b0_alignment_differences_recorded",
        },
    }
    diagnostics = compact_diagnostics(
        {
            "alignment": alignment,
            "sessions": summaries,
        },
        paired["daily"],
        panel,
    )
    summary = {
        "layer": "restricted_current_membership_exploratory",
        "alignment": alignment,
        "sessions": summaries,
        "bootstrap_h20_close": paired["bootstrap_h20_close"],
        "yearly_h20_close_mean": paired["yearly_h20_close_mean"],
        "common_sample_ic": paired.get("common_sample_ic"),
        "other_profile_horizon": extras or None,
        "compact": diagnostics,
        "broad_market_median": None,
        "broad_market_median_reason": "restricted 214-name pool cannot be called a full-market median; see broad_slices if present",
        "qualified_entry_n_note": "volume and dollar liquidity remain unverified; qualified_entry stays empty unless those flags are true",
    }
    validation = build_validation_md(
        manifest=manifest,
        summary=summary,
        diagnostics=diagnostics,
        caches=caches,
        commands=[
            "PYTHONPATH=backend python -m pytest -q tests/test_screener_gate_candidates_v1.py tests/test_screener_gate_replay_adapter.py",
            "PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py discover",
            "PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py download --start 2016-01-01 --end 2024-06-28",
            "PYTHONPATH=backend python -u scripts/research/screener_gate_replay_v1.py replay --start 2022-01-03 --end 2024-03-28 --other-variants --extra-sessions 3 --broad-slices 2024-06-28,2023-12-29,2022-12-30 --align-sessions 5",
        ],
    )
    export_pack(
        dest,
        manifest=manifest,
        summary=summary,
        paired=paired["daily"],
        attribution=attribution,
        examples=examples,
        validation=validation,
    )
    print(json.dumps({"pack": str(dest / "return_pack"), "alignment": alignment, "n_sessions": len(sessions)}, indent=2, default=str))


def cmd_finalize(args: argparse.Namespace) -> None:
    dest = research_dir(args.research_dir)
    pack = dest / "return_pack"
    started = time.perf_counter()
    caches = discover_old_caches()
    panel, coverage, yahoo_payload = load_restricted_panel(dest, end=LAST_OPEN_SESSION)
    summary = json.loads((pack / "summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    paired = _paired_from_summaries(summary.get("sessions") or [], panel)
    paired["daily"] = enrich_paired_daily(paired["daily"], panel)
    paired["common_sample_ic"] = summary.get("common_sample_ic")
    diagnostics = compact_diagnostics(summary, paired["daily"], panel)
    summary["compact"] = diagnostics
    summary["bootstrap_h20_close"] = paired["bootstrap_h20_close"]
    summary["yearly_h20_close_mean"] = paired["yearly_h20_close_mean"]
    manifest.setdefault("code_hashes", {})["screener_gate_replay_v1.py"] = sha256_file(Path(__file__).resolve())
    manifest.setdefault("universe", {})["tracks"] = panel_track_counts(panel)
    manifest["data"] = manifest.get("data") or {}
    manifest["data"]["coverage"] = coverage
    manifest["data"]["provider"] = yahoo_payload.get("provider")
    manifest["data"]["price_adjustment"] = yahoo_payload.get("price_adjustment")
    manifest["run"] = manifest.get("run") or {}
    manifest["run"]["finalize_elapsed_s"] = time.perf_counter() - started
    examples = []
    if args.refresh_examples:
        registry = load_market_registry()
        theme_ids = list(registry["sectors"])
        families = list(ALGORITHMS)
        wanted = []
        for day in paired["daily"]:
            ov = ((day.get(G1_STOCK_REFERENCE) or {}).get("overlap_vs_b0") or {})
            if ov.get("added") or ov.get("removed"):
                wanted.append(date.fromisoformat(str(day["session_date"])))
            if len(wanted) >= 4:
                break
        if not wanted and paired["daily"]:
            wanted = [date.fromisoformat(str(paired["daily"][0]["session_date"]))]
        for session in wanted:
            result = run_one_session(
                panel,
                session,
                registry=registry,
                theme_ids=theme_ids,
                families=families,
                profile=str(manifest.get("run", {}).get("profile") or "balanced"),
                horizon=str(manifest.get("run", {}).get("horizon") or "mid"),
            )
            examples.extend(example_rows([result], limit=12 - len(examples)))
            if len(examples) >= 12:
                break
    else:
        with (pack / "examples.csv").open(encoding="utf-8") as handle:
            examples = list(csv.DictReader(handle))
    test_results = None
    if args.test_results:
        test_results = json.loads(Path(args.test_results).read_text(encoding="utf-8"))
    validation = build_validation_md(
        manifest=manifest,
        summary=summary,
        diagnostics=diagnostics,
        caches=caches,
        commands=[
            "PYTHONPATH=backend python -m pytest -q tests/test_screener_gate_candidates_v1.py tests/test_screener_gate_replay_adapter.py",
            "PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py discover",
            "PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py download --start 2016-01-01 --end 2024-06-28",
            "PYTHONPATH=backend python -u scripts/research/screener_gate_replay_v1.py replay --start 2022-01-03 --end 2024-03-28 --other-variants --extra-sessions 3 --broad-slices 2024-06-28,2023-12-29,2022-12-30 --align-sessions 5",
            "PYTHONPATH=backend python scripts/research/screener_gate_replay_v1.py finalize --refresh-examples",
        ],
        test_results=test_results,
    )
    attribution = []
    attr_path = pack / "gate_attribution.csv"
    if attr_path.is_file():
        with attr_path.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                parsed = dict(row)
                for key in ("added_reasons", "removed_reasons", "added_theme_family"):
                    raw = parsed.get(key)
                    if raw:
                        try:
                            parsed[key] = json.loads(raw)
                        except json.JSONDecodeError:
                            pass
                attribution.append(parsed)
    export_pack(
        dest,
        manifest=manifest,
        summary=summary,
        paired=paired["daily"],
        attribution=attribution,
        examples=examples,
        validation=validation,
    )
    print(json.dumps({"pack": str(pack), "checks": diagnostics.get("engineering_checks"), "finalize_elapsed_s": manifest["run"]["finalize_elapsed_s"]}, indent=2, default=str))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)
    discover = sub.add_parser("discover")
    discover.add_argument("--research-dir")
    discover.set_defaults(func=cmd_discover)
    download = sub.add_parser("download")
    download.add_argument("--research-dir")
    download.add_argument("--start", default="2016-01-01")
    download.add_argument("--end", default="2024-06-28")
    download.set_defaults(func=cmd_download)
    replay = sub.add_parser("replay")
    replay.add_argument("--research-dir")
    replay.add_argument("--start", default="2023-01-03")
    replay.add_argument("--end", default="2024-03-28")
    replay.add_argument("--profile", default="balanced")
    replay.add_argument("--horizon", default="mid")
    replay.add_argument("--max-sessions", type=int, default=0)
    replay.add_argument("--align-sessions", type=int, default=5)
    replay.add_argument("--align-only", action="store_true")
    replay.add_argument("--other-variants", action="store_true")
    replay.add_argument("--extra-sessions", type=int, default=3)
    replay.add_argument("--broad-slices", default="")
    replay.set_defaults(func=cmd_replay)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--research-dir")
    finalize.add_argument("--refresh-examples", action="store_true")
    finalize.add_argument("--test-results")
    finalize.set_defaults(func=cmd_finalize)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
