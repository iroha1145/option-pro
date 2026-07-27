from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from app.services import scoring, signals
from app.services.strength import scanner
from app.services.strength.scoring import rsi_score


@pytest.mark.parametrize(
    ("value", "expected"),
    [(0, 0), (35, 42), (50, 58), (68, 88), (78, 66), (100, 33)],
)
def test_rsi_score_hits_declared_continuous_knots(value: float, expected: float) -> None:
    assert rsi_score(value) == expected


@pytest.mark.parametrize("boundary", [50.0, 68.0, 78.0])
def test_rsi_score_is_continuous_around_boundaries(boundary: float) -> None:
    left = rsi_score(boundary - 0.0001)
    exact = rsi_score(boundary)
    right = rsi_score(boundary + 0.0001)
    assert left is not None and exact is not None and right is not None
    assert abs(left - exact) < 0.001
    assert abs(right - exact) < 0.001


def test_obv_divergence_is_bounded_and_independent_of_earlier_history() -> None:
    close = pd.Series(np.linspace(1.0, 3.0, 21))
    volume = pd.Series([1.0] * 21)
    prefix = pd.Series(np.linspace(0.5, 0.99, 100))

    window_only = signals.compute_obv_divergence(close, volume)
    with_prefix = signals.compute_obv_divergence(
        pd.concat([prefix, close], ignore_index=True),
        pd.Series([1.0] * 121),
    )

    assert window_only == with_prefix
    assert window_only is not None
    assert -100 <= window_only <= 100


def test_obv_divergence_normalizes_over_directional_window_volume() -> None:
    close = pd.Series(np.linspace(100.0, 120.0, 21))
    volume = pd.Series([100.0] * 21)

    # Every directional bar is positive: signed-volume ratio is 1.0, while
    # price return is 0.2, so the bounded divergence is (0.2 - 1.0) * 100.
    assert signals.compute_obv_divergence(close, volume) == -80.0


def test_obv_divergence_requires_usable_volume() -> None:
    close = pd.Series(np.linspace(10.0, 12.0, 21))
    assert signals.compute_obv_divergence(close, pd.Series([0.0] * 21)) is None
    volume = pd.Series([100.0] * 21)
    volume.iloc[-1] = float("nan")
    assert signals.compute_obv_divergence(close, volume) is None


def test_obv_divergence_does_not_backfill_an_invalid_latest_bar() -> None:
    close = pd.Series(np.linspace(10.0, 20.0, 80))
    volume = pd.Series([100.0] * 80)
    volume.iloc[-1] = float("nan")

    assert signals.compute_obv_divergence(close, volume) is None


def _flat_history(size: int = 20) -> pd.DataFrame:
    close = pd.Series([100.0] * size)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close,
            "Low": close,
            "Close": close,
            "Volume": pd.Series([1_000_000.0] * size),
        }
    )


def test_flat_range_and_zero_volume_variance_stay_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    history = _flat_history()
    monkeypatch.setattr(signals, "_history", lambda _symbol, period="1y": history)
    monkeypatch.setattr("app.services.yahoo.get_stock_iv", lambda _symbol: None)
    signals._cache.clear()

    payload = signals.compute_stock_signals("FLAT")

    assert payload["volume_zscore"]["value"] is None
    assert payload["volume_zscore"]["top_score"] is None
    assert payload["volume_zscore"]["bottom_score"] is None
    assert payload["close_position"]["value"] is None
    assert payload["close_position"]["top_score"] is None
    assert payload["close_position"]["bottom_score"] is None
    assert payload["_volume_ratio"]["value"] == 1.0


def test_missing_signal_value_has_missing_scores() -> None:
    payload = signals._with_score(
        "rsi14",
        None,
        "RSI(14)",
        signals._score_stock_signal,
    )
    assert payload == {
        "value": None,
        "label": "RSI(14)",
        "top_score": None,
        "bottom_score": None,
    }


def test_display_only_atm_iv_does_not_receive_a_neutral_score() -> None:
    payload = signals._with_score(
        "atm_iv_percent",
        32.5,
        "当前ATM IV%",
        signals._score_stock_signal,
    )

    assert payload["value"] == 32.5
    assert payload["top_score"] is None
    assert payload["bottom_score"] is None


def test_empty_stock_scores_are_insufficient_instead_of_neutral() -> None:
    result = scoring.compute_stock_scores({})

    assert result["top_score"] is None
    assert result["bottom_score"] is None
    assert result["dip_buy_quality"] is None
    assert result["top_status"] == "insufficient_data"
    assert result["bottom_status"] == "insufficient_data"
    assert result["dip_buy_status"] == "insufficient_data"
    assert result["top_active_weight"] == 0
    assert result["bottom_active_weight"] == 0
    assert result["data_quality"] == 0


