"""Index old current-list results as CONTROL_CURRENT_LIST_214 without rewriting them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.research_eod_v1.paths import REPO_ROOT, RETURN_PACK_DIR
from app.services.research_eod_v1.source_bind import EXPECTED_B0_SHA256, sha256_file

CONTROL_LABEL = "CONTROL_CURRENT_LIST_214"
AUTOMOTIVE_NOTE = (
    "Automotive D/V is a seen small-pool contrast only. "
    "It is not a full-market baseline or inherited winner."
)

PACK_FILES = {
    "b0_results": "measurement_results.json",
    "b0_rows": "measurement_factor_rows.jsonl",
    "round1_manifest": "algorithm_round1_manifest.json",
    "round1_results": "algorithm_round1_results.json",
    "round1b_manifest": "algorithm_round1b_manifest.json",
    "round1b_results": "algorithm_round1b_results.json",
    "round2_manifest": "algorithm_round2_manifest.json",
    "round2_results": "algorithm_round2_results.json",
    "freeze_validation": "algorithm_round2_freeze_validation.json",
    "freeze_stop": "algorithm_round2_freeze_stop.json",
    "fixed_matrix_capabilities": "algorithm_fixed_matrix_capabilities.json",
    "fixed_matrix_quality": "algorithm_fixed_matrix_quality.json",
    "fixed_matrix_sources": "algorithm_fixed_matrix_sources.json",
    "fixed_matrix_head": "algorithm_fixed_matrix_head.json",
}


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    if path.suffix == ".jsonl":
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_control_index(pack_dir: Path | None = None) -> dict[str, Any]:
    root = pack_dir or RETURN_PACK_DIR
    artifacts: dict[str, Any] = {}
    for name, relative in PACK_FILES.items():
        path = root / relative
        rel = str(path.relative_to(REPO_ROOT)) if path.is_relative_to(REPO_ROOT) else str(path)
        if not path.exists():
            artifacts[name] = {"path": rel, "status": "MISSING"}
            continue
        artifacts[name] = {
            "path": rel,
            "sha256": sha256_file(path),
            "bytes": path.stat().st_size,
            "rewritten": False,
        }
    quality = _read_json(root / "algorithm_fixed_matrix_quality.json") or {}
    sources = _read_json(root / "algorithm_fixed_matrix_sources.json") or {}
    freeze = _read_json(root / "algorithm_round2_freeze_stop.json") or {}
    r1 = _read_json(root / "algorithm_round1_manifest.json") or {}
    r1b = _read_json(root / "algorithm_round1b_manifest.json") or {}
    r2 = _read_json(root / "algorithm_round2_manifest.json") or {}
    return {
        "archive_label": CONTROL_LABEL,
        "do_not_reuse_as_full_market_baseline": True,
        "do_not_hide_213_vs_214": True,
        "list_n": 214,
        "quality_valid_n": quality.get("valid_n"),
        "quality_insufficient_n": quality.get("insufficient_n"),
        "quality_outlier": quality.get("isolated_outliers"),
        "213_is_valid_subset_of_214_list": True,
        "automotive_dv": {
            "role": "small_pool_contrast_only",
            "inherited_winner": False,
            "note": AUTOMOTIVE_NOTE,
        },
        "old_feature_version": "us-eod-research-features-v1.5",
        "new_feature_version_not_backfilled": True,
        "b0_sha256_expected": EXPECTED_B0_SHA256,
        "yahoo_bars_sha256_expected": sources.get("expected_yahoo_sha256"),
        "historical_statuses": {
            "b0": r1.get("status") or "archived_current_list_214",
            "round1_feature_version": r1.get("feature_version"),
            "round1b_feature_version": r1b.get("feature_version"),
            "round2_feature_version": r2.get("feature_version"),
            "freeze_outcome": freeze.get("outcome"),
            "freeze_not_a_winner": freeze.get("not_a_production_champion"),
            "fixed_matrix_protocol": sources.get("protocol"),
            "fixed_matrix_dataset": sources.get("dataset_version"),
        },
        "artifacts": artifacts,
        "holdout_still_sealed": True,
        "weight_search_closed": True,
    }
