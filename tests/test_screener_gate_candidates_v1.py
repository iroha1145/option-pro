from __future__ import annotations

import sys
from copy import deepcopy
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
    MIN_ADV20_USD,
    MIN_HISTORY_SESSIONS,
    MIN_RAW_PRICE_USD,
    MIN_REFERENCE_N,
    VOLUME_SESSION_UNVERIFIED,
    Decision,
    Facts,
    InsufficientReference,
    ReferenceError,
    ReferenceInput,
    attach_research,
    build_reference,
    evaluate,
    frozen_experiment_constants,
    high_atr_hit,
    stock_rank,
    upper_median,
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
        atr_pct=2.0 + (i % 10) * 0.05,
        history_sessions=300,
        currently_tradable=True,
        halted=False,
        zero_volume=False,
        complete_bar=True,
        venue_eligible=True,
    )
    values.update(kwargs)
    return ReferenceInput(**values)


def pool(n: int = 30, **kwargs) -> list[ReferenceInput]:
    return [member(i, **kwargs) if not kwargs else member(i, **{k: v for k, v in kwargs.items()}) for i in range(n)]


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
        "configured_weights": {"T": 0.24, "M": 0.25},
        "status": "rejected",
        "rejection_reasons": ["HIGH_ATR"],
        "gate_results": {"common": ("HIGH_ATR",), "setup": ()},
    }
    values.update(kwargs)
    return values


def decide(variant: str, *, src=None, ref=None, old=3.0, **fact_kw) -> Decision:
    return evaluate(
        variant=variant,
        row=src if src is not None else row(),
        facts=facts(**fact_kw),
        reference=ref if ref is not None else build_reference(pool(30)),
        old_reference_median=old,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )


def test_etfs_excluded_from_reference():
    stocks = pool(30)
    etfs = [member(100 + i, security_id=f"E{i:03d}", asset_track="etf", atr_pct=0.2) for i in range(20)]
    built = build_reference([*stocks, *etfs])
    assert all(not sid.startswith("E") for sid in built.member_ids)
    assert built.n == 30


def test_many_low_vol_etfs_do_not_change_stock_median():
    stocks = pool(30)
    baseline = build_reference(stocks).median_atr_pct
    etfs = [member(200 + i, security_id=f"ETF{i:03d}", asset_track="etf", atr_pct=0.05) for i in range(80)]
    mixed = build_reference([*etfs, *stocks])
    assert mixed.median_atr_pct == baseline


def test_low_liquidity_stocks_do_not_change_median():
    stocks = pool(30)
    baseline = build_reference(stocks).median_atr_pct
    thin = [member(300 + i, security_id=f"T{i:03d}", adv20=1_000_000.0, atr_pct=20.0) for i in range(40)]
    assert build_reference([*thin, *stocks]).median_atr_pct == baseline


def test_price_below_five_excluded():
    stocks = pool(30)
    cheap = member(400, security_id="CHEAP", raw_close=4.99, atr_pct=0.1)
    built = build_reference([*stocks, cheap])
    assert "CHEAP" not in built.member_ids


def test_adv_below_20m_excluded():
    stocks = pool(30)
    thin = member(401, security_id="THIN", adv20=MIN_ADV20_USD - 1, atr_pct=0.1)
    assert "THIN" not in build_reference([*stocks, thin]).member_ids


def test_history_below_252_excluded():
    stocks = pool(30)
    young = member(402, security_id="YOUNG", history_sessions=MIN_HISTORY_SESSIONS - 1)
    assert "YOUNG" not in build_reference([*stocks, young]).member_ids


def test_incomplete_bar_excluded():
    stocks = pool(30)
    partial = member(403, security_id="PART", complete_bar=False)
    assert "PART" not in build_reference([*stocks, partial]).member_ids


def test_not_tradable_excluded():
    stocks = pool(30)
    halted_like = member(404, security_id="NT", currently_tradable=False)
    assert "NT" not in build_reference([*stocks, halted_like]).member_ids


def test_venue_ineligible_excluded():
    stocks = pool(30)
    otc = member(405, security_id="OTC", venue_eligible=False)
    assert "OTC" not in build_reference([*stocks, otc]).member_ids


def test_halted_and_zero_volume_excluded():
    stocks = pool(30)
    halted = member(406, security_id="HLT", halted=True, currently_tradable=False)
    zero = member(407, security_id="ZVOL", zero_volume=True, currently_tradable=False)
    built = build_reference([*stocks, halted, zero])
    assert "HLT" not in built.member_ids
    assert "ZVOL" not in built.member_ids


