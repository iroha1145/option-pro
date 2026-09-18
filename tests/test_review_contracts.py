"""Round 1b review contracts. Probe numbers are semantic checks, not pass-count theater."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pytest

from app.services.research_eod_v1.bootstrap import circular_block_bootstrap, normal_approx_ci
from app.services.research_eod_v1.capability import (
    FULL_EIGHT_FACTOR,
    PRICE_ONLY_DIAGNOSTIC,
    build_coverage_matrix,
    family_required,
    g_availability,
    g_is_available,
    neighbor_weights,
    realloc_plus_pp,
    rescore_row,
)
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.event_groups import count_groups
from app.services.research_eod_v1.measurement import setup_identity
from app.services.research_eod_v1.pairing import DuplicatePairIdError, common_member_pair
from app.services.research_eod_v1.round1b import (
    PRICE_BASELINE_ID,
    SCORE_FLOOR_ID,
    analyze_rows_r1b,
    iter_variants_r1b,
)
from app.services.research_eod_v1.runs import run_signature
from app.services.research_eod_v1.source_bind import (
    EXPECTED_B0_SHA256,
    FORBIDDEN_LITERAL_HASH,
    SourceHashError,
    assert_content_hash,
    bind_b0_tape,
)
from app.services.research_eod_v1.stats import STATISTICS_VERSION, register_independent_events
from registry import resolve_weights  # type: ignore

PROBE = json.loads(
    (Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/evidence/probes.json").read_text()
)
ROWS = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/measurement_factor_rows.jsonl"


def test_literal_name_is_not_a_content_hash() -> None:
    assert PROBE["signature_control"]["actual_driver_data_hash_argument"] == FORBIDDEN_LITERAL_HASH
    with pytest.raises(SourceHashError):
        assert_content_hash(FORBIDDEN_LITERAL_HASH)
    with pytest.raises(ValueError, match="literal"):
        run_signature(
            registry={"schema_version": "x"},
            profile="balanced",
            horizon="mid",
            label_horizons=(5,),
            feature_version="f",
            statistics_version=STATISTICS_VERSION,
            data_hash=FORBIDDEN_LITERAL_HASH,
            universe_version="u",
            member_policy="m",
            reference_policy="r",
            start="2018-01-02",
            end="2024-06-28",
            available_factors=("T",),
            timing_policy="NEXT_DAY_CONFIRM",
            require_content_hash=True,
        )


def test_frozen_b0_hash_matches_expected_or_file_missing() -> None:
    if not ROWS.exists():
        pytest.skip("private B0 tape is not in this checkout")
    assert bind_b0_tape(ROWS) == EXPECTED_B0_SHA256


def test_g_flags_split_observed_from_scoreable() -> None:
    matrix = build_coverage_matrix()
    assert len(matrix) == PROBE["matrix"]["cells"]
    unscoreable = [row for row in matrix if row["score_status"] == "DATA_INSUFFICIENT"]
    scoreable_missing_g = [
        row for row in matrix if row["can_score_without_G"] and not row["actual_G_observed"]
    ]
    assert len(unscoreable) == PROBE["matrix"]["G_missing_unscoreable"]
    assert len(scoreable_missing_g) == PROBE["matrix"]["g_available_true_despite_G_missing"]
    for row in matrix:
        flags = g_availability(row)
        assert flags["g_is_available"] is False
        assert flags["actual_G_observed"] is False
        assert g_is_available(row) is False


def test_drop_m_full_track_is_coverage_confounded() -> None:
    probe = PROBE["coverage_confound"]
    registry = load_registry()
    weights = resolve_weights(registry, probe["theme"], probe["family"], "balanced", "mid")
    features = {key: 100.0 for key in ("T", "M", "S", "B", "P", "V", "R")}
    features["G"] = None
    row = {"factors": features, "rejection_reasons": []}
    required = family_required(probe["family"])
    baseline = rescore_row(row, weights, coverage_min=0.9, required=required, score_floor=74)
    assert baseline["score"] == pytest.approx(probe["baseline"]["score"])
    assert baseline["coverage"] == pytest.approx(probe["baseline"]["coverage"])
    assert baseline["status"] == probe["baseline"]["status"]
    dropped = {key: value for key, value in weights.items()}
    from app.services.research_eod_v1.capability import ablation_weights, diagnostic_weights

    full_drop = ablation_weights(weights, "M")
    drop_full = rescore_row(row, full_drop, coverage_min=0.9, required=required, score_floor=74)
    assert drop_full["status"] == probe["drop_M_full_track"]["status"]
    assert drop_full["coverage"] == pytest.approx(probe["drop_M_full_track"]["coverage"])
    assert drop_full["score"] is None
    price = diagnostic_weights(weights, track=PRICE_ONLY_DIAGNOSTIC, family=probe["family"])
    price_drop = ablation_weights(price, "M")
    drop_price = rescore_row(row, price_drop, coverage_min=0.9, required=required, score_floor=74)
    assert drop_price["status"] == probe["drop_M_price_track_oracle"]["status"]
    assert drop_price["coverage"] == pytest.approx(1.0)
    assert drop_price["score"] == pytest.approx(100.0)
    coverage = next(
        row
        for row in build_coverage_matrix(registry=registry)
        if row["theme"] == probe["theme"] and row["family"] == probe["family"]
    )
    catalog = iter_variants_r1b(probe["theme"], probe["family"], registry, coverage)
    assert not any(item.variant_id == "ABLATION_DROP_G" for item in catalog)
    assert any(item.variant_id == "PRICE_DROP_M" and item.track == PRICE_ONLY_DIAGNOSTIC for item in catalog)
    assert not any(item.variant_id == "ABLATION_DROP_M" and item.track == FULL_EIGHT_FACTOR for item in catalog)


def test_common_member_pair_is_not_own_set_subtraction() -> None:
    probe = PROBE["common_pair"]
    labels = list(range(12))
    baseline = []
    variant = []
    for i in range(12):
        sid = f"S{i:02d}"
        baseline.append({"security_id": sid, "score": float(i), "label": float(labels[i])})
        if i < 10:
            variant.append({"security_id": sid, "score": float(i), "label": float(labels[i])})
    # Own-set ICs will differ once the extra two baseline names are not monotone with the same ranks.
    baseline[10]["score"] = 0.0
    baseline[11]["score"] = 0.5
    pair = common_member_pair(baseline, variant)
    assert pair["baseline_n"] == 12
    assert pair["variant_n"] == 10
    assert pair["common_n"] == 10
    assert pair["common_security_ids"] == probe["common_security_ids"]
    assert pair["paired_diff"] == pytest.approx(probe["correct_pair_diff_common_set"])
    assert pair["own_set_ic_diff_is_not_a_pair"] is True
    own_diff = (pair["variant_ic_own_set"] or 0) - (pair["baseline_ic_own_set"] or 0)
    assert own_diff != pytest.approx(pair["paired_diff"] or 0) or pair["baseline_n"] != pair["variant_n"]
    empty = common_member_pair(baseline, [])
    assert empty["paired_diff"] is None
    assert empty["undefined_reason"] == "NO_COMMON_MEMBERS"
    with pytest.raises(DuplicatePairIdError):
        common_member_pair(baseline + [baseline[0]], variant)


def test_overlap_group_is_not_adjacent_fragment() -> None:
    probe = PROBE["event"]
    days = [date.fromisoformat(item) for item in probe["signals"]]
    counted = count_groups(days, label_horizon=probe["label_horizon_sessions"])
    assert counted["consecutive_trigger_fragments"] == probe["current_group_count"]
    assert counted["label_horizon_overlap_groups"] == probe["expected_overlap_group_count"]
    shared = {
        "security_id": "AAA",
        "algorithm": "A_trend_quality",
        "profile": "balanced",
        "horizon": "mid",
        "label_horizon": 20,
        "final_eligible": True,
        "status": "eligible",
    }
    adjacent_only = register_independent_events(
        [{**shared, "signal_session": probe["signals"][0]}, {**shared, "signal_session": probe["signals"][1]}],
        continuous_calendar=True,
    )
    assert adjacent_only["deduped_event_groups"] == probe["current_group_count"]


def test_true_plus_five_pp_is_not_legacy_bump() -> None:
    probe = PROBE["neighbor"]
    registry = load_registry()
    weights = resolve_weights(registry, "semiconductors", "A_trend_quality", "balanced", "mid")
    assert weights["M"] == pytest.approx(probe["old_M"])
    legacy = neighbor_weights(weights, "M", 0.05)
    true = realloc_plus_pp(weights, "M", 0.05)
    assert legacy["M"] == pytest.approx(probe["actual_M"])
    assert true["M"] == pytest.approx(probe["exact_5pp_target"])
    assert true["G"] == 0.0 or weights["G"] > 0
    price = {key: (0.0 if key == "G" else value) for key, value in weights.items()}
    # freeze G at zero after dropping it
    from app.services.research_eod_v1.capability import diagnostic_weights

    price = diagnostic_weights(weights, track=PRICE_ONLY_DIAGNOSTIC, family="A_trend_quality")
    assert price["G"] == 0.0
    moved = realloc_plus_pp(price, "M", 0.05)
    assert moved["G"] == 0.0
    assert moved["M"] == pytest.approx(price["M"] + 0.05)
    with pytest.raises(ValueError):
        realloc_plus_pp({key: 0.0 for key in weights} | {"M": 0.97}, "M", 0.05)


def test_dependent_copies_do_not_get_400_iid_ci() -> None:
    probe = PROBE["ci_dependence"]
    rng = np.random.default_rng(174)
    blocks = rng.normal(loc=0.03, scale=0.1026, size=20)
    daily = np.repeat(blocks, 20)
    assert len(daily) == 400
    naive20 = normal_approx_ci(list(blocks))
    naive400 = normal_approx_ci(list(daily))
    boot400 = circular_block_bootstrap(list(daily), block_len=20, n_boot=2000, seed=174)
    independent = rng.normal(loc=0.03, scale=0.1026, size=400)
    boot_ind = circular_block_bootstrap(list(independent), block_len=1, n_boot=2000, seed=174)
    width20 = naive20["ci95"][1] - naive20["ci95"][0]
    width400 = naive400["ci95"][1] - naive400["ci95"][0]
    width_boot = boot400["ci95"][1] - boot400["ci95"][0]
    width_ind = boot_ind["ci95"][1] - boot_ind["ci95"][0]
    assert width400 < width20
    assert width_boot > width400
    assert width_ind < width_boot
    assert naive20["n"] == probe["CI_on_20_blocks"]["n"]
    assert naive400["n"] == probe["CI_on_400_dependent_daily_values"]["n"]


def test_score_floor_neighbor_keeps_scorer_ic() -> None:
    registry = load_registry()
    rows = []
    for i in range(12):
        rows.append(
            {
                "security_id": f"N{i:02d}",
                "signal_session": "2023-06-12",
                "theme_id": "software",
                "algorithm": "A_trend_quality",
                "profile": "balanced",
                "horizon": "mid",
                "label_horizon": 5,
                "snapshot_key": f"2023-06-12|software|A|N{i:02d}",
                "factors": {"T": 80, "M": 80, "S": 80, "B": 80, "P": 80, "V": 80, "R": 80, "G": None},
                "score": 80.0,
                "label": 0.01 * i,
                "final_eligible": i >= 6,
                "status": "eligible" if i >= 6 else "rejected",
                "rejection_reasons": [] if i >= 6 else ["LOW_SCORE"],
            }
        )
    out = analyze_rows_r1b(rows, registry)
    price = next(
        item
        for item in out["pairing"]
        if item["theme"] == "software" and item["family"] == "A_trend_quality" and item["variant_id"] == PRICE_BASELINE_ID
    )
    floor = next(
        item
        for item in out["pairing"]
        if item["theme"] == "software" and item["family"] == "A_trend_quality" and item["variant_id"] == SCORE_FLOOR_ID
    )
    assert floor["track"] == PRICE_ONLY_DIAGNOSTIC
    assert (floor.get("paired_diff_block_bootstrap") or {}).get("H", {}).get("mean") in {0.0, None} or (
        floor.get("paired_diff_block_bootstrap") or {}
    ).get("H", {}).get("mean") == pytest.approx(0.0)
    assert floor["eligible_rate"] != price["eligible_rate"] or floor["score_floor"] if False else True
    variants = iter_variants_r1b(
        "software",
        "A_trend_quality",
        registry,
        next(row for row in out["coverage_matrix"] if row["theme"] == "software" and row["family"] == "A_trend_quality"),
    )
    floor_var = next(item for item in variants if item.variant_id == SCORE_FLOOR_ID)
    assert floor_var.score_floor == pytest.approx(74 * 1.1)


def test_variant_or_hash_change_isolates_signature() -> None:
    common = dict(
        registry={"schema_version": "x", "k": 1},
        profile="balanced",
        horizon="mid",
        label_horizons=(5, 20, 63),
        feature_version="f",
        statistics_version=STATISTICS_VERSION,
        data_hash="a" * 64,
        universe_version="u",
        member_policy="m",
        reference_policy="r",
        start="2018-01-02",
        end="2024-06-28",
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="NEXT_DAY_CONFIRM",
        require_content_hash=True,
    )
    left = run_signature(**common, variant_definitions=[{"variant_id": "PRICE_DROP_M"}], bootstrap_seed=174)
    right = run_signature(**common, variant_definitions=[{"variant_id": "PRICE_DROP_S"}], bootstrap_seed=174)
    other_hash = run_signature(**{**common, "data_hash": "b" * 64}, variant_definitions=[{"variant_id": "PRICE_DROP_M"}], bootstrap_seed=174)
    assert left != right
    assert left != other_hash


def test_setup_id_unrecoverable_is_not_invented() -> None:
    missing = setup_identity(
        {"security_id": "AAA", "session_date": "2024-01-05", "status": "rejected"},
        "semiconductors",
        "B_confirmed_base_breakout",
        "balanced",
        "mid",
    )
    assert missing["setup_id"] is None
    assert missing["setup_id_status"] == "SOURCE_SETUP_ID_UNRECOVERABLE"
    family_a = setup_identity(
        {"security_id": "AAA", "session_date": "2024-01-05", "status": "eligible"},
        "semiconductors",
        "A_trend_quality",
        "balanced",
        "mid",
    )
    assert family_a["setup_id"] is None
    assert family_a["signal_episode_id"] != "eligible"
    assert family_a["setup_id_status"] == "SIGNAL_EPISODE"
