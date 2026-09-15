from __future__ import annotations

from datetime import date

import pytest

from app.services.research.compare import (
    aligned_rank_ic,
    named_tail_stats,
    row_aligned_pairs,
    top10_identity,
    top_set_stats,
)
from app.services.research.dataset import dataset_from_records
from app.services.research.metrics import event_equal_calendar_block_ci, summarize_return_targets
from app.services.research.path_stats import mae_mfe_from_entry
from app.services.research.portfolio import simulate_c1_sector_budget, simulate_long_only
from app.services.research.t1_priority import compare_t1_priority, rank_same_day, t1_confirmed
from app.services.research.t2_wait import compare_t2_wait_paths
from app.services.strength.ranking_variants import apply_a0_mid_long


def _row(ticker: str, score: float, outcome: float, **extra: object) -> dict:
    payload = {
        "ticker": ticker,
        "ranking_score": score,
        "signal_date": "2019-03-04",
        "excess": {"20": {"excess_vs_universe": outcome}},
        "labels": {
            "20": {
                "status": "active",
                "start_date": "2019-03-04",
                "end_date": "2019-04-01",
                "forward_return": extra.pop("raw", outcome),
            }
        },
    }
    payload.update(extra)
    return payload


def test_m1_a0_ic_uses_same_row_identity() -> None:
    items = [
        _row("AAA", 90, 0.01, score_mid=10, score_long=10),
        _row("BBB", 10, 0.09, score_mid=90, score_long=90),
        _row("CCC", 50, 0.05, score_mid=50, score_long=50),
        _row("DDD", 40, 0.04, score_mid=40, score_long=40),
        _row("EEE", 30, 0.03, score_mid=30, score_long=30),
    ]
    a0 = apply_a0_mid_long(items)
    aligned = aligned_rank_ic(a0, "candidate_score")
    assert a0[0]["ticker"] == "BBB"
    assert aligned["status"] == "active"
    assert aligned["ic"] == pytest.approx(1.0)
    shuffled = list(reversed(a0))
    assert aligned_rank_ic(shuffled, "candidate_score")["ic"] == pytest.approx(aligned["ic"])
    changed = [dict(row) for row in a0]
    for row in changed:
        if row["ticker"] == "BBB":
            row["excess"] = {"20": {"excess_vs_universe": -0.50}}
    pairs_before = {ticker: (score, outcome) for ticker, score, outcome in row_aligned_pairs(a0, "candidate_score")}
    pairs_after = {ticker: (score, outcome) for ticker, score, outcome in row_aligned_pairs(changed, "candidate_score")}
    assert pairs_before["AAA"] == pairs_after["AAA"]
    assert pairs_before["BBB"] != pairs_after["BBB"]
    assert aligned_rank_ic(changed, "candidate_score")["ic"] < aligned["ic"]


def test_m1_perfect_positive_ic_is_one() -> None:
    rows = [_row(f"T{i}", float(i), float(i)) for i in range(1, 8)]
    result = aligned_rank_ic(rows, "ranking_score")
    assert result["ic"] == pytest.approx(1.0)


def test_m1_top10_identity_is_ticker_order() -> None:
    days = {
        "2019-03-04": [
            {**_row("BBB", 1, 0.1), "selected_view_rank": 2},
            {**_row("AAA", 2, 0.2), "selected_view_rank": 1},
        ]
    }
    assert top10_identity(days, k=2) == [("2019-03-04", ("AAA", "BBB"))]


def test_m2_three_tail_definitions() -> None:
    daily = []
    for idx, worst in enumerate((-0.40, -0.10, 0.00, 0.05)):
        names = [_row(f"A{idx}", 1, 0.0, raw=worst), _row(f"B{idx}", 1, 0.0, raw=0.20)]
        for rank, row in enumerate(names, start=1):
            row["selected_view_rank"] = rank
        daily.append({"original": top_set_stats(names, k=2)})
    tails = named_tail_stats(daily, "original")
    assert tails["daily_worst_name_mean"]["hurdle_random_variable"] is True
    assert tails["daily_worst_name_mean"]["mean"] == pytest.approx((-0.40 - 0.10 + 0.00 + 0.05) / 4)
    assert tails["pooled_stock_date_worst5pct"]["n_stock_dates"] == 8
    assert tails["daily_ew_portfolio_worst5pct"]["n_days"] == 4
    assert tails["not_max_drawdown"] is True
    assert tails["pooled_stock_date_worst5pct"]["mean"] != tails["daily_worst_name_mean"]["mean"]


def test_m3_event_and_date_means_are_separate() -> None:
    values = [
        ("2019-03-04", 0.10),
        ("2019-03-04", 0.10),
        ("2019-03-05", -0.08),
    ]
    summary = summarize_return_targets(values, horizon_days=2)
    assert summary["event_equal"]["mean"] == pytest.approx((0.10 + 0.10 - 0.08) / 3)
    assert summary["date_equal"]["mean"] == pytest.approx((0.10 - 0.08) / 2)
    assert summary["empty_trading_days_are_not_zero_events"] is True
    ci = event_equal_calendar_block_ci(values, horizon_days=2, n_bootstrap=50)
    assert ci["target"] == "event_equal"
    assert ci["n_empty_trading_days"] >= 0


