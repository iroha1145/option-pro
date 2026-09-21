"""Boundary regressions for the #184 review. Synthetic only."""

from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "research"))

from screener_gate_candidates_v1 import (
    B0_CURRENT,
    DOLLAR_LIQUIDITY_UNVERIFIED,
    EXTENDED,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    HIGH_ATR,
    Facts,
    ReferenceInput,
    _finite_positive,
    b0_recompute_diagnostic,
    build_reference,
    evaluate,
    stock_rank,
)
from screener_gate_statistics_v2 import (
    HOLDOUT_FROM,
    LabelBook,
    aggregate_discovery_records,
    circular_block_indices,
    compare_frozen_to_old_session,
    entry_invariant_failures,
    freeze_layers,
    paired_bootstrap,
    write_checkpoint,
)

SESSION = "2024-01-02"
CAP = 8.0
MULT = 1.75


def member(i: int, **kwargs) -> ReferenceInput:
    values = dict(
        security_id=f"S{i:03d}",
        session_date=SESSION,
        asset_track="stock",
        raw_close=25.0,
        adv20=40_000_000.0,
        atr_pct=2.0,
        history_sessions=300,
        currently_tradable=True,
        halted=False,
        zero_volume=False,
        complete_bar=True,
        venue_eligible=True,
    )
    values.update(kwargs)
    return ReferenceInput(**values)


def facts(**kwargs) -> Facts:
    values = dict(
        security_id="AAA",
        session_date=SESSION,
        asset_track="stock",
        raw_close=20.0,
        adv20=50_000_000.0,
        atr_pct=4.0,
        history_sessions=300,
        currently_tradable=True,
        halted=False,
        zero_volume=False,
        complete_bar=True,
        venue_eligible=True,
    )
    values.update(kwargs)
    return Facts(**values)


def row(**kwargs) -> dict:
    values = {
        "security_id": "AAA",
        "session_date": SESSION,
        "stock_or_etf_track": "stock",
        "algorithm_id": "A_trend_quality",
        "sector_context": "semiconductors",
        "score": 81.0,
        "status": "eligible",
        "rejection_reasons": [],
        "gate_results": {"common": (), "setup": ()},
    }
    values.update(kwargs)
    return values


def decide(variant: str, src: dict, **fact_kw):
    return evaluate(
        variant=variant,
        row=src,
        facts=facts(**fact_kw),
        reference=build_reference([member(i, atr_pct=3.0) for i in range(30)]),
        old_reference_median=3.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )


def test_finite_positive_rejects_inf():
    assert _finite_positive(float("inf")) is None


def test_finite_positive_rejects_nan():
    assert _finite_positive(float("nan")) is None


def test_finite_positive_rejects_bool():
    assert _finite_positive(True) is None


def test_stock_rank_skips_nan():
    ranked = stock_rank([
        {"security_id": "A", "score": float("nan"), "stock_or_etf_track": "stock"},
        {"security_id": "B", "score": 10.0, "stock_or_etf_track": "stock"},
    ])
    assert [item["security_id"] for item in ranked] == ["B"]


def test_stock_rank_skips_inf():
    ranked = stock_rank([
        {"security_id": "A", "score": float("inf"), "stock_or_etf_track": "stock"},
        {"security_id": "B", "score": 10.0, "stock_or_etf_track": "stock"},
    ])
    assert [item["security_id"] for item in ranked] == ["B"]


def test_stock_rank_skips_bool():
    ranked = stock_rank([
        {"security_id": "A", "score": True, "stock_or_etf_track": "stock"},
        {"security_id": "B", "score": 10.0, "stock_or_etf_track": "stock"},
    ])
    assert [item["security_id"] for item in ranked] == ["B"]


def test_evaluate_rejects_nan_score():
    src = row(score=float("nan"))
    assert decide(G1_STOCK_REFERENCE, src, atr_pct=2.0).technical_entry_passed is False


def test_evaluate_rejects_inf_score():
    src = row(score=float("inf"))
    assert decide(G1_STOCK_REFERENCE, src, atr_pct=2.0).discovery_passed is False


def test_evaluate_rejects_bool_score():
    src = row(score=True)
    assert decide(G1_STOCK_REFERENCE, src, atr_pct=2.0).technical_entry_passed is False


def test_evaluate_rejects_missing_score():
    src = row()
    src.pop("score")
    assert decide(G1_STOCK_REFERENCE, src, atr_pct=2.0).qualified_entry_passed is False


