from __future__ import annotations

from datetime import date

import pytest

from app.services.breakouts.daily_confirmation import (
    daily_bar_location,
    rvol_daily_20med,
    t1_checks,
    t2_holds,
)
from app.services.research.dataset import dataset_from_records
from app.services.research.path_stats import mae_mfe_from_entry
from app.services.research.timing import evaluate_t1, evaluate_t2
from app.services.strength.ranking_variants import (
    a0_family_score,
    apply_a0_mid_long,
    apply_c0_sector_quota,
    apply_research_ranking,
)


def test_a0_uses_family_scores_not_view_mix() -> None:
    row = {"ticker": "AAA", "score_mid": 80.0, "score_long": 60.0, "ranking_score": 10.0}
    assert a0_family_score(row) == 70.0
    missing = {"ticker": "BBB", "score_mid": 80.0, "score_long": None, "ranking_score": 99.0}
    assert a0_family_score(missing) is None
    ranked = apply_a0_mid_long(
        [
            {"ticker": "AAA", "score_mid": 40.0, "score_long": 40.0, "ranking_score": 99.0},
            {"ticker": "BBB", "score_mid": 90.0, "score_long": 90.0, "ranking_score": 10.0},
        ]
    )
    assert ranked[0]["ticker"] == "BBB"
    assert ranked[0]["research_ranking"] == "a0_mid_long"


def test_c0_quota_keeps_vacancies_and_unclassified_bucket() -> None:
    rows = [
        {"ticker": "A1", "ranking_score": 90, "primary_sector_id": "semiconductors"},
        {"ticker": "A2", "ranking_score": 89, "primary_sector_id": "semiconductors"},
        {"ticker": "A3", "ranking_score": 88, "primary_sector_id": "semiconductors"},
        {"ticker": "B1", "ranking_score": 70, "primary_sector_id": "software"},
        {"ticker": "U1", "ranking_score": 60, "primary_sector_id": None},
        {"ticker": "U2", "ranking_score": 59, "primary_sector_id": ""},
        {"ticker": "U3", "ranking_score": 58, "primary_sector_id": None},
    ]
    result = apply_c0_sector_quota(rows, top_k=10, max_per_sector=2)
    selected = [row["ticker"] for row in result["selected"]]
    assert selected == ["A1", "A2", "B1", "U1", "U2"]
    assert result["vacancies"] == 5
    assert any(item["ticker"] == "A3" for item in result["displaced"])
    assert all(row["selected_view_rank"] >= 11 for row in result["remainder"])
    assert "A3" not in selected


def test_research_ranking_hook_names() -> None:
    rows = [{"ticker": "ZZ", "score_mid": 10, "score_long": 10, "ranking_score": 1}]
    production = apply_research_ranking(rows, None)
    assert production[0]["selected_view_rank"] == 1
    a0 = apply_research_ranking(rows, "a0_mid_long")
    assert a0[0]["research_ranking"] == "a0_mid_long"


def test_t1_rvol_and_zero_range() -> None:
    prior = [100] * 20
    assert rvol_daily_20med(150, prior)["rvol_daily_20med"] == 1.5
    assert rvol_daily_20med(150, [100] * 19)["status"] == "unavailable"
    assert rvol_daily_20med(150, [0] * 20)["status"] == "unavailable"
    loc = daily_bar_location(10, 10, 10, 10)
    assert loc["zero_range"] is True
    assert loc["clv"] is None
    judged = t1_checks(clv=0.8, rvol=1.6, upper_shadow=0.1, distance_atr=0.3)
    assert judged["confirmed"] is True
    weak = t1_checks(clv=0.5, rvol=1.6, upper_shadow=0.1, distance_atr=0.3)
    assert weak["confirmed"] is False


def test_t2_uses_frozen_buffer_and_both_closes() -> None:
    held = t2_holds(close_t=12, close_t1=12.2, resistance_high=10, buffer=0.5)
    assert held["confirmed"] is True
    lost = t2_holds(close_t=12, close_t1=10.2, resistance_high=10, buffer=0.5)
    assert lost["confirmed"] is False
    assert lost["hold_t"] is True