def test_m5_skip_zero_is_selection_contribution_name() -> None:
    from app.services.research.radar_compare import evaluate_timing_variant

    records = []
    cursor = date(2019, 3, 4)
    for _ in range(40):
        if cursor.weekday() < 5:
            records.append(
                {
                    "ticker": "AAA",
                    "date": cursor.isoformat(),
                    "open": 10.0,
                    "high": 10.2,
                    "low": 9.8,
                    "close": 10.0,
                    "adj_close": 10.0,
                    "volume": 1000,
                }
            )
        cursor = date.fromordinal(cursor.toordinal() + 1)
    dataset = dataset_from_records(records, dataset_id="m5", source="fixture")
    events = [
        {
            "ticker": "AAA",
            "trading_date": "2019-03-04",
            "t1": {"status": "active", "confirmed": False},
        }
    ]
    result = evaluate_timing_variant(dataset, events, variant_key="t1", entry_lag=1)
    assert "selection_contribution_on_original_opportunity_set" in result
    assert result["overall_skip_as_zero"]["n"] == result["selection_contribution_on_original_opportunity_set"]["n"]
    note = " ".join(result["notes"])
    assert "cash-versus-fully-invested" in note


def _weekday_records(ticker: str, start: date, count: int, *, close: float, open_: float | None = None) -> list[dict]:
    records = []
    cursor = start
    made = 0
    price = close
    while made < count:
        if cursor.weekday() < 5:
            session_open = price if open_ is None else open_
            records.append(
                {
                    "ticker": ticker,
                    "date": cursor.isoformat(),
                    "open": session_open,
                    "high": session_open + 1.0,
                    "low": session_open - 1.0,
                    "close": price,
                    "adj_close": price,
                    "volume": 1_000_000,
                }
            )
            made += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return records


def test_c1_blocks_third_same_sector_using_existing_book() -> None:
    start = date(2019, 3, 4)
    records = []
    for ticker in ("AAA", "BBB", "CCC", "DDD"):
        records.extend(_weekday_records(ticker, start, 40, close=10.0, open_=10.0))
    dataset = dataset_from_records(records, dataset_id="c1", source="fixture")
    signals = [
        {"ticker": "AAA", "signal_date": "2019-03-04", "selected_view_rank": 1, "primary_sector_id": "chips"},
        {"ticker": "BBB", "signal_date": "2019-03-04", "selected_view_rank": 2, "primary_sector_id": "chips"},
        {"ticker": "CCC", "signal_date": "2019-03-04", "selected_view_rank": 3, "primary_sector_id": "chips"},
        {"ticker": "DDD", "signal_date": "2019-03-05", "selected_view_rank": 1, "primary_sector_id": "chips"},
    ]
    result = simulate_c1_sector_budget(
        dataset,
        signals,
        cost_bps=0.0,
        hold_days=20,
        initial_cash=100_000,
        max_positions=10,
        max_weight=0.10,
        sector_notional_cap=0.20,
    )
    buys = [trade for trade in result["trades"] if trade["action"] == "buy"]
    bought = {trade["ticker"] for trade in buys}
    assert {"AAA", "BBB"} <= bought
    assert "CCC" not in bought
    assert "DDD" not in bought
    assert any(row.get("reason") == "sector_budget" for row in result["rejected"])


def test_c1_freezes_shares_on_prior_close_not_fill_open() -> None:
    start = date(2019, 3, 4)
    records = _weekday_records("AAA", start, 40, close=10.0, open_=10.0)
    records.extend(_weekday_records("BBB", start, 40, close=10.0, open_=10.0))
    dataset = dataset_from_records(records, dataset_id="c1gap", source="fixture")
    fill = date(2019, 3, 5)
    dataset.by_ticker["AAA"][fill]["open"] = 30.0
    dataset.by_ticker["AAA"][fill]["adj_open"] = 30.0
    signals = [
        {"ticker": "AAA", "signal_date": "2019-03-04", "selected_view_rank": 1, "primary_sector_id": "chips"},
    ]
    result = simulate_c1_sector_budget(
        dataset,
        signals,
        cost_bps=0.0,
        hold_days=5,
        initial_cash=100_000,
        max_weight=0.10,
        sector_notional_cap=0.20,
    )
    buy = [trade for trade in result["trades"] if trade["action"] == "buy"][0]
    assert buy["plan_price"] == pytest.approx(10.0)
    assert buy["frozen_shares"] == 1000
    assert buy["shares"] == 1000
    assert buy["quote_open"] == pytest.approx(30.0)
    assert result["sector_overruns"]


