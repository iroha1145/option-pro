"""Round 2 guards, revised cards, and at most three targeted candidates.

Does not overwrite B0, Round 1, or Round 1b files.
"""

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
    MAIN_LABEL_HORIZON,
    PAIRING_RULE,
    STATISTICS_RULE,
    analyze_rows_r1b,
    iter_variants_r1b,
    preregistered_realloc_neighbors,
)
from app.services.research_eod_v1.round2 import (  # noqa: E402
    _is_nonfinite,
    analyze_targeted_candidates,
    select_targeted_candidates,
)
from app.services.research_eod_v1.runs import cache_hit_definitions, run_dir_name, run_signature  # noqa: E402
from app.services.research_eod_v1.source_bind import (  # noqa: E402
    EXPECTED_B0_SHA256,
    bind_b0_tape,
    bind_limited_prefix,
    research_code_hashes,
    sha256_file,
)
from app.services.research_eod_v1.stats import STATISTICS_VERSION  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
ROWS = PACK / "measurement_factor_rows.jsonl"
RUNS = PACK / "round2_runs"
ALLOWED_END = "2024-06-28"
HOLDOUT_START = "2024-07-01"
REVIEW_ANCHOR = "ddbd042004fd28b1cb84d8427ab920b1f0c71776"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def _iter_rows(path: Path, *, limit: int | None, scan_box: dict | None = None):
    count = 0
    with path.open("rb") as handle:
        for raw in handle:
            count += 1
            if count % 200_000 == 0:
                print(json.dumps({"rows_read": count}), flush=True)
            row = json.loads(raw)
            if scan_box is not None:
                score_bad = _is_nonfinite(row.get("score"))
                label_bad = _is_nonfinite(row.get("label"))
                if score_bad:
                    scan_box["nonfinite_score_rows"] += 1
                if label_bad:
                    scan_box["nonfinite_label_rows"] += 1
                if (score_bad or label_bad) and len(scan_box["examples"]) < 5:
                    scan_box["examples"].append(
                        {
                            "signal_session": row.get("signal_session"),
                            "theme_id": row.get("theme_id"),
                            "security_id": row.get("security_id"),
                            "score": row.get("score"),
                            "label": row.get("label"),
                        }
                    )
                scan_box["rows_scanned"] += 1
            yield row
            if limit is not None and count >= limit:
                return


def _finalize_scan(scan_box: dict) -> dict:
    present = scan_box["nonfinite_score_rows"] > 0 or scan_box["nonfinite_label_rows"] > 0
    return {
        **scan_box,
        "present_on_tape": present,
        "implication": (
            "B0 tape contains non-finite score/label rows; pairing now rejects them before the common set."
            if present
            else (
                "B0 tape scan found no non-finite score/label. This round only adds a guard. "
                "It does not imply that prior pairing results are invalid."
            )
        ),
    }


