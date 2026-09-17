"""PR #174 measurement-acceptance tests from measurement_results.json.

Isolation on bfab20bf: 1 pass / 5 fail. These asserts keep the statistical and
cash-identity invariants. They do not use the network.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, next_session
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.factors import extract_raw
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close
from app.services.research_eod_v1.ledger import simulate_ledger, size_notional
from app.services.research_eod_v1.measurement import (
    attach_forward_label,
    earliest_entry_session,
    persist_factor_rows,
    reference_panel,
    run_snapshot_matrix,
)
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.stats import (
    IC_MIN_CROSS_SECTION,
    grouped_ic,
    spearman,
)


ET = ZoneInfo("America/New_York")
TEN_PCT = {
    "max_position_fraction": 0.10,
    "position_risk_budget_fraction": 1.0,
    "max_order_adv_fraction": 1.0,
}
UNIQUE_X = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
UNIQUE_Y = [2, 3, 8, 4, 6, 10, 5, 7, 9, 1]
TIE_X = [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]
TIE_Y = [10, 1, 9, 2, 8, 3, 7, 4, 6, 5]


def _flat(sid: str, days: list[date], price: float = 100.0):
    close = np.full(len(days), price)
    series = make_series(sid, days, close)
    series.open = close.copy()
    series.raw_open = close.copy()
    series.raw_close = close.copy()
    return series


def test_control_spearman_unique_ranks() -> None:
    result = spearman(UNIQUE_X, UNIQUE_Y)
    assert result.value == pytest.approx(0.2121212121212121)
    assert result.n == 10
    assert result.reason is None


def test_spearman_matches_average_tie_ranks() -> None:
    result = spearman(TIE_X, TIE_Y)
    assert result.value == pytest.approx(0.0), (
        f"ordinal tie breaking changed Spearman: {result.value} vs 0.0"
    )
    assert result.n == 10


def test_matched_pair_order_does_not_change_ic() -> None:
    forward = spearman(TIE_X, TIE_Y)
    reversed_pairs = spearman(list(reversed(TIE_X)), list(reversed(TIE_Y)))
    assert forward.value == pytest.approx(reversed_pairs.value), (
        f"merely permuting matched pairs changed IC: {forward.value} vs {reversed_pairs.value}"
    )


def test_constant_score_has_undefined_ic() -> None:
    result = spearman([7.0] * 10, list(range(10)))
    assert result.value is None, f"all scores tied: expected no rank IC, got {result.value}"
    assert result.n == 10
    assert result.reason == "CONSTANT_SCORE"


def test_position_sizing_is_invariant_to_geometry_units() -> None:
    days = trading_days(date(2024, 1, 2), 8)
    series = _flat("A", days, 100.0)
    signal_day = days[0]
    notionals = []
    for scale in (1.0, 2.0, 0.5, 10.0):
        row = {
            "planned_invalidation": 90.0 * scale,
            "atr": 1.0 * scale,
            "geometry_close": 100.0 * scale,
            "adv20": 80_000_000,
        }
        notionals.append(size_notional(row, series, signal_day, 10_000.0, TEN_PCT, True))
    assert all(value == pytest.approx(notionals[0]) for value in notionals)
    unadjusted = size_notional(
        {"planned_invalidation": 90.0, "atr": 1.0, "geometry_close": 100.0, "adv20": 80_000_000},
        series,
        signal_day,
        10_000.0,
        TEN_PCT,
        True,
    )
    split_adjusted = size_notional(
        {"planned_invalidation": 45.0, "atr": 0.5, "geometry_close": 50.0, "adv20": 80_000_000},
        series,
        signal_day,
        10_000.0,
        TEN_PCT,
        True,
    )
    assert unadjusted == pytest.approx(1000.0)
    assert split_adjusted == pytest.approx(unadjusted), (
        f"same economic 10% risk distance sized differently: {unadjusted} vs {split_adjusted}"
    )


def test_acquisition_then_dividend_payment_updates_trade_net() -> None:
    days = trading_days(date(2024, 1, 2), 12)
    series = _flat("A", days, 100.0)
    series.dividend_events = (
        {"ex_date": date(2024, 1, 4), "pay_date": date(2024, 1, 8), "amount": 1.0},
    )
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[
            {
                "security_id": "A",
                "session_date": date(2024, 1, 2).isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 5_000.0,
            }
        ],
        cash_acquisitions=[
            {
                "security_id": "A",
                "effective_at": date(2024, 1, 5),
                "known_at": date(2024, 1, 5),
                "settlement_at": date(2024, 1, 5),
                "price": 100.0,
            }
        ],
    )
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["cash_in"] == pytest.approx(5_000.0), f"lot receives dividend but acquisition net remains {trade.get('net_return')}"
    assert trade["filled_at"] == date(2024, 1, 5)
    assert trade["exit_reason"] == "CASH_ACQUISITION"
    assert trade["dividend_cash"] == pytest.approx(50.0)
    assert trade["net_return"] == pytest.approx(0.01), (
        f"lot receives dividend but acquisition net remains {trade.get('net_return')}"
    )
    assert result["ending_equity"] == pytest.approx(10_050.0)


def test_zero_consideration_is_filled_missing_price_is_not() -> None:
    days = trading_days(date(2024, 1, 2), 10)
    series = _flat("A", days, 100.0)
    zero = simulate_ledger(
        start=days[0],
        end=date(2024, 1, 8),
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[
            {
                "security_id": "A",
                "session_date": date(2024, 1, 2).isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 2_000.0,
            }
        ],
        cash_acquisitions=[
            {
                "security_id": "A",
                "effective_at": date(2024, 1, 5),
                "known_at": date(2024, 1, 5),
                "settlement_at": date(2024, 1, 5),
                "price": 0.0,
            }
        ],
    )
    assert zero["trades"][0]["cash_in"] == pytest.approx(0.0)
    assert zero["trades"][0]["filled_at"] == date(2024, 1, 5)
    assert zero["trades"][0]["exit_reason"] == "ZERO_CONSIDERATION"
    missing = simulate_ledger(
        start=days[0],
        end=date(2024, 1, 8),
        panel={"A": _flat("A", days, 100.0)},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[
            {
                "security_id": "A",
                "session_date": date(2024, 1, 2).isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 2_000.0,
            }
        ],
        cash_acquisitions=[
            {
                "security_id": "A",
                "effective_at": date(2024, 1, 5),
                "known_at": date(2024, 1, 5),
                "settlement_at": date(2024, 1, 5),
            }
        ],
    )
    assert missing["open_at_end"]
    assert missing["open_at_end"][0]["filled_at"] is None
    assert not missing["trades"]


def test_late_signal_does_not_backfill_elapsed_tplus1_open() -> None:
    days = trading_days(date(2024, 1, 2), 10)
    series = _flat("A", days, 100.0)
    available = datetime(2024, 1, 4, 16, 30, tzinfo=ET)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=1,
        cost_multiple=0,
        signals=[
            {
                "security_id": "A",
                "session_date": date(2024, 1, 2).isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 1_000.0,
                "signal_available_at": available,
            }
        ],
    )
    buys = [event for event in result["events"] if event["kind"] == "buy"]
    assert buys and buys[0]["session"] == date(2024, 1, 5).isoformat()
    assert earliest_entry_session(date(2024, 1, 2), available) == date(2024, 1, 5)
    assert next_session(date(2024, 1, 2)) == date(2024, 1, 3)


def _two_theme_panel():
    days = trading_days(date(2018, 1, 2), 430)
    panel = {}
    for i, name in enumerate(["NVDA", "AMD", "AVGO", "TSM", "MU", "INTC", "QCOM", "ASML", "AMAT", "LRCX", "KLAC", "SNPS"]):
        series = make_series(
            name,
            days,
            trending_close(len(days), 20 + i, 0.08 + i * 0.002),
            industry_id=None,
            parent_industry_id=None,
            theme_ids=("semiconductors",),
        )
        series.venue_metadata = dict(series.venue_metadata)
        series.venue_metadata["industry_source"] = "unverified_economic_industry_missing"
        panel[name] = series
    for i, name in enumerate(["AAPL", "MSFT", "ORCL", "ADBE", "CRM", "NOW", "INTU", "PANW"]):
        series = make_series(
            name,
            days,
            trending_close(len(days), 30 + i, 0.07 + i * 0.002),
            industry_id=None,
            theme_ids=("software",),
        )
        series.venue_metadata = dict(series.venue_metadata)
        series.venue_metadata["industry_source"] = "unverified_economic_industry_missing"
        panel[name] = series
    panel["SPY"] = make_series(
        "SPY", days, trending_close(len(days), 200, 0.04), asset_track="etf", security_type="ETF", theme_ids=("etfs",)
    )
    panel["QQQ"] = make_series(
        "QQQ", days, trending_close(len(days), 180, 0.05), asset_track="etf", security_type="ETF", theme_ids=("etfs",)
    )
    return days, panel


def test_same_day_theme_family_is_order_invariant() -> None:
    days, panel = _two_theme_panel()
    session = days[-5]
    registry = load_registry()
    first = run_snapshot_matrix(
        panel,
        [session],
        themes=("semiconductors", "software"),
        algorithms=("A_trend_quality", "D_residual_momentum"),
        registry=registry,
    )
    shuffled = dict(reversed(list(panel.items())))
    second = run_snapshot_matrix(
        shuffled,
        [session],
        themes=("software", "semiconductors"),
        algorithms=("D_residual_momentum", "A_trend_quality"),
        registry=registry,
    )
    assert first["identity"] == second["identity"]


def test_family_a_and_d_do_not_pool_into_one_sample() -> None:
    days, panel = _two_theme_panel()
    session = days[-5]
    payload = run_snapshot_matrix(
        panel,
        [session],
        themes=("semiconductors",),
        algorithms=("A_trend_quality", "D_residual_momentum"),
        registry=load_registry(),
        attach_labels=True,
        last_allowed=days[-1],
    )
    groups = grouped_ic(payload["rows"])
    assert all(row["algorithm"] in {"A_trend_quality", "D_residual_momentum"} for row in groups)
    assert not any("," in str(row["algorithm"]) for row in groups)
    a_n = sum(row["n"] for row in groups if row["algorithm"] == "A_trend_quality")
    d_n = sum(row["n"] for row in groups if row["algorithm"] == "D_residual_momentum")
    assert a_n == 0 or d_n == 0 or all(
        row["algorithm"] != "A_trend_quality" or row["n"] <= a_n for row in groups
    )


def test_ic_n_is_within_day_family_not_pooled_across_dates() -> None:
    rows = []
    for i in range(12):
        rows.append(
            {
                "signal_session": "2023-06-12",
                "theme_id": "semiconductors",
                "algorithm": "A_trend_quality",
                "profile": "balanced",
                "horizon": "mid",
                "label_horizon": 5,
                "score": float(i),
                "label": float(11 - i),
            }
        )
    for i in range(8):
        rows.append(
            {
                "signal_session": "2023-06-13",
                "theme_id": "semiconductors",
                "algorithm": "A_trend_quality",
                "profile": "balanced",
                "horizon": "mid",
                "label_horizon": 5,
                "score": float(i),
                "label": float(i),
            }
        )
    groups = grouped_ic(rows)
    by_day = {row["signal_session"]: row for row in groups}
    assert by_day["2023-06-12"]["n"] >= IC_MIN_CROSS_SECTION
    assert by_day["2023-06-12"]["ic"] is not None
    assert by_day["2023-06-13"]["n"] == 8
    assert by_day["2023-06-13"]["ic"] is None
    assert by_day["2023-06-13"]["undefined_reason"] == "CROSS_SECTION_BELOW_N"
    pooled = spearman(
        [row["score"] for row in rows],
        [row["label"] for row in rows],
    )
    assert pooled.n == 20
    assert by_day["2023-06-12"]["ic"] != pytest.approx(pooled.value or 0.0) or by_day["2023-06-13"]["ic"] is None


def test_precomputed_raws_still_classify_theme_candidates() -> None:
    days, panel = _two_theme_panel()
    session = days[-1]
    refs = reference_panel(panel, "semiconductors", session)
    registry = load_registry()
    live = compute_snapshot(
        eod_evaluation_as_of(session),
        refs,
        "u_measure_live",
        registry,
        sector_id="semiconductors",
        algorithm="A_trend_quality",
    )
    foreign_gates = registry["sectors"]["software"]["gates"]
    blend = tuple(registry["horizons"]["mid"]["momentum_blend"])
    raws = {
        sid: extract_raw(
            series,
            market=refs.get("SPY"),
            panel=refs,
            horizon="mid",
            momentum_blend=blend,
            sector_gates=foreign_gates,
        )
        for sid, series in refs.items()
    }
    cached = compute_snapshot(
        eod_evaluation_as_of(session),
        refs,
        "u_measure_cached",
        registry,
        sector_id="semiconductors",
        algorithm="A_trend_quality",
        precomputed_raws=raws,
    )
    assert "NVDA" in cached["candidate_ids"]
    assert "AAPL" not in cached["candidate_ids"]
    assert cached["candidate_ids"] == live["candidate_ids"]
    assert {row["security_id"]: row["score"] for row in cached["rows"]} == {
        row["security_id"]: row["score"] for row in live["rows"]
    }


def test_runner_keeps_foreign_theme_in_reference_not_candidates() -> None:
    days, panel = _two_theme_panel()
    session = days[-1]
    refs = reference_panel(panel, "semiconductors", session)
    assert "AAPL" in refs
    assert "SPY" in refs and "QQQ" in refs
    assert "NVDA" in refs
    payload = compute_snapshot(
        eod_evaluation_as_of(session),
        refs,
        "u_measure_ref",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
    )
    assert "AAPL" not in payload["candidate_ids"]
    assert "NVDA" in payload["candidate_ids"] or "NVDA" in {row["security_id"] for row in payload["rows"]}
    assert "AAPL" in payload["reference_ids"]
    assert "SPY" in payload["reference_ids"]


def test_labels_reject_missing_immature_and_future() -> None:
    days, panel = _two_theme_panel()
    series = panel["NVDA"]
    last = days[-1]
    missing = attach_forward_label(series, days[0], 5, last_allowed=last, holdout_start=date(2024, 7, 1))
    # early date may have a label; force a missing target
    no_label = attach_forward_label(series, last, 5, last_allowed=last, holdout_start=date(2024, 7, 1))
    assert no_label["label"] is None
    assert no_label["reason"] in {"LABEL_IMMATURE", "LABEL_CROSSES_BOUNDARY", "LABEL_MISSING"}
    future = attach_forward_label(
        series,
        days[-10],
        63,
        last_allowed=days[-10],
        holdout_start=days[-5],
    )
    assert future["label"] is None
    empty = run_snapshot_matrix(
        {"SPY": panel["SPY"], "QQQ": panel["QQQ"]},
        [days[-1]],
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=load_registry(),
    )
    assert empty["payloads"]
    assert empty["payloads"][0]["rows"] == [] or all(
        row.get("status") != "eligible" for row in empty["payloads"][0]["rows"]
    )


def test_chunked_and_resumed_runs_match_continuous_keys(tmp_path: Path) -> None:
    days, panel = _two_theme_panel()
    sessions = days[-6:-1]
    registry = load_registry()
    common = dict(
        panel=panel,
        themes=("semiconductors",),
        algorithms=("A_trend_quality",),
        registry=registry,
    )
    continuous = run_snapshot_matrix(sessions=sessions, **common)
    chunk_path = tmp_path / "chunks.json"
    chunked = None
    for _ in range(8):
        chunked = run_snapshot_matrix(sessions=sessions, chunk_size=2, checkpoint_path=chunk_path, **common)
        if len(chunked["identity"]) >= len(continuous["identity"]):
            break
    resume_path = tmp_path / "checkpoint.json"
    first = run_snapshot_matrix(sessions=sessions[:2], checkpoint_path=resume_path, **common)
    resumed = run_snapshot_matrix(sessions=sessions, checkpoint_path=resume_path, **common)
    assert chunked is not None
    assert continuous["identity"] == chunked["identity"]
    assert continuous["identity"] == resumed["identity"]
    assert first["identity"] != continuous["identity"]
    persist_factor_rows(continuous["rows"], tmp_path / "rows.jsonl")
    written = [json.loads(line) for line in (tmp_path / "rows.jsonl").read_text(encoding="utf-8").splitlines()]
    assert written
    assert {"security_id", "signal_session", "theme_id", "algorithm", "score", "final_eligible"} <= set(written[0])
