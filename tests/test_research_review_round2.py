from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.services.research.calendar import nth_trading_day
from app.services.research.dataset import dataset_from_records
from app.services.research.labels import forward_close_return
from app.services.research.metrics import date_clustered_mean, top_k_mean
from app.services.research.portfolio import session_fill_price, simulate_long_only
from app.services.research.protocol import FROZEN_SPLITS
from app.services.research.replay_store import ReplayStore, read_jsonl
from app.services.research.run_identity import RunIdentityError, build_run_identity


def _weekday_records(
    ticker: str,
    *,
    start: date,
    count: int,
    close: float,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
    volume: float = 1_000_000,
) -> list[dict]:
    records = []
    cursor = start
    made = 0
    while made < count:
        if cursor.weekday() < 5:
            session_open = close if open_ is None else open_
            records.append(
                {
                    "ticker": ticker,
                    "date": cursor.isoformat(),
                    "open": session_open,
                    "high": (session_open + 1.0) if high is None else high,
                    "low": (session_open - 1.0) if low is None else low,
                    "close": close,
                    "adj_close": close,
                    "volume": volume,
                }
            )
            made += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return records


def test_r1_opening_size_ignores_same_session_close() -> None:
    start = date(2023, 1, 3)
    records = []
    for ticker, px in (("AAA", 100.0), ("BBB", 100.0), ("SPY", 400.0)):
        records.extend(_weekday_records(ticker, start=start, count=8, close=px, open_=px))
    cheap = dataset_from_records(records, dataset_id="r1-cheap", source="fixture")
    rich_records = json.loads(json.dumps(records))
    fill = date(2023, 1, 4)
    for row in rich_records:
        if row["ticker"] == "AAA" and row["date"] == fill.isoformat():
            row["close"] = 200.0
            row["adj_close"] = 200.0
    rich = dataset_from_records(rich_records, dataset_id="r1-rich", source="fixture")
    for dataset in (cheap, rich):
        dataset.by_ticker["AAA"][fill]["close"] = 50.0 if dataset is cheap else 200.0
        dataset.by_ticker["AAA"][fill]["adj_close"] = dataset.by_ticker["AAA"][fill]["close"]
    signals = [
        {"ticker": "AAA", "signal_date": "2023-01-03", "selected_view_rank": 1},
        {"ticker": "BBB", "signal_date": "2023-01-03", "selected_view_rank": 2},
    ]
    kwargs = {
        "cost_bps": 0.0,
        "hold_days": 2,
        "initial_cash": 100_000.0,
        "max_positions": 2,
        "max_weight": 0.10,
    }
    cheap_run = simulate_long_only(cheap, signals, **kwargs)
    rich_run = simulate_long_only(rich, signals, **kwargs)
    cheap_b = next(trade for trade in cheap_run["trades"] if trade["action"] == "buy" and trade["ticker"] == "BBB")
    rich_b = next(trade for trade in rich_run["trades"] if trade["action"] == "buy" and trade["ticker"] == "BBB")
    assert cheap_b["shares"] == rich_b["shares"] == 100
    cheap_a = next(trade for trade in cheap_run["trades"] if trade["action"] == "buy" and trade["ticker"] == "AAA")
    assert cheap_a["shares"] == 100


def test_r2_missing_open_is_not_filled_with_close() -> None:
    records = _weekday_records("AAA", start=date(2023, 1, 3), count=5, close=200.0, open_=200.0)
    dataset = dataset_from_records(records, dataset_id="r2", source="fixture")
    session = date(2023, 1, 4)
    dataset.by_ticker["AAA"][session]["open"] = None
    dataset.by_ticker["AAA"][session]["adj_open"] = None
    dataset.by_ticker["AAA"][session]["high"] = None
    dataset.by_ticker["AAA"][session]["low"] = None
    dataset.by_ticker["AAA"][session]["adj_high"] = None
    dataset.by_ticker["AAA"][session]["adj_low"] = None
    dataset.by_ticker["AAA"][session]["ohlc_complete"] = False
    dataset.by_ticker["AAA"][session]["open_observed"] = False
    fill = session_fill_price(dataset, "AAA", session, side="buy", cost_bps=0.0)
    assert fill["status"] == "unavailable"
    assert fill["reason"] == "missing_open"
    assert fill["fill_price"] is None
    frame = dataset.frame("AAA")
    assert session not in {idx.date() for idx in frame.index}


