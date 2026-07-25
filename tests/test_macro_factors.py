"""Registry completeness and the thirty raw factor formulas.

Every expected value below is derived by hand from the formula in
``registry.py``/``calculations.py`` — no number comes from any third-party report.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from app.services.macro_conditions.alignment import AsOfSeries, build_grid
from app.services.macro_conditions.calculations import (
    compute_factor_points,
    handler_coverage,
    population_std,
    relative_return,
    rolling_median,
)
from app.services.macro_conditions.registry import (
    BREAKEVEN_TARGET_PERCENT,
    ETF_PROXIES,
    ETF_SYMBOLS,
    FACTORS,
    FACTORS_BY_ID,
    FACTOR_IDS_BY_MODULE,
    FRED_SERIES,
    MODULES,
    ON_RRP_FULL_BUFFER_BILLIONS,
    SERIES_BY_ID,
    TRADING_DAYS_PER_YEAR,
    validate_registry,
)


GRID_START = dt.date(2026, 1, 2)
GRID_END = dt.date(2026, 7, 24)
GRID = build_grid(GRID_START, GRID_END)
LAST = GRID[-1]


def _series(values_by_series: dict[str, dict[dt.date, float]]) -> dict[str, AsOfSeries]:
    output: dict[str, AsOfSeries] = {}
    for series_id, values in values_by_series.items():
        spec = SERIES_BY_ID[series_id]
        rows = [
            (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
            for day, value in sorted(values.items())
        ]
        output[series_id] = AsOfSeries(
            series_id,
            rows,
            max_stale_calendar_days=spec.max_stale_calendar_days,
        )
    return output


def _etfs(values_by_symbol: dict[str, dict[dt.date, float]]) -> dict[str, AsOfSeries]:
    from app.services.macro_conditions.registry import ETF_MAX_STALE_TRADING_DAYS

    output: dict[str, AsOfSeries] = {}
    for symbol, values in values_by_symbol.items():
        rows = [
            (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
            for day, value in sorted(values.items())
        ]
        output[symbol] = AsOfSeries(
            symbol,
            rows,
            max_stale_trading_days=ETF_MAX_STALE_TRADING_DAYS,
            grid=GRID,
        )
    return output


def _flat(series_id: str, value: float, *, days=None) -> dict[dt.date, float]:
    return {day: value for day in (days or GRID)}


def _point(factor_id: str, series=None, etfs=None, when: dt.date = LAST):
    points = compute_factor_points(GRID, series or {}, etfs or {})
    index = GRID.index(when)
    return points[factor_id][index]


# ---------------------------------------------------------------------------
# 26-29 registry integrity
# ---------------------------------------------------------------------------


def test_registry_is_internally_consistent() -> None:
    validate_registry()


def test_thirty_factors_seven_modules_twenty_four_series_eight_etfs() -> None:
    assert len(FACTORS) == 30
    assert len(MODULES) == 7
    assert len(FRED_SERIES) == 24
    assert len(ETF_PROXIES) == 8
    assert sum(len(members) for members in FACTOR_IDS_BY_MODULE.values()) == 30


def test_every_factor_id_is_unique_and_has_a_formula_version_and_inputs() -> None:
    ids = [factor.factor_id for factor in FACTORS]
    assert len(ids) == len(set(ids))
    for factor in FACTORS:
        assert factor.formula_version
        assert factor.display_name_zh
        assert factor.description_zh
        assert factor.transform
        assert factor.display_unit
        assert factor.stale_rule
        assert factor.required_series or factor.required_etfs
        assert factor.direction in {"high", "low", "target"}


def test_every_factor_has_a_calculation() -> None:
    assert handler_coverage() == ()


def test_module_membership_matches_the_specified_split() -> None:
    counts = {module_id: len(members) for module_id, members in FACTOR_IDS_BY_MODULE.items()}
    assert counts == {
        "liquidity": 5,
        "funding": 6,
        "treasury": 3,
        "rates": 3,
        "credit": 4,
        "risk": 4,
        "external": 5,
    }
    floors = {module.module_id: module.minimum_valid_factors for module in MODULES}
    assert floors == {
        "liquidity": 3,
        "funding": 4,
        "treasury": 2,
        "rates": 2,
        "credit": 3,
        "risk": 3,
        "external": 3,
    }


# ---------------------------------------------------------------------------
# 30-33 liquidity
# ---------------------------------------------------------------------------


def test_fed_net_liquidity_uses_billions_consistently() -> None:
    series = _series(
        {
            "WALCL": _flat("WALCL", 6_800.0),
            "WTREGEN": _flat("WTREGEN", 700.0),
            "RRPONTSYD": _flat("RRPONTSYD", 150.0),
        }
    )
    point = _point("fed_net_liquidity", series=series)
    assert point.raw_value == pytest.approx(6_800.0 - 700.0 - 150.0)
    assert FACTORS_BY_ID["fed_net_liquidity"].display_unit == "usd_billions"


def test_net_liquidity_momentum_uses_an_as_of_join_thirteen_weeks_back() -> None:
    # Step the level up by 100 exactly 13 weeks before the last grid date.
    step_date = LAST - dt.timedelta(weeks=13)
    walcl = {day: (6_800.0 if day <= step_date else 6_900.0) for day in GRID}
    series = _series(
        {
            "WALCL": walcl,
            "WTREGEN": _flat("WTREGEN", 700.0),
            "RRPONTSYD": _flat("RRPONTSYD", 150.0),
        }
    )
    point = _point("net_liquidity_momentum_13w", series=series)
    assert point.raw_value == pytest.approx(100.0)


def test_tga_deviation_uses_a_rolling_median_of_fifty_two_weekly_observations() -> None:
    # 60 weekly Wednesdays: the last 52 are 1..52 (billions), current = 52.
    weeks = [GRID_START - dt.timedelta(weeks=60 - index) for index in range(60)]
    values = {day: float(index + 1) for index, day in enumerate(weeks)}
    # Extend to the grid end so the as-of read is fresh.
    latest = GRID[-1]
    values[latest] = 100.0
    spec = SERIES_BY_ID["WTREGEN"]
    rows = [
        (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
        for day, value in sorted(values.items())
    ]
    series = {
        "WTREGEN": AsOfSeries(
            "WTREGEN", rows, max_stale_calendar_days=spec.max_stale_calendar_days
        )
    }
    point = _point("tga_deviation_52w", series=series)
    window = sorted(values.values())[-52:]
    expected_median = rolling_median(window)
    assert point.raw_value == pytest.approx(100.0 - expected_median)
    assert FACTORS_BY_ID["tga_deviation_52w"].score_method == "supportive_low_percentile"


@pytest.mark.parametrize(
    "balance,expected_risk,expected_score",
    [
        (0.0, 1.0, 0.0),
        (25.0, 0.5625, 43.75),
        (50.0, 0.25, 75.0),
        (100.0, 0.0, 100.0),
        (250.0, 0.0, 100.0),
    ],
)
def test_on_rrp_buffer_risk_curve_boundaries(
    balance: float,
    expected_risk: float,
    expected_score: float,
) -> None:
    series = _series({"RRPONTSYD": _flat("RRPONTSYD", balance)})
    point = _point("on_rrp_buffer_risk", series=series)
    assert point.raw_value == pytest.approx(expected_risk)
    assert point.score_value == pytest.approx(expected_score)
    assert FACTORS_BY_ID["on_rrp_buffer_risk"].score_method == "direct_score"
    assert ON_RRP_FULL_BUFFER_BILLIONS == 100.0


# ---------------------------------------------------------------------------
# 34-35 funding
# ---------------------------------------------------------------------------


def test_the_five_funding_spreads_keep_a_signed_value_and_score_a_transform() -> None:
    series = _series(
        {
            "SOFR": _flat("SOFR", 4.30),
            "OBFR": _flat("OBFR", 4.36),
            "IORB": _flat("IORB", 4.40),
            "RRPONTSYAWARD": _flat("RRPONTSYAWARD", 4.25),
            "EFFR": _flat("EFFR", 4.33),
            "DCPF3M": _flat("DCPF3M", 4.20),
            "DTB3": _flat("DTB3", 4.30),
        }
    )
    expectations = {
        # (signed, scored)
        "collateral_repo_friction": (4.30 - 4.36, abs(4.30 - 4.36)),
        "corridor_friction_1": (4.30 - 4.40, abs(4.30 - 4.40)),
        "corridor_friction_2": (4.30 - 4.25, abs(4.30 - 4.25)),
        "effr_iorb_spread": (4.33 - 4.40, abs(4.33 - 4.40)),
        # A negative CP−T-bill spread is not scored as better than parity.
        "cp_tbill_spread": (4.20 - 4.30, 0.0),
    }
    for factor_id, (signed, scored) in expectations.items():
        point = _point(factor_id, series=series)
        assert point.signed_value == pytest.approx(signed), factor_id
        assert point.raw_value == pytest.approx(signed), factor_id
        assert point.score_value == pytest.approx(scored), factor_id


def test_positive_cp_tbill_spread_is_scored_as_stress() -> None:
    series = _series(
        {
            "DCPF3M": _flat("DCPF3M", 4.85),
            "DTB3": _flat("DTB3", 4.30),
        }
    )
    point = _point("cp_tbill_spread", series=series)
    assert point.score_value == pytest.approx(0.55)


def test_funding_fragmentation_averages_daily_cross_sectional_dispersion() -> None:
    series = _series(
        {
            "SOFR": _flat("SOFR", 4.30),
            "OBFR": _flat("OBFR", 4.36),
            "IORB": _flat("IORB", 4.40),
            "RRPONTSYAWARD": _flat("RRPONTSYAWARD", 4.25),
            "EFFR": _flat("EFFR", 4.33),
            "DCPF3M": _flat("DCPF3M", 4.85),
            "DTB3": _flat("DTB3", 4.30),
        }
    )
    spreads = [
        4.30 - 4.36,
        4.30 - 4.40,
        4.30 - 4.25,
        4.33 - 4.40,
        4.85 - 4.30,
    ]
    expected = population_std(spreads)
    point = _point("funding_fragmentation_21d", series=series)
    # Every day has the same panel, so the 21-day mean equals the daily value.
    assert point.raw_value == pytest.approx(expected)


def test_fragmentation_needs_at_least_four_available_spreads() -> None:
    series = _series(
        {
            "SOFR": _flat("SOFR", 4.30),
            "OBFR": _flat("OBFR", 4.36),
            "IORB": _flat("IORB", 4.40),
        }
    )
    # Only three spreads can be formed (SOFR−OBFR, SOFR−IORB, EFFR−IORB is absent).
    point = _point("funding_fragmentation_21d", series=series)
    assert point.raw_value is None
    assert point.status == "missing"


# ---------------------------------------------------------------------------
# 36-37 treasury
# ---------------------------------------------------------------------------


def test_ten_year_rate_volatility_is_an_unannualised_population_std_of_changes() -> None:
    levels = {}
    for index, day in enumerate(GRID):
        levels[day] = 4.0 + (0.05 if index % 2 else 0.0)
    series = _series({"DGS10": levels})
    point = _point("rate_volatility_10y_21d", series=series)
    # Alternating ±0.05 changes: population std of 21 alternating values.
    ordered = [levels[day] for day in GRID[-22:]]
    changes = [ordered[i] - ordered[i - 1] for i in range(1, len(ordered))]
    assert point.raw_value == pytest.approx(population_std(changes))
    # Not annualised.
    assert point.raw_value < 0.06
    assert FACTORS_BY_ID["rate_volatility_10y_21d"].display_unit == "percentage_points"


def test_curve_curvature_is_the_absolute_two_ten_thirty_butterfly() -> None:
    series = _series(
        {
            "DGS2": _flat("DGS2", 3.90),
            "DGS10": _flat("DGS10", 4.20),
            "DGS30": _flat("DGS30", 4.65),
        }
    )
    point = _point("curve_curvature_abs", series=series)
    assert point.raw_value == pytest.approx(abs(2 * 4.20 - 3.90 - 4.65))
    term = _point("term_premium_30y_10y", series=series)
    assert term.raw_value == pytest.approx(4.65 - 4.20)


# ---------------------------------------------------------------------------
# 38-39 rates
# ---------------------------------------------------------------------------


def test_real_rate_level_is_the_sixty_forty_blend() -> None:
    series = _series({"DFII5": _flat("DFII5", 1.50), "DFII10": _flat("DFII10", 2.00)})
    point = _point("real_rate_level", series=series)
    assert point.raw_value == pytest.approx(0.6 * 1.50 + 0.4 * 2.00)
    curve = _point("real_curve_10y_5y", series=series)
    assert curve.raw_value == pytest.approx(2.00 - 1.50)


@pytest.mark.parametrize("level,distance", [(2.0, 0.0), (2.4, 0.4), (1.55, 0.45)])
def test_breakeven_shows_the_level_and_scores_the_distance_from_two_percent(
    level: float,
    distance: float,
) -> None:
    series = _series({"T10YIE": _flat("T10YIE", level)})
    point = _point("breakeven_10y", series=series)
    assert point.raw_value == pytest.approx(level)
    assert point.score_value == pytest.approx(distance)
    assert point.signed_value == pytest.approx(distance)
    assert BREAKEVEN_TARGET_PERCENT == 2.0
    assert FACTORS_BY_ID["breakeven_10y"].score_method == "target_distance"


# ---------------------------------------------------------------------------
# 40 ETF relative returns
# ---------------------------------------------------------------------------


def test_relative_return_formula_is_the_log_difference_times_one_hundred() -> None:
    value = relative_return(110.0, 100.0, 105.0, 100.0)
    assert value == pytest.approx(100 * (math.log(1.10) - math.log(1.05)))
    # A non-positive price makes the result missing rather than an error.
    assert relative_return(0.0, 100.0, 105.0, 100.0) is None


def test_etf_factor_needs_sixty_four_shared_closes() -> None:
    from app.services.macro_conditions.registry import (
        RELATIVE_RETURN_MINIMUM_OBSERVATIONS,
    )

    assert RELATIVE_RETURN_MINIMUM_OBSERVATIONS == 64
    short = GRID[-40:]
    etfs = _etfs(
        {
            "HYG": {day: 80.0 + index for index, day in enumerate(short)},
            "IEI": {day: 118.0 for day in short},
        }
    )
    assert _point("hy_credit", etfs=etfs).raw_value is None

    long_days = GRID[-70:]
    etfs = _etfs(
        {
            "HYG": {day: 80.0 * (1.001 ** index) for index, day in enumerate(long_days)},
            "IEI": {day: 118.0 for day in long_days},
        }
    )
    point = _point("hy_credit", etfs=etfs)
    assert point.raw_value is not None
    expected = 100 * math.log((1.001 ** 69) / (1.001 ** 6))
    assert point.raw_value == pytest.approx(expected)


def test_one_missing_etf_only_breaks_the_factors_that_need_it() -> None:
    days = GRID[-70:]
    prices = {day: 100.0 * (1.002 ** index) for index, day in enumerate(days)}
    etfs = _etfs({"SPY": prices, "IWM": prices})
    points = compute_factor_points(GRID, {}, etfs)
    index = GRID.index(LAST)
    assert points["high_beta_preference"][index].raw_value is not None
    # risk_vs_safe needs TLT, which is absent.
    absent = points["risk_vs_safe"][index]
    assert absent.raw_value is None
    assert "TLT" in absent.missing_inputs


# ---------------------------------------------------------------------------
# 41-43 risk and external
# ---------------------------------------------------------------------------


def test_vix_term_structure_is_a_ratio_and_rejects_a_non_positive_denominator() -> None:
    series = _series({"VIXCLS": _flat("VIXCLS", 18.0), "VXVCLS": _flat("VXVCLS", 20.0)})
    assert _point("vix_term_structure", series=series).raw_value == pytest.approx(0.9)

    series = _series({"VIXCLS": _flat("VIXCLS", 18.0), "VXVCLS": _flat("VXVCLS", 0.0)})
    point = _point("vix_term_structure", series=series)
    assert point.raw_value is None
    assert "VXVCLS" in point.missing_inputs


def test_fx_realized_volatility_is_annualised_by_the_square_root_of_252() -> None:
    levels = {}
    for index, day in enumerate(GRID):
        levels[day] = 120.0 * (1.001 if index % 2 else 1.0)
    series = _series({"DTWEXBGS": levels})
    point = _point("fx_realized_volatility_63d", series=series)
    ordered = [levels[day] for day in GRID[-64:]]
    returns = [math.log(ordered[i] / ordered[i - 1]) for i in range(1, len(ordered))]
    expected = population_std(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)
    assert point.raw_value == pytest.approx(expected)
    assert FACTORS_BY_ID["fx_realized_volatility_63d"].display_unit == "ratio"


def test_oil_volatility_deviation_is_clipped_at_zero_below_its_yearly_median() -> None:
    long_grid = build_grid(dt.date(2024, 1, 2), GRID_END)
    spec = SERIES_BY_ID["OVXCLS"]
    # 252-observation window of 30, with the latest print above and below it.
    values = {day: 30.0 for day in long_grid}
    rows = [
        (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
        for day, value in sorted(values.items())
    ]
    series = {
        "OVXCLS": AsOfSeries("OVXCLS", rows, max_stale_calendar_days=spec.max_stale_calendar_days)
    }
    points = compute_factor_points(long_grid, series, {})
    at_median = points["oil_volatility_deviation"][-1]
    assert at_median.raw_value == pytest.approx(0.0)

    values[long_grid[-1]] = 44.0
    rows = [
        (day, value, "2026-07-24T00:00:00Z", "latest_revised_backfill")
        for day, value in sorted(values.items())
    ]
    series = {
        "OVXCLS": AsOfSeries("OVXCLS", rows, max_stale_calendar_days=spec.max_stale_calendar_days)
    }
    points = compute_factor_points(long_grid, series, {})
    above = points["oil_volatility_deviation"][-1]
    assert above.raw_value == pytest.approx(14.0)


def test_level_factors_pass_the_source_value_through_unchanged() -> None:
    series = _series(
        {
            "WRESBAL": _flat("WRESBAL", 3_300.0),
            "NFCI": _flat("NFCI", -0.42),
            "VIXCLS": _flat("VIXCLS", 17.25),
            "DTWEXBGS": _flat("DTWEXBGS", 121.5),
            "DCOILWTICO": _flat("DCOILWTICO", 78.4),
            "DHHNGSP": _flat("DHHNGSP", 3.15),
        }
    )
    expected = {
        "bank_reserves": 3_300.0,
        "nfci": -0.42,
        "vix": 17.25,
        "broad_dollar_index": 121.5,
        "wti_oil": 78.4,
        "natural_gas": 3.15,
    }
    for factor_id, value in expected.items():
        assert _point(factor_id, series=series).raw_value == pytest.approx(value), factor_id


def test_every_registered_series_and_etf_is_required_by_at_least_one_factor() -> None:
    used_series = {sid for factor in FACTORS for sid in factor.required_series}
    used_etfs = {symbol for factor in FACTORS for symbol in factor.required_etfs}
    assert used_series == {spec.series_id for spec in FRED_SERIES}
    assert used_etfs == set(ETF_SYMBOLS)
