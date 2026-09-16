from datetime import date

import pytest

from app.services.research_eod_v1.composite import (
    classify_regime,
    m1_consensus,
    m2_utility,
    m3_diversified,
    m4_regime,
    timeframe_all_ok,
)


def _row(sid: str, algo: str, score: float, industry: str = "tech") -> dict:
    return {
        "security_id": sid,
        "algorithm_id": algo,
        "score": score,
        "status": "eligible",
        "primary_industry_id": industry,
        "factors": {"R": 70.0, "G": 60.0},
        "R": 70.0,
        "G": 60.0,
    }


def test_m1_requires_two_families_and_does_not_revive_rejects() -> None:
    rows = [
        _row("AAA", "A_trend_quality", 90),
        _row("AAA", "D_residual_momentum", 88),
        {**_row("BBB", "A_trend_quality", 95), "status": "rejected"},
        _row("CCC", "A_trend_quality", 91),
    ]
    out = m1_consensus(rows, "balanced", 20)
    ids = {row["security_id"] for row in out}
    assert "AAA" in ids
    assert "BBB" not in ids
    assert "CCC" not in ids


def test_m2_without_matured_fold_out_labels_is_empty() -> None:
    assert m2_utility([_row("AAA", "A_trend_quality", 90)], "balanced", 10) == []
    with pytest.raises((ValueError, TypeError)):
        m2_utility(
            [_row("AAA", "A_trend_quality", 90)],
            "balanced",
            10,
            matured_returns={"AAA": {"returns": [0.02] * 100, "label_matured_at": date(2099, 1, 1)}},
        )


def test_m3_stops_on_missing_correlation() -> None:
    rows = [_row("AAA", "A_trend_quality", 90), _row("BBB", "A_trend_quality", 89)]
    # One name can enter an empty book; the second needs correlations.
    out = m3_diversified(rows, "balanced", 5, corr=None)
    assert len(out) == 1


def test_m4_unknown_regime_and_defense_gates() -> None:
    assert classify_regime(spy_close=None, spy_sma200=1, sma200_slope20=1, breadth50=0.6) == "REGIME_UNKNOWN"
    rows = [_row("AAA", "D_residual_momentum", 100)]
    assert m4_regime(rows, "conservative", 10, "defense") == []
    assert m4_regime(rows, "aggressive", 10, "defense")[0]["security_id"] == "AAA"


def test_timeframe_all_is_not_half_mid_half_long() -> None:
    ok, reasons = timeframe_all_ok(
        t_score=80,
        long_momentum_raw=0.01,
        long_structure_invalid=False,
        unresolved_upthrust=True,
        extension_atr=1.0,
        max_extension=2.0,
        below_invalidation=False,
    )
    assert ok is False
    assert "SHORT_UPTHRUST" in reasons
    ok, _ = timeframe_all_ok(
        t_score=80,
        long_momentum_raw=0.01,
        long_structure_invalid=False,
        unresolved_upthrust=False,
        extension_atr=1.0,
        max_extension=2.0,
        below_invalidation=False,
    )
    assert ok is True
