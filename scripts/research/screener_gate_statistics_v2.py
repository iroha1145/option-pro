"""Fixed-calendar labels and paired increments for frozen gate lists.

Does not rescore, search parameters, or overwrite the #184 return_pack.
Old technical Top-K ids stay on their original candidate hash. Discovery
checkpoints from a later run live in a separate directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from screener_gate_candidates_v1 import (  # noqa: E402
    B0_CURRENT,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    VARIANTS,
)

HOLDOUT_FROM = date(2024, 7, 1)
LAST_OPEN_SESSION = date(2024, 6, 28)
LABEL_HS = (5, 20, 63)
BOOTSTRAP_SEED = 174
LAYERS = ("discovery", "technical_entry", "qualified_entry")
IDENTITY_KEYS = (
    "production_anchor",
    "candidates_sha256",
    "adapter_sha256",
    "bars_sha256",
    "profile",
    "horizon",
    "session_date",
)


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _percentile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = q * (len(ordered) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(ordered) - 1)
    frac = rank - lo
    return ordered[lo] * (1.0 - frac) + ordered[hi] * frac


def _ci(samples: Sequence[float]) -> list[float | None]:
    if not samples:
        return [None, None]
    ordered = sorted(samples)
    return [ordered[int(0.025 * (len(ordered) - 1))], ordered[int(0.975 * (len(ordered) - 1))]]


def exchange_calendar(panel: Mapping[str, Any], *, last_open: date = LAST_OPEN_SESSION, holdout_from: date = HOLDOUT_FROM) -> list[date]:
    dates = set()
    for series in panel.values():
        for session in series.dates:
            if session <= last_open and session < holdout_from:
                dates.add(session)
    return sorted(dates)


class LabelBook:
    """Labels on one exchange calendar. Missing bars stay missing."""

    def __init__(self, panel: Mapping[str, Any], calendar: Sequence[date], *, holdout_from: date = HOLDOUT_FROM):
        self.panel = panel
        self.calendar = list(calendar)
        self.holdout_from = holdout_from
        self._at = {session: index for index, session in enumerate(self.calendar)}
        self._series_at = {
            sid: {session: index for index, session in enumerate(series.dates)}
            for sid, series in panel.items()
        }

    def _shift(self, session: date, steps: int) -> date | None:
        index = self._at.get(session)
        if index is None:
            return None
        target = index + steps
        if target < 0 or target >= len(self.calendar):
            return None
        shifted = self.calendar[target]
        if shifted >= self.holdout_from:
            return None
        return shifted

    def _px(self, security_id: str, session: date | None, field: str) -> float | None:
        if session is None:
            return None
        series = self.panel.get(security_id)
        index = None if series is None else self._series_at.get(security_id, {}).get(session)
        if series is None or index is None:
            return None
        value = float(getattr(series, field)[index])
        if not math.isfinite(value) or value <= 0:
            return None
        return value

    def label(self, security_id: str, session: date, horizon: int, *, use_open: bool) -> float | None:
        if horizon < 1:
            return None
        if use_open:
            left_day = self._shift(session, 1)
            right_day = self._shift(session, 1 + horizon)
            left = self._px(security_id, left_day, "open")
            right = self._px(security_id, right_day, "open")
        else:
            right_day = self._shift(session, horizon)
            left = self._px(security_id, session, "close")
            right = self._px(security_id, right_day, "close")
        if left is None or right is None:
            return None
        return right / left - 1.0

    def path(self, security_id: str, session: date, horizon: int, *, use_open: bool) -> dict[str, float | None]:
        """MAE/MFE inside the hold. Open exits exclude the exit session's later range."""

        empty = {"mae": None, "mfe": None}
        if use_open:
            entry_day = self._shift(session, 1)
            exit_day = self._shift(session, 1 + horizon)
            entry = self._px(security_id, entry_day, "open")
            if entry_day is None or exit_day is None or entry is None:
                return empty
            start = self._at[entry_day]
            stop = self._at[exit_day]
            window = self.calendar[start:stop]
        else:
            exit_day = self._shift(session, horizon)
            entry = self._px(security_id, session, "close")
            if exit_day is None or entry is None or session not in self._at:
                return empty
            start = self._at[session] + 1
            stop = self._at[exit_day]
            window = self.calendar[start : stop + 1]
        if not window:
            return empty
        highs = []
        lows = []
        for day in window:
            high = self._px(security_id, day, "high")
            low = self._px(security_id, day, "low")
            if high is None or low is None:
                return empty
            highs.append(high)
            lows.append(low)
        return {"mae": min(lows) / entry - 1.0, "mfe": max(highs) / entry - 1.0}

    def basket(self, ids: Sequence[str], session: date, horizon: int, *, use_open: bool) -> dict[str, Any]:
        ordered = [str(item) for item in ids]
        if not ordered:
            return {
                "full_mean": None,
                "available_mean": None,
                "n_ids": 0,
                "n_labels": 0,
                "coverage": None,
                "reason": "empty_basket_not_zero",
            }
        labels = [self.label(item, session, horizon, use_open=use_open) for item in ordered]
        present = [float(value) for value in labels if value is not None]
        paths = [self.path(item, session, horizon, use_open=use_open) for item in ordered]
        maes = [item["mae"] for item in paths if item["mae"] is not None]
        mfes = [item["mfe"] for item in paths if item["mfe"] is not None]
        return {
            "full_mean": _mean(present) if len(present) == len(ordered) else None,
            "available_mean": _mean(present),
            "n_ids": len(ordered),
            "n_labels": len(present),
            "coverage": len(present) / len(ordered),
            "mae_mean": _mean([float(value) for value in maes]),
            "mfe_mean": _mean([float(value) for value in mfes]),
            "reason": None if len(present) == len(ordered) else "incomplete_basket",
        }