def test_reference_order_independent():
    stocks = pool(36)
    forward = build_reference(stocks)
    backward = build_reference(list(reversed(stocks)))
    assert forward.median_atr_pct == backward.median_atr_pct
    assert forward.member_ids == backward.member_ids


def test_duplicate_ids_fail():
    stocks = pool(30)
    stocks.append(member(0, atr_pct=9.0))
    with pytest.raises(ReferenceError, match="duplicate"):
        build_reference(stocks)


def test_mixed_dates_fail():
    stocks = pool(30)
    stocks[3] = member(3, session_date="2024-01-03")
    with pytest.raises(ReferenceError, match="mixes session dates"):
        build_reference(stocks)


def test_insufficient_reference_fails():
    with pytest.raises(InsufficientReference):
        build_reference(pool(MIN_REFERENCE_N - 1))


def test_exactly_thirty_succeeds():
    built = build_reference(pool(MIN_REFERENCE_N))
    assert built.n == MIN_REFERENCE_N


def test_upper_median_is_sorted_n_over_2():
    values = [1.0, 2.0, 3.0, 9.0]
    assert upper_median(values) == sorted(values)[len(values) // 2]
    built = build_reference(pool(30))
    assert built.median_atr_pct == sorted(built.atr_pcts)[built.n // 2]


def test_absolute_cap_preserved():
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    assert high_atr_hit(8.01, median_atr_pct=ref.median_atr_pct, absolute_atr_cap=8.0, reference_multiplier=10.0)
    assert not high_atr_hit(8.0, median_atr_pct=ref.median_atr_pct, absolute_atr_cap=8.0, reference_multiplier=10.0)


def test_multiplier_preserved():
    ref = build_reference([member(i, atr_pct=2.0) for i in range(30)])
    assert ref.actual_cap(12.0, 1.75) == 2.0 * 1.75
    assert high_atr_hit(3.51, median_atr_pct=2.0, absolute_atr_cap=12.0, reference_multiplier=1.75)
    assert not high_atr_hit(3.50, median_atr_pct=2.0, absolute_atr_cap=12.0, reference_multiplier=1.75)


def test_g1_can_add_candidates():
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    src = row(rejection_reasons=["HIGH_ATR"], gate_results={"common": ("HIGH_ATR",), "setup": ()})
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, old=1.0, atr_pct=4.0)
    b0 = decide(B0_CURRENT, src=src, ref=ref, old=1.0, atr_pct=4.0)
    assert b0.high_atr is True
    assert g1.high_atr is False
    assert g1.technical_entry_passed is True


def test_g1_can_remove_candidates():
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    src = row(rejection_reasons=[], status="eligible", gate_results={"common": (), "setup": ()})
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, old=6.0, atr_pct=4.0)
    b0 = decide(B0_CURRENT, src=src, ref=ref, old=6.0, atr_pct=4.0)
    assert b0.high_atr is False
    assert g1.high_atr is True
    assert g1.technical_entry_passed is False


def test_g1_does_not_change_score_or_weights():
    src = row()
    original_score = src["score"]
    original_weights = deepcopy(src["configured_weights"])
    out = attach_research(src, decide(G1_STOCK_REFERENCE, src=src))
    assert out["score"] == original_score
    assert out["configured_weights"] == original_weights


def test_too_far_from_base_preserved():
    src = row(rejection_reasons=["HIGH_ATR", "TOO_FAR_FROM_BASE"])
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, old=1.0, atr_pct=4.0)
    assert "TOO_FAR_FROM_BASE" in g1.research_reasons
    assert g1.technical_entry_passed is False


def test_low_event_rvol_preserved():
    src = row(
        rejection_reasons=["LOW_EVENT_RVOL"],
        status="rejected",
        gate_results={"common": (), "setup": ("LOW_EVENT_RVOL",)},
    )
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, old=1.0, atr_pct=4.0)
    assert "LOW_EVENT_RVOL" in g1.research_reasons


def test_atr_pct_four_is_percent_not_fraction():
    assert high_atr_hit(4.0, median_atr_pct=2.0, absolute_atr_cap=12.0, reference_multiplier=1.75)
    assert not high_atr_hit(0.04, median_atr_pct=2.0, absolute_atr_cap=12.0, reference_multiplier=1.75)


def test_g1_does_not_fill_with_etf_or_self_atr():
    stocks = pool(10)
    etfs = [member(i + 50, security_id=f"E{i:03d}", asset_track="etf", atr_pct=0.3) for i in range(40)]
    with pytest.raises(InsufficientReference):
        build_reference([*stocks, *etfs])


