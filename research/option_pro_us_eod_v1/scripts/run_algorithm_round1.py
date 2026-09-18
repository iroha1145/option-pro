"""First-round 24-theme ablations from the frozen B0 factor tape. No price re-download."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT))

from app.services.research_eod_v1 import FEATURE_VERSION, SCORE_VERSION  # noqa: E402
from app.services.research_eod_v1.ablation import (  # noqa: E402
    DATE_BLOCKS,
    NEIGHBOR_SPECS,
    analyze_rows,
    preregistered_date_blocks,
    preregistered_neighbors,
)
from app.services.research_eod_v1.capability import build_coverage_matrix  # noqa: E402
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.runs import cache_hit_definitions, run_signature  # noqa: E402
from app.services.research_eod_v1.stats import STATISTICS_VERSION  # noqa: E402

PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
ROWS = PACK / "measurement_factor_rows.jsonl"
MANIFEST = PACK / "algorithm_round1_manifest.json"
RESULTS = PACK / "algorithm_round1_results.json"
MATRIX = PACK / "algorithm_round1_capability_matrix.json"
EVENTS = PACK / "algorithm_round1_event_revision.json"
PAIRING = PACK / "algorithm_round1_ablation_pairing.json"
CARDS = PACK / "algorithm_round1_theme_cards.json"
NEIGHBORS = PACK / "algorithm_round1_neighbors.json"
RANGES = PACK / "algorithm_round1_date_ranges.json"
AUDIT = PACK / "algorithm_round1_audit.md"
SCHEMA = PACK / "algorithm_round1_row_schema.json"
SAMPLE = PACK / "algorithm_round1_anonymous_sample.json"
B0_RESULTS = PACK / "measurement_results.json"
ALLOWED_END = "2024-06-28"
HOLDOUT_START = "2024-07-01"


def _git_sha() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _hashed_rows(path: Path, digest):
    with path.open("rb") as handle:
        for raw in handle:
            digest.update(raw)
            yield json.loads(raw)


def main() -> None:
    if not ROWS.exists():
        raise SystemExit(f"missing frozen B0 tape: {ROWS}")
    registry = load_registry()
    head = _git_sha()
    coverage = build_coverage_matrix(registry=registry, profile="balanced", horizon="mid")
    signature = run_signature(
        registry=registry,
        profile="balanced",
        horizon="mid",
        label_horizons=(5, 20, 63),
        feature_version=FEATURE_VERSION,
        statistics_version=STATISTICS_VERSION,
        data_hash="b0_measurement_factor_rows",
        universe_version="u_measurement_continuous",
        member_policy="known_theme_members",
        reference_policy="same_track_t_complete_plus_spy_qqq",
        start="2018-01-02",
        end=ALLOWED_END,
        available_factors=("T", "M", "S", "B", "P", "V", "R", "G"),
        timing_policy="NEXT_DAY_CONFIRM",
        registry_version=registry.get("schema_version"),
    )
    manifest = {
        "round": "algorithm_round1",
        "reviewed_anchor": "155685daf93982d480fa06a06eb095f7bf8ae984",
        "head": head,
        "written_at": datetime.now(timezone.utc).isoformat(),
        "holdout_start": HOLDOUT_START,
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END,
        "profile": "balanced",
        "horizon": "mid",
        "b0_restricted": True,
        "b0_results": str(B0_RESULTS.relative_to(ROOT)),
        "b0_rows": str(ROWS.relative_to(ROOT)),
        "b0_not_overwritten": True,
        "run_signature": signature,
        "feature_version": FEATURE_VERSION,
        "score_version": SCORE_VERSION,
        "statistics_version": STATISTICS_VERSION,
        "neighbors_preregistered_before_ic": preregistered_neighbors(),
        "date_blocks_preregistered_before_ic": preregistered_date_blocks(),
        "cache_hits": cache_hit_definitions(),
        "notes": [
            "Neighbors and date blocks are registered in this file before the tape is scored.",
            "PRICE_ONLY_DIAGNOSTIC and D_MARKET_RESIDUAL_DIAGNOSTIC are separate tracks.",
            "G-missing coverage failures are data-capability, not strategy loss.",
            "4514 remains SUPERSEDED_EVENT_COUNT.",
            "executed_backtests stays 0.",
        ],
    }
    _write(MANIFEST, manifest)
    _write(MATRIX, coverage)
    _write(NEIGHBORS, {"preregistered": True, "specs": list(NEIGHBOR_SPECS), "date_blocks": list(DATE_BLOCKS)})
    digest = hashlib.sha256()
    analysis = analyze_rows(_hashed_rows(ROWS, digest), registry, profile="balanced", horizon="mid")
    row_hash = digest.hexdigest()
    analysis["source_row_sha256"] = row_hash
    analysis["run_signature"] = signature
    analysis["head"] = head
    analysis["b0_not_overwritten"] = True
    analysis["executed_backtests"] = 0
    analysis["holdout_unsealed"] = False
    _write(RESULTS, {key: value for key, value in analysis.items() if key not in {"pairing", "registered", "coverage_matrix", "theme_cards"}})
    _write(PAIRING, analysis["pairing"])
    _write(CARDS, analysis["theme_cards"])
    _write(EVENTS, analysis["events"])
    _write(
        RANGES,
        {
            "window": analysis["date_ranges_window"],
            "empty_ratios": analysis["empty_ratios"],
            "per_cell": [
                {
                    "theme": item["theme"],
                    "family": item["family"],
                    "variant_id": item["variant_id"],
                    "label_horizon": item["label_horizon"],
                    "date_ranges": item["date_ranges"],
                    "statistically_thin": item["statistically_thin"],
                }
                for item in analysis["pairing"]
                if item["variant_id"] == "BASELINE_FULL_EIGHT"
            ],
        },
    )
    _write(
        SCHEMA,
        {
            "schema_version": "us-eod-research-factor-row-v1.0",
            "source": str(ROWS.relative_to(ROOT)),
            "sha256": row_hash,
            "fields": [
                "security_id",
                "signal_session",
                "theme_id",
                "algorithm",
                "profile",
                "horizon",
                "label_horizon",
                "factors",
                "score",
                "label",
                "final_eligible",
                "rejection_reasons",
            ],
            "note": "Authorized cache-derived tape stays gitignored. This schema plus hash is the public replay handle.",
        },
    )
    _write(
        SAMPLE,
        {
            "note": "Synthetic anonymous sample. Not a market print and not copied from the private tape.",
            "rows": [
                {
                    "security_id": "SEC_ANON_1",
                    "signal_session": "2020-06-15",
                    "theme_id": "software",
                    "algorithm": "A_trend_quality",
                    "profile": "balanced",
                    "horizon": "mid",
                    "label_horizon": 5,
                    "factors": {"T": 72.0, "M": 68.0, "S": 64.0, "B": 40.0, "P": 35.0, "V": 55.0, "R": 70.0, "G": None},
                    "score": None,
                    "label": 0.012,
                    "final_eligible": False,
                }
            ],
        },
    )
    baseline_events = analysis["events"]["by_variant"].get("BASELINE_FULL_EIGHT", {})
    thin = sum(1 for item in analysis["pairing"] if item["statistically_thin"])
    AUDIT.write_text(
        "\n".join(
            [
                "# PR #174 algorithm round 1",
                "",
                f"Head `{head}`. Review anchor `155685daf93982d480fa06a06eb095f7bf8ae984` remains the B0 code/data freeze.",
                "B0 continuous files are not rewritten. Holdout 2024-07-01 stays sealed. executed_backtests = 0.",
                "",
                "## Isolation",
                "",
                f"- run_signature `{signature}`",
                "- Checkpoint mismatch raises `CheckpointSignatureError`; profile/horizon/registry_version cannot reuse done keys.",
                "- Crash after row append without checkpoint replays the session and skips `committed_row_key`.",
                "- Cache layers: feature / score / event / IC. Weight variants reuse factors and rescore.",
                "",
                "## Coverage",
                "",
                f"- 24×4 capability matrix written to `{MATRIX.name}`.",
                "- G-missing cells that fail coverage_min=0.9 are DATA_INSUFFICIENT data-capability failures.",
                "- PRICE_ONLY_DIAGNOSTIC and D_MARKET_RESIDUAL_DIAGNOSTIC are named tracks, not same-track ablations.",
                "",
                "## Events",
                "",
                "- Trading-session adjacency. Weekend/holiday gaps are not new events.",
                "- Count name: 去重事件组数. `independent_events` is null. 4514 is SUPERSEDED_EVENT_COUNT.",
                f"- Baseline theme-report groups: {baseline_events.get('theme_report_deduped_event_groups')}.",
                f"- Baseline global-book groups: {baseline_events.get('global_book_deduped_event_groups')}.",
                "",
                "## Ablation",
                "",
                f"- Registered configurations: {analysis['registered_configurations']}.",
                f"- Actual rescore calls: {analysis['actual_rescore_calls']}.",
                f"- Feature-reuse hits: {analysis['feature_reuse_hits']}.",
                f"- Unique snapshots: {analysis['unique_snapshots']}.",
                f"- Inspected outcomes / transformed label rows: {analysis['inspected_outcomes']}.",
                f"- Pairing cells statistically thin: {thin} / {len(analysis['pairing'])}.",
                "- Neighbors N_M_PLUS_5PP, N_S_PLUS_5PP, N_SCORE_FLOOR_PLUS_10PCT were registered before scoring.",
                "- No family was inverted or deleted because an IC was negative.",
                "- No cross-track champion.",
                "",
                "## Date ranges",
                "",
                f"- Raw window: {analysis['date_ranges_window']['raw']}.",
                "- Empty ratios use all-window / post-warmup / data-capable denominators.",
                "- `len(sessions)/252` is not evaluable years.",
                "",
                "## Not done",
                "",
                "- Portfolio / raw corporate-action verified book remains unrun.",
                "- New E / weekly / macro stay planned.",
                "- Holdout remains sealed. Search stops after these preregistered neighbors.",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "head": head,
                "run_signature": signature,
                "row_sha256": row_hash,
                "registered": analysis["registered_configurations"],
                "rescore_calls": analysis["actual_rescore_calls"],
                "unique_snapshots": analysis["unique_snapshots"],
                "inspected": analysis["inspected_outcomes"],
                "baseline_theme_events": baseline_events.get("theme_report_deduped_event_groups"),
                "baseline_global_events": baseline_events.get("global_book_deduped_event_groups"),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
