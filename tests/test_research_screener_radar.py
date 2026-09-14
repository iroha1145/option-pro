from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.research.calendar import nth_trading_day, session_close
from app.services.research.dataset import dataset_from_records, write_dataset_dir, load_dataset
from app.services.research.labels import attach_screener_labels, forward_close_return
from app.services.research.metrics import spearman_rank_ic
from app.services.research.portfolio import simulate_long_only
from app.services.research.protocol import FROZEN_SPLITS, assert_split_access
from app.services.research.radar import reconstruct_daily_base_events
from app.services.research.screener import replay_screener_day
from app.services.strength import scanner


def _records(
    ticker: str,
    *,
    start: date,
    count: int,
    start_price: float,
    slope: float,
    volume: float = 2_000_000,
) -> list[dict]:
    records = []
    price = start_price
    cursor = start
    made = 0
    while made < count:
        if cursor.weekday() < 5:
            close = price
            records.append(
                {
                    "ticker": ticker,
                    "date": cursor.isoformat(),
                    "open": close - 0.15,
                    "high": close + 0.4,
                    "low": close - 0.4,
                    "close": close,
                    "adj_close": close,
                    "volume": volume,
                }
            )
            price += slope
            made += 1
        cursor = date.fromordinal(cursor.toordinal() + 1)
    return records


def _synth_dataset() -> object:
    start = date(2018, 2, 1)
    records = []
    records.extend(_records("AAA", start=start, count=320, start_price=40, slope=0.18))
    records.extend(_records("BBB", start=start, count=320, start_price=45, slope=0.08))
    records.extend(_records("CCC", start=start, count=320, start_price=50, slope=0.02))
    records.extend(_records("SPY", start=start, count=320, start_price=200, slope=0.06))
    records.extend(_records("QQQ", start=start, count=320, start_price=150, slope=0.07))
    return dataset_from_records(records, dataset_id="fixture", source="fixture")


def _install_small_universe(monkeypatch) -> None:
    metadata = {
        "AAA": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software"],
            "theme_names": ["软件"],
        },
        "BBB": {
            "sector_id": "software",
            "sector_name": "软件",
            "primary_sector_id": "software",
            "primary_sector_name": "软件",
            "theme_ids": ["software"],
            "theme_names": ["软件"],
        },
        "CCC": {
            "sector_id": "energy",
            "sector_name": "能源",
            "primary_sector_id": "energy",
            "primary_sector_name": "能源",
            "theme_ids": ["energy"],
            "theme_names": ["能源"],
        },
    }
    monkeypatch.setattr(
        scanner,
        "_theme_universe",
        lambda sector_id=None: (["AAA", "BBB", "CCC"], metadata),
    )
    monkeypatch.setattr(
        "app.services.research.screener._theme_universe",
        lambda sector_id=None: (["AAA", "BBB", "CCC"], metadata),
    )
    monkeypatch.setattr(
        scanner,
        "compute_market_regime",
        lambda _frames, **_kwargs: {
            "status": "active",
            "score": 70.0,
            "confidence": 1.0,
            "risk_on_spread_score": 65.0,
            "market_context": {},
            "spread_matrix": {},
        },
    )


def test_object_index_history_is_trimmed_to_as_of() -> None:
    index = pd.Index([value.date().isoformat() for value in pd.bdate_range("2019-03-01", periods=40)])
    hist = pd.DataFrame(
        {
            "Open": 10.0,
            "High": 11.0,
            "Low": 9.0,
            "Close": range(40),
            "Volume": 1_000_000.0,
        },
        index=index,
    )
    assert not isinstance(hist.index, pd.DatetimeIndex)
    bounded, _cutoff = scanner._complete_daily_frame(hist, session_close(date(2019, 3, 20)))
    assert isinstance(bounded.index, pd.DatetimeIndex)
    assert bounded.index.max().date() == date(2019, 3, 20)
    assert float(bounded["Close"].iloc[-1]) < float(hist["Close"].iloc[-1])


def test_future_bars_do_not_change_completed_ranks(monkeypatch) -> None:
    _install_small_universe(monkeypatch)
    dataset = _synth_dataset()
    signal = date(2019, 3, 20)
    truncated = replay_screener_day(dataset, signal, parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3})
    with_future = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
        include_future_bars=True,
    )
    assert truncated.get("view_rows")
    assert len(truncated["view_rows"]) >= len(truncated["rows"])
    assert truncated["as_of"] == with_future["as_of"]
    left = [(row["ticker"], row["ranking_score"], row["intrinsic_score"]) for row in truncated["rows"]]
    right = [(row["ticker"], row["ranking_score"], row["intrinsic_score"]) for row in with_future["rows"]]
    assert left == right
    assert all(row["score_scope"] == "ranking" for row in truncated["rows"])


