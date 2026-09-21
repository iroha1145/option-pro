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


FIXED_COMPARISONS = (
    ("technical_entry", G1_STOCK_REFERENCE, B0_CURRENT),
    ("discovery", G2_EXTENSION_DISCOVERY, G1_STOCK_REFERENCE),
    ("discovery", G3_RISK_DISCOVERY, G2_EXTENSION_DISCOVERY),
)


def checkpoint_identity_core(payload: Mapping[str, Any]) -> dict[str, Any]:
    identity = payload["identity"]
    return {key: identity[key] for key in IDENTITY_KEYS if key != "session_date"}


def entry_invariant_failures(payload: Mapping[str, Any]) -> list[str]:
    """technical_entry and qualified_entry must keep G2 = G3 = G1. Discovery may differ."""

    failures = []
    for layer in ("technical_entry", "qualified_entry"):
        baseline = [str(item["security_id"]) for item in payload["layers"][G1_STOCK_REFERENCE][layer]]
        for variant in (G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY):
            current = [str(item["security_id"]) for item in payload["layers"][variant][layer]]
            if current != baseline:
                failures.append(layer)
                break
    return failures


def _layer_ids(payload: Mapping[str, Any], variant: str, layer: str, k: int | None = None) -> list[str]:
    rows = payload["layers"][variant][layer]
    if k is not None:
        rows = rows[:k]
    return [str(item["security_id"]) for item in rows]


def _present(values: Sequence[Any]) -> list[float]:
    return [float(value) for value in values if value is not None]


def records_for_checkpoint(payload: Mapping[str, Any], book: LabelBook) -> list[dict[str, Any]]:
    session = date.fromisoformat(str(payload["identity"]["session_date"]))
    records = []
    for layer, left_name, right_name in FIXED_COMPARISONS:
        for k in (5, 10, 20):
            left_ids = _layer_ids(payload, left_name, layer, k)
            right_ids = _layer_ids(payload, right_name, layer, k)
            added = sorted(set(left_ids) - set(right_ids))
            removed = sorted(set(right_ids) - set(left_ids))
            record: dict[str, Any] = {
                "session_date": session.isoformat(),
                "layer": layer,
                "comparison": f"{left_name} - {right_name}",
                "k": k,
                "added": added,
                "removed": removed,
                "order_only": bool(left_ids != right_ids and set(left_ids) == set(right_ids)),
            }
            for horizon in LABEL_HS:
                left_close = book.basket(left_ids, session, horizon, use_open=False)
                right_close = book.basket(right_ids, session, horizon, use_open=False)
                left_open = book.basket(left_ids, session, horizon, use_open=True)
                right_open = book.basket(right_ids, session, horizon, use_open=True)
                added_close = book.basket(added, session, horizon, use_open=False)
                removed_close = book.basket(removed, session, horizon, use_open=False)
                close_diff = (
                    None
                    if left_close["full_mean"] is None or right_close["full_mean"] is None
                    else left_close["full_mean"] - right_close["full_mean"]
                )
                open_diff = (
                    None
                    if left_open["full_mean"] is None or right_open["full_mean"] is None
                    else left_open["full_mean"] - right_open["full_mean"]
                )
                record[f"h{horizon}_close_diff"] = close_diff
                record[f"h{horizon}_open_diff"] = open_diff
                record[f"h{horizon}_close_coverage_left"] = left_close["coverage"]
                record[f"h{horizon}_close_coverage_right"] = right_close["coverage"]
                record[f"h{horizon}_mae_left"] = left_close.get("mae_mean")
                record[f"h{horizon}_mfe_left"] = left_close.get("mfe_mean")
                record[f"h{horizon}_added_close_full"] = added_close["full_mean"]
                record[f"h{horizon}_removed_close_full"] = removed_close["full_mean"]
            records.append(record)
    return records


def _side_summary(values: Sequence[Any], *, block: int, reps: int) -> dict[str, Any]:
    clean = _present(values)
    return {
        "H": paired_bootstrap(values, block=block, reps=reps, seed=BOOTSTRAP_SEED),
        "2H": paired_bootstrap(values, block=2 * block, reps=reps, seed=BOOTSTRAP_SEED),
        "percentiles": {
            "p05": _percentile(clean, 0.05),
            "p50": _percentile(clean, 0.50),
            "p95": _percentile(clean, 0.95),
        },
    }


def _group_return(rows: Sequence[Mapping[str, Any]], key: str) -> dict[str, Any]:
    """Mean of a substitution group. Days with an empty group are omitted, not scored as zero."""

    selected = [row.get(key) for row in rows]
    present = _present(selected)
    return {
        "conditional_mean": _mean(present),
        "n_substitution_days": len(selected),
        "n_complete": len(present),
        "n_missing": len(selected) - len(present),
        "note": "empty substitution groups are omitted; they are not an observed zero return",
    }


