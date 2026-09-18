"""PR #174 Round 2 contracts. Probe numbers are semantic checks, not pass-count theater."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from app.services.research_eod_v1.bootstrap import circular_block_bootstrap
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    realloc_plus_pp,
)
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.pairing import DuplicatePairIdError, common_member_pair
from app.services.research_eod_v1.round1b import (
    PRICE_BASELINE_ID,
    analyze_rows_r1b,
    qualified_mean_common_n,
    _theme_card,
)
from app.services.research_eod_v1.round2 import select_targeted_candidates
from app.services.research_eod_v1.source_bind import member_set_hash
from app.services.research_eod_v1.stats import STATISTICS_VERSION
from registry import resolve_weights  # type: ignore

PROBE = json.loads(
    (
        Path(__file__).resolve().parents[1] / "research/option_pro_us_eod_v1/evidence/probes_round2.json"
    ).read_text()
)


def _ten(*, score_at=None, label_at=None, score_value=None, label_value=None):
    rows = [{"security_id": f"S{i:02d}", "score": float(i), "label": float(i)} for i in range(10)]
    if score_at is not None:
        rows[score_at]["score"] = score_value
    if label_at is not None:
        rows[label_at]["label"] = label_value
    return rows


def test_statistics_version_bumped_for_trim_and_finite_guards() -> None:
    assert STATISTICS_VERSION == PROBE["statistics_version"]


def test_nonfinite_score_rejected_before_common_set() -> None:
    probe = PROBE["nonfinite_score_guard"]
    pair = common_member_pair(_ten(score_at=0, score_value=float("nan")), _ten(score_at=0, score_value=float("nan")))
    assert pair["common_n"] == probe["expected_common_n"]
    assert pair["paired_diff"] is probe["expected_pair_diff"]
    assert pair["undefined_reason"] == probe["expected_undefined_reason"]
    assert pair["invalid_reasons"]["baseline"]["S00"] == probe["expected_invalid_reason"]
    assert pair["common_n"] == len(pair["common_security_ids"])
    assert pair["member_hash"] == member_set_hash(pair["common_security_ids"])
    with pytest.raises(DuplicatePairIdError):
        common_member_pair(
            [
                {"security_id": "S00", "score": float("nan"), "label": 1.0},
                {"security_id": "S00", "score": 1.0, "label": 1.0},
            ],
            _ten(),
        )


def test_nonfinite_label_rejected_before_common_set() -> None:
    probe = PROBE["nonfinite_label_guard"]
    pair = common_member_pair(
        _ten(label_at=3, label_value=float("inf")),
        _ten(label_at=3, label_value=float("inf")),
    )
    assert pair["common_n"] == probe["expected_common_n"]
    assert pair["paired_diff"] is probe["expected_pair_diff"]
    assert pair["undefined_reason"] == probe["expected_undefined_reason"]
    assert pair["invalid_reasons"]["variant"]["S03"] == probe["expected_invalid_reason"]


def test_bootstrap_trims_to_original_timeline() -> None:
    probe = PROBE["bootstrap_padding"]
    rng = np.random.default_rng(174)
    long = [float(item) for item in rng.normal(0.0, 0.1, probe["timeline"])]
    padded = circular_block_bootstrap(long, block_len=probe["block_len"], n_boot=2000, seed=174)
    assert padded["n_timeline"] == probe["timeline"]
    assert padded["n_samples_per_replicate"] == probe["expected_samples_per_replicate"]
    even = [0.03] * probe["even_timeline"]
    even_out = circular_block_bootstrap(even, block_len=probe["even_block_len"], n_boot=2000, seed=174)
    assert even_out["n_samples_per_replicate"] == probe["even_timeline"]
    assert even_out["ci95"][0] == pytest.approx(0.03)
    assert even_out["ci95"][1] == pytest.approx(0.03)
    sparse = [None if i % 5 == 0 else 0.02 for i in range(400)]
    sparse_out = circular_block_bootstrap(sparse, block_len=20, n_boot=200, seed=174)
    assert sparse_out["n_timeline"] == 400
    assert sparse_out["n_samples_per_replicate"] == 400
    assert sparse_out["n_observed"] == 320
    dirty = [0.01] * 198 + [float("nan"), float("inf")] + [0.01] * 200
    dirty_out = circular_block_bootstrap(dirty, block_len=20, n_boot=200, seed=174)
    assert dirty_out["nonfinite_inputs_marked_missing"] == 2
    assert dirty_out["n_samples_per_replicate"] == 400


def test_mean_common_n_uses_qualified_dates_only() -> None:
    probe = PROBE["mean_common_n_formula"]
    wrong = sum(probe["fixture_common_counts"]) / sum(probe["fixture_defined_pair_flags"])
    assert wrong == pytest.approx(probe["wrong_formula_value"])
    assert qualified_mean_common_n(
        probe["fixture_common_counts"], probe["fixture_defined_pair_flags"]
    ) == pytest.approx(probe["correct_defined_pair_mean"])


def test_analyze_mean_common_n_and_baseline_not_thin() -> None:
    registry = load_registry()
    rows = []
    for day, n_names in (("2023-06-12", 9), ("2023-06-13", 10), *[(f"2023-06-{14+i:02d}", 12) for i in range(10)]):
        for i in range(n_names):
            rows.append(
                {
                    "security_id": f"N{i:02d}",
                    "signal_session": day,
                    "theme_id": "software",
                    "algorithm": "A_trend_quality",
                    "profile": "balanced",
                    "horizon": "mid",
                    "label_horizon": 20,
                    "snapshot_key": f"{day}|software|A|N{i:02d}",
                    "factors": {"T": 80, "M": 80, "S": 80, "B": 80, "P": 80, "V": 80, "R": 80, "G": None},
                    "score": 80.0,
                    "label": 0.01 * i,
                    "final_eligible": True,
                    "status": "eligible",
                    "rejection_reasons": [],
                }
            )
    out = analyze_rows_r1b(rows, registry)
    drop = next(
        item
        for item in out["pairing"]
        if item["variant_id"] == "PRICE_DROP_T" and item["label_horizon"] == 20
    )
    # first day N=9 is insufficient; later days are defined. Mean must not add 9 into the pair_days denominator.
    assert drop["insufficient_date_n"] >= 1
    assert drop["pair_days"] >= 10
    assert drop["mean_common_n"] == pytest.approx(drop["mean_common_n"])
    assert drop["mean_common_n"] != pytest.approx(19.0)
    baseline = next(
        item
        for item in out["pairing"]
        if item["variant_id"] == PRICE_BASELINE_ID and item["label_horizon"] == 20
    )
    assert baseline["pair_days"] == 0
    assert baseline["own_ic_days"] >= 10
    assert baseline["statistically_thin"] is False
    assert baseline["statistically_thin_basis"] == "own_ic_days"


def _card_row(**kwargs):
    row = {
        "theme": "software",
        "family": "D_residual_momentum",
        "track": PRICE_ONLY_DIAGNOSTIC,
        "kind": "ablation",
        "profile": "balanced",
        "score_horizon": "mid",
        "label_horizon": 20,
        "statistically_thin": False,
        "pair_days": 100,
        "own_ic_days": 100,
        "mean_common_n": 13,
        "insufficient_date_n": 2,
        "yearly_direction": {"2023": {"n": 100, "mean": 0.02, "direction": "positive"}},
        "paired_diff_block_bootstrap": {
            "H": {"mean": 0.02, "ci95": [0.01, 0.04]},
            "2H": {"mean": 0.015, "ci95": [0.004, 0.03]},
        },
    }
    row.update(kwargs)
    return row


def test_price_track_card_not_blocked_by_eight_factor() -> None:
    probe = PROBE["price_track_card_block"]
    pairing = [
        _card_row(
            variant_id=PRICE_BASELINE_ID,
            kind="baseline",
            dropped=None,
            pair_days=0,
            mean_common_n=None,
            statistically_thin=False,
            paired_diff_block_bootstrap=None,
        ),
        _card_row(variant_id="PRICE_DROP_R", dropped="R"),
    ]
    coverage = {
        ("software", "D_residual_momentum"): {
            "score_status": probe["eight_factor_status"],
            "actual_G_observed": probe["actual_G_observed"],
            "can_score_without_G": probe["can_score_without_G"],
        }
    }
    card = _theme_card(
        "software",
        ["D_residual_momentum"],
        pairing,
        coverage,
        {"note": "fixture"},
        profile="balanced",
        horizon="mid",
    )
    family = card["families"][0]
    assert family["eight_factor_status"] == probe["eight_factor_status"]
    assert family["price_track_status"] == "EVALUABLE"
    assert family["decision"] != probe["do_not_decision"]
    assert family["pair_days_price_ablations"] == probe["pair_days_price_ablations"]
    assert family["mean_common_n"] == pytest.approx(probe["mean_common_n"])
    assert family["track_alias"] == probe["alias"]
    assert family["not_a_second_experiment"] is True
    assert family["aliases"] == [D_MARKET_RESIDUAL_DIAGNOSTIC]
    assert family["next_neighbors"]
    assert isinstance(family["next_neighbors"][0], dict)
    neighbor = family["next_neighbors"][0]
    for key in (
        "family",
        "profile",
        "score_horizon",
        "label_horizon",
        "track",
        "variant_id",
        "direction",
        "reason",
    ):
        assert key in neighbor
    assert neighbor["label_horizon"] == 20
    assert neighbor["direction"] == "REDUCE_WEIGHT_CANDIDATE"


def test_drop_worse_is_keep_factor_not_abs_improvement() -> None:
    pairing = [
        _card_row(
            variant_id=PRICE_BASELINE_ID,
            kind="baseline",
            dropped=None,
            pair_days=0,
            mean_common_n=None,
            statistically_thin=False,
            paired_diff_block_bootstrap=None,
        ),
        _card_row(
            variant_id="PRICE_DROP_M",
            dropped="M",
            paired_diff_block_bootstrap={
                "H": {"mean": -0.04, "ci95": [-0.06, -0.01]},
                "2H": {"mean": -0.03, "ci95": [-0.05, -0.005]},
            },
            yearly_direction={"2023": {"n": 100, "mean": -0.04, "direction": "negative"}},
        ),
        _card_row(
            variant_id="PRICE_DROP_T",
            dropped="T",
            pair_days=100,
            paired_diff_block_bootstrap={
                "H": {"mean": 0.001, "ci95": [-0.02, 0.02]},
                "2H": {"mean": 0.0, "ci95": [-0.03, 0.03]},
            },
        ),
    ]
    coverage = {
        ("software", "A_trend_quality"): {
            "score_status": "SCORED_NOT_SETUP_VALIDATED",
            "actual_G_observed": False,
            "can_score_without_G": True,
        }
    }
    pairing[0]["family"] = "A_trend_quality"
    pairing[1]["family"] = "A_trend_quality"
    pairing[2]["family"] = "A_trend_quality"
    card = _theme_card("software", ["A_trend_quality"], pairing, coverage, {})
    family = card["families"][0]
    assert family["decision"] == "KEEP_FACTOR_EVIDENCE"
    assert family["keep_factor_variants"] == ["PRICE_DROP_M"]
    assert family["robust_variants"] == []
    assert family["next_neighbors"][0]["direction"] == "KEEP_FACTOR"
    assert family["next_neighbors"][0]["variant_id"] == "PRICE_DROP_M"
    assert "abs" not in family["next_neighbors"][0]["reason"] or "not an abs-magnitude" in family["next_neighbors"][0]["reason"]


def test_true_plus_five_pp_still_holds_on_price_track() -> None:
    registry = load_registry()
    weights = resolve_weights(registry, "semiconductors", "A_trend_quality", "balanced", "mid")
    from app.services.research_eod_v1.capability import diagnostic_weights

    price = diagnostic_weights(weights, track=PRICE_ONLY_DIAGNOSTIC, family="A_trend_quality")
    moved = realloc_plus_pp(price, "M", 0.05)
    assert moved["M"] == pytest.approx(price["M"] + 0.05)
    assert moved["G"] == 0.0
    assert sum(moved.values()) == pytest.approx(1.0)


def test_select_targeted_candidates_caps_at_three_and_skips_h_only() -> None:
    pairing = [
        _card_row(
            theme="software",
            family="D_residual_momentum",
            variant_id="PRICE_DROP_R",
            dropped="R",
        ),
        _card_row(
            theme="healthcare",
            family="A_trend_quality",
            variant_id="PRICE_DROP_T",
            dropped="T",
            paired_diff_block_bootstrap={
                "H": {"mean": 0.03, "ci95": [0.01, 0.05]},
                "2H": {"mean": 0.01, "ci95": [-0.02, 0.04]},
            },
        ),
        _card_row(
            theme="semiconductors",
            family="A_trend_quality",
            variant_id="N_M_PLUS_5PP_REALLOC_V1",
            dropped=None,
            kind="neighbor",
        ),
    ]
    chosen = select_targeted_candidates(pairing)
    assert len(chosen) == 3
    registered = [item for item in chosen if item.status == "registered"]
    assert len(registered) == 1
    assert registered[0].theme == "software"
    assert registered[0].factor == "R"
    assert registered[0].delta == -0.05
    assert chosen[1].status == "keep_baseline"
    assert chosen[2].status == "keep_baseline"