def test_reused_panel_matches_per_day_slice(monkeypatch) -> None:
    _install_small_universe(monkeypatch)
    dataset = _synth_dataset()
    signal = date(2019, 3, 21)
    panel = dataset.adjusted_panel(through=date(2019, 4, 1))
    sliced = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
    )
    reused = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
        panel=panel,
    )
    assert [
        (row["ticker"], row["ranking_score"]) for row in sliced["rows"]
    ] == [(row["ticker"], row["ranking_score"]) for row in reused["rows"]]


def test_truncation_matches_full_panel_replay(monkeypatch) -> None:
    _install_small_universe(monkeypatch)
    dataset = _synth_dataset()
    signal = date(2019, 3, 21)
    a = replay_screener_day(dataset, signal, parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3})
    b = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
        include_future_bars=True,
    )
    assert [row["ticker"] for row in a["rows"]] == [row["ticker"] for row in b["rows"]]
    assert a["research"]["evidence_grade"] == "C"


def test_labels_do_not_roll_missing_sessions() -> None:
    dataset = _synth_dataset()
    signal = date(2019, 3, 20)
    target = nth_trading_day(signal, 5)
    assert target is not None
    # Drop BBB on the exact label end date.
    dataset.by_ticker["BBB"].pop(target, None)
    missing = forward_close_return(dataset, "BBB", signal, 5)
    present = forward_close_return(dataset, "AAA", signal, 5)
    assert missing["status"] == "unavailable"
    assert missing["reason"] == "missing_exit_session_not_extended"
    assert present["status"] == "active"
    assert present["end_date"] == target.isoformat()
    assert present["entry_is_fill"] is False


def test_sealed_split_is_blocked() -> None:
    sealed = FROZEN_SPLITS["sealed"]["start"]
    with pytest.raises(PermissionError):
        assert_split_access(sealed, allow_sealed=False, purpose="unit-test")
    assert assert_split_access(sealed, allow_sealed=True) == "sealed"


def test_portfolio_uses_next_open_not_signal_close() -> None:
    records = []
    records.extend(_records("AAA", start=date(2019, 1, 2), count=40, start_price=10, slope=0.1))
    records.extend(_records("SPY", start=date(2019, 1, 2), count=40, start_price=100, slope=0.05))
    # Make next-day open clearly different from signal close.
    dataset = dataset_from_records(records, dataset_id="fills", source="fixture")
    signal = date(2019, 1, 3)
    next_day = nth_trading_day(signal, 1)
    assert next_day is not None
    signal_close = dataset.bar("AAA", signal)["close"]
    dataset.by_ticker["AAA"][next_day]["open"] = 50.0
    dataset.by_ticker["AAA"][next_day]["adj_open"] = 50.0
    result = simulate_long_only(
        dataset,
        [{"ticker": "AAA", "signal_date": signal.isoformat(), "selected_view_rank": 1}],
        cost_bps=10.0,
        hold_days=5,
        initial_cash=10_000,
        max_positions=1,
        max_weight=0.5,
    )
    buys = [trade for trade in result["trades"] if trade["action"] == "buy"]
    assert buys
    assert buys[0]["quote_open"] == pytest.approx(50.0)
    assert buys[0]["fill_price"] == pytest.approx(50.0 * 1.001)
    assert buys[0]["fill_price"] != pytest.approx(signal_close)
    assert result["final_equity"] is not None
    assert all(point["cash"] >= -1e-9 for point in result["equity_curve"])


def test_dataset_roundtrip_and_hash(tmp_path: Path) -> None:
    dataset = _synth_dataset()
    records = []
    for ticker, points in dataset.by_ticker.items():
        for session, bar in points.items():
            records.append(
                {
                    "ticker": ticker,
                    "date": session.isoformat(),
                    "open": bar["open"],
                    "high": bar["high"],
                    "low": bar["low"],
                    "close": bar["close"],
                    "adj_close": bar["adj_close"],
                    "volume": bar["volume"],
                }
            )
    path = write_dataset_dir(tmp_path / "ds", records, dataset_id="roundtrip", source="fixture")
    loaded = load_dataset(path)
    assert loaded.manifest["content_sha256"]
    assert loaded.bar("AAA", date(2018, 2, 1))["close"] == pytest.approx(40.0)


def test_screener_labels_attach_without_entering_scores(monkeypatch) -> None:
    _install_small_universe(monkeypatch)
    dataset = _synth_dataset()
    signal = date(2019, 3, 22)
    payload = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
    )
    labeled = attach_screener_labels(payload["rows"], dataset, signal_date=signal)
    assert "labels" in labeled[0]
    assert labeled[0]["ranking_score"] == payload["rows"][0]["ranking_score"]
    ic = spearman_rank_ic(
        [row["ranking_score"] for row in labeled],
        [row["excess"]["5"]["raw"] for row in labeled],
    )
    assert ic["n"] == 3 or ic["status"] == "unavailable"