def aggregate_discovery_records(records: Sequence[Mapping[str, Any]], *, reps: int) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in records:
        grouped.setdefault(f"{row['layer']}:{row['comparison']}:k{row['k']}", []).append(row)
    summary = {}
    for key, rows in grouped.items():
        added_names: list[str] = []
        removed_names: list[str] = []
        added_days = removed_days = membership_days = order_only_days = 0
        for row in rows:
            added = list(row.get("added") or [])
            removed = list(row.get("removed") or [])
            if added:
                added_days += 1
                added_names.extend(str(item) for item in added)
            if removed:
                removed_days += 1
                removed_names.extend(str(item) for item in removed)
            if added or removed:
                membership_days += 1
            if row.get("order_only"):
                order_only_days += 1
        horizons = {}
        for horizon in LABEL_HS:
            close_vals = [row.get(f"h{horizon}_close_diff") for row in rows]
            open_vals = [row.get(f"h{horizon}_open_diff") for row in rows]
            yearly: dict[str, list[Any]] = {}
            for row in rows:
                yearly.setdefault(str(row["session_date"])[:4], []).append(row.get(f"h{horizon}_close_diff"))
            added_rows = [row for row in rows if row.get("added")]
            removed_rows = [row for row in rows if row.get("removed")]
            horizons[str(horizon)] = {
                "close": _side_summary(close_vals, block=horizon, reps=reps),
                "open": _side_summary(open_vals, block=horizon, reps=reps),
                "label_coverage_left_mean": _mean(_present([row.get(f"h{horizon}_close_coverage_left") for row in rows])),
                "label_coverage_right_mean": _mean(_present([row.get(f"h{horizon}_close_coverage_right") for row in rows])),
                "mae_left": {
                    "mean": _mean(_present([row.get(f"h{horizon}_mae_left") for row in rows])),
                    "percentiles": {
                        "p05": _percentile(_present([row.get(f"h{horizon}_mae_left") for row in rows]), 0.05),
                        "p50": _percentile(_present([row.get(f"h{horizon}_mae_left") for row in rows]), 0.50),
                        "p95": _percentile(_present([row.get(f"h{horizon}_mae_left") for row in rows]), 0.95),
                    },
                },
                "mfe_left": {
                    "mean": _mean(_present([row.get(f"h{horizon}_mfe_left") for row in rows])),
                    "percentiles": {
                        "p05": _percentile(_present([row.get(f"h{horizon}_mfe_left") for row in rows]), 0.05),
                        "p50": _percentile(_present([row.get(f"h{horizon}_mfe_left") for row in rows]), 0.50),
                        "p95": _percentile(_present([row.get(f"h{horizon}_mfe_left") for row in rows]), 0.95),
                    },
                },
                "yearly_close_paired_mean": {
                    year: {
                        "mean": _mean(_present(values)),
                        "n_days": len(values),
                        "n_valid": len(_present(values)),
                    }
                    for year, values in yearly.items()
                },
                "added_names_close": _group_return(added_rows, f"h{horizon}_added_close_full"),
                "removed_names_close": _group_return(removed_rows, f"h{horizon}_removed_close_full"),
            }
        summary[key] = {
            "n_days": len(rows),
            "substitution": {
                "membership_change_days": membership_days,
                "added_days": added_days,
                "removed_days": removed_days,
                "order_only_days": order_only_days,
                "added_name_slots": len(added_names),
                "removed_name_slots": len(removed_names),
                "unique_added": sorted(set(added_names)),
                "unique_removed": sorted(set(removed_names)),
            },
            "horizons": horizons,
        }
    return summary


def evaluate_frozen_checkpoints(research: Path, checkpoint_dir: Path, *, reps: int) -> dict[str, Any]:
    """Score frozen three-layer lists. Does not write, and does not touch return_pack/."""

    from screener_gate_replay_v1 import load_restricted_panel

    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    book = LabelBook(panel, exchange_calendar(panel))
    files = sorted(checkpoint_dir.glob("*.json"))
    rows: list[dict[str, Any]] = []
    invariant_failures = []
    for path in files:
        payload = _load_json(path)
        failures = entry_invariant_failures(payload)
        if failures:
            invariant_failures.append({"session_date": payload["identity"]["session_date"], "layers": failures})
        rows.extend(records_for_checkpoint(payload, book))
    summary = aggregate_discovery_records(rows, reps=reps)
    bootstrap = {
        key: {
            horizon: {"H": blob["close"]["H"], "2H": blob["close"]["2H"]}
            for horizon, blob in group["horizons"].items()
        }
        for key, group in summary.items()
    }
    return {
        "rows": rows,
        "bootstrap": bootstrap,
        "summary": summary,
        "n_checkpoints": len(files),
        "entry_invariant_failures": invariant_failures,
    }


