"""Round 1b re-evaluation on the frozen B0 tape. Does not overwrite Round 1 files."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION  # noqa: E402
from app.services.research_eod_v1.bootstrap import BOOTSTRAP_REPEATS, BOOTSTRAP_SEED  # noqa: E402
from app.services.research_eod_v1.capability import build_coverage_matrix  # noqa: E402
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.event_groups import EVENT_RULE_VERSION  # noqa: E402
from app.services.research_eod_v1.round1b import (  # noqa: E402
    EVENT_RULE,
    PAIRING_RULE,
    STATISTICS_RULE,
    analyze_rows_r1b,
    iter_variants_r1b,
    preregistered_realloc_neighbors,
)
from app.services.research_eod_v1.runs import cache_hit_definitions, run_dir_name, run_signature  # noqa: E402
from app.services.research_eod_v1.source_bind import (  # noqa: E402
    EXPECTED_B0_SHA256,
    bind_b0_tape,
    bind_limited_prefix,
    research_code_hashes,
)
from app.services.research_eod_v1.stats import STATISTICS_VERSION  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
ROWS = PACK / "measurement_factor_rows.jsonl"
RUNS = PACK / "round1b_runs"
ALLOWED_END = "2024-06-28"
HOLDOUT_START = "2024-07-01"
REVIEW_ANCHOR = "70a57ce5b60acfedb7ac4337b13aa2ee3a627d5f"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _iter_rows(path: Path, *, limit: int | None):
    count = 0
    with path.open("rb") as handle:
        for raw in handle:
            count += 1
            if count % 200_000 == 0:
                print(json.dumps({"rows_read": count}), flush=True)
            yield json.loads(raw)
            if limit is not None and count >= limit:
                return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    args = parser.parse_args()
    if not ROWS.exists():
        raise SystemExit(f"missing frozen B0 tape: {ROWS}")
    if args.limit is None:
        data_hash = bind_b0_tape(ROWS)
        kind = "full_tape"
    else:
        data_hash = bind_limited_prefix(ROWS, limit=args.limit)
        kind = f"limit_{args.limit}"
    registry = load_registry()
    head = _git_sha()
    coverage = build_coverage_matrix(registry=registry, profile="balanced", horizon="mid")
    sample_variants = [
        {
            "variant_id": item.variant_id,
            "track": item.track,
            "kind": item.kind,
            "dropped": item.dropped,
            "score_floor": item.score_floor,
            "note": item.note,
        }
        for item in iter_variants_r1b("software", "A_trend_quality", registry, coverage[0])
    ]
    code_hashes = research_code_hashes()
    signature = run_signature(
        registry=registry,
        profile="balanced",
        horizon="mid",
        label_horizons=(5, 20, 63),
        feature_version=FEATURE_VERSION,
        statistics_version=STATISTICS_VERSION,
        data_hash=data_hash,
        universe_version="u_measurement_continuous",
        member_policy="known_theme_members",
        reference_policy="same_track_t_complete_plus_spy_qqq",
        start="2018-01-02",
        end=ALLOWED_END,
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="NEXT_DAY_CONFIRM",
        registry_version=registry.get("schema_version"),
        capability_mask={"actual_G_observed": False, "tracks": ["FULL_EIGHT_FACTOR", "PRICE_ONLY_DIAGNOSTIC"]},
        variant_definitions=sample_variants,
        event_rule=EVENT_RULE,
        pairing_rule=PAIRING_RULE,
        statistics_rule=STATISTICS_RULE,
        block_lengths={"H": "label_horizon", "2H": "2*label_horizon"},
        bootstrap_seed=BOOTSTRAP_SEED,
        code_hashes=code_hashes,
        require_content_hash=True,
    )
    if args.out_dir is not None:
        out = args.out_dir
    else:
        out = RUNS / f"{run_dir_name(signature)}_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    public = PACK
    manifest = {
        "round": "algorithm_round1b",
        "reviewed_anchor": REVIEW_ANCHOR,
        "head": head,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "holdout_start": HOLDOUT_START,
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END,
        "profile": "balanced",
        "horizon": "mid",
        "b0_restricted": True,
        "b0_not_overwritten": True,
        "round1_not_overwritten": True,
        "run_kind": kind,
        "run_dir": str(out.relative_to(ROOT)),
        "run_signature": signature,
        "source_row_sha256": data_hash,
        "expected_b0_sha256": EXPECTED_B0_SHA256 if args.limit is None else None,
        "feature_version": FEATURE_VERSION,
        "score_version": SCORE_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "event_rule_version": EVENT_RULE_VERSION,
        "neighbors_preregistered_before_ic": preregistered_realloc_neighbors(),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "repeats": BOOTSTRAP_REPEATS},
        "code_hashes": code_hashes,
        "cache_hits": cache_hit_definitions(),
        "notes": [
            "Round 1 files stay untouched. R1b writes algorithm_round1b_* and a signed run directory.",
            "URL/CI metadata is not part of run_signature.",
            "82 historical G ablations are capability-confounded and are not re-run.",
            "N_M_PLUS_5PP / N_S_PLUS_5PP remain legacy_raw_weight_bump.",
            "executed_backtests stays 0.",
        ],
    }
    _write(out / "manifest.json", manifest)
    if args.limit is None:
        _write(public / "algorithm_round1b_manifest.json", manifest)
        _write(public / "algorithm_round1b_capability_matrix.json", coverage)
    analysis = analyze_rows_r1b(
        _iter_rows(ROWS, limit=args.limit),
        registry,
        profile="balanced",
        horizon="mid",
    )
    analysis["source_row_sha256"] = data_hash
    analysis["run_signature"] = signature
    analysis["head"] = head
    analysis["b0_not_overwritten"] = True
    analysis["round1_not_overwritten"] = True
    analysis["run_kind"] = kind
    analysis["executed_backtests"] = 0
    analysis["holdout_unsealed"] = False
    shard_dir = out / "pairing_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shards = []
    by_theme: dict[str, list] = {}
    for row in analysis["pairing"]:
        by_theme.setdefault(row["theme"], []).append(row)
    for theme, rows in by_theme.items():
        path = shard_dir / f"{theme}.json"
        _write(path, rows)
        shards.append({"theme": theme, "path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "rows": len(rows)})
        if path.stat().st_size > 1_000_000:
            raise SystemExit(f"pairing shard exceeds 1MB: {path}")
    public_payload = {key: value for key, value in analysis.items() if key not in {"pairing", "registered", "coverage_matrix"}}
    public_payload["pairing_shards"] = shards
    _write(out / "results.json", public_payload)
    _write(out / "theme_cards.json", analysis["theme_cards"])
    _write(out / "events.json", analysis["events"])
    if args.limit is None:
        _write(public / "algorithm_round1b_results.json", public_payload)
        _write(public / "algorithm_round1b_theme_cards.json", analysis["theme_cards"])
        _write(public / "algorithm_round1b_events.json", analysis["events"])
        _write(public / "algorithm_round1b_pairing_index.json", shards)
        overview = [
            {
                "theme": card["theme"],
                "families": [
                    {
                        "family": item["family"],
                        "decision": item["decision"],
                        "conclusion": item["conclusion"],
                        "next_neighbors": item["next_neighbors"],
                    }
                    for item in card["families"]
                ],
            }
            for card in analysis["theme_cards"]
        ]
        _write(public / "algorithm_round1b_overview.json", overview)
        counts = {
            "head": head,
            "run_signature": signature,
            "source_row_sha256": data_hash,
            "registered_configurations": analysis["registered_configurations"],
            "actual_rescore_calls": analysis["actual_rescore_calls"],
            "feature_reuse_hits": analysis["feature_reuse_hits"],
            "unique_snapshots": analysis["unique_snapshots"],
            "inspected_outcomes": analysis["inspected_outcomes"],
            "transformed_label_rows": analysis["transformed_label_rows"],
            "pairing_cells": len(analysis["pairing"]),
            "executed_backtests": 0,
            "holdout_unsealed": False,
            "historical_g_ablations_confounded": analysis["historical_g_ablations"]["count"],
        }
        _write(public / "algorithm_round1b_counts.json", counts)
        decisions = {}
        for card in analysis["theme_cards"]:
            for item in card["families"]:
                decisions[item["decision"]] = decisions.get(item["decision"], 0) + 1
        audit = [
            "# PR #174 algorithm round 1b",
            "",
            f"Head `{head}`. Review anchor `{REVIEW_ANCHOR}`.",
            "B0 and Round 1 files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.",
            "",
            f"- source_row_sha256 `{data_hash}`",
            f"- run_signature `{signature}`",
            f"- registered {analysis['registered_configurations']}; rescore {analysis['actual_rescore_calls']}",
            f"- unique snapshots {analysis['unique_snapshots']}; inspected {analysis['inspected_outcomes']}",
            f"- historical G ablations {analysis['historical_g_ablations']['count']} marked capability-confounded",
            f"- theme-family decisions {decisions}",
            "- pairing shards are per theme and stay under 1MB",
            "- current-head GitHub CI is recorded after the workflow finishes",
            "",
        ]
        text = "\n".join(audit) + "\n"
        (public / "algorithm_round1b_audit.md").write_text(text, encoding="utf-8")
        (out / "audit.md").write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "head": head,
                "run_signature": signature,
                "data_hash": data_hash,
                "run_dir": str(out),
                "registered": analysis["registered_configurations"],
                "rescore_calls": analysis["actual_rescore_calls"],
                "unique_snapshots": analysis["unique_snapshots"],
                "inspected": analysis["inspected_outcomes"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