def _bars(ticker: str, start: date, count: int, *, close: float, volume: float = 1000) -> list[dict]:
    records = []
    cursor = start
    made = 0
    price = close
    while made < count:
        if cursor.weekday() < 5:
            records.append(
                {
                    "ticker": ticker,
                    "date": cursor.isoformat(),
                    "open": price - 0.2,
                    "high": price + 1.0,
                    "low": price - 0.2,
                    "close": price,
                    "adj_close": price,
                    "volume": volume,
                }
            )
            made += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return records


def test_t1_t2_dataset_clocks() -> None:
    start = date(2019, 1, 2)
    records = _bars("AAA", start, 40, close=20.0, volume=2000)
    # Last two sessions: strong T, then still-held T+1.
    records[-2]["high"] = 22.0
    records[-2]["low"] = 19.5
    records[-2]["open"] = 19.6
    records[-2]["close"] = 21.8
    records[-2]["adj_close"] = 21.8
    records[-2]["volume"] = 5000
    records[-1]["close"] = 21.9
    records[-1]["adj_close"] = 21.9
    dataset = dataset_from_records(records, dataset_id="t", source="fixture")
    event = {
        "ticker": "AAA",
        "trading_date": records[-2]["date"],
        "resistance_high": 20.0,
    }
    t1 = evaluate_t1(dataset, event)
    t2 = evaluate_t2(dataset, event)
    assert t1["executable_from"] == "T+1 open"
    assert t2["executable_from"] == "T+2 open"
    assert t2["information_ready"] == records[-1]["date"]
    assert t2["resistance_high_frozen"] == 20.0


def test_f1_drops_negative_rs_and_missing() -> None:
    from app.services.research.followups import apply_f1_rs_filter

    result = apply_f1_rs_filter(
        [
            {"ticker": "POS", "ranking_score": 50, "rs_spy_63d": 0.1},
            {"ticker": "NEG", "ranking_score": 90, "rs_spy_63d": -0.2},
            {"ticker": "MISS", "ranking_score": 80, "rs_spy_63d": None},
        ]
    )
    assert [row["ticker"] for row in result["rows"]] == ["POS"]
    assert result["missing_rs"] == 1
    assert result["coverage"] == 1 / 3


def test_percentile_requires_dispersion() -> None:
    from app.services.research.followups import xs_percentile

    assert xs_percentile([1.0] * 10, 1.0) is None
    assert xs_percentile([1.0, 2.0], 2.0) is None
    values = [float(i) for i in range(10)]
    assert xs_percentile(values, 0.0) == 0.0
    assert xs_percentile(values, 9.0) == 100.0


def test_structure_lookback_keeps_trigger_identity() -> None:
    from app.services.research.radar import reconstruct_ticker_dates
    from tests.test_research_screener_radar import _synth_dataset

    dataset = _synth_dataset()
    dates = [date(2019, 3, day) for day in (18, 19, 20, 21, 22, 25)]
    full = reconstruct_ticker_dates(dataset, "AAA", dates)
    short = reconstruct_ticker_dates(dataset, "AAA", dates, max_lookback=120)
    full_keys = {
        (event["ticker"], event["trading_date"], event["pivot_id"], event["resistance_high"])
        for event in full["events"]
    }
    short_keys = {
        (event["ticker"], event["trading_date"], event["pivot_id"], event["resistance_high"])
        for event in short["events"]
    }
    assert full_keys == short_keys


def test_mae_excludes_exit_day_extremes() -> None:
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
        },
        {
            "ticker": "AAA",
            "date": "2019-03-05",
            "open": 10.5,
            "high": 20.0,
            "low": 1.0,
            "close": 10.5,
            "adj_close": 10.5,
            "volume": 1,
        },
    ]
    dataset = dataset_from_records(records, dataset_id="p", source="fixture")
    path = mae_mfe_from_entry(dataset, "AAA", date(2019, 3, 4), date(2019, 3, 5), entry_price=10.0)
    assert path["status"] == "active"
    assert path["mae"] == pytest.approx(-0.01)  # entry-day low only
    assert path["mfe"] == pytest.approx(0.05)  # exit open, not the 20 high
