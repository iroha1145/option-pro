"""Percentile scoring, module gates, Funding EMA, composite and seven-day deltas."""

from __future__ import annotations

import datetime as dt
import math

import pytest

from app.services.macro_conditions.models import FactorPoint
from app.services.macro_conditions.registry import (
    COMPOSITE_MINIMUM_VALID_MODULES,
    FACTORS_BY_ID,
    FACTOR_IDS_BY_MODULE,
    MODULES,
    regime_for,
)
from app.services.macro_conditions.scoring import (
    aggregate_composite,
    aggregate_modules,
    apply_direction,
    mid_rank_percentile,
    rolling_percentiles,
    score_factor_series,
    seven_day_changes,
)


def _days(count: int, *, start: dt.date = dt.date(2020, 1, 1)) -> list[dt.date]:
    return [start + dt.timedelta(days=index) for index in range(count)]


def _points(factor_id: str, dates, values, signed=None) -> list[FactorPoint]:
    return [
        FactorPoint(
            factor_id=factor_id,
            snapshot_date=day,
            raw_value=value,
            score_value=value,
            signed_value=None if signed is None else signed[index],
            status="ok" if value is not None else "missing",
            data_through=day if value is not None else None,
            available_at="2026-07-24T00:00:00Z" if value is not None else None,
            history_basis="latest_revised_backfill" if value is not None else None,
        )
        for index, (day, value) in enumerate(zip(dates, values))
    ]


# ---------------------------------------------------------------------------
# 44-46 percentile mechanics
# ---------------------------------------------------------------------------


def test_mid_rank_percentile_is_deterministic_and_stable_across_ties() -> None:
    history = [1.0, 2.0, 2.0, 3.0]
    assert mid_rank_percentile(history, 1.0) == pytest.approx(12.5)
    assert mid_rank_percentile(history, 2.0) == pytest.approx(50.0)
    assert mid_rank_percentile(history, 3.0) == pytest.approx(87.5)
    # Identical values always receive an identical percentile.
    assert mid_rank_percentile(history, 2.0) == mid_rank_percentile(history, 2.0)
    assert mid_rank_percentile([], 1.0) is None


def test_all_equal_history_lands_at_the_midpoint() -> None:
    assert mid_rank_percentile([5.0] * 10, 5.0) == pytest.approx(50.0)


def test_supportive_high_and_low_invert_each_other() -> None:
    assert apply_direction(30.0, "supportive_high_percentile") == pytest.approx(30.0)
    assert apply_direction(30.0, "supportive_low_percentile") == pytest.approx(70.0)
    assert apply_direction(30.0, "target_distance") == pytest.approx(70.0)
    assert apply_direction(None, "supportive_high_percentile") is None
    with pytest.raises(ValueError):
        apply_direction(30.0, "direct_score")


def test_scores_are_bounded_to_zero_and_one_hundred() -> None:
    dates = _days(40)
    values = [float(index) for index in range(40)]
    scores = rolling_percentiles(dates, values, window_years=5, minimum_history=1)
    assert all(0.0 <= score <= 100.0 for score in scores if score is not None)


# ---------------------------------------------------------------------------
# 47-48 minimum history and missing handling
# ---------------------------------------------------------------------------


def test_below_minimum_history_the_score_is_null_not_fifty() -> None:
    dates = _days(10)
    values = [float(index) for index in range(10)]
    scores = rolling_percentiles(dates, values, window_years=5, minimum_history=5)
    assert scores[:4] == [None, None, None, None]
    assert scores[4] is not None
    assert 50.0 not in [score for score in scores[:4] if score is not None]


def test_missing_values_are_excluded_and_never_replaced_with_fifty() -> None:
    dates = _days(8)
    values = [1.0, None, 2.0, None, 3.0, 4.0, 5.0, None]
    scores = rolling_percentiles(dates, values, window_years=5, minimum_history=2)
    assert scores[1] is None and scores[3] is None and scores[7] is None
    # The window only ever contains the five real observations.
    assert scores[6] == pytest.approx(mid_rank_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 5.0))


def test_the_window_excludes_observations_older_than_the_score_window() -> None:
    dates = [dt.date(2019, 1, 1), dt.date(2024, 6, 30), dt.date(2024, 7, 1)]
    values = [1_000.0, 5.0, 6.0]
    scores = rolling_percentiles(dates, values, window_years=5, minimum_history=1)
    # By 2024-07-01 the 2019 observation has aged out, so 6.0 is the maximum of
    # a two-value window rather than the middle of a three-value one.
    assert scores[2] == pytest.approx(mid_rank_percentile([5.0, 6.0], 6.0))