def test_rejected_with_empty_reasons_does_not_pass():
    src = row(status="rejected", rejection_reasons=[])
    decision = decide(G1_STOCK_REFERENCE, src, atr_pct=2.0)
    assert decision.technical_entry_passed is False
    assert decision.discovery_passed is False


def test_watch_with_empty_reasons_does_not_pass():
    src = row(status="watch", rejection_reasons=[])
    assert decide(G2_EXTENSION_DISCOVERY, src, atr_pct=2.0).discovery_passed is False


def test_missing_security_id_does_not_fall_back_to_facts():
    src = row()
    src.pop("security_id")
    with pytest.raises(ValueError, match="security_id"):
        decide(B0_CURRENT, src)


def test_missing_session_date_does_not_fall_back_to_facts():
    src = row()
    src.pop("session_date")
    with pytest.raises(ValueError, match="session_date"):
        decide(B0_CURRENT, src)


def test_missing_track_does_not_fall_back_to_facts():
    src = row()
    src.pop("stock_or_etf_track")
    with pytest.raises(ValueError, match="stock_or_etf_track"):
        decide(B0_CURRENT, src)


def test_reference_date_mismatch_is_rejected():
    ref = build_reference([member(i, session_date="2024-02-01") for i in range(30)])
    with pytest.raises(ValueError, match="reference session_date"):
        evaluate(
            variant=G1_STOCK_REFERENCE,
            row=row(),
            facts=facts(),
            reference=ref,
            old_reference_median=3.0,
            absolute_atr_cap=CAP,
            reference_multiplier=MULT,
        )


def test_setup_high_atr_is_not_softened_by_g3():
    src = row(
        status="rejected",
        rejection_reasons=["HIGH_ATR"],
        gate_results={"common": (), "setup": ("HIGH_ATR",)},
    )
    decision = decide(G3_RISK_DISCOVERY, src, atr_pct=4.0)
    assert decision.discovery_passed is False
    assert HIGH_ATR not in decision.risk_hints


def test_setup_extended_is_not_softened_by_g2():
    src = row(
        status="rejected",
        rejection_reasons=["EXTENDED"],
        gate_results={"common": (), "setup": ("EXTENDED",)},
    )
    decision = decide(G2_EXTENSION_DISCOVERY, src, atr_pct=2.0)
    assert decision.discovery_passed is False
    assert EXTENDED not in decision.risk_hints