def test_compact_row_keeps_mode_fields_and_unadjusted_close(monkeypatch) -> None:
    from app.services.research.screener import compact_screener_row

    _install_small_universe(monkeypatch)
    dataset = _synth_dataset()
    signal = date(2019, 3, 22)
    payload = replay_screener_day(
        dataset,
        signal,
        parameters={"min_price": 1, "min_avg_dollar_volume": 0, "top": 3},
    )
    labeled = attach_screener_labels(payload["view_rows"], dataset, signal_date=signal)
    compact = compact_screener_row(labeled[0], signal_date=signal, dataset=dataset)
    assert compact["ticker"]
    assert compact["score_short"] is not None or compact["ranking_score"] is not None
    assert compact["unadjusted_close"] == pytest.approx(dataset.bar(compact["ticker"], signal)["close"])
    assert "factor_breakdown" not in compact
    assert "benchmark_labels" not in compact


def test_mode_rerank_changes_profile_order() -> None:
    from app.services.research.screener import apply_screener_mode

    rows = [
        {
            "ticker": "LOWVOL",
            "ranking_score": 60.0,
            "intrinsic_score": 60.0,
            "score_short": 80.0,
            "score_mid": 50.0,
            "score_long": 40.0,
            "market_fit_score": 50.0,
            "profile_fit_score": 50.0,
            "intrinsic_confidence": 1.0,
            "market_fit_confidence": 1.0,
            "profile_fit_confidence": 1.0,
            "atr_pct": 1.0,
            "ma_alignment": 80.0,
            "avg_dollar_volume_20d": 50_000_000,
        },
        {
            "ticker": "HIVOL",
            "ranking_score": 61.0,
            "intrinsic_score": 61.0,
            "score_short": 40.0,
            "score_mid": 50.0,
            "score_long": 80.0,
            "market_fit_score": 50.0,
            "profile_fit_score": 50.0,
            "intrinsic_confidence": 1.0,
            "market_fit_confidence": 1.0,
            "profile_fit_confidence": 1.0,
            "atr_pct": 8.0,
            "ma_alignment": 80.0,
            "avg_dollar_volume_20d": 50_000_000,
        },
    ]
    short = apply_screener_mode(rows, timeframe="short", profile="balanced")
    long = apply_screener_mode(rows, timeframe="long", profile="balanced")
    conservative = apply_screener_mode(rows, timeframe="all", profile="conservative")
    assert short[0]["ticker"] == "LOWVOL"
    assert long[0]["ticker"] == "HIVOL"
    assert conservative[0]["ticker"] == "LOWVOL"


def test_prior_screener_overlap_uses_strictly_earlier_snapshot() -> None:
    from app.services.research.radar import prior_screener_overlap

    events = [
        {"ticker": "AAA", "trading_date": "2019-03-21"},
        {"ticker": "BBB", "trading_date": "2019-03-21"},
    ]
    rows = [
        {"ticker": "AAA", "signal_date": "2019-03-20", "selected_view_rank": 1},
        {"ticker": "BBB", "signal_date": "2019-03-21", "selected_view_rank": 1},
    ]
    marked = prior_screener_overlap(events, rows, top=20)
    by_ticker = {item["ticker"]: item for item in marked}
    assert by_ticker["AAA"]["in_prior_screener_top"] is True
    assert by_ticker["AAA"]["prior_screener_date"] == "2019-03-20"
    assert by_ticker["BBB"]["in_prior_screener_top"] is False


def test_replay_store_resume_reads_completed_days(tmp_path: Path) -> None:
    from app.services.research.replay_store import append_jsonl, completed_sessions, partial_paths

    out = tmp_path / "run.json"
    days_path, _rows_path = partial_paths(out)
    append_jsonl(days_path, [{"signal_date": "2019-01-02", "ic": {"status": "active", "ic": 0.1}}])
    assert completed_sessions(days_path) == {"2019-01-02"}


def test_radar_marks_intraday_types_unverifiable(monkeypatch) -> None:
    _install_small_universe(monkeypatch)
    monkeypatch.setattr(
        "app.services.research.radar._theme_universe",
        lambda sector_id=None: (["AAA"], {}),
    )
    dataset = _synth_dataset()
    payload = reconstruct_daily_base_events(dataset, date(2019, 3, 25), tickers=["AAA"])
    assert "OPENING_RANGE_BREAKOUT" in payload["unverifiable_setup_types"]
    assert payload["evidence_grade"] == "C"
    for event in payload["events"]:
        assert event["event_price_is_fill"] is False
        assert event["intraday_verified"] is False