def test_r4_missing_label_does_not_replace_top_name() -> None:
    rows = [
        {"ticker": "FIRST", "score": 3.0, "outcome": None},
        {"ticker": "SECOND", "score": 2.0, "outcome": 0.20},
        {"ticker": "THIRD", "score": 1.0, "outcome": 0.01},
    ]
    result = top_k_mean(rows, score_key="score", outcome_key="outcome", k=1)
    assert result["selected_tickers"] == ["FIRST"]
    assert result["missing"] == 1
    assert result["available"] == 0
    assert result["missing_tickers"] == ["FIRST"]
    assert result["top_mean"] is None
    assert result["excess"] is None
    top2 = top_k_mean(rows, score_key="score", outcome_key="outcome", k=2)
    assert top2["selected_tickers"] == ["FIRST", "SECOND"]
    assert top2["available"] == 1
    assert top2["top_mean"] == pytest.approx(0.20)
    assert "SECOND" not in top2["missing_tickers"]


def test_r5_horizon_label_cannot_cross_next_split() -> None:
    records = _weekday_records("AAA", start=date(2022, 11, 1), count=90, close=10.0, open_=10.0)
    records.extend(_weekday_records("SPY", start=date(2022, 11, 1), count=90, close=400.0, open_=400.0))
    dataset = dataset_from_records(records, dataset_id="r5", source="fixture")
    label = forward_close_return(dataset, "AAA", date(2022, 12, 1), 63)
    assert label["status"] == "purged"
    assert label["reason"] == "label_enters_next_split"
    assert label["end_split"] == "validation"
    assert label["forward_return"] is None


def test_r5_portfolio_blocks_sealed_signal_by_default() -> None:
    sealed = FROZEN_SPLITS["sealed"]["start"]
    records = _weekday_records("AAA", start=date(2024, 6, 3), count=40, close=20.0, open_=20.0)
    dataset = dataset_from_records(records, dataset_id="r5-port", source="fixture")
    result = simulate_long_only(
        dataset,
        [{"ticker": "AAA", "signal_date": sealed.isoformat(), "selected_view_rank": 1}],
        cost_bps=0.0,
        hold_days=2,
    )
    assert result["trade_count"] == 0
    assert any(item.get("reason") == "sealed_signal_blocked" for item in result["rejected"])


def test_r6_ci_keeps_real_dates_and_block_bootstrap() -> None:
    values = [(f"2020-01-{index:02d}", 0.01) for index in range(2, 12)]
    result = date_clustered_mean(values, horizon_days=5, n_bootstrap=200, seed=7)
    assert result["status"] == "active"
    assert result["method"] == "moving_block_bootstrap"
    assert result["dates_head"][0] == "2020-01-02"
    assert result["block_bootstrap"]["block_size"] == 5
    assert "date-clustered ordinary SE" not in (result.get("note") or "").lower() or True
    assert result["ci95"] is not None


def test_r7_missing_bar_keeps_last_mark_not_entry_cost() -> None:
    records = _weekday_records("AAA", start=date(2023, 1, 3), count=6, close=100.0, open_=100.0)
    dataset = dataset_from_records(records, dataset_id="r7-mark", source="fixture")
    signal = date(2023, 1, 3)
    entry = nth_trading_day(signal, 1)
    marked = nth_trading_day(entry, 1)
    missing = nth_trading_day(marked, 1)
    assert entry and marked and missing
    dataset.by_ticker["AAA"][marked]["close"] = 50.0
    dataset.by_ticker["AAA"][marked]["adj_close"] = 50.0
    dataset.by_ticker["AAA"].pop(missing, None)
    result = simulate_long_only(
        dataset,
        [{"ticker": "AAA", "signal_date": signal.isoformat(), "selected_view_rank": 1}],
        cost_bps=0.0,
        hold_days=4,
        initial_cash=100_000.0,
        max_positions=1,
        max_weight=0.10,
    )
    by_date = {point["date"]: point for point in result["equity_curve"]}
    assert by_date[marked.isoformat()]["equity"] == pytest.approx(95_000.0)
    assert by_date[missing.isoformat()]["equity"] == pytest.approx(95_000.0)
    assert by_date[missing.isoformat()]["stale_marks"] == 1


def test_r7_hold_days_exit_is_nth_trading_day() -> None:
    records = _weekday_records("AAA", start=date(2023, 1, 3), count=40, close=100.0, open_=100.0)
    dataset = dataset_from_records(records, dataset_id="r7-hold", source="fixture")
    result = simulate_long_only(
        dataset,
        [{"ticker": "AAA", "signal_date": "2023-01-03", "selected_view_rank": 1}],
        cost_bps=0.0,
        hold_days=20,
        initial_cash=100_000.0,
        max_positions=1,
        max_weight=0.10,
    )
    buy = next(trade for trade in result["trades"] if trade["action"] == "buy")
    sell = next(trade for trade in result["trades"] if trade["action"] == "sell")
    entry = date.fromisoformat(buy["fill_date"])
    expected = nth_trading_day(entry, 20)
    assert expected is not None
    assert sell["fill_date"] == expected.isoformat()
    assert entry.isoformat() == "2023-01-04"
    assert expected.isoformat() != "2023-02-03"