def circular_block_indices(n: int, block: int, reps: int, seed: int) -> list[list[int]]:
    if n <= 0:
        return []
    width = max(1, min(int(block), n))
    rng = random.Random(seed)
    draws = []
    for _ in range(reps):
        sample: list[int] = []
        while len(sample) < n:
            start = rng.randrange(n)
            for step in range(width):
                sample.append((start + step) % n)
                if len(sample) >= n:
                    break
        draws.append(sample)
    return draws


def paired_bootstrap(values: Sequence[float | None], *, block: int, reps: int, seed: int) -> dict[str, Any]:
    """Resample the full calendar, including missing slots. No compounded drawdown."""

    observed = [float(value) for value in values if value is not None]
    replicates = []
    for indexes in circular_block_indices(len(values), block, reps, seed):
        picked = [values[index] for index in indexes if values[index] is not None]
        if picked:
            replicates.append(sum(float(value) for value in picked) / len(picked))
    return {
        "conditional_mean": _mean(observed),
        "n_days": len(values),
        "n_valid": len(observed),
        "n_missing": len(values) - len(observed),
        "ci95": _ci(replicates),
        "reps": reps,
        "seed": seed,
        "block": block,
        "note": "same-day paired increment; missing calendar slots are kept and not concatenated",
    }


def freeze_layers(result: Mapping[str, Any], ranked_stocks: Callable[..., Sequence[Mapping[str, Any]]], *, identity: Mapping[str, Any]) -> dict[str, Any]:
    for key in IDENTITY_KEYS:
        if not identity.get(key):
            raise ValueError(f"checkpoint identity missing {key}")
    if str(identity["session_date"]) != str(result.get("session_date")):
        raise ValueError("identity session_date does not match the scored session")
    frozen: dict[str, Any] = {"identity": {key: identity[key] for key in IDENTITY_KEYS}, "layers": {}}
    for variant in VARIANTS:
        frozen["layers"][variant] = {}
        for layer in LAYERS:
            rows = list(ranked_stocks(result, variant, layer=layer))
            frozen["layers"][variant][layer] = [
                {
                    "security_id": row.get("security_id"),
                    "score": row.get("score"),
                    "algorithm_id": row.get("algorithm_id"),
                    "sector_context": row.get("sector_context"),
                    "rejection_reasons": list(row.get("rejection_reasons") or []),
                }
                for row in rows
            ]

    def ids(variant: str, layer: str) -> list[str]:
        return [str(item["security_id"]) for item in frozen["layers"][variant][layer]]

    for layer in ("technical_entry", "qualified_entry"):
        baseline = ids(G1_STOCK_REFERENCE, layer)
        if ids(G2_EXTENSION_DISCOVERY, layer) != baseline or ids(G3_RISK_DISCOVERY, layer) != baseline:
            raise ValueError(f"{layer} G2/G3 list differs from G1")
    return frozen


