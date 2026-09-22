from datetime import datetime, timezone

import pytest

from app.services.strength.market_regime import (
    _compute_breadth_score,
    _compute_momentum_score,
    _compute_risk_appetite_score,
    _relative_return,
    _ret,
    compute_market_regime,
)
from market_regime_support import history


@pytest.mark.parametrize("missing_side", ["left", "right"])
def test_market_relative_return_uses_matching_dates_with_interior_gaps(missing_side):
    left = history()["Close"]
    right = left.copy()
    if missing_side == "left":
        left = left.drop(left.index[-2])
    else:
        right = right.drop(right.index[-2])
    assert _relative_return(left, right, 20) == 0.0


@pytest.mark.parametrize("endpoint", ["start", "end"])
def test_market_relative_return_requires_benchmark_endpoints(endpoint):
    left = history()["Close"]
    right = left.drop(left.index[-21] if endpoint == "start" else left.index[-1])
    assert _relative_return(left, right, 20) is None


def test_market_relative_return_ignores_newer_benchmark_bars():
    right = history()["Close"]
    left = right.iloc[:-1].copy()
    right.iloc[-1] *= 2
    assert _relative_return(left, right, 20) == 0.0


def test_market_relative_return_preserves_rounding_and_session_date_matching():
    left = history(start=137.12, step=.2137)["Close"]
    right = history(start=102.37, step=.3543)["Close"]
    expected = round(_ret(left, 20) - _ret(right, 20), 5)
    right.index = right.index.tz_localize("America/New_York").tz_convert("UTC")
    assert _relative_return(left, right, 20) == expected


def test_market_scores_keep_neutral_relative_evidence_when_benchmark_has_gap():
    close = history()["Close"]
    closes = {symbol: close.copy() for symbol in ("SPY", "QQQ", "RSP", "IWM", "HYG", "IEF", "TLT")}
    closes["SPY"] = close.drop(close.index[-2])
    closes["TLT"] = close.drop(close.index[-2])
    momentum_score, momentum = _compute_momentum_score(closes)
    breadth_score, breadth = _compute_breadth_score(closes)
    risk_score, _, risk, _ = _compute_risk_appetite_score(closes)
    assert [momentum[name] for name in ("qqq_spy_20d", "iwm_spy_20d", "rsp_spy_20d")] == [0.0] * 3
    assert breadth["rsp_spy_20d"] == breadth["iwm_spy_20d"] == 0.0
    assert [risk[name] for name in ("hyg_tlt_20d", "hyg_ief_20d", "ief_tlt_20d")] == [0.0] * 3
    assert momentum_score is not None and breadth_score is not None and risk_score is not None
    assert risk["component_scores"]["credit_hyg_tlt"] == 50.0
    assert risk["component_scores"]["credit_hyg_ief"] == 50.0


def test_missing_breadth_endpoints_make_market_score_unavailable():
    frame = history()
    data = {symbol: frame.copy() for symbol in ("SPY", "QQQ", "RSP", "IWM")}
    data["SPY"] = frame.drop(frame.index[-21])
    result = compute_market_regime(data, as_of=datetime(2026, 1, 30, 22, tzinfo=timezone.utc))
    assert result["breadth"]["rsp_spy_20d"] is None
    assert result["breadth"]["iwm_spy_20d"] is None
    assert "rsp_or_iwm_breadth" in result["hard_missing"]
    assert result["status"] == "insufficient_data"
    assert result["score"] is None