def test_scores_never_read_a_future_date() -> None:
    dates = _days(6)
    values = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0]
    scores = rolling_percentiles(dates, values, window_years=5, minimum_history=1)
    # Strictly decreasing values: each is the minimum of everything seen so far,
    # which is only true if no later value ever enters the window.
    assert scores[0] == pytest.approx(50.0)
    assert all(score == pytest.approx(100.0 * 0.5 / (index + 1)) for index, score in enumerate(scores))


def test_identical_inputs_score_identically_on_repeat_runs() -> None:
    dates = _days(300)
    values = [math.sin(index / 7) for index in range(300)]
    first = rolling_percentiles(dates, values, window_years=5, minimum_history=10)
    second = rolling_percentiles(dates, values, window_years=5, minimum_history=10)
    assert first == second


def test_direct_score_factors_skip_the_percentile_entirely() -> None:
    dates = _days(5)
    points = [
        FactorPoint(
            factor_id="on_rrp_buffer_risk",
            snapshot_date=day,
            raw_value=0.25,
            score_value=75.0,
            status="ok",
            data_through=day,
            available_at="2026-07-24T00:00:00Z",
            history_basis="latest_revised_backfill",
        )
        for day in dates
    ]
    scored = score_factor_series("on_rrp_buffer_risk", points, window_years=5)
    assert [item.score for item in scored] == [75.0] * 5
    assert FACTORS_BY_ID["on_rrp_buffer_risk"].minimum_history == 0


# ---------------------------------------------------------------------------
# 53 seven-day changes
# ---------------------------------------------------------------------------


def test_seven_day_change_uses_the_newest_valid_value_at_least_seven_days_back() -> None:
    dates = [
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 5),
        dt.date(2026, 1, 8),
        dt.date(2026, 1, 9),
    ]
    values = [10.0, 20.0, 30.0, 40.0]
    changes = seven_day_changes(dates, values)
    assert changes[0] is None
    assert changes[1] is None
    # 2026-01-08 minus seven days is 2026-01-01, so it compares against 10.0.
    assert changes[2] == pytest.approx(20.0)
    # 2026-01-09 minus seven days is 2026-01-02; the newest valid value at or
    # before that is still 2026-01-01.
    assert changes[3] == pytest.approx(30.0)


def test_a_missing_comparison_returns_null_rather_than_zero() -> None:
    dates = [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]
    assert seven_day_changes(dates, [1.0, 2.0]) == [None, None]
    dates = [dt.date(2026, 1, 1), dt.date(2026, 1, 20)]
    assert seven_day_changes(dates, [1.0, None]) == [None, None]


# ---------------------------------------------------------------------------
# 49-51 module and composite gates
# ---------------------------------------------------------------------------


def _scored_grid(grid, per_factor_scores):
    """Build a ScoredFactor map straight from explicit score sequences."""

    from app.services.macro_conditions.scoring import ScoredFactor

    output = {}
    for factor_id, scores in per_factor_scores.items():
        spec = FACTORS_BY_ID[factor_id]
        output[factor_id] = [
            ScoredFactor(
                factor_id=factor_id,
                module_id=spec.module_id,
                snapshot_date=grid[index],
                raw_value=None if score is None else float(index),
                signed_value=None,
                score=score,
                score_method=spec.score_method,
                status="ok" if score is not None else "missing",
                valid_observations=252,
                confidence=None,
                data_through=grid[index] if score is not None else None,
                available_at="2026-07-24T00:00:00Z" if score is not None else None,
                history_basis="latest_revised_backfill" if score is not None else None,
                missing_inputs=(),
                stale_inputs=(),
            )
            for index, score in enumerate(scores)
        ]
    return output


def test_a_module_below_its_factor_floor_publishes_no_score() -> None:
    grid = _days(3)
    # Liquidity needs 3 of 5. Give it exactly 2.
    members = FACTOR_IDS_BY_MODULE["liquidity"]
    scores = {members[0]: [60.0] * 3, members[1]: [40.0] * 3}
    for factor_id in members[2:]:
        scores[factor_id] = [None] * 3
    for module in MODULES:
        if module.module_id == "liquidity":
            continue
        for factor_id in FACTOR_IDS_BY_MODULE[module.module_id]:
            scores[factor_id] = [None] * 3
    modules = aggregate_modules(grid, _scored_grid(grid, scores))
    liquidity = modules["liquidity"]
    assert all(item.score is None for item in liquidity)
    assert all(item.status == "insufficient_factors" for item in liquidity)
    assert liquidity[0].valid_factor_count == 2
    assert liquidity[0].total_factor_count == 5


