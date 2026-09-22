"""Characterize numeric boundaries before sharing their implementations."""
from __future__ import annotations

import importlib

import pytest


ROUNDED_MODULES = (
    "strength.features", "strength.finnhub", "strength.market_regime",
    "strength.marketdata", "strength.price_action", "strength.relative_spreads",
    "strength.vol_price_match", "strength.yahoo_options", "signals",
)
FINITE_MODULES = (
    ("breakouts.normalizer", "finite_number"),
    ("breakouts.range_interactions", "_finite"),
    ("breakouts.realtime", "_finite"),
    ("breakouts.relative_strength", "_finite"),
    ("breakouts.daily_confirmation", "_finite"),
    ("breakouts.t1_priority", "_finite"),
    ("breakouts.service", "_finite"),
    ("strength.market_shape", "_finite"),
    ("technical.chart_analysis", "_finite_number"),
)


class BrokenNumber:
    def __float__(self):
        raise RuntimeError("numeric adapter failed")


def load(module, name):
    return getattr(importlib.import_module("app.services." + module), name)


@pytest.mark.parametrize("module", ROUNDED_MODULES)
def test_rounded_values_keep_precision_and_missing_semantics(module):
    rounded = load(module, "_safe_float")
    assert rounded("1.23456") == 1.2346
    assert rounded(-1.23456, 2) == -1.23
    assert rounded(True) == 1.0
    assert rounded(0) == 0.0
    for value in (None, "bad", float("nan"), float("inf"), -float("inf"), 10 ** 1000, BrokenNumber()):
        assert rounded(value) is None


@pytest.mark.parametrize("module,name", FINITE_MODULES)
def test_finite_values_accept_numeric_strings_but_keep_conversion_errors(module, name):
    finite = load(module, name)
    assert finite("1.23456") == 1.23456
    assert finite(False) == 0.0
    assert finite(-3) == -3.0
    for value in (None, "bad", float("nan"), float("inf"), -float("inf")):
        assert finite(value) is None
    with pytest.raises(OverflowError):
        finite(10 ** 1000)
    with pytest.raises(RuntimeError, match="numeric adapter failed"):
        finite(BrokenNumber())


@pytest.mark.parametrize("module", ("market_regime", "marketdata", "relative_spreads", "vol_price_match", "yahoo_options", "scanner"))
def test_clamp_keeps_each_callers_default_and_bounds(module):
    clamp = load("strength." + module, "_clamp")
    default = None if module == "scanner" else 50.0
    for value in (None, "bad", float("nan"), float("inf"), BrokenNumber()):
        assert clamp(value) == default
        assert clamp(value, default=17.0) == 17.0
    assert clamp(-10) == 0.0
    assert clamp(300) == 100.0
    assert clamp("3", lo=4, hi=8) == 4


@pytest.mark.parametrize("module", ("marketdata", "yahoo_options"))
def test_option_aggregation_ignores_unusable_quotes_without_inventing_values(module):
    total = load("strength." + module, "_sum")
    average = load("strength." + module, "_weighted_average")
    assert total([None, -2, 0, float("nan"), "1.23456", 2]) == 3.2346
    assert average([2, 4, None, -1], [1, 3, 100, 100]) == 3.5
    assert average([None, -1], [1, 1]) is None
    assert average([2, 4], [0, -1]) is None


@pytest.mark.parametrize("module", ("features", "market_regime", "relative_spreads"))
def test_positional_returns_keep_missing_and_rounding_contract(module):
    import pandas as pd

    calculate = load("strength." + module, "_ret")
    assert calculate(pd.Series([100.0, 101.23456]), 1) == 0.01235
    assert calculate(pd.Series([100.0]), 1) is None
    assert calculate(pd.Series([0.0, 12.0]), 1) is None
    assert calculate(pd.Series([-1.0, 12.0]), 1) is None
    assert calculate(pd.Series([float("nan"), 12.0]), 1) is None
    assert calculate(pd.Series([100.0, float("inf")]), 1) is None


@pytest.mark.parametrize("module", ("app.worker.tasks", "app.services.ai_jobs.worker"))
def test_analysis_permission_adapters_preserve_mode_precedence(module):
    from types import SimpleNamespace

    permissions = getattr(importlib.import_module(module), "_personal_analysis_permissions")
    for mode, expected in (("off", (False, False)), ("read", (False, False)), ("manual", (True, False)), ("scheduled", (True, True)), ("unknown", (False, False))):
        config = SimpleNamespace(features=SimpleNamespace(catalyst_mode=mode), catalyst_manual_enabled=True, catalyst_scheduled_enabled=True)
        assert permissions(config) == expected
    assert permissions(SimpleNamespace()) == (False, False)
    assert permissions(SimpleNamespace(catalyst_manual_enabled=True)) == (True, False)
    assert permissions(SimpleNamespace(catalyst_scheduled_enabled=True)) == (False, True)