def write_checkpoint(directory: str | Path, payload: Mapping[str, Any]) -> Path:
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    session = str(payload["identity"]["session_date"])
    path = folder / f"{session}.json"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _top_ids(blob: Mapping[str, Any], k: int) -> list[str]:
    value = blob.get(f"top{k}")
    if value is None:
        top = blob.get("top") or {}
        value = top.get(k) if isinstance(top, Mapping) else None
        if value is None and isinstance(top, Mapping):
            value = top.get(str(k))
    return [str(item) for item in (value or [])]


def relabel_old_technical_pack(research: Path, *, reps: int) -> dict[str, Any]:
    pack = research / "return_pack"
    out = research / "return_pack_metrics_v2"
    if (out / "manifest.json").exists():
        raise FileExistsError(out / "manifest.json")
    manifest = _load_json(pack / "manifest.json")
    bars = research / "restricted_yahoo_bars.json"
    expected = ((manifest.get("data") or {}).get("yahoo_bars_sha256"))
    actual = sha256_file(bars)
    if not expected or actual != expected:
        raise RuntimeError(f"yahoo bars hash mismatch: expected {expected} actual {actual}")
    from screener_gate_replay_v1 import LAST_OPEN_SESSION as replay_last
    from screener_gate_replay_v1 import HOLDOUT_FROM as replay_holdout
    from screener_gate_replay_v1 import load_restricted_panel

    if replay_last != LAST_OPEN_SESSION or replay_holdout != HOLDOUT_FROM:
        raise RuntimeError("statistics calendar constants drifted from the replay module")
    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    book = LabelBook(panel, exchange_calendar(panel))
    with (pack / "paired_daily.csv").open(encoding="utf-8") as handle:
        daily_rows = list(csv.DictReader(handle))
    old_hashes = {path.name: sha256_file(path) for path in sorted(pack.iterdir()) if path.is_file()}
    paired_rows = []
    diffs: dict[str, dict[int, dict[str, list]]] = {
        variant: {h: {"close": [], "open": []} for h in LABEL_HS} for variant in VARIANTS if variant != B0_CURRENT
    }
    levels: dict[str, dict[int, dict[str, list]]] = {
        variant: {h: {"close_full": [], "close_available": [], "open_full": []} for h in LABEL_HS} for variant in VARIANTS
    }
    coverage = {variant: {"added_days": 0, "removed_days": 0, "added_names": 0, "removed_names": 0} for variant in VARIANTS if variant != B0_CURRENT}
    for row in daily_rows:
        session = date.fromisoformat(row["session_date"])
        parsed = {variant: json.loads(row[variant]) for variant in VARIANTS}
        out_row: dict[str, Any] = {"session_date": row["session_date"], "layer": "technical_entry", "provenance": "old_candidate_lists"}
        baskets = {}
        for variant in VARIANTS:
            baskets[variant] = {}
            for k in (5, 10, 20):
                ids = _top_ids(parsed[variant], k)
                baskets[variant][k] = {
                    "ids": ids,
                    "close": {h: book.basket(ids, session, h, use_open=False) for h in LABEL_HS},
                    "open": {h: book.basket(ids, session, h, use_open=True) for h in LABEL_HS},
                }
            for h in LABEL_HS:
                close_b = baskets[variant][20]["close"][h]
                open_b = baskets[variant][20]["open"][h]
                levels[variant][h]["close_full"].append(close_b["full_mean"])
                levels[variant][h]["close_available"].append(close_b["available_mean"])
                levels[variant][h]["open_full"].append(open_b["full_mean"])
                out_row[f"{variant}_h{h}_close_full"] = close_b["full_mean"]
                out_row[f"{variant}_h{h}_close_available"] = close_b["available_mean"]
                out_row[f"{variant}_h{h}_close_n"] = close_b["n_labels"]
                out_row[f"{variant}_h{h}_close_ids"] = close_b["n_ids"]
                out_row[f"{variant}_h{h}_open_full"] = open_b["full_mean"]
                out_row[f"{variant}_h{h}_open_available"] = open_b["available_mean"]
                out_row[f"{variant}_old_h{h}_close_mean"] = parsed[variant].get(f"h{h}_close_mean")
        b0_ids = set(baskets[B0_CURRENT][20]["ids"])
        for variant in VARIANTS:
            if variant == B0_CURRENT:
                continue
            ids = set(baskets[variant][20]["ids"])
            added, removed = sorted(ids - b0_ids), sorted(b0_ids - ids)
            if added:
                coverage[variant]["added_days"] += 1
                coverage[variant]["added_names"] += len(added)
            if removed:
                coverage[variant]["removed_days"] += 1
                coverage[variant]["removed_names"] += len(removed)
            out_row[f"{variant}_added"] = added
            out_row[f"{variant}_removed"] = removed
            for h in LABEL_HS:
                left = baskets[B0_CURRENT][20]["close"][h]["full_mean"]
                right = baskets[variant][20]["close"][h]["full_mean"]
                open_left = baskets[B0_CURRENT][20]["open"][h]["full_mean"]
                open_right = baskets[variant][20]["open"][h]["full_mean"]
                close_diff = None if left is None or right is None else right - left
                open_diff = None if open_left is None or open_right is None else open_right - open_left
                diffs[variant][h]["close"].append(close_diff)
                diffs[variant][h]["open"].append(open_diff)
                out_row[f"{variant}_minus_b0_h{h}_close"] = close_diff
                out_row[f"{variant}_minus_b0_h{h}_open"] = open_diff
        paired_rows.append(out_row)
    bootstrap = {}
    for variant, horizons in diffs.items():
        bootstrap[variant] = {}
        for h, sides in horizons.items():
            clean_close = [float(value) for value in sides["close"] if value is not None]
            bootstrap[variant][str(h)] = {
                "close": {
                    "H": paired_bootstrap(sides["close"], block=h, reps=reps, seed=BOOTSTRAP_SEED),
                    "2H": paired_bootstrap(sides["close"], block=2 * h, reps=reps, seed=BOOTSTRAP_SEED),
                    "percentiles": {
                        "p05": _percentile(clean_close, 0.05),
                        "p50": _percentile(clean_close, 0.50),
                        "p95": _percentile(clean_close, 0.95),
                    },
                },
                "open": {
                    "H": paired_bootstrap(sides["open"], block=h, reps=reps, seed=BOOTSTRAP_SEED),
                    "2H": paired_bootstrap(sides["open"], block=2 * h, reps=reps, seed=BOOTSTRAP_SEED),
                },
            }
    yearly: dict[str, dict[str, Any]] = {}
    for row in paired_rows:
        year = row["session_date"][:4]
        bucket = yearly.setdefault(year, {variant: [] for variant in VARIANTS})
        for variant in VARIANTS:
            bucket[variant].append(row.get(f"{variant}_h20_close_full"))
    summary = {
        "layer": "technical_entry_relabel_of_old_lists",
        "provenance": "old #184 technical Top-K ids; labels recomputed; not a new candidate replay",
        "discovery_lists": None,
        "discovery_reason": "old summary stored discovery_n and display_best only; discovery Top-K was not frozen and is not invented here",
        "bootstrap_paired_top20": bootstrap,
        "yearly_h20_close_full_mean": {
            year: {variant: _mean([value for value in values if value is not None]) for variant, values in buckets.items()}
            for year, buckets in yearly.items()
        },
        "coverage_top20": coverage,
        "level_means": {
            variant: {
                str(h): {name: _mean([value for value in values if value is not None]) for name, values in sides.items()}
                for h, sides in horizons.items()
            }
            for variant, horizons in levels.items()
        },
        "empty_basket_is_not_zero": True,
        "pseudo_nav_drawdown_emitted": False,
    }
    scoring_hashes = manifest.get("code_hashes") or {}
    out_manifest = {
        "protocol": "us-eod-screener-gate-metrics-v2",
        "production_anchor": manifest.get("production_anchor"),
        "scoring_code_hashes": scoring_hashes,
        "label_code": "screener_gate_statistics_v2.py",
        "label_code_sha256": sha256_file(Path(__file__).resolve()),
        "yahoo_bars_sha256": actual,
        "old_pack_sha256": old_hashes,
        "sessions": {"start": daily_rows[0]["session_date"], "end": daily_rows[-1]["session_date"], "n": len(daily_rows)},
        "holdout_sealed_from": HOLDOUT_FROM.isoformat(),
        "label_definitions": {
            "close": "T_close_to_calendar_T_plus_H_close",
            "open": "E_open_to_E_plus_H_open",
            "E": "T_plus_1_exchange_session",
        },
        "bootstrap": {"seed": BOOTSTRAP_SEED, "reps": reps, "blocks": ["H", "2H"]},
        "broad_slices_used_as_g1": False,
        "broad_slice_note": "stored grouped-daily slices used a ticker-suffix type guess and are not G1 evidence",
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "manifest.json").write_text(json.dumps(out_manifest, indent=2), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(out / "paired_daily.csv", paired_rows)
    _write_csv(out / "gate_attribution.csv", _attribution_rows(paired_rows))
    examples_src = pack / "examples.csv"
    (out / "examples.csv").write_text(examples_src.read_text(encoding="utf-8"), encoding="utf-8")
    validation = _validation_text(out_manifest, summary)
    (out / "validation.md").write_text(validation, encoding="utf-8")
    return {"pack": str(out), "n": len(paired_rows), "coverage": coverage}


def _attribution_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY):
            added = row.get(f"{variant}_added") or []
            removed = row.get(f"{variant}_removed") or []
            out.append(
                {
                    "session_date": row["session_date"],
                    "layer": "technical_entry",
                    "variant": variant,
                    "comparison": f"{variant} - {B0_CURRENT}",
                    "added_n": len(added),
                    "removed_n": len(removed),
                    "added": added,
                    "removed": removed,
                }
            )
    return out


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    for row in rows:
        for key in row:
            if key not in keys:
                keys.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: json.dumps(row.get(key), ensure_ascii=False) if isinstance(row.get(key), (dict, list)) else row.get(key)
                    for key in keys
                }
            )