def test_b0_reproduces_industry_median_high_atr():
    """B0 keeps the upstream HIGH_ATR decision. Recompute is diagnostic only."""

    from screener_gate_candidates_v1 import b0_recompute_diagnostic

    src = row(
        rejection_reasons=["HIGH_ATR"],
        status="rejected",
        gate_results={"common": ("HIGH_ATR",), "setup": ()},
    )
    b0 = decide(B0_CURRENT, src=src, old=1.5, atr_pct=4.0)
    assert b0.high_atr is True
    diagnostic = b0_recompute_diagnostic(
        src,
        facts(atr_pct=4.0),
        old_reference_median=1.5,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert diagnostic["agrees"] is True
    assert diagnostic["recomputed_high_atr"] is True
    assert b0.high_atr is diagnostic["upstream_high_atr"]


def test_frozen_constants():
    spec = frozen_experiment_constants()
    assert spec["min_raw_price_usd"] == MIN_RAW_PRICE_USD == 5.0
    assert spec["min_adv20_usd"] == 20_000_000.0
    assert spec["min_history_sessions"] == 252
    assert spec["min_reference_n"] == 30
    assert spec["weights_changed"] is False
    assert spec["etf_algorithm_changed"] is False


def test_g2_discovery_passes_extended():
    src = row(rejection_reasons=["EXTENDED"], gate_results={"common": ("EXTENDED",), "setup": ()})
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g2 = decide(G2_EXTENSION_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g2.discovery_passed is True
    assert EXTENDED in g2.risk_hints


def test_g2_technical_entry_rejects_extended():
    src = row(rejection_reasons=["EXTENDED"], gate_results={"common": ("EXTENDED",), "setup": ()})
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g2 = decide(G2_EXTENSION_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g2.technical_entry_passed is False
    assert g2.qualified_entry_passed is False


def test_g3_discovery_passes_high_atr():
    src = row(rejection_reasons=["HIGH_ATR"], gate_results={"common": ("HIGH_ATR",), "setup": ()})
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g3 = decide(G3_RISK_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g3.high_atr is True
    assert g3.discovery_passed is True
    assert HIGH_ATR in g3.risk_hints


def test_g3_technical_entry_rejects_high_atr():
    src = row(rejection_reasons=["HIGH_ATR"])
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g3 = decide(G3_RISK_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g3.technical_entry_passed is False


def test_g1_g2_g3_technical_entry_identical():
    src = row(rejection_reasons=["HIGH_ATR", "EXTENDED"], gate_results={"common": ("HIGH_ATR", "EXTENDED"), "setup": ()})
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    decisions = [decide(variant, src=src, ref=ref, atr_pct=4.0) for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)]
    assert {item.technical_entry_passed for item in decisions} == {False}
    src2 = row(rejection_reasons=[], status="eligible", gate_results={"common": (), "setup": ()})
    ref2 = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    ok = [decide(variant, src=src2, ref=ref2, atr_pct=4.0) for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)]
    assert {item.technical_entry_passed for item in ok} == {True}


def test_g1_g2_g3_qualified_entry_identical():
    src = row(rejection_reasons=["DOLLAR_LIQUIDITY_UNVERIFIED"], status="watch", gate_results={"common": (), "setup": ()})
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    decisions = [decide(variant, src=src, ref=ref, atr_pct=4.0) for variant in (G1_STOCK_REFERENCE, G2_EXTENSION_DISCOVERY, G3_RISK_DISCOVERY)]
    assert {item.qualified_entry_passed for item in decisions} == {False}
    assert {item.technical_entry_passed for item in decisions} == {True}


def test_g2_discovery_still_rejects_high_atr():
    src = row(rejection_reasons=["HIGH_ATR"])
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g2 = decide(G2_EXTENSION_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g2.discovery_passed is False


def test_g1_discovery_rejects_extended():
    src = row(rejection_reasons=["EXTENDED"], gate_results={"common": ("EXTENDED",), "setup": ()})
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, atr_pct=4.0)
    assert g1.discovery_passed is False


def test_g3_extended_and_high_atr_are_hints():
    src = row(rejection_reasons=["HIGH_ATR", "EXTENDED"], gate_results={"common": ("HIGH_ATR", "EXTENDED"), "setup": ()})
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g3 = decide(G3_RISK_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g3.discovery_passed is True
    assert set(g3.risk_hints) == {HIGH_ATR, EXTENDED}
    assert g3.technical_entry_passed is False


def test_risk_hints_do_not_erase_other_rejects():
    src = row(rejection_reasons=["HIGH_ATR", "EXTENDED", "WEAK_STRUCTURE"])
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g3 = decide(G3_RISK_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert "WEAK_STRUCTURE" in g3.research_reasons
    assert g3.discovery_passed is False


def test_dollar_liquidity_unverified_not_promoted():
    src = row(rejection_reasons=["DOLLAR_LIQUIDITY_UNVERIFIED"], status="watch", gate_results={"common": (), "setup": ()})
    g1 = decide(G1_STOCK_REFERENCE, src=src, atr_pct=4.0)
    assert DOLLAR_LIQUIDITY_UNVERIFIED in g1.research_reasons
    assert g1.qualified_entry_passed is False


def test_volume_session_unverified_not_promoted():
    src = row(
        algorithm_id="B_confirmed_base_breakout",
        rejection_reasons=["VOLUME_SESSION_UNVERIFIED"],
        status="watch",
        gate_results={"common": (), "setup": ()},
    )
    g1 = decide(G1_STOCK_REFERENCE, src=src, atr_pct=4.0)
    assert VOLUME_SESSION_UNVERIFIED in g1.research_reasons
    assert g1.qualified_entry_passed is False


def test_unverified_allows_technical_not_qualified():
    src = row(
        rejection_reasons=["DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED"],
        algorithm_id="C_trend_pullback",
        status="watch",
        gate_results={"common": (), "setup": ()},
    )
    ref = build_reference([member(i, atr_pct=3.0) for i in range(30)])
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, atr_pct=4.0)
    assert g1.technical_entry_passed is True
    assert g1.qualified_entry_passed is False


def test_structural_rejects_not_erased():
    src = row(rejection_reasons=["SETUP_NOT_MET", "INVALIDATED", "UNRESOLVED_UPTHRUST"])
    g3 = decide(G3_RISK_DISCOVERY, src=src, atr_pct=4.0)
    for reason in ("SETUP_NOT_MET", "INVALIDATED", "UNRESOLVED_UPTHRUST"):
        assert reason in g3.research_reasons
    assert g3.discovery_passed is False


def test_halted_does_not_pass():
    src = row(rejection_reasons=[], status="eligible", gate_results={"common": (), "setup": ()})
    g1 = decide(G1_STOCK_REFERENCE, src=src, atr_pct=4.0, halted=True, currently_tradable=False)
    assert "HALTED_SESSION" in g1.research_reasons
    assert g1.discovery_passed is False


def test_baseline_row_not_mutated():
    src = row()
    snapshot = deepcopy(src)
    decide(G3_RISK_DISCOVERY, src=src, atr_pct=4.0)
    assert src == snapshot


def test_research_fields_only():
    src = row(status="rejected", score=81.0)
    out = attach_research(src, decide(G2_EXTENSION_DISCOVERY, src=src))
    assert out["status"] == "rejected"
    assert out["score"] == 81.0
    assert out["rejection_reasons"] == src["rejection_reasons"]
    assert "research_gate" in out


def test_etf_row_does_not_use_stock_reference():
    src = row(
        security_id="SPY",
        stock_or_etf_track="etf",
        status="eligible",
        rejection_reasons=[],
        gate_results={"common": (), "setup": ()},
    )
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    etf_facts = facts(security_id="SPY", asset_track="etf", atr_pct=4.0)
    g1 = evaluate(
        variant=G1_STOCK_REFERENCE,
        row=src,
        facts=etf_facts,
        reference=ref,
        old_reference_median=6.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert g1.high_atr is False
    assert g1.new_reference_median == ref.median_atr_pct


def test_facts_identity_must_match_row():
    with pytest.raises(ValueError, match="security_id"):
        evaluate(
            variant=B0_CURRENT,
            row=row(),
            facts=facts(security_id="OTHER"),
            reference=build_reference(pool(30)),
            old_reference_median=2.0,
            absolute_atr_cap=CAP,
            reference_multiplier=MULT,
        )


def test_venue_eligible_not_default_true():
    stocks = [member(i, venue_eligible=False) for i in range(30)]
    with pytest.raises(InsufficientReference):
        build_reference(stocks)


def test_complete_bar_required_for_reference():
    stocks = [member(i, complete_bar=False) for i in range(30)]
    with pytest.raises(InsufficientReference):
        build_reference(stocks)


def test_reference_is_not_topk_or_theme():
    low_score = [member(i, security_id=f"L{i:03d}", atr_pct=2.0) for i in range(30)]
    high_score_theme = [member(i + 80, security_id=f"H{i:03d}", atr_pct=8.0) for i in range(5)]
    built = build_reference([*low_score, *high_score_theme])
    assert built.n == 35
    assert built.median_atr_pct == sorted(built.atr_pcts)[built.n // 2]


def test_stock_rank_keeps_best_family_theme_score():
    rows = [
        {"security_id": "AAA", "score": 70.0, "algorithm_id": "A_trend_quality", "sector_context": "ai_cloud", "stock_or_etf_track": "stock"},
        {"security_id": "AAA", "score": 88.0, "algorithm_id": "C_trend_pullback", "sector_context": "semiconductors", "stock_or_etf_track": "stock"},
        {"security_id": "BBB", "score": 80.0, "algorithm_id": "A_trend_quality", "sector_context": "finance", "stock_or_etf_track": "stock"},
    ]
    ranked = stock_rank(rows)
    assert [item["security_id"] for item in ranked] == ["AAA", "BBB"]
    assert ranked[0]["score"] == 88.0
    assert ranked[0]["algorithm_id"] == "C_trend_pullback"


def test_stock_rank_order_independent():
    rows = [
        {"security_id": "AAA", "score": 70.0, "algorithm_id": "A_trend_quality", "sector_context": "a", "stock_or_etf_track": "stock"},
        {"security_id": "AAA", "score": 90.0, "algorithm_id": "B_confirmed_base_breakout", "sector_context": "b", "stock_or_etf_track": "stock"},
    ]
    assert stock_rank(rows)[0]["score"] == stock_rank(list(reversed(rows)))[0]["score"] == 90.0


def test_stock_rank_separates_etf():
    rows = [
        {"security_id": "AAA", "score": 70.0, "algorithm_id": "A_trend_quality", "sector_context": "x", "stock_or_etf_track": "stock"},
        {"security_id": "SPY", "score": 99.0, "algorithm_id": "A_trend_quality", "sector_context": "etfs", "stock_or_etf_track": "etf"},
    ]
    assert [item["security_id"] for item in stock_rank(rows, track="stock")] == ["AAA"]
    assert [item["security_id"] for item in stock_rank(rows, track="etf")] == ["SPY"]


def test_stock_rank_ignores_consensus_z():
    rows = [
        {"security_id": "AAA", "score": 70.0, "consensus_z": 99.0, "algorithm_id": "A_trend_quality", "sector_context": "x", "stock_or_etf_track": "stock"},
        {"security_id": "BBB", "score": 80.0, "consensus_z": 10.0, "algorithm_id": "A_trend_quality", "sector_context": "x", "stock_or_etf_track": "stock"},
    ]
    ranked = stock_rank(rows)
    assert ranked[0]["security_id"] == "BBB"
    assert stock_rank(rows, score_key="consensus_z") == []


def test_insufficient_reference_evaluate_does_not_relax():
    src = row(rejection_reasons=[], status="eligible", gate_results={"common": (), "setup": ()})
    g1 = evaluate(
        variant=G1_STOCK_REFERENCE,
        row=src,
        facts=facts(atr_pct=4.0),
        reference=None,
        old_reference_median=6.0,
        absolute_atr_cap=CAP,
        reference_multiplier=MULT,
    )
    assert g1.reference_limited is True
    assert g1.discovery_passed is False
    assert g1.technical_entry_passed is False


def test_attach_research_preserves_status_and_score():
    src = row(status="watch", score=77.5, rejection_reasons=["DOLLAR_LIQUIDITY_UNVERIFIED"])
    out = attach_research(src, decide(G1_STOCK_REFERENCE, src=src, atr_pct=4.0))
    assert out["status"] == "watch"
    assert out["score"] == 77.5
    assert src["status"] == "watch"


def test_g3_cannot_pass_strict_entry_when_g1_fails():
    src = row(rejection_reasons=["HIGH_ATR", "EXTENDED"])
    ref = build_reference([member(i, atr_pct=1.0) for i in range(30)])
    g1 = decide(G1_STOCK_REFERENCE, src=src, ref=ref, atr_pct=4.0)
    g3 = decide(G3_RISK_DISCOVERY, src=src, ref=ref, atr_pct=4.0)
    assert g1.technical_entry_passed is False
    assert g3.technical_entry_passed is False
    assert g3.qualified_entry_passed is False