def test_non_common_high_atr_is_not_removed_by_g1():
    src = row(
        status="rejected",
        rejection_reasons=["HIGH_ATR"],
        gate_results={"common": (), "setup": ("HIGH_ATR",)},
    )
    ref = build_reference([member(i, atr_pct=6.0) for i in range(30)])
    decision = evaluate(
        variant=G1_STOCK_REFERENCE,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=ref,
        old_reference_median=6.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert decision.high_atr is True
    assert HIGH_ATR in decision.research_reasons


def test_extended_without_common_key_is_not_a_g2_hint():
    src = row(status="rejected", rejection_reasons=["EXTENDED"], gate_results={"common": ("EXTENDED",)})
    decision = decide(G2_EXTENSION_DISCOVERY, src, atr_pct=2.0)
    assert decision.discovery_passed is False


def test_partial_gate_map_does_not_soften():
    src = row(status="rejected", rejection_reasons=["HIGH_ATR", "EXTENDED"], gate_results={"setup": ("HIGH_ATR", "EXTENDED")})
    decision = decide(G3_RISK_DISCOVERY, src, atr_pct=4.0)
    assert decision.discovery_passed is False


def test_b0_keeps_upstream_high_atr_when_recompute_would_clear():
    src = row(status="rejected", rejection_reasons=["HIGH_ATR"], gate_results={"common": ("HIGH_ATR",), "setup": ()})
    decision = decide(B0_CURRENT, src, atr_pct=4.0)
    decision_low_median = evaluate(
        variant=B0_CURRENT,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=build_reference([member(i) for i in range(30)]),
        old_reference_median=6.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert decision_low_median.high_atr is True
    diagnostic = b0_recompute_diagnostic(
        src, facts(atr_pct=4.0), old_reference_median=6.0, absolute_atr_cap=CAP, reference_multiplier=MULT,
    )
    assert diagnostic["recomputed_high_atr"] is False
    assert diagnostic["agrees"] is False
    assert decision.high_atr is True


def test_b0_does_not_adopt_recompute_when_upstream_is_clear():
    src = row(status="eligible", rejection_reasons=[], gate_results={"common": (), "setup": ()})
    decision = evaluate(
        variant=B0_CURRENT,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=build_reference([member(i, atr_pct=1.0) for i in range(30)]),
        old_reference_median=1.5,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    diagnostic = b0_recompute_diagnostic(
        src, facts(atr_pct=4.0), old_reference_median=1.5, absolute_atr_cap=CAP, reference_multiplier=MULT,
    )
    assert diagnostic["recomputed_high_atr"] is True
    assert diagnostic["agrees"] is False
    assert decision.high_atr is False


def test_b0_diagnostic_agreement_does_not_change_the_flag():
    src = row(status="rejected", rejection_reasons=["HIGH_ATR"], gate_results={"common": ("HIGH_ATR",), "setup": ()})
    decision = evaluate(
        variant=B0_CURRENT,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=build_reference([member(i, atr_pct=1.0) for i in range(30)]),
        old_reference_median=1.5,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    diagnostic = b0_recompute_diagnostic(
        src, facts(atr_pct=4.0), old_reference_median=1.5, absolute_atr_cap=CAP, reference_multiplier=MULT,
    )
    assert diagnostic["agrees"] is True
    assert decision.high_atr is True


def test_complete_eligible_row_can_pass_technical():
    decision = decide(G1_STOCK_REFERENCE, row(), atr_pct=2.0)
    assert decision.technical_entry_passed is True
    assert DOLLAR_LIQUIDITY_UNVERIFIED in decision.research_reasons
    assert decision.qualified_entry_passed is False


def test_g1_can_still_add_on_complete_common_gate():
    src = row(status="eligible", gate_results={"common": (), "setup": ()})
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    decision = evaluate(
        variant=G1_STOCK_REFERENCE,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=ref,
        old_reference_median=6.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert decision.high_atr is True
    assert decision.technical_entry_passed is False


def test_g1_can_still_remove_common_high_atr():
    src = row(status="rejected", rejection_reasons=["HIGH_ATR"], gate_results={"common": ("HIGH_ATR",), "setup": ()})
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    decision = evaluate(
        variant=G1_STOCK_REFERENCE,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=ref,
        old_reference_median=1.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert decision.high_atr is False
    assert decision.technical_entry_passed is True


def test_facts_mismatch_still_raises():
    with pytest.raises(ValueError, match="security_id"):
        evaluate(
            variant=B0_CURRENT,
            row=row(),
            facts=facts(security_id="OTHER"),
            reference=build_reference([member(i) for i in range(30)]),
            old_reference_median=3.0,
            absolute_atr_cap=CAP,
            reference_multiplier=MULT,
        )


def test_g2_g3_technical_entry_matches_g1_on_complete_row():
    src = row(
        status="rejected",
        rejection_reasons=["HIGH_ATR", "EXTENDED"],
        gate_results={"common": ("HIGH_ATR", "EXTENDED"), "setup": ()},
    )
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    decisions = [
        evaluate(
            variant=variant,
            row=src,
            facts=facts(atr_pct=4.0),
            reference=ref,
            old_reference_median=1.0,
            absolute_atr_cap=CAP,
            reference_multiplier=MULT,
        )
        for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)
    ]
    assert len({item.technical_entry_passed for item in decisions}) == 1
    assert decisions[2].discovery_passed is True
    assert decisions[0].discovery_passed is False


class _Series:
    def __init__(self, dates, open_, high, low, close):
        self.dates = dates
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


def _book():
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4), date(2024, 1, 5)]
    series = _Series(days, [10, 10, 12, 14], [11, 11, 13, 100], [9, 9, 11, 13], [10, 11, 12, 13])
    return LabelBook({"AAA": series, "BBB": series}, days), days


def test_open_hold_is_one_full_session_not_zero():
    book, days = _book()
    assert book.label("AAA", days[0], 1, use_open=True) == pytest.approx(0.2)


def test_open_exit_is_e_plus_h_not_t_plus_h():
    book, days = _book()
    assert book.label("AAA", days[0], 1, use_open=True) != 0.0
    assert book._shift(days[0], 2) == days[2]


def test_missing_bar_does_not_shift_the_endpoint():
    days = [date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 4)]
    series = _Series([days[0], days[2]], [10, 12], [11, 13], [9, 11], [10, 12])
    book = LabelBook({"AAA": series}, days)
    assert book.label("AAA", days[0], 1, use_open=False) is None


def test_close_does_not_fill_a_missing_open():
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    series = _Series(days, [10, float("nan")], [11, 12], [9, 10], [10, 11])
    book = LabelBook({"AAA": series}, days)
    assert book.label("AAA", days[0], 1, use_open=True) is None
    assert book.label("AAA", days[0], 1, use_open=False) == pytest.approx(0.1)


def test_empty_basket_is_not_a_zero_return():
    book, days = _book()
    basket = book.basket([], days[0], 1, use_open=False)
    assert basket["full_mean"] is None
    assert basket["available_mean"] is None
    assert basket["reason"] == "empty_basket_not_zero"


def test_partial_basket_separates_full_and_available_means():
    days = [date(2024, 1, 2), date(2024, 1, 3)]
    full = _Series(days, [10, 10], [11, 11], [9, 9], [10, 12])
    short = _Series([days[0]], [10], [11], [9], [10])
    book = LabelBook({"AAA": full, "BBB": short}, days)
    basket = book.basket(["AAA", "BBB"], days[0], 1, use_open=False)
    assert basket["full_mean"] is None
    assert basket["available_mean"] == pytest.approx(0.2)
    assert basket["n_labels"] == 1


def test_complete_basket_full_mean_matches_available():
    book, days = _book()
    basket = book.basket(["AAA"], days[0], 1, use_open=False)
    assert basket["full_mean"] == basket["available_mean"] == pytest.approx(0.1)


def test_paired_bootstrap_keeps_missing_calendar_slots():
    values = [0.1, None, 0.1]
    indexes = circular_block_indices(3, 3, 1, 174)
    assert 1 in indexes[0]
    result = paired_bootstrap(values, block=3, reps=20, seed=174)
    assert result["n_days"] == 3
    assert result["n_missing"] == 1
    assert result["conditional_mean"] == pytest.approx(0.1)
    assert "max_drawdown" not in result
    assert "drawdown" not in json.dumps(result)


def test_bootstrap_seed_is_reproducible():
    values = [0.01, None, -0.02, 0.03]
    assert paired_bootstrap(values, block=2, reps=30, seed=174) == paired_bootstrap(values, block=2, reps=30, seed=174)


def test_label_does_not_cross_holdout():
    days = [date(2024, 6, 28), HOLDOUT_FROM]
    series = _Series(days, [10, 10], [11, 11], [9, 9], [10, 12])
    book = LabelBook({"AAA": series}, days, holdout_from=HOLDOUT_FROM)
    assert book.label("AAA", days[0], 1, use_open=False) is None


def test_freeze_layers_requires_complete_identity():
    with pytest.raises(ValueError, match="bars_sha256"):
        freeze_layers(
            {"session_date": SESSION},
            lambda *_args, **_kwargs: [],
            identity={
                "production_anchor": "d16b25e",
                "candidates_sha256": "a" * 64,
                "adapter_sha256": "b" * 64,
                "profile": "balanced",
                "horizon": "mid",
                "session_date": SESSION,
            },
        )


def test_freeze_layers_rejects_divergent_technical_lists():
    def ranked(_result, variant, layer):
        if layer == "technical_entry" and variant == G2_EXTENSION_DISCOVERY:
            return [{"security_id": "OTHER", "score": 1.0}]
        return [{"security_id": "AAA", "score": 1.0}]

    with pytest.raises(ValueError, match="technical_entry"):
        freeze_layers(
            {"session_date": SESSION},
            ranked,
            identity={
                "production_anchor": "d16b25e",
                "candidates_sha256": "a" * 64,
                "adapter_sha256": "b" * 64,
                "bars_sha256": "c" * 64,
                "profile": "balanced",
                "horizon": "mid",
                "session_date": SESSION,
            },
        )


def test_write_checkpoint_refuses_overwrite(tmp_path):
    payload = {"identity": {"session_date": SESSION}}
    write_checkpoint(tmp_path, payload)
    with pytest.raises(FileExistsError):
        write_checkpoint(tmp_path, payload)


def test_open_path_excludes_exit_session_range():
    book, days = _book()
    path = book.path("AAA", days[0], 1, use_open=True)
    assert path["mfe"] == pytest.approx(0.1)
    assert path["mfe"] < 1.0


def test_freeze_layers_saves_all_three_layers_when_entry_matches():
    def ranked(_result, variant, layer):
        return [{"security_id": "AAA", "score": 2.0, "algorithm_id": "A_trend_quality", "sector_context": "x", "rejection_reasons": []}]

    payload = freeze_layers(
        {"session_date": SESSION},
        ranked,
        identity={
            "production_anchor": "d16b25e3812dce9adf16e2ca993b4f2c38d7b1ef",
            "candidates_sha256": "a" * 64,
            "adapter_sha256": "b" * 64,
            "bars_sha256": "c" * 64,
            "profile": "balanced",
            "horizon": "mid",
            "session_date": SESSION,
        },
    )
    assert set(payload["layers"][G1_STOCK_REFERENCE]) == {"discovery", "technical_entry", "qualified_entry"}
    assert payload["layers"][G3_RISK_DISCOVERY]["technical_entry"][0]["security_id"] == "AAA"


def test_paired_diff_uses_same_day_not_an_absolute_level():
    values = [0.02, None, -0.01]
    result = paired_bootstrap(values, block=1, reps=5, seed=174)
    assert result["n_valid"] == 2
    assert result["conditional_mean"] == pytest.approx(0.005)
    assert math.isfinite(result["conditional_mean"])


def _frozen(discovery_ids, technical_ids):
    def rows(ids):
        return [
            {
                "security_id": sid,
                "score": 1.0,
                "algorithm_id": "A_trend_quality",
                "sector_context": "x",
                "rejection_reasons": [],
            }
            for sid in ids
        ]

    layers = {}
    for variant in (B0_CURRENT, G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY):
        layers[variant] = {
            "discovery": rows(discovery_ids if variant == G2_EXTENSION_DISCOVERY else technical_ids),
            "technical_entry": rows(technical_ids),
            "qualified_entry": [],
        }
    return {"layers": layers, "identity": {"session_date": "2022-01-03"}}


def test_entry_invariant_allows_discovery_differences_only():
    assert entry_invariant_failures(_frozen(["AAA", "BBB"], ["AAA"])) == []


def test_entry_invariant_flags_technical_drift():
    payload = _frozen(["AAA"], ["AAA"])
    payload["layers"][G3_RISK_DISCOVERY]["technical_entry"] = [
        {"security_id": "OTHER", "score": 1.0, "algorithm_id": "A", "sector_context": "x", "rejection_reasons": []}
    ]
    assert entry_invariant_failures(payload) == ["technical_entry"]


def test_added_name_group_skips_days_with_no_substitution():
    rows = [
        {
            "session_date": "2022-01-03",
            "layer": "discovery",
            "comparison": f"{G2_EXTENSION_DISCOVERY} - {G1_STOCK_REFERENCE}",
            "k": 20,
            "added": [],
            "removed": [],
            "order_only": False,
            "h20_close_diff": 0.0,
            "h20_added_close_full": None,
            "h20_removed_close_full": None,
            "h20_close_coverage_left": 1.0,
            "h20_mae_left": -0.02,
            "h20_mfe_left": 0.03,
        },
        {
            "session_date": "2023-01-03",
            "layer": "discovery",
            "comparison": f"{G2_EXTENSION_DISCOVERY} - {G1_STOCK_REFERENCE}",
            "k": 20,
            "added": ["BBB"],
            "removed": ["AAA"],
            "order_only": False,
            "h20_close_diff": 0.01,
            "h20_added_close_full": 0.2,
            "h20_removed_close_full": -0.05,
            "h20_close_coverage_left": 1.0,
            "h20_mae_left": -0.01,
            "h20_mfe_left": 0.04,
        },
    ]
    summary = aggregate_discovery_records(rows, reps=4)
    key = f"discovery:{G2_EXTENSION_DISCOVERY} - {G1_STOCK_REFERENCE}:k20"
    group = summary[key]
    assert group["substitution"]["added_days"] == 1
    assert group["substitution"]["unique_added"] == ["BBB"]
    assert group["substitution"]["unique_removed"] == ["AAA"]
    added = group["horizons"]["20"]["added_names_close"]
    assert added["conditional_mean"] == pytest.approx(0.2)
    assert added["n_substitution_days"] == 1
    yearly = group["horizons"]["20"]["yearly_close_paired_mean"]
    assert yearly["2022"]["mean"] == pytest.approx(0.0)
    assert yearly["2023"]["mean"] == pytest.approx(0.01)


def test_old_technical_comparison_does_not_treat_discovery_count_as_top20():
    payload = _frozen(["AAA", "BBB"], ["AAA"])
    old = {
        "variants": {
            variant: {"discovery_n": 1, "top": {"20": ["AAA"]}}
            for variant in (B0_CURRENT, G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)
        }
    }
    compared = compare_frozen_to_old_session(payload, old)
    assert compared["technical"] == []
    assert compared["discovery_count"][0]["variant"] == G2_EXTENSION_DISCOVERY
    assert compared["discovery_count"][0]["new"] == 2
