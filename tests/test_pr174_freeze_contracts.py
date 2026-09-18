"""PR #174 freeze contracts. Two local fixes plus freeze rules. Not a new grid."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.freeze import (
    CHECKLIST,
    FROZEN_CANDIDATE_ID,
    checklist_manifest,
    decide_stop,
    etfs_parameter_correction,
    freeze_snapshot,
    load_frozen_candidate,
    next_stage_data_requirements,
    project_gaps,
    public_validation_table,
    theme_family_status,
    validate_frozen_candidate,
)
from app.services.research_eod_v1.round1b import (
    PRICE_BASELINE_ID,
    SCORE_FLOOR_ID,
    _theme_card,
    signed_ablation_direction,
    transform_kind,
)
from app.services.research_eod_v1.round2 import (
    TargetedCandidate,
    candidate_weights,
    historical_halve_at_boundary,
    planned_candidate_weights,
)
from registry import resolve_weights, score_features  # type: ignore

PROBE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/evidence/probes_freeze.json"
    ).read_text()
)
CANDIDATES = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/algorithm_round2_candidates.json"
CARDS = Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/return_pack/algorithm_round2_theme_cards.json"
CHECKLIST_PATH = (
    Path(__file__).resolve().parents[1]
    / "research/option_pro_us_eod_v1/return_pack/algorithm_round2_freeze_checklist.json"
)


def _candidate(
    theme: str,
    family: str,
    factor: str,
    *,
    delta: float = -0.05,
    kind: str = "reduce_weight",
    status: str = "registered",
) -> TargetedCandidate:
    return TargetedCandidate(
        candidate_id=f"R2_{theme}_{family}_{factor}_TEST",
        theme=theme,
        family=family,
        track=PRICE_ONLY_DIAGNOSTIC,
        profile="balanced",
        score_horizon="mid",
        label_horizon=20,
        kind=kind,
        factor=factor,
        delta=delta,
        source_variant=f"PRICE_DROP_{factor}",
        status=status,
        reason="contract",
        aliases=(D_MARKET_RESIDUAL_DIAGNOSTIC,) if family.startswith("D_") else (),
    )


def _direction_row(variant_id: str, kind: str, ci: list[float], mean: float) -> dict:
    return {
        "kind": kind,
        "variant_id": variant_id,
        "statistically_thin": False,
        "pair_days": 100,
        "paired_diff_block_bootstrap": {"H": {"mean": mean, "ci95": ci}},
    }


def test_low_weight_candidate_must_not_silently_change_registered_delta() -> None:
    registry = load_registry()
    probe = PROBE["etfs_p_historical"]
    candidate = _candidate(probe["theme"], probe["family"], probe["factor"])
    before = diagnostic_weights(
        resolve_weights(registry, candidate.theme, candidate.family),
        track=PRICE_ONLY_DIAGNOSTIC,
        family=candidate.family,
    )
    assert before[probe["factor"]] == pytest.approx(probe["before_weight"])
    with pytest.raises(ValueError, match="HALVE_AT_BOUNDARY"):
        candidate_weights(registry, candidate)
    assert historical_halve_at_boundary(before[probe["factor"]], -0.05) == pytest.approx(-0.5 * before[probe["factor"]])


def test_automotive_really_changes_five_pp() -> None:
    registry = load_registry()
    probe = PROBE["automotive_v"]
    candidate = _candidate(probe["theme"], probe["family"], probe["factor"])
    plan = planned_candidate_weights(registry, candidate)
    assert plan["requested_delta"] == pytest.approx(probe["requested_delta"])
    assert plan["effective_delta"] == pytest.approx(probe["effective_delta"])
    assert plan["after"][probe["factor"]] == pytest.approx(probe["after_weight"])
    assert plan["after"][probe["factor"]] - plan["before"][probe["factor"]] == pytest.approx(-0.05)
    assert sum(plan["after"].values()) == pytest.approx(1.0)
    assert plan["boundary_policy"] == "REJECT_INFEASIBLE"


def test_reported_effective_vector_rebuilds_score() -> None:
    registry = load_registry()
    candidate = _candidate("automotive", "D_residual_momentum", "V")
    plan = planned_candidate_weights(registry, candidate)
    factors = {"T": 40.0, "M": 55.0, "S": 60.0, "B": 30.0, "P": 25.0, "V": 80.0, "R": 70.0, "G": None}
    required = family_required("D_residual_momentum")
    coverage_min = float(registry["profiles"]["balanced"]["coverage_min"])
    floor = float(registry["profiles"]["balanced"]["score_floor"])
    scored = score_features(factors, plan["after"], coverage_min=coverage_min, required=required)
    rebuilt = rescore_row(
        {"factors": factors, "rejection_reasons": []},
        plan["after"],
        coverage_min=coverage_min,
        required=required,
        score_floor=floor,
    )
    assert rebuilt["score"] == pytest.approx(scored.score)
    assert rebuilt["score"] is not None


def test_positive_increase_must_not_be_labeled_reduce() -> None:
    probe = PROBE["neighbor_semantics"]
    row = _direction_row(probe["input_variant"], probe["input_kind"], probe["H_ci95"], probe["H_mean"])
    assert transform_kind(row) == "REALLOC_INCREASE"
    assert signed_ablation_direction(row) == probe["expected_direction"]
    assert signed_ablation_direction(row) != probe["must_not_be"]


def test_negative_increase_is_reject_increase_not_delete() -> None:
    row = _direction_row("N_S_PLUS_5PP_REALLOC_V1", "neighbor", [-0.04, -0.01], -0.02)
    assert signed_ablation_direction(row) == "REJECT_INCREASE"
    assert signed_ablation_direction(row) != "REDUCE_WEIGHT_CANDIDATE"
    assert signed_ablation_direction(row) != "KEEP_FACTOR"


def test_existing_drop_positive_and_negative_stay_correct() -> None:
    probe = PROBE["drop_semantics"]
    reduce_row = _direction_row(probe["reduce_variant"], "ablation", [0.01, 0.03], 0.02)
    keep_row = _direction_row(probe["keep_variant"], "ablation", [-0.06, -0.01], -0.04)
    reduce_row["dropped"] = "R"
    keep_row["dropped"] = "M"
    assert transform_kind(reduce_row) == "DROP"
    assert signed_ablation_direction(reduce_row) == probe["reduce_expected"]
    assert signed_ablation_direction(keep_row) == probe["keep_expected"]


def test_threshold_cross_zero_is_inconclusive() -> None:
    probe = PROBE["threshold_semantics"]
    row = _direction_row(probe["variant_id"], "threshold", [-0.01, 0.01], 0.0)
    assert transform_kind(row) == "THRESHOLD"
    assert signed_ablation_direction(row) == probe["cross_zero_expected"]


def test_increase_neighbor_on_card_is_not_reduce() -> None:
    pairing = [
        {
            "theme": "software",
            "family": "A_trend_quality",
            "track": PRICE_ONLY_DIAGNOSTIC,
            "kind": "baseline",
            "variant_id": PRICE_BASELINE_ID,
            "dropped": None,
            "profile": "balanced",
            "score_horizon": "mid",
            "label_horizon": 20,
            "statistically_thin": False,
            "pair_days": 0,
            "own_ic_days": 100,
            "mean_common_n": None,
            "insufficient_date_n": 0,
            "yearly_direction": {},
            "paired_diff_block_bootstrap": None,
        },
        {
            "theme": "software",
            "family": "A_trend_quality",
            "track": PRICE_ONLY_DIAGNOSTIC,
            "kind": "ablation",
            "variant_id": "PRICE_DROP_T",
            "dropped": "T",
            "profile": "balanced",
            "score_horizon": "mid",
            "label_horizon": 20,
            "statistically_thin": False,
            "pair_days": 100,
            "own_ic_days": 100,
            "mean_common_n": 13,
            "insufficient_date_n": 0,
            "yearly_direction": {"2023": {"n": 100, "mean": 0.0, "direction": "mixed"}},
            "paired_diff_block_bootstrap": {
                "H": {"mean": 0.0, "ci95": [-0.02, 0.02]},
                "2H": {"mean": 0.0, "ci95": [-0.03, 0.03]},
            },
        },
        {
            "theme": "software",
            "family": "A_trend_quality",
            "track": PRICE_ONLY_DIAGNOSTIC,
            "kind": "neighbor",
            "variant_id": "N_S_PLUS_5PP_REALLOC_V1",
            "dropped": None,
            "profile": "balanced",
            "score_horizon": "mid",
            "label_horizon": 20,
            "statistically_thin": False,
            "pair_days": 100,
            "own_ic_days": 100,
            "mean_common_n": 13,
            "insufficient_date_n": 0,
            "yearly_direction": {"2023": {"n": 100, "mean": 0.02, "direction": "positive"}},
            "paired_diff_block_bootstrap": {
                "H": {"mean": 0.02, "ci95": [0.01, 0.03]},
                "2H": {"mean": 0.015, "ci95": [0.004, 0.03]},
            },
        },
    ]
    coverage = {
        ("software", "A_trend_quality"): {
            "score_status": "SCORED_NOT_SETUP_VALIDATED",
            "actual_G_observed": False,
            "can_score_without_G": True,
        }
    }
    card = _theme_card("software", ["A_trend_quality"], pairing, coverage, {})
    directions = [item["direction"] for item in card["families"][0]["next_neighbors"]]
    assert "INCREASE_WEIGHT_CANDIDATE" in directions
    assert "REDUCE_WEIGHT_CANDIDATE" not in directions


def test_etfs_parameter_correction_keeps_historical_halve() -> None:
    registry = load_registry()
    executed = next(item for item in json.loads(CANDIDATES.read_text()) if item["theme"] == "etfs")
    probe = PROBE["etfs_p_historical"]
    correction = etfs_parameter_correction(registry, executed)
    assert correction["requested_delta"] == pytest.approx(probe["requested_delta"])
    assert correction["effective_delta"] == pytest.approx(probe["effective_delta"])
    assert correction["boundary_policy"] == probe["boundary_policy"]
    assert correction["new_policy"] == probe["new_policy"]
    assert correction["after_P"] == pytest.approx(probe["after_weight"])
    assert correction["do_not_rewrite_old_run_as_true_minus_5pp"] is True
    assert correction["recompute_full_history"] is False
    assert executed["delta"] == -0.05


def test_freeze_snapshot_reads_r2_vector() -> None:
    row = load_frozen_candidate(CANDIDATES)
    snap = freeze_snapshot(row)
    probe = PROBE["automotive_v"]
    assert snap["candidate_id"] == FROZEN_CANDIDATE_ID
    assert snap["after_V"] == pytest.approx(probe["after_weight"])
    assert snap["effective_delta"] == pytest.approx(probe["effective_delta"])
    assert snap["relative_reduction"] == pytest.approx(probe["relative_reduction"])
    assert snap["not_a_small_relative_cut"] is True
    assert snap["do_not_retune_to_1_2_3pp"] is True
    assert snap["track_alias"] == D_MARKET_RESIDUAL_DIAGNOSTIC


def test_theme_family_status_covers_twenty_four_without_new_versions() -> None:
    cards = json.loads(CARDS.read_text())
    rows = theme_family_status(cards)
    assert len(rows) == 96
    assert {row["theme"] for row in rows} == {card["theme"] for card in cards}
    assert all(row["new_version_minted"] is False for row in rows)
    frozen = next(row for row in rows if row["theme"] == "automotive" and row["family"] == "D_residual_momentum")
    etfs = next(row for row in rows if row["theme"] == "etfs" and row["family"] == "D_residual_momentum")
    fintech = next(row for row in rows if row["theme"] == "fintech" and row["family"] == "B_confirmed_base_breakout")
    assert frozen["final_status"] == "FROZEN_EXPLORATORY_CANDIDATE"
    assert etfs["final_status"] == "KEEP_BASELINE"
    assert fintech["final_status"] == "KEEP_BASELINE"
    assert any(row["final_status"] == "THIN_INHERIT" for row in rows)


def test_checklist_is_complete_and_pre_daily() -> None:
    manifest = checklist_manifest(daily_effects_read=False)
    assert [item["item"] for item in manifest["checklist"]] == list(CHECKLIST)
    assert manifest["daily_effects_read"] is False
    assert manifest["do_not_rerun_1152"] is True
    assert "mean_delta" not in manifest
    assert "valid_pair_days" not in manifest
    if CHECKLIST_PATH.exists():
        written = json.loads(CHECKLIST_PATH.read_text())
        assert written["daily_effects_read"] is False
        assert "mean_delta" not in written
        assert [item["item"] for item in written["checklist"]] == list(CHECKLIST)


def test_project_gaps_are_three_state_and_not_claimed_complete() -> None:
    rows = project_gaps()
    assert {row["gap"] for row in rows} == {
        "三档三周期",
        "十年以上数据",
        "历史成员/退市/行业",
        "独立综合层",
        "经济执行与生产EOD接入",
    }
    assert {row["status"] for row in rows} <= {"已验证", "已实现未验证", "未做"}
    assert all(row["status"] != "已完成全方案" for row in rows)
    assert next_stage_data_requirements()
    assert all("do not assume" in item["access"].lower() or "do not auto-buy" in item["access"].lower() for item in next_stage_data_requirements())


def _synthetic_rows() -> list[dict]:
    rows = []
    members = [f"S{i:02d}" for i in range(10)]
    sessions = (
        ["2018-03-01"]
        + [f"2023-01-{day:02d}" for day in range(3, 15)]
        + ["2023-06-01", "2023-06-02"]
    )
    for session in sessions:
        label_ready = session != "2023-06-01"
        names = members if session != "2023-06-02" else members[:9]
        for i, sid in enumerate(names):
            rows.append(
                {
                    "security_id": sid,
                    "signal_session": session,
                    "theme_id": "automotive",
                    "algorithm": "D_residual_momentum",
                    "profile": "balanced",
                    "horizon": "mid",
                    "label_horizon": 20,
                    "snapshot_key": f"{session}|automotive|D|{sid}",
                    "factors": {
                        "T": 40 + i,
                        "M": 50 + i,
                        "S": 60 + i,
                        "B": 20 + i,
                        "P": 15 + i,
                        "V": 10 + 9 * i,
                        "R": 70 + i,
                        "G": None,
                    },
                    "label": None if not label_ready else 0.02 * i,
                    "rejection_reasons": [],
                }
            )
    return rows


def test_validate_classifies_n0_unmature_and_n_below_10() -> None:
    registry = load_registry()
    frozen = load_frozen_candidate(CANDIDATES)
    timeline = ["2018-01-02"] + sorted({row["signal_session"] for row in _synthetic_rows()})
    result = validate_frozen_candidate(_synthetic_rows(), registry, frozen=frozen, sessions=timeline)
    counts = result["rejection_counts"]
    assert counts.get("N0_NO_THEME_ROWS") == 1
    assert counts.get("LABEL_NOT_MATURE") == 1
    assert counts.get("COMMON_N_BELOW_10") == 1
    assert result["valid_pair_days"] >= 10
    assert result["yearly"]
    assert result["quarterly"]
    assert result["absolute_ics_may_be_negative"] is True
    assert result["execution_prices"] == "MISSING"
    assert result["nav_winrate_capacity"] == "NOT_INVENTED"
    assert result["eligible_not_independent_trades"] is True
    leave = result["leave_one_member"]
    assert leave
    assert all(item["remaining_statistically_thin"] or item["days_lost_below_n"] for item in leave)
    own_small = next(item for item in result["daily"] if item["session"] == "2023-06-02")
    assert own_small["defined_pair"] is False
    assert own_small.get("own_ic_below_pair_threshold") is True
    table = public_validation_table(result, limit=PROBE["public_table_limit"])
    assert len(table) <= PROBE["public_table_limit"]
    assert all(row["paired_diff"] is not None for row in table)


def test_stop_rule_is_exactly_one_of_three() -> None:
    keep = decide_stop(
        {
            "valid_pair_days": 312,
            "paired_diff_block_bootstrap": {
                "H": {"ci95": [0.003, 0.014]},
                "2H": {"ci95": [0.003, 0.015]},
            },
            "yearly": {"2018": {"direction": "positive"}, "2023": {"direction": "negative"}},
            "leave_one_member": [{"remaining_statistically_thin": True}],
        }
    )
    revert = decide_stop(
        {
            "valid_pair_days": 312,
            "paired_diff_block_bootstrap": {
                "H": {"ci95": [-0.01, 0.01]},
                "2H": {"ci95": [-0.02, 0.02]},
            },
            "yearly": {},
            "leave_one_member": [],
        }
    )
    missing = decide_stop({"valid_pair_days": 0, "paired_diff_block_bootstrap": {}, "yearly": {}, "leave_one_member": []})
    assert keep["outcome"] in PROBE["stop_outcomes"]
    assert revert["outcome"] == "REVERT_BASELINE"
    assert missing["outcome"] == "INSUFFICIENT"
    assert keep["not_a_production_champion"] is True
    assert keep["do_not_retune_weights"] is True
    assert keep["holdout_unsealed"] is False