def test_nonfinite_scores_are_treated_as_missing() -> None:
    result = scoring.compute_stock_scores(
        {
            "rsi14": {
                "value": float("nan"),
                "top_score": float("nan"),
                "bottom_score": float("inf"),
                "label": "RSI",
            }
        }
    )

    assert result["top_score"] is None
    assert result["bottom_score"] is None
    assert result["data_quality"] == 0


def test_sparse_market_scores_are_insufficient_instead_of_overconfident() -> None:
    result = scoring.compute_market_scores(
        {"credit_risk": _scored_signal()}
    )

    assert result["top_score"] is None
    assert result["bottom_score"] is None
    assert result["top_status"] == "insufficient_data"
    assert result["bottom_status"] == "insufficient_data"
    assert result["coverage"]["top_active_weight"] == 0.1
    assert result["coverage"]["bottom_active_weight"] == 0.1


def test_dip_uses_only_available_moving_average_distance() -> None:
    result = scoring._compute_dip_buy_quality(
        {"sma50_dist": {"value": 10.0}}
    )

    assert result["breakdown"]["pullback_to_key_level"] == 0.0
    assert result["breakdown"]["market_environment_stable"] is None
    assert result["active_weight"] == 0.45
    assert "market_environment_stable" in result["missing_components"]


def _scored_signal(value: float = 1.0) -> dict:
    return {
        "value": value,
        "top_score": 20,
        "bottom_score": 30,
        "label": "fixture",
    }


def test_stock_data_quality_is_derived_from_model_signal_schema() -> None:
    payload = {
        key: _scored_signal()
        for key in scoring.STOCK_SCORE_SIGNAL_KEYS
    }
    # Current ATM IV is deliberately displayed but is not a historical-rank
    # scoring input and therefore must not change the denominator.
    payload["atm_iv_percent"] = _scored_signal(35.0)

    complete = scoring.compute_stock_scores(payload)
    # 与市场分数同口径（审计 2.6.10）：data_quality = 信号可得率 × 模型
    # 覆盖率。11 个价格类信号齐全时信号可得率是 100，但 top/bottom 各有
    # 4/7 个分类恒缺（期权拥挤、财报反应、估值预期、事件风险），覆盖率
    # 只有 0.5——不允许再报出「数据质量 100」。
    assert complete["signal_data_quality"] == 100
    assert complete["coverage"]["top_ratio"] == pytest.approx(0.5, abs=0.06)
    expected_coverage = (
        complete["coverage"]["top_ratio"] + complete["coverage"]["bottom_ratio"]
    ) / 2
    assert complete["data_quality"] == round(100 * expected_coverage)
    assert complete["data_quality"] < 100
    assert complete["data_quality_available"] == 11
    assert complete["data_quality_expected"] == 11

    payload["rsi14"] = {
        "value": None,
        "top_score": None,
        "bottom_score": None,
        "label": "fixture",
    }
    partial = scoring.compute_stock_scores(payload)
    assert partial["data_quality_available"] == 10
    assert partial["data_quality_expected"] == 11
    assert partial["signal_data_quality"] == 91
    partial_coverage = (
        partial["coverage"]["top_ratio"] + partial["coverage"]["bottom_ratio"]
    ) / 2
    assert partial["data_quality"] == round(91 * partial_coverage)
    assert partial["data_quality"] < partial["signal_data_quality"]


def test_atr_does_not_masquerade_as_an_iv_rank_component() -> None:
    payload = {"atr_percentile": _scored_signal(80.0)}

    result = scoring.compute_stock_scores(payload)

    assert result["top_breakdown"]["options_crowding"] is None
    assert result["bottom_breakdown"]["options_panic_falling"] is None


def test_stock_strength_explicitly_disables_price_and_liquidity_filters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    async def fake_scan_strength(**kwargs):
        captured.update(kwargs)
        return {
            "as_of": "2026-07-10T20:00:00+00:00",
            "rows": [{"ticker": "PENNY", "price": 2.5}],
            "market_regime": {},
        }

    monkeypatch.setattr(scanner, "scan_strength", fake_scan_strength)

    payload = asyncio.run(scanner.stock_strength("penny"))

    assert payload["row"]["price"] == 2.5
    assert captured["min_price"] == 0
    assert captured["min_avg_dollar_volume"] == 0