def _old_top20(session_blob: Mapping[str, Any], variant: str) -> list[str]:
    top = ((session_blob.get("variants") or {}).get(variant) or {}).get("top") or {}
    return [str(item) for item in (top.get(20) or top.get("20") or [])]


def compare_frozen_to_old_session(payload: Mapping[str, Any], old_session: Mapping[str, Any]) -> dict[str, Any]:
    technical = []
    discovery_counts = []
    for variant in VARIANTS:
        new_ids = _layer_ids(payload, variant, "technical_entry", 20)
        old_ids = _old_top20(old_session, variant)
        if new_ids != old_ids:
            technical.append({"variant": variant, "new": new_ids, "old": old_ids})
        old_blob = (old_session.get("variants") or {}).get(variant) or {}
        old_n = old_blob.get("discovery_n")
        new_n = len(payload["layers"][variant]["discovery"])
        if old_n is not None and int(old_n) != new_n:
            discovery_counts.append({"variant": variant, "old": int(old_n), "new": new_n})
    return {"technical": technical, "discovery_count": discovery_counts}


def write_discovery_evaluation(research: Path, *, reps: int) -> dict[str, Any]:
    """Write an independent discovery pack next to checkpoints. Refuses to overwrite."""

    from screener_gate_replay_v1 import load_restricted_panel

    dest = research / "return_pack_discovery_v2"
    checkpoints = dest / "checkpoints"
    outputs = (
        "manifest.json",
        "summary.json",
        "paired_daily.csv",
        "gate_attribution.csv",
        "examples.csv",
        "validation.md",
        "checkpoint_index.csv",
    )
    existing = [name for name in outputs if (dest / name).exists()]
    if existing:
        raise FileExistsError(dest / existing[0])
    files = sorted(checkpoints.glob("*.json"))
    if not files:
        raise FileNotFoundError(checkpoints)
    old_summary = _load_json(research / "return_pack" / "summary.json")
    old_by_day = {item["session_date"]: item for item in old_summary["sessions"]}
    panel, _coverage, _payload = load_restricted_panel(research, end=LAST_OPEN_SESSION)
    book = LabelBook(panel, exchange_calendar(panel))
    records: list[dict[str, Any]] = []
    identities = []
    invariant_failures = []
    technical_examples = []
    technical_mismatch_dates: list[str] = []
    discovery_count_examples = []
    technical_days = 0
    discovery_count_days = 0
    index_rows = []
    length_sums = {variant: {layer: 0 for layer in LAYERS} for variant in VARIANTS}
    qualified_nonzero = 0
    first_sample = None
    for path in files:
        payload = _load_json(path)
        session = str(payload["identity"]["session_date"])
        identities.append(checkpoint_identity_core(payload))
        failures = entry_invariant_failures(payload)
        if failures:
            invariant_failures.append({"session_date": session, "layers": failures})
        records.extend(records_for_checkpoint(payload, book))
        old = old_by_day.get(session)
        if old is None:
            technical_days += 1
            if len(technical_examples) < 8:
                technical_examples.append({"session_date": session, "missing_old_session": True})
        else:
            compared = compare_frozen_to_old_session(payload, old)
            if compared["technical"]:
                technical_days += 1
                technical_mismatch_dates.append(session)
                if len(technical_examples) < 8:
                    technical_examples.append({"session_date": session, "variants": compared["technical"]})
            if compared["discovery_count"]:
                discovery_count_days += 1
                if len(discovery_count_examples) < 8:
                    discovery_count_examples.append({"session_date": session, "variants": compared["discovery_count"]})
        counts = {}
        for variant in VARIANTS:
            counts[variant] = {layer: len(payload["layers"][variant][layer]) for layer in LAYERS}
            for layer, length in counts[variant].items():
                length_sums[variant][layer] += length
            if counts[variant]["qualified_entry"]:
                qualified_nonzero += 1
        if first_sample is None:
            first_sample = {
                "session_date": session,
                "discovery_top5": {
                    variant: _layer_ids(payload, variant, "discovery", 5) for variant in VARIANTS
                },
                "technical_top5": {
                    variant: _layer_ids(payload, variant, "technical_entry", 5) for variant in VARIANTS
                },
            }
        index_rows.append(
            {
                "session_date": session,
                "sha256": sha256_file(path),
                "counts": counts,
            }
        )
    cores = {json.dumps(item, sort_keys=True) for item in identities}
    if len(cores) != 1:
        raise RuntimeError("checkpoint identity hashes are not uniform")
    identity = identities[0]
    n = len(files)
    sessions = [row["session_date"] for row in index_rows]
    aggregated = aggregate_discovery_records(records, reps=reps)
    old_manifest = _load_json(research / "return_pack" / "manifest.json")
    old_pack = research / "return_pack"
    name_check = {
        "compared_sessions": n,
        "technical_top20_mismatch_days": technical_days,
        "technical_mismatch_dates": technical_mismatch_dates,
        "technical_examples": technical_examples,
        "discovery_count_diff_days": discovery_count_days,
        "discovery_count_examples": discovery_count_examples,
        "aligned_with_old_technical_top20": technical_days == 0,
        "provenance_note": (
            "new code reproduced the frozen #184 technical Top-20 on every checkpoint date; "
            "discovery lists still belong to this run's code hashes"
            if technical_days == 0
            else "new guards changed technical Top-20 versus the #184 pack; "
            "do not attach these discovery statistics to the old candidate hash"
        ),
    }
    summary = {
        "scope": "restricted_current_membership_exploratory",
        "not_full_market_excess": True,
        "scope_note": (
            "Labels use the restricted current-membership panel and its exchange calendar. "
            "This is not a full-market excess return."
        ),
        "entry_invariant": {
            "technical_entry_and_qualified_entry_g2_g3_equal_g1": not invariant_failures,
            "failures": invariant_failures,
            "note": "entry-list equality is an invariant, not a return experiment",
        },
        "technical_top20_vs_old_pack": name_check,
        "ranking_coverage": {
            "n_checkpoints": n,
            "mean_lengths": {
                variant: {layer: length_sums[variant][layer] / n for layer in LAYERS} for variant in VARIANTS
            },
            "qualified_entry_nonzero_variant_days": qualified_nonzero,
        },
        "sample": first_sample,
        "comparisons": aggregated,
        "empty_basket_is_not_zero": True,
        "pseudo_nav_drawdown_emitted": False,
        "fixed_comparisons": [f"{layer}: {left} - {right}" for layer, left, right in FIXED_COMPARISONS],
    }
    manifest = {
        "protocol": "us-eod-screener-gate-discovery-v2",
        "production_anchor": identity.get("production_anchor"),
        "profile": identity.get("profile"),
        "horizon": identity.get("horizon"),
        "scoring_code_hashes": {
            "candidates_sha256": identity.get("candidates_sha256"),
            "adapter_sha256": identity.get("adapter_sha256"),
        },
        "bars_sha256": identity.get("bars_sha256"),
        "label_code": "screener_gate_statistics_v2.py",
        "label_code_sha256": sha256_file(Path(__file__).resolve()),
        "old_pack_scoring_code_hashes": old_manifest.get("code_hashes"),
        "old_pack_sha256": {path.name: sha256_file(path) for path in sorted(old_pack.iterdir()) if path.is_file()},
        "sessions": {"start": sessions[0], "end": sessions[-1], "n": n},
        "complete_open_window": sessions[0] == "2022-01-03" and sessions[-1] == "2024-03-28" and n == 562,
        "holdout_sealed_from": HOLDOUT_FROM.isoformat(),
        "label_definitions": {
            "close": "T_close_to_calendar_T_plus_H_close",
            "open": "E_open_to_E_plus_H_open",
            "E": "T_plus_1_exchange_session",
        },
        "bootstrap": {"seed": BOOTSTRAP_SEED, "reps": reps, "blocks": ["H", "2H"]},
        "broad_slices_used_as_g1": False,
        "patch_note": "pr184_review_fixes.patch was not attached; boundary guards were reconstructed from REVIEW(4)",
    }
    examples = [
        row
        for row in records
        if row["k"] == 20 and (row["added"] or row["removed"] or row["order_only"]) and row["layer"] == "discovery"
    ][:12]
    if len(examples) < 12:
        examples.extend(
            row
            for row in records
            if row["k"] == 20 and (row["added"] or row["removed"]) and row["layer"] == "technical_entry"
        )
        examples = examples[:12]
    validation = _discovery_validation_text(manifest, summary)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (dest / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _write_csv(
        dest / "paired_daily.csv",
        [{key: value for key, value in row.items() if key not in {"added", "removed"}} for row in records],
    )
    _write_csv(
        dest / "gate_attribution.csv",
        [
            {key: row[key] for key in ("session_date", "layer", "comparison", "k", "added", "removed", "order_only")}
            for row in records
        ],
    )
    _write_csv(dest / "examples.csv", examples)
    _write_csv(dest / "checkpoint_index.csv", index_rows)
    (dest / "validation.md").write_text(validation, encoding="utf-8")
    (research / "alignment_v2").mkdir(parents=True, exist_ok=True)
    (research / "alignment_v2" / "full_technical_top20.json").write_text(
        json.dumps(name_check, indent=2),
        encoding="utf-8",
    )
    return {"pack": str(dest), "n": n, "technical_top20_mismatch_days": technical_days}


def _discovery_validation_text(manifest: Mapping[str, Any], summary: Mapping[str, Any]) -> str:
    name_check = summary.get("technical_top20_vs_old_pack") or {}
    g2 = ((summary.get("comparisons") or {}).get(f"discovery:{G2_EXTENSION_DISCOVERY} - {G1_STOCK_REFERENCE}:k20") or {})
    h20 = ((g2.get("horizons") or {}).get("20") or {}).get("close") or {}
    return "\n".join(
        [
            "# Validation — discovery v2",
            "",
            "Checkpoints are a separate scoring run from return_pack/. Entry lists are an invariant.",
            "Discovery comparisons are G2-G1 and G3-G2. Technical comparison is G1-B0.",
            "",
            f"- scope: `{summary.get('scope')}`",
            f"- {summary.get('scope_note')}",
            f"- production anchor: `{manifest.get('production_anchor')}`",
            f"- bars sha256: `{manifest.get('bars_sha256')}`",
            f"- this-run scoring hashes: `{json.dumps(manifest.get('scoring_code_hashes'), ensure_ascii=False)}`",
            f"- old pack scoring hashes: `{json.dumps(manifest.get('old_pack_scoring_code_hashes'), ensure_ascii=False)}`",
            f"- sessions: {manifest.get('sessions')}",
            f"- complete open window: {manifest.get('complete_open_window')}",
            f"- entry invariant holds: {(summary.get('entry_invariant') or {}).get('technical_entry_and_qualified_entry_g2_g3_equal_g1')}",
            f"- technical Top-20 versus old pack: `{json.dumps({k: name_check.get(k) for k in ('technical_top20_mismatch_days', 'aligned_with_old_technical_top20', 'provenance_note')}, ensure_ascii=False)}`",
            f"- discovery G2-G1 H20 close paired: `{json.dumps(h20, ensure_ascii=False)}`",
            "- added and removed names are grouped on their own; an empty group is not zero",
            "- no compounded overlapping-label drawdown",
            "- holdout 2024-07-01+ not crossed",
            "- suffix-classified broad slices are not a G1 reference",
            f"- {manifest.get('patch_note')}",
        ]
    ) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--research-dir", default=str(Path.home() / "optix-research" / "screener-gate-v1"))
    parser.add_argument("--reps", type=int, default=2000)
    parser.add_argument("--align-sessions", type=int, default=0)
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--resume-discover", action="store_true")
    parser.add_argument("--evaluate-discovery", action="store_true")
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


def _discover(research: Path, *, max_sessions: int, resume: bool = False) -> dict[str, Any]:
    from screener_gate_adapter_v1 import ranked_stocks
    from screener_gate_replay_v1 import load_restricted_panel, run_one_session, session_dates
    from app.services.eod_limited.market_registry import load_market_registry
    from app.services.research_eod_v1.constants import ALGORITHMS

    dest = research / "return_pack_discovery_v2"
    checkpoints = dest / "checkpoints"
    if any(checkpoints.glob("*.json")) and not resume:
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
        existing = checkpoints / f"{session.isoformat()}.json"
        if existing.exists():
            if not resume:
                raise FileExistsError(existing)
            kept = checkpoint_identity_core(_load_json(existing))
            for key, value in identity_base.items():
                if kept.get(key) != value:
                    raise RuntimeError(f"resume identity mismatch for {key} on {session.isoformat()}")
            print(f"keep {index}/{len(sessions)} {session.isoformat()}", flush=True)
            continue
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
        print(json.dumps(_discover(research, max_sessions=args.max_sessions, resume=args.resume_discover), indent=2))
        if not args.evaluate_discovery:
            return
    if args.evaluate_discovery:
        print(json.dumps(write_discovery_evaluation(research, reps=args.reps), indent=2, default=str))
        return
    if args.align_sessions and not args.discover:
        return
    print(json.dumps(relabel_old_technical_pack(research, reps=args.reps), indent=2, default=str))


if __name__ == "__main__":
    main()