def _validation_text(manifest: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    g1 = (((summary.get("bootstrap_paired_top20") or {}).get(G1_STOCK_REFERENCE) or {}).get("20") or {}).get("close") or {}
    return "\n".join(
        [
            "# Validation — metrics v2",
            "",
            "This directory relabels frozen #184 technical Top-K ids. It does not replace return_pack/.",
            "",
            f"- production anchor: `{manifest.get('production_anchor')}`",
            f"- yahoo bars sha256: `{manifest.get('yahoo_bars_sha256')}`",
            f"- label code sha256: `{manifest.get('label_code_sha256')}`",
            f"- scoring hashes kept from the old manifest: `{json.dumps(manifest.get('scoring_code_hashes'), ensure_ascii=False)}`",
            f"- sessions: {manifest.get('sessions')}",
            f"- G1-B0 H20 close paired bootstrap: `{json.dumps(g1, ensure_ascii=False)}`",
            "- discovery Top-K: not recovered from discovery_n; left null",
            "- empty baskets stay null, not zero",
            "- no compounded overlapping-label drawdown",
            "- holdout 2024-07-01+ not crossed",
            "- suffix-classified broad slices are not used",
        ]
    ) + "\n"


def evaluate_frozen_checkpoints(research: Path, checkpoint_dir: Path, *, reps: int) -> dict[str, Any]:
    """Score discovery/technical/qualified lists saved by freeze_layers. Separate from the old pack."""

    from screener_gate_replay_v1 import load_restricted_panel

    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    book = LabelBook(panel, exchange_calendar(panel))
    files = sorted(checkpoint_dir.glob("*.json"))
    comparisons = (
        ("technical_entry", G1_STOCK_REFERENCE, B0_CURRENT),
        ("discovery", G2_EXTENSION_DISCOVERY, G1_STOCK_REFERENCE),
        ("discovery", G3_RISK_DISCOVERY, G2_EXTENSION_DISCOVERY),
    )
    rows = []
    series: dict[str, dict[int, list]] = {}
    for path in files:
        payload = _load_json(path)
        session = date.fromisoformat(payload["identity"]["session_date"])
        for layer, left, right in comparisons:
            for k in (5, 10, 20):
                left_ids = [item["security_id"] for item in payload["layers"][left][layer][:k]]
                right_ids = [item["security_id"] for item in payload["layers"][right][layer][:k]]
                key = f"{layer}:{left}-{right}:k{k}"
                bucket = series.setdefault(key, {h: [] for h in LABEL_HS})
                record: dict[str, Any] = {
                    "session_date": session.isoformat(),
                    "layer": layer,
                    "comparison": f"{left} - {right}",
                    "k": k,
                    "added": sorted(set(left_ids) - set(right_ids)),
                    "removed": sorted(set(right_ids) - set(left_ids)),
                }
                for h in LABEL_HS:
                    a = book.basket(left_ids, session, h, use_open=False)
                    b = book.basket(right_ids, session, h, use_open=False)
                    ao = book.basket(left_ids, session, h, use_open=True)
                    bo = book.basket(right_ids, session, h, use_open=True)
                    close_diff = None if a["full_mean"] is None or b["full_mean"] is None else a["full_mean"] - b["full_mean"]
                    open_diff = None if ao["full_mean"] is None or bo["full_mean"] is None else ao["full_mean"] - bo["full_mean"]
                    bucket[h].append(close_diff)
                    record[f"h{h}_close_diff"] = close_diff
                    record[f"h{h}_open_diff"] = open_diff
                    record[f"h{h}_close_coverage_left"] = a["coverage"]
                    record[f"h{h}_mae_left"] = a["mae_mean"]
                    record[f"h{h}_mfe_left"] = a["mfe_mean"]
                rows.append(record)
    summary = {
        key: {
            str(h): {
                "H": paired_bootstrap(values, block=h, reps=reps, seed=BOOTSTRAP_SEED),
                "2H": paired_bootstrap(values, block=2 * h, reps=reps, seed=BOOTSTRAP_SEED),
            }
            for h, values in horizons.items()
        }
        for key, horizons in series.items()
    }
    return {"rows": rows, "bootstrap": summary, "n_checkpoints": len(files)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", default=str(Path.home() / "optix-research" / "screener-gate-v1"))
    parser.add_argument("--reps", type=int, default=2000)
    parser.add_argument("--align-sessions", type=int, default=0)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--max-sessions", type=int, default=0)
    return parser


def _align(research: Path, n: int) -> dict[str, Any]:
    from screener_gate_adapter_v1 import ranked_stocks
    from screener_gate_replay_v1 import load_restricted_panel, run_one_session, session_dates
    from app.services.eod_limited.inference import UNIVERSE_VERSION
    from app.services.eod_limited.market_registry import load_market_registry
    from app.services.research_eod_v1.constants import ALGORITHMS
    from screener_gate_candidates_v1 import HIGH_ATR

    del UNIVERSE_VERSION
    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    summary = _load_json(research / "return_pack" / "summary.json")
    by_day = {item["session_date"]: item for item in summary["sessions"]}
    sessions = session_dates(panel, start=date(2022, 1, 3), end=date(2024, 3, 28))[:n]
    registry = load_market_registry()
    mismatches = []
    for session in sessions:
        result = run_one_session(
            panel,
            session,
            registry=registry,
            theme_ids=list(registry["sectors"]),
            families=list(ALGORITHMS),
            profile="balanced",
            horizon="mid",
        )
        old = by_day[session.isoformat()]
        for variant in VARIANTS:
            new_ids = [row["security_id"] for row in ranked_stocks(result, variant, layer="technical_entry")[:20]]
            old_top = (old.get("variants") or {}).get(variant, {}).get("top") or {}
            old_ids = list(old_top.get(20) or old_top.get("20") or [])
            if new_ids != old_ids:
                mismatches.append({"session_date": session.isoformat(), "variant": variant, "new": new_ids, "old": old_ids})
        g1 = [row["security_id"] for row in ranked_stocks(result, G1_STOCK_REFERENCE, layer="technical_entry")]
        g2 = [row["security_id"] for row in ranked_stocks(result, G2_EXTENSION_DISCOVERY, layer="technical_entry")]
        g3 = [row["security_id"] for row in ranked_stocks(result, G3_RISK_DISCOVERY, layer="technical_entry")]
        if g1 != g2 or g1 != g3:
            mismatches.append({"session_date": session.isoformat(), "variant": "entry_identity", "new": g1, "old": g2})
        for item in result["records"]:
            if item["facts"].asset_track != "stock":
                continue
            production = HIGH_ATR in (item["row"].get("rejection_reasons") or ())
            if bool(item["decisions"][B0_CURRENT].high_atr) != bool(production):
                mismatches.append(
                    {
                        "session_date": session.isoformat(),
                        "security_id": item["row"].get("security_id"),
                        "production_high_atr": production,
                        "b0_high_atr": item["decisions"][B0_CURRENT].high_atr,
                    }
                )
                break
    report = {"compared_sessions": [item.isoformat() for item in sessions], "mismatches": mismatches, "aligned": not mismatches}
    dest = research / "alignment_v2"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _discover(research: Path, *, max_sessions: int) -> dict[str, Any]:
    from screener_gate_adapter_v1 import ranked_stocks
    from screener_gate_replay_v1 import load_restricted_panel, run_one_session, session_dates
    from app.services.eod_limited.market_registry import load_market_registry
    from app.services.research_eod_v1.constants import ALGORITHMS

    dest = research / "return_pack_discovery_v2"
    checkpoints = dest / "checkpoints"
    if any(checkpoints.glob("*.json")):
        raise FileExistsError(checkpoints)
    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    sessions = session_dates(panel, start=date(2022, 1, 3), end=date(2024, 3, 28))
    if max_sessions:
        sessions = sessions[:max_sessions]
    registry = load_market_registry()
    identity_base = {
        "production_anchor": "d16b25e3812dce9adf16e2ca993b4f2c38d7b1ef",
        "candidates_sha256": sha256_file(Path(__file__).resolve().parent / "screener_gate_candidates_v1.py"),
        "adapter_sha256": sha256_file(Path(__file__).resolve().parent / "screener_gate_adapter_v1.py"),
        "bars_sha256": sha256_file(research / "restricted_yahoo_bars.json"),
        "profile": "balanced",
        "horizon": "mid",
    }
    for index, session in enumerate(sessions, start=1):
        result = run_one_session(
            panel,
            session,
            registry=registry,
            theme_ids=list(registry["sectors"]),
            families=list(ALGORITHMS),
            profile="balanced",
            horizon="mid",
        )
        payload = freeze_layers(
            result,
            ranked_stocks,
            identity={**identity_base, "session_date": result["session_date"]},
        )
        write_checkpoint(checkpoints, payload)
        print(f"checkpoint {index}/{len(sessions)} {session.isoformat()}", flush=True)
        del result
    return {"checkpoints": str(checkpoints), "n": len(sessions)}


def main() -> None:
    args = build_parser().parse_args()
    research = Path(args.research_dir)
    if args.align_sessions:
        report = _align(research, args.align_sessions)
        print(json.dumps({"aligned": report["aligned"], "mismatches": len(report["mismatches"])}, indent=2))
        if not report["aligned"]:
            raise SystemExit(1)
    if args.discover:
        print(json.dumps(_discover(research, max_sessions=args.max_sessions), indent=2))
        return
    if args.align_sessions and not args.discover:
        return
    print(json.dumps(relabel_old_technical_pack(research, reps=args.reps), indent=2, default=str))


if __name__ == "__main__":
    main()