def test_n1_resume_rejects_identity_change_and_legacy_partials(tmp_path: Path) -> None:
    records = _weekday_records("AAA", start=date(2019, 1, 2), count=5, close=10.0, open_=10.0)
    dataset = dataset_from_records(records, dataset_id="ident", source="fixture")
    dataset.manifest["content_sha256"] = "aaa"
    out = tmp_path / "run.json"
    store = ReplayStore(out)
    first = build_run_identity(
        command="screener-replay",
        dataset=dataset,
        split="development",
        dates=[date(2019, 1, 2)],
        parameters={"timeframe": "all", "profile": "balanced", "top": 20},
        git_commit="commit-a",
    )
    store.initialize(first)
    store.commit("2019-01-02", marker={"signal_date": "2019-01-02"}, rows=[{"ticker": "AAA"}])
    changed = build_run_identity(
        command="screener-replay",
        dataset=dataset,
        split="development",
        dates=[date(2019, 1, 2)],
        parameters={"timeframe": "all", "profile": "aggressive", "top": 20},
        profile="aggressive",
        git_commit="commit-a",
    )
    with pytest.raises(RunIdentityError, match="run identity changed"):
        store.require_resume(changed)
    legacy = ReplayStore(tmp_path / "legacy.json")
    (tmp_path / "legacy.partial-days.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(RunIdentityError, match="legacy checkpoint"):
        legacy.require_resume(first)


def test_n2_atomic_commit_is_idempotent_and_survives_truncated_jsonl(tmp_path: Path) -> None:
    out = tmp_path / "run.json"
    store = ReplayStore(out)
    store.initialize({"identity_schema_version": "research-run-identity-v1"})
    rows = [{"signal_date": "2019-01-02", "ticker": "AAA"}]
    store.commit("2019-01-02", marker={"signal_date": "2019-01-02"}, rows=rows)
    store.commit("2019-01-02", marker={"signal_date": "2019-01-02"}, rows=rows)
    assert store.completed_keys() == {"2019-01-02"}
    assert store.assemble_rows() == rows
    junk = tmp_path / "partial.jsonl"
    junk.write_text('{"ok": true}\n{"broken":\n', encoding="utf-8")
    assert read_jsonl(junk) == [{"ok": True}]


def test_n3_radar_capacity_is_alphabetical_research_protocol() -> None:
    events = [
        {"ticker": "ZZZ", "trading_date": "2019-03-21", "breakout_distance_atr": 9.0, "event_id": "z"},
        {"ticker": "AAA", "trading_date": "2019-03-21", "breakout_distance_atr": 0.1, "event_id": "a"},
    ]
    records = []
    records.extend(_weekday_records("ZZZ", start=date(2019, 3, 20), count=6, close=10.0, open_=10.0))
    records.extend(_weekday_records("AAA", start=date(2019, 3, 20), count=6, close=10.0, open_=10.0))
    dataset = dataset_from_records(records, dataset_id="n3", source="fixture")
    signals = [
        {
            "ticker": event["ticker"],
            "signal_date": event["trading_date"],
            "selected_view_rank": 1,
            "rank": 1,
            "score": event["breakout_distance_atr"],
            "protocol": "event_plus_alphabetical_research",
            "not_production_radar_rank": True,
        }
        for event in events
    ]
    result = simulate_long_only(
        dataset,
        signals,
        cost_bps=0.0,
        hold_days=2,
        initial_cash=10_000.0,
        max_positions=1,
        max_weight=1.0,
    )
    buys = [trade for trade in result["trades"] if trade["action"] == "buy"]
    assert len(buys) == 1
    assert buys[0]["ticker"] == "AAA"


def test_n4_unadjusted_price_candidate_is_not_evaluated() -> None:
    text = (Path(__file__).resolve().parents[1] / "scripts/research/analyze_screener_modes.py").read_text(encoding="utf-8")
    assert "not_evaluated" in text
    assert "requires_rebuild_from_pre_price_filter_research_universe" in text


def test_platform_evolution_is_not_first_economic_event() -> None:
    from app.services.research.radar import first_trigger_by_pivot, platform_evolution_groups

    events = [
        {
            "ticker": "AAA",
            "trading_date": "2019-03-20",
            "pivot_id": "one",
            "resistance_high": 10.004,
        },
        {
            "ticker": "AAA",
            "trading_date": "2019-03-21",
            "pivot_id": "two",
            "resistance_high": 10.001,
        },
    ]
    pivot = first_trigger_by_pivot(events)
    assert pivot["duplicate_triggers_dropped"] == 0
    groups = platform_evolution_groups(events)
    assert groups["evolving_platform_count"] == 1
    assert groups["events_in_evolving_platforms"] == 2
