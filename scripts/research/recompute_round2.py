#!/usr/bin/env python3
"""Recompute repaired development metrics without overwriting pre-repair dumps."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.dataset import load_dataset
from app.services.research.labels import attach_event_labels
from app.services.research.portfolio import simulate_long_only
from app.services.research.protocol import PRIMARY_HORIZON


PENDING = {
    "review_status": "pending_review",
    "reason": "Pre-repair development dump or summary. Numbers are not an accepted conclusion.",
    "superseded_by_prefix": ".repaired-r2",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n")


def _run(args: list[str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.check_call(args, cwd=ROOT)


def _mark_pending(path: Path) -> None:
    if not path.is_file():
        return
    sidecar = path.with_name(path.name + ".PENDING_REVIEW.json")
    if sidecar.is_file():
        return
    _write(
        sidecar,
        {
            **PENDING,
            "original": str(path),
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="/opt/cursor/research/screener-radar-data/yahoo-daily")
    parser.add_argument("--runs", default="/opt/cursor/research/screener-radar-data/runs")
    parser.add_argument("--artifact-dir", default=str(ROOT / "docs/research/screener-radar/round2"))
    parser.add_argument("--skip-portfolio", action="store_true")
    parser.add_argument("--skip-radar-relabel", action="store_true")
    args = parser.parse_args()
    runs = Path(args.runs)
    artifacts = Path(args.artifact_dir)
    artifacts.mkdir(parents=True, exist_ok=True)
    python = sys.executable
    screener_rows = runs / "dev-screener-balanced-all-rows.json"
    screener_run = runs / "dev-screener-balanced-all.json"
    radar_events = runs / "dev-radar-daily-base-events.json"
    for path in runs.glob("dev-*"):
        if path.suffix in {".json", ".jsonl"} and ".repaired-r2" not in path.name:
            _mark_pending(path)

    _run(
        [
            python,
            "scripts/research/audit_run_dump.py",
            "--rows",
            str(screener_rows),
            "--days",
            str(screener_run),
            "--events",
            str(radar_events),
            "--partial-days",
            str(runs / "dev-screener-balanced-all.partial-days.jsonl"),
            "--partial-rows",
            str(runs / "dev-screener-balanced-all.partial-rows.jsonl"),
            "--out",
            str(artifacts / "dump-uniqueness.json"),
        ]
    )
    _run(
        [
            python,
            "scripts/research/analyze_screener_rows.py",
            "--rows",
            str(screener_rows),
            "--out",
            str(runs / "dev-screener-balanced-all.repaired-r2.rows-analysis.json"),
        ]
    )
    _run(
        [
            python,
            "scripts/research/analyze_screener_modes.py",
            "--rows",
            str(screener_rows),
            "--out",
            str(runs / "dev-screener-balanced-all.repaired-r2.modes.json"),
        ]
    )
    _run(
        [
            python,
            "scripts/research/summarize_screener_run.py",
            "--run",
            str(screener_run),
            "--out",
            str(runs / "dev-screener-balanced-all.repaired-r2.summary-from-old-days.json"),
        ]
    )

    labeled_radar = runs / "dev-radar-daily-base.repaired-r2-events.json"
    if not args.skip_radar_relabel and radar_events.is_file():
        dataset = load_dataset(args.dataset)
        events = json.loads(radar_events.read_text(encoding="utf-8"))
        labeled = attach_event_labels(
            events,
            dataset,
            horizon=PRIMARY_HORIZON,
            universe_tickers=dataset.tickers(),
            allow_sealed=False,
        )
        _write(labeled_radar, labeled)
        _run(
            [
                python,
                "scripts/research/analyze_radar.py",
                "--events",
                str(labeled_radar),
                "--out",
                str(runs / "dev-radar-daily-base.repaired-r2.summary.json"),
            ]
        )
        _run(
            [
                python,
                "scripts/research/analyze_combo.py",
                "--events",
                str(labeled_radar),
                "--screener-rows",
                str(screener_rows),
                "--out",
                str(runs / "dev-combo-prior-screener.repaired-r2.json"),
            ]
        )
        _run(
            [
                python,
                "scripts/research/extract_radar_portfolio_signals.py",
                "--events",
                str(labeled_radar),
                "--out",
                str(runs / "dev-radar-daily-base.repaired-r2.signals.json"),
            ]
        )

    if not args.skip_portfolio:
        dataset = load_dataset(args.dataset)
        jobs = [
            (
                runs / "dev-screener-balanced-all.top10-signals.json",
                runs / "dev-screener-balanced-all.repaired-r2.portfolio-10bps.json",
                10.0,
            ),
            (
                runs / "dev-momentum63.top10-signals.json",
                runs / "dev-momentum63.repaired-r2.portfolio-10bps.json",
                10.0,
            ),
        ]
        radar_signals = runs / "dev-radar-daily-base.repaired-r2.signals.json"
        if radar_signals.is_file():
            jobs.append(
                (
                    radar_signals,
                    runs / "dev-radar-daily-base.repaired-r2.portfolio-10bps.json",
                    10.0,
                )
            )
        combo = runs / "dev-combo-prior-screener.repaired-r2.json"
        if combo.is_file():
            combo_payload = json.loads(combo.read_text(encoding="utf-8"))
            combo_signals = [
                {
                    "ticker": event.get("ticker"),
                    "signal_date": event.get("trading_date"),
                    "selected_view_rank": 1,
                    "rank": 1,
                    "protocol": "event_plus_alphabetical_research",
                    "not_production_radar_rank": True,
                }
                for event in combo_payload.get("overlap_events") or []
            ]
            combo_signal_path = runs / "dev-combo-prior-screener.repaired-r2.signals.json"
            _write(combo_signal_path, combo_signals)
            jobs.append(
                (
                    combo_signal_path,
                    runs / "dev-combo-prior-screener.repaired-r2.portfolio-10bps.json",
                    10.0,
                )
            )
        for signals_path, out_path, cost in jobs:
            signals = json.loads(Path(signals_path).read_text(encoding="utf-8"))
            result = simulate_long_only(dataset, signals, cost_bps=cost, hold_days=20)
            slim = {
                key: result[key]
                for key in result
                if key not in {"trades", "equity_curve", "rejected"}
            }
            slim["trade_head"] = (result.get("trades") or [])[:5]
            slim["equity_head"] = (result.get("equity_curve") or [])[:5]
            slim["equity_tail"] = (result.get("equity_curve") or [])[-5:]
            _write(out_path, result)
            _write(out_path.with_name(out_path.stem + ".slim.json"), slim)

    hashes = {}
    for path in sorted(runs.glob("*.repaired-r2*")):
        hashes[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    for path in (artifacts / "dump-uniqueness.json",):
        if path.is_file():
            hashes[path.name] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    _write(
        artifacts / "artifact-hashes.json",
        {
            "review_status": "repaired_round2",
            "runs_dir_note": "Full repaired JSON lives beside the original dumps; this file is the reviewable hash index.",
            "reproduce": [
                "PYTHONPATH=backend python scripts/research/recompute_round2.py",
                "PYTHONPATH=backend python -m pytest tests/test_research_review_round2.py tests/test_research_screener_radar.py",
            ],
            "files": hashes,
        },
    )
    print(json.dumps({"wrote_hashes": str(artifacts / "artifact-hashes.json"), "files": len(hashes)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