def test_c1_does_not_resize_after_seeing_open() -> None:
    start = date(2019, 3, 4)
    records = _weekday_records("AAA", start, 40, close=10.0, open_=10.0)
    dataset = dataset_from_records(records, dataset_id="c1hold", source="fixture")
    fill = date(2019, 3, 5)
    dataset.by_ticker["AAA"][fill]["open"] = 20.0
    dataset.by_ticker["AAA"][fill]["adj_open"] = 20.0
    result = simulate_c1_sector_budget(
        dataset,
        [{"ticker": "AAA", "signal_date": "2019-03-04", "selected_view_rank": 1, "primary_sector_id": "chips"}],
        cost_bps=0.0,
        hold_days=5,
        initial_cash=100_000,
        max_weight=0.10,
        sector_notional_cap=0.20,
    )
    buy = [trade for trade in result["trades"] if trade["action"] == "buy"][0]
    assert buy["shares"] == buy["frozen_shares"]


def test_gross_cap_80_is_separate_from_c1() -> None:
    start = date(2019, 3, 4)
    records = []
    for ticker in ("AAA", "BBB"):
        records.extend(_weekday_records(ticker, start, 30, close=10.0, open_=10.0))
    dataset = dataset_from_records(records, dataset_id="g80", source="fixture")
    signals = [
        {"ticker": "AAA", "signal_date": "2019-03-04", "selected_view_rank": 1},
        {"ticker": "BBB", "signal_date": "2019-03-04", "selected_view_rank": 2},
    ]
    result = simulate_long_only(
        dataset,
        signals,
        cost_bps=0.0,
        hold_days=5,
        initial_cash=100_000,
        max_positions=10,
        max_weight=0.10,
        max_gross_exposure=0.10,
    )
    buys = [trade for trade in result["trades"] if trade["action"] == "buy"]
    assert len(buys) == 1


def test_t1_priority_reorders_but_fills_empty_slots() -> None:
    events = [
        {"ticker": "WEAK", "trading_date": "2019-03-04", "volume": 9_000_000, "t1": {"status": "active", "confirmed": False}},
        {"ticker": "STRONG", "trading_date": "2019-03-04", "volume": 1_000_000, "t1": {"status": "active", "confirmed": True}},
        {"ticker": "MID", "trading_date": "2019-03-04", "volume": 5_000_000, "t1": {"status": "active", "confirmed": False}},
    ]
    raw = rank_same_day(events, t1_first=False)
    prio = rank_same_day(events, t1_first=True)
    assert raw[0]["ticker"] == "WEAK"
    assert prio[0]["ticker"] == "STRONG"
    assert [row["ticker"] for row in prio[:3]] == ["STRONG", "WEAK", "MID"]
    assert t1_confirmed(prio[0]) is True


def test_t1_priority_does_not_claim_production_score() -> None:
    start = date(2019, 3, 4)
    records = _weekday_records("AAA", start, 40, close=10.0, open_=10.0)
    dataset = dataset_from_records(records, dataset_id="t1p", source="fixture")
    events = [
        {
            "ticker": "AAA",
            "trading_date": "2019-03-04",
            "volume": 1000,
            "t1": {"status": "active", "confirmed": True},
            "t2": {"status": "active", "confirmed": True, "hold_t1": True},
        }
    ]
    result = compare_t1_priority(dataset, events, k=3)
    assert "not live ranking" in result["rank_proxy"]["not_production"]
    assert result["default_k"] == 3
    assert "1" in result["by_k"] and "5" in result["by_k"]


def test_m4_t2_wait_keeps_two_clocks_and_complete_mae() -> None:
    start = date(2019, 3, 4)
    records = _weekday_records("AAA", start, 40, close=10.0, open_=10.0)
    dataset = dataset_from_records(records, dataset_id="t2w", source="fixture")
    events = [
        {
            "ticker": "AAA",
            "trading_date": "2019-03-04",
            "t2": {"status": "active", "confirmed": True, "hold_t": True, "hold_t1": True},
        }
    ]
    result = compare_t2_wait_paths(dataset, events)
    assert result["no_backfill_earlier_fill"] is True
    assert "own_20d_clock" in result
    assert "common_exit_clock" in result
    assert result["mae_mfe"]["incomplete_excluded_from_complete_mean"] is True
    assert "economic" in result["failure_definitions"]["note"]


def test_mae_incomplete_not_mixed_into_complete_mean() -> None:
    start = date(2019, 3, 4)
    records = [
        {
            "ticker": "AAA",
            "date": "2019-03-04",
            "open": 10,
            "high": 10.1,
            "low": 9.9,
            "close": 10,
            "adj_close": 10,
            "volume": 1,
        }
    ]
    dataset = dataset_from_records(records, dataset_id="mae", source="fixture")
    path = mae_mfe_from_entry(dataset, "AAA", date(2019, 3, 4), date(2019, 3, 5), entry_price=10.0)
    assert path["status"] == "incomplete"
