from __future__ import annotations

import math

from app.api.strength import (
    DEFAULT_STRENGTH_SCAN_PARAMETERS,
    normalize_strength_scan_parameters,
    strength_scan_parameters_hash,
)
from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM
from app.services.strength.ranking_variants import (
    a0_family_score,
    a0_request_can_score,
    apply_a0_mid_long,
)


def _row(ticker: str, *, mid, long, ranking=80.0) -> dict:
    return {
        "ticker": ticker,
        "score_mid": mid,
        "score_long": long,
        "ranking_score": ranking,
        "final_score": ranking,
        "strength_score": ranking,
    }


def test_a0_formula_is_equal_weight_mid_long() -> None:
    assert a0_family_score(_row("AAA", mid=80, long=60)) == 70.0
    assert a0_family_score(_row("BBB", mid=0, long=0)) == 0.0


def test_zero_is_valid_and_nan_is_not_coerced() -> None:
    assert a0_family_score(_row("ZERO", mid=0, long=10)) == 5.0
    assert a0_family_score(_row("NONE", mid=None, long=90)) is None
    assert a0_family_score(_row("NAN", mid=float("nan"), long=90)) is None
    assert a0_family_score(_row("INF", mid=float("inf"), long=90)) is None


def test_full_pool_is_sorted_before_truncation() -> None:
    rows = [
        _row("LOW", mid=10, long=10, ranking=99),
        _row("MID", mid=40, long=40, ranking=50),
        _row("HIGH", mid=90, long=90, ranking=1),
        _row("GAP", mid=None, long=90, ranking=100),
    ]
    ranked = apply_a0_mid_long(rows)
    assert [item["ticker"] for item in ranked] == ["HIGH", "MID", "LOW", "GAP"]
    assert ranked[0]["sort_score"] == 90.0
    assert ranked[-1]["sort_score"] is None
    assert ranked[-1]["selected_view_rank"] == 4
    original = {row["ticker"]: row["ranking_score"] for row in rows}
    assert all(item["ranking_score"] == original[item["ticker"]] for item in ranked)
    truncated = ranked[:2]
    assert [item["ticker"] for item in truncated] == ["HIGH", "MID"]


def test_input_order_does_not_change_a0_ranks() -> None:
    rows = [
        _row("B", mid=50, long=50, ranking=10),
        _row("A", mid=50, long=50, ranking=90),
        _row("C", mid=70, long=70, ranking=1),
    ]
    first = [item["ticker"] for item in apply_a0_mid_long(rows)]
    second = [item["ticker"] for item in apply_a0_mid_long(list(reversed(rows)))]
    assert first == second == ["C", "B", "A"]


def test_partial_missing_rows_do_not_force_request_fallback() -> None:
    rows = [_row("OK", mid=80, long=70), _row("GAP", mid=None, long=None)]
    assert a0_request_can_score(rows) is True
    assert a0_request_can_score([_row("GAP", mid=None, long=math.nan)]) is False


def test_production_parameter_hash_stays_on_the_default_identity() -> None:
    production = normalize_strength_scan_parameters(dict(DEFAULT_STRENGTH_SCAN_PARAMETERS))
    with_explicit = normalize_strength_scan_parameters(
        {**DEFAULT_STRENGTH_SCAN_PARAMETERS, "ranking_algorithm": PRODUCTION_ALGORITHM}
    )
    a0 = normalize_strength_scan_parameters(
        {**DEFAULT_STRENGTH_SCAN_PARAMETERS, "ranking_algorithm": A0_ALGORITHM}
    )
    assert production == dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
    assert with_explicit == production
    assert "ranking_algorithm" not in production
    assert a0["ranking_algorithm"] == A0_ALGORITHM
    assert strength_scan_parameters_hash(production) == strength_scan_parameters_hash(
        dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
    )
    assert strength_scan_parameters_hash(a0) != strength_scan_parameters_hash(production)