def _write_daily_artifact(out: Path, sessions: list[str], series: list[dict], *, public: bool) -> dict[str, str]:
    private = out / "daily_paired_diffs.jsonl"
    with private.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps({"sessions": sessions}, separators=(",", ":")) + "\n")
        for row in series:
            handle.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
    digest = sha256_file(private)
    meta = {
        "path": _rel(private),
        "sha256": digest,
        "rows": len(series) + 1,
        "public_git": False,
        "note": "Controlled daily paired-diff artifact. Raw B0 tape is not copied here.",
    }
    _write(out / "daily_paired_diffs.sha256.json", meta)
    if public:
        _write(PACK / "algorithm_round2_daily_paired_diffs.sha256.json", meta)
    return meta


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument("--skip-candidates", action="store_true")
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
        block_lengths={"H": "label_horizon", "2H": "2*label_horizon", "trim": "original_timeline_n"},
        bootstrap_seed=BOOTSTRAP_SEED,
        code_hashes=code_hashes,
        require_content_hash=True,
    )
    out = args.out_dir if args.out_dir is not None else RUNS / f"{run_dir_name(signature)}_{kind}"
    out.mkdir(parents=True, exist_ok=True)
    public = args.limit is None
    scan_box = {
        "rows_scanned": 0,
        "nonfinite_score_rows": 0,
        "nonfinite_label_rows": 0,
        "examples": [],
    }
    analysis = analyze_rows_r1b(
        _iter_rows(ROWS, limit=args.limit, scan_box=scan_box),
        registry,
        profile="balanced",
        horizon="mid",
    )
    scan = _finalize_scan(scan_box)
    manifest = {
        "round": "algorithm_round2",
        "reviewed_anchor": REVIEW_ANCHOR,
        "head": head,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "holdout_start": HOLDOUT_START,
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END,
        "profile": "balanced",
        "horizon": "mid",
        "main_label_horizon": MAIN_LABEL_HORIZON,
        "b0_restricted": True,
        "b0_not_overwritten": True,
        "round1_not_overwritten": True,
        "round1b_not_overwritten": True,
        "run_kind": kind,
        "run_dir": _rel(out),
        "run_signature": signature,
        "source_row_sha256": data_hash,
        "expected_b0_sha256": EXPECTED_B0_SHA256 if args.limit is None else None,
        "feature_version": FEATURE_VERSION,
        "score_version": SCORE_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "event_rule_version": EVENT_RULE_VERSION,
        "neighbors_preregistered_before_ic": preregistered_realloc_neighbors(),
        "bootstrap": {"seed": BOOTSTRAP_SEED, "repeats": BOOTSTRAP_REPEATS, "trim_to_timeline": True},
        "code_hashes": code_hashes,
        "cache_hits": cache_hit_definitions(),
        "b0_nonfinite_scan": scan,
        "notes": [
            "B0, Round 1, and Round 1b files stay untouched. Round 2 writes algorithm_round2_* and a signed run directory.",
            "URL/CI metadata is not part of run_signature.",
            "Bootstrap replicates are cropped to the original timeline length.",
            "Eight-factor DATA_INSUFFICIENT does not overwrite an evaluable PRICE_ONLY card.",
            "executed_backtests stays 0.",
        ],
    }
    _write(out / "manifest.json", manifest)
    if public:
        _write(PACK / "algorithm_round2_manifest.json", manifest)
        _write(PACK / "algorithm_round2_capability_matrix.json", coverage)
        _write(PACK / "algorithm_round2_b0_nonfinite_scan.json", scan)
    sessions = analysis.pop("daily_pair_sessions")
    series = analysis.pop("daily_pair_series")
    daily_meta = _write_daily_artifact(out, sessions, series, public=public)
    analysis["source_row_sha256"] = data_hash
    analysis["run_signature"] = signature
    analysis["head"] = head
    analysis["b0_not_overwritten"] = True
    analysis["round1_not_overwritten"] = True
    analysis["round1b_not_overwritten"] = True
    analysis["run_kind"] = kind
    analysis["executed_backtests"] = 0
    analysis["holdout_unsealed"] = False
    analysis["daily_paired_diffs"] = daily_meta
    analysis["b0_nonfinite_scan"] = scan
    candidates = select_targeted_candidates(analysis["pairing"])
    registered = [item.as_dict() for item in candidates]
    if args.skip_candidates:
        executed = [item | {"executed": False, "result": "skipped_this_invocation"} for item in registered]
    else:
        executed = analyze_targeted_candidates(
            _iter_rows(ROWS, limit=args.limit),
            registry,
            candidates,
            sessions=sessions,
        )
    analysis["targeted_candidates"] = executed
    shard_dir = out / "pairing_shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    public_shard_dir = PACK / "algorithm_round2_pairing"
    if public:
        public_shard_dir.mkdir(parents=True, exist_ok=True)
    shards = []
    by_theme: dict[str, list] = {}
    for row in analysis["pairing"]:
        by_theme.setdefault(row["theme"], []).append(row)
    for theme, rows in by_theme.items():
        path = shard_dir / f"{theme}.json"
        _write(path, rows)
        public_path = public_shard_dir / f"{theme}.json" if public else path
        if public:
            _write(public_path, rows)
        if path.stat().st_size > 1_000_000:
            raise SystemExit(f"pairing shard exceeds 1MB: {path}")
        shards.append(
            {
                "theme": theme,
                "path": _rel(public_path if public else path),
                "bytes": (public_path if public else path).stat().st_size,
                "rows": len(rows),
            }
        )
    public_payload = {
        key: value for key, value in analysis.items() if key not in {"pairing", "registered", "coverage_matrix"}
    }
    public_payload["pairing_shards"] = shards
    _write(out / "results.json", public_payload)
    _write(out / "theme_cards.json", analysis["theme_cards"])
    _write(out / "events.json", analysis["events"])
    _write(out / "candidates.json", executed)
    if public:
        _write(PACK / "algorithm_round2_results.json", public_payload)
        _write(PACK / "algorithm_round2_theme_cards.json", analysis["theme_cards"])
        _write(PACK / "algorithm_round2_events.json", analysis["events"])
        _write(PACK / "algorithm_round2_pairing_index.json", shards)
        _write(PACK / "algorithm_round2_candidates.json", executed)
        overview = []
        for card in analysis["theme_cards"]:
            families = []
            for item in card["families"]:
                neighbors = item.get("next_neighbors") or []
                families.append(
                    {
                        "family": item["family"],
                        "eight_factor_status": item.get("eight_factor_status"),
                        "price_track_status": item.get("price_track_status"),
                        "decision": item["decision"],
                        "conclusion": item["conclusion"],
                        "main_label_horizon": item.get("main_label_horizon"),
                        "next_neighbors": neighbors,
                    }
                )
            overview.append({"theme": card["theme"], "families": families})
        _write(PACK / "algorithm_round2_overview.json", overview)
        decisions: dict[str, int] = {}
        for card in analysis["theme_cards"]:
            for item in card["families"]:
                decisions[item["decision"]] = decisions.get(item["decision"], 0) + 1
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
            "targeted_candidates": [
                {"candidate_id": item["candidate_id"], "status": item.get("status"), "result": item.get("result")}
                for item in executed
            ],
            "b0_nonfinite_scan": {
                "present_on_tape": scan["present_on_tape"],
                "nonfinite_score_rows": scan["nonfinite_score_rows"],
                "nonfinite_label_rows": scan["nonfinite_label_rows"],
            },
            "daily_paired_diffs_sha256": daily_meta["sha256"],
            "theme_family_decisions": decisions,
        }
        _write(PACK / "algorithm_round2_counts.json", counts)
        audit = [
            "# PR #174 algorithm round 2",
            "",
            f"Head `{head}`. Review anchor `{REVIEW_ANCHOR}`.",
            "B0, Round 1, and Round 1b files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.",
            "",
            f"- source_row_sha256 `{data_hash}`",
            f"- run_signature `{signature}`",
            f"- statistics_version `{STATISTICS_VERSION}`",
            f"- registered {analysis['registered_configurations']}; rescore {analysis['actual_rescore_calls']}",
            f"- unique snapshots {analysis['unique_snapshots']}; inspected {analysis['inspected_outcomes']}",
            f"- B0 nonfinite scan: score={scan['nonfinite_score_rows']} label={scan['nonfinite_label_rows']} present={scan['present_on_tape']}",
            f"- daily paired-diff sha256 `{daily_meta['sha256']}`",
            f"- theme-family decisions {decisions}",
            f"- targeted candidates {[item['candidate_id'] + ':' + str(item.get('result')) for item in executed]}",
            "- pairing shards are per theme and stay under 1MB",
            "- current-head GitHub CI is recorded after the workflow finishes",
            "",
        ]
        text = "\n".join(audit) + "\n"
        (PACK / "algorithm_round2_audit.md").write_text(text, encoding="utf-8")
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
                "candidates": [item["candidate_id"] for item in executed],
                "b0_nonfinite_present": scan["present_on_tape"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