def test_a_module_at_its_floor_equal_weights_only_the_valid_factors() -> None:
    grid = _days(2)
    members = FACTOR_IDS_BY_MODULE["liquidity"]
    scores = {
        members[0]: [60.0] * 2,
        members[1]: [30.0] * 2,
        members[2]: [90.0] * 2,
        members[3]: [None] * 2,
        members[4]: [None] * 2,
    }
    for module in MODULES:
        if module.module_id == "liquidity":
            continue
        for factor_id in FACTOR_IDS_BY_MODULE[module.module_id]:
            scores[factor_id] = [None] * 2
    modules = aggregate_modules(grid, _scored_grid(grid, scores))
    liquidity = modules["liquidity"][0]
    assert liquidity.score == pytest.approx((60.0 + 30.0 + 90.0) / 3)
    assert liquidity.confidence == pytest.approx(3 / 5)


def test_funding_applies_a_five_day_ema_over_its_daily_module_score() -> None:
    grid = _days(4)
    members = FACTOR_IDS_BY_MODULE["funding"]
    raw = [50.0, 60.0, 70.0, 80.0]
    scores = {factor_id: list(raw) for factor_id in members}
    for module in MODULES:
        if module.module_id == "funding":
            continue
        for factor_id in FACTOR_IDS_BY_MODULE[module.module_id]:
            scores[factor_id] = [None] * 4
    modules = aggregate_modules(grid, _scored_grid(grid, scores))
    alpha = 2 / (5 + 1)
    expected = []
    state = None
    for value in raw:
        state = value if state is None else alpha * value + (1 - alpha) * state
        expected.append(state)
    assert [item.score for item in modules["funding"]] == pytest.approx(expected)
    # Every other module is unsmoothed.
    assert next(module.ema_days for module in MODULES if module.module_id == "funding") == 5
    assert all(
        module.ema_days is None for module in MODULES if module.module_id != "funding"
    )


def test_the_composite_requires_five_of_seven_modules() -> None:
    grid = _days(1)
    assert COMPOSITE_MINIMUM_VALID_MODULES == 5

    def build(active_modules: int):
        scores = {}
        for index, module in enumerate(MODULES):
            members = FACTOR_IDS_BY_MODULE[module.module_id]
            value = 60.0 if index < active_modules else None
            for factor_id in members:
                scores[factor_id] = [value]
        modules = aggregate_modules(grid, _scored_grid(grid, scores))
        return aggregate_composite(grid, modules)[0]

    assert build(4).score is None
    assert build(4).status == "insufficient_modules"
    assert build(4).regime is None
    published = build(5)
    assert published.score == pytest.approx(60.0)
    assert published.valid_module_count == 5
    assert published.status == "ok"
    # Confidence = valid module share × mean in-module factor coverage.
    assert published.confidence == pytest.approx(5 / 7 * 1.0)


def test_a_failed_module_is_dropped_rather_than_filled_with_fifty() -> None:
    grid = _days(1)
    scores = {}
    for index, module in enumerate(MODULES):
        value = 80.0 if index < 6 else None
        for factor_id in FACTOR_IDS_BY_MODULE[module.module_id]:
            scores[factor_id] = [value]
    composite = aggregate_composite(grid, aggregate_modules(grid, _scored_grid(grid, scores)))[0]
    # Six modules at 80 → 80, not (6×80 + 50)/7 ≈ 75.7.
    assert composite.score == pytest.approx(80.0)
    assert composite.valid_module_count == 6


# ---------------------------------------------------------------------------
# 52 regime boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "score,label",
    [
        (0.0, "明显收紧"),
        (29.9, "明显收紧"),
        (30.0, "偏紧"),
        (44.9, "偏紧"),
        (45.0, "中性"),
        (54.9, "中性"),
        (55.0, "偏松"),
        (69.9, "偏松"),
        (70.0, "明显宽松"),
        (100.0, "明显宽松"),
    ],
)
def test_regime_band_boundaries(score: float, label: str) -> None:
    assert regime_for(score) == label


def test_regime_is_absent_without_a_score() -> None:
    assert regime_for(None) is None
