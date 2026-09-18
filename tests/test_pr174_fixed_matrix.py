"""Fixed-matrix contracts: calendar, quality gates, 864 plan, selection vs background."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from app.services.market_calendar import is_trading_day, trading_sessions
from app.services.research_eod_v1.calendar_asof import shift_sessions
from app.services.research_eod_v1.capability import PRICE_ONLY_DIAGNOSTIC
from app.services.research_eod_v1.data.contract import ResearchBar, validate_research_bars
from app.services.research_eod_v1.data_readiness import LAYER_A, LAYER_C
from app.services.research_eod_v1.fixed_matrix import (
    MISSING_ENDPOINT,
    STATUS_INVALID,
    STATUS_VALID,
    background_basket_note,
    calendar_diff_audit,
    canonical_label_endpoint,
    capability_plan,
    classify_symbol,
    endpoint_fields,
    feature_cache_key,
    first_scoreable,
    next_day_confirm_example,
    official_allowed_sessions,
    quality_pool,
    registered_warmup,
    run_fixed_matrix,
)
from app.services.research_eod_v1.measurement import earliest_entry_session, next_day_confirm_available_at
from app.services.sectors import SECTORS


def _bar(session: date, close: float, **overrides: object) -> ResearchBar:
    payload = dict(
        security_id="AAA",
        session_date=session,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        raw_open=close,
        raw_close=close,
        volume=100.0,
        dollar_volume=close * 100.0,
        tri=close,
        price_adjustment="yahoo_unverified_raw",
    )
    payload.update(overrides)
    return ResearchBar(**payload)  # type: ignore[arg-type]


def test_official_calendar_matches_1633_and_skips_mourning_day() -> None:
    sessions = official_allowed_sessions()
    assert len(sessions) == 1633
    assert date(2018, 12, 5) not in sessions
    assert is_trading_day(date(2018, 12, 5)) is False
    spy_like = [session for session in sessions]
    audit = calendar_diff_audit(spy_like)
    assert audit["official_n"] == 1633
    assert audit["legacy_is_trading_day_without_adhoc"] == 1634
    assert audit["exception_2018_12_05"]["official_closed"] is True
    assert audit["exception_2018_12_05"]["do_not_invent_missing_bar"] is True


def test_missing_quote_does_not_move_canonical_t20() -> None:
    signal = date(2024, 4, 1)
    assert canonical_label_endpoint(signal, 20) == date(2024, 4, 29)
    assert shift_sessions(signal, 20) == date(2024, 4, 29)
    indexed = {
        date(2024, 4, 1): _bar(date(2024, 4, 1), 10.0),
        date(2024, 4, 29): _bar(date(2024, 4, 29), 11.0),
        date(2024, 4, 30): _bar(date(2024, 4, 30), 12.0),
    }
    fields = endpoint_fields(indexed, signal, 20)
    assert fields["endpoint"] == "2024-04-29"
    assert fields["quote_available"] is True
    missing = endpoint_fields({date(2024, 4, 1): _bar(date(2024, 4, 1), 10.0)}, signal, 20)
    assert missing["endpoint"] == "2024-04-29"
    assert missing["reason"] == MISSING_ENDPOINT
    assert missing["do_not_move_endpoint"] is True


def test_malformed_and_nonfinite_bars_are_invalid() -> None:
    bad = _bar(date(2024, 4, 1), 10.0, high=9.0, low=8.0)
    assert classify_symbol([bad])["status"] == STATUS_INVALID
    with pytest.raises(ValueError, match="OHLC"):
        validate_research_bars([bad])
    nan = _bar(date(2024, 4, 1), float("nan"))
    assert classify_symbol([nan])["status"] == STATUS_INVALID
    good = [_bar(date(2024, 4, 1), 10.0), _bar(date(2024, 4, 2), 10.5)]
    pool = quality_pool({"SPY": good, "BAD": [bad]})
    assert pool["per_security"]["SPY"]["status"] == STATUS_VALID
    assert pool["per_security"]["BAD"]["status"] == STATUS_INVALID
    assert pool["valid_n"] == 1
    assert pool["isolated_invalid_does_not_fail_pool"] is True
    assert pool["execution_gates"] == "EXECUTION_GATES_UNVERIFIED"


def test_registered_warmup_is_not_the_old_readiness_constants() -> None:
    assert registered_warmup("A_trend_quality") == 252
    assert registered_warmup("B_confirmed_base_breakout") == 252
    assert registered_warmup("C_trend_pullback") == 252
    assert registered_warmup("D_residual_momentum") == 330
    sessions = trading_sessions(date(2018, 1, 2), date(2024, 6, 28))
    assert first_scoreable(sessions, 252) == sessions[251]
    assert first_scoreable(sessions, 330) == sessions[329]


def test_capability_plan_has_864_and_keeps_price_only() -> None:
    plan = capability_plan({"per_security": {}})
    assert len(plan) == 864
    assert {row["theme"] for row in plan} == set(SECTORS)
    assert all(row["eligible_nav"] is False for row in plan)
    assert all(row["layers"][LAYER_C] == "blocked" for row in plan)
    d_rows = [row for row in plan if row["family"] == "D_residual_momentum"]
    others = [row for row in plan if row["family"] != "D_residual_momentum"]
    assert all(row["track"].endswith("DIAGNOSTIC") for row in d_rows)
    assert all(row["track"] == PRICE_ONLY_DIAGNOSTIC for row in others)
    shorts = [row for row in plan if row["score_horizon"] == "short"]
    assert all("SCORE_HORIZON_FEATURES_NOT_IN_B0" in row["unexecuted_reasons"] for row in shorts)


def test_feature_cache_key_includes_score_horizon() -> None:
    mid = feature_cache_key(session=date(2024, 4, 1), security_id="TSLA", score_horizon="mid", feature_version="v")
    short = feature_cache_key(session=date(2024, 4, 1), security_id="TSLA", score_horizon="short", feature_version="v")
    assert mid != short


def test_next_day_confirm_is_not_the_overnight_gap() -> None:
    example = next_day_confirm_example(date(2024, 4, 1))
    assert example["descriptive_overnight_endpoint"] == "2024-04-02"
    assert example["actual_helper_earliest_entry"] == "2024-04-03"
    available = next_day_confirm_available_at(date(2024, 4, 1))
    assert earliest_entry_session(date(2024, 4, 1), available) == date(2024, 4, 3)
    note = background_basket_note()
    assert note["not_candidate_strategy_return"] is True
    assert note["left_tail_p05"]["index"] == 0
    assert "not a time-series portfolio VaR" in note["left_tail_p05"]["meaning"]


def test_run_fixed_matrix_without_yahoo_still_plans_864() -> None:
    result = run_fixed_matrix(root=Path("/tmp/empty-fixed-matrix"), load_yahoo=False, rescore_b0=False)
    assert result["plan_n"] == 864
    assert result["calendar"]["official_n"] == 1633
    assert result["stop"]["new_weight_search"] is False
    assert result["stop"]["executed_backtests"] == 0
    assert result["engineering_pilot"]["mid_blend_not_reused"] is True
    assert result["capability_plan"][0]["layers"][LAYER_A] in {"available", "blocked"}
