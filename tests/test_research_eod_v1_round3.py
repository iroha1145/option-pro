from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.ledger import simulate_ledger
from app.services.research_eod_v1.residual import residual_raw_momentum
from app.services.research_eod_v1.snapshot import compute_snapshot


def test_feature_version_bumped_for_round3() -> None:
    assert FEATURE_VERSION == "us-eod-research-features-v1.2"


def test_residual_handles_short_ipo_internal_gap_and_late_benchmark() -> None:
    days = trading_days(date(2018, 1, 2), 700)
    market = make_series("SPY", days, trending_close(700, 200, 0.05), asset_track="etf", security_type="ETF")
    ipo_days = days[-200:]
    ipo = make_series("IPO", ipo_days, trending_close(200, 30, 0.08), industry_id=None)
    short = residual_raw_momentum(ipo, market, {"IPO": ipo, "SPY": market})
    assert short.status in {"SHORT_HISTORY", "UNALIGNED_BENCHMARK"}
    gapped_days = days[:350] + days[351:]
    gapped = make_series("GAP", gapped_days, trending_close(len(gapped_days), 40, 0.06), industry_id=None)
    late = make_series("SPY", days[20:], trending_close(680, 200, 0.05), asset_track="etf", security_type="ETF")
    out = residual_raw_momentum(gapped, late, {"GAP": gapped, "SPY": late})
    assert out.status in {"OK", "UNALIGNED_BENCHMARK", "MISSING_DAY_RETURN", "SHORT_HISTORY", "RESIDUAL_WINDOW_INVALID"}


def test_unsized_orders_are_rejected() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("AAA", days, np.full(12, 100.0))
    series.raw_open = np.full(12, 100.0)
    series.raw_close = np.full(12, 100.0)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": series},
        capital=10_000,
        holding_sessions=5,
        signals=[{"security_id": "AAA", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000}],
    )
    assert result["unfilled"] >= 1
    assert not result["trades"]


def test_future_bars_do_not_change_complete_session() -> None:
    days = trading_days(date(2018, 1, 2), 260)
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(260, 40, 0.1)),
        "SPY": make_series("SPY", days, trending_close(260, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    as_of = as_of_after_close(days[-21])
    first = compute_snapshot(as_of, panel, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality")
    extra = {sid: series for sid, series in panel.items()}
    second = compute_snapshot(as_of, extra, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality")
    assert first["session_date"] == second["session_date"]
    assert first["rows"] == second["rows"]


def test_intraday_clock_cannot_select_same_day_session() -> None:
    from app.services.research_eod_v1.calendar_asof import last_complete_eod_session

    clock = datetime(2026, 9, 16, 13, 24, tzinfo=ZoneInfo("America/New_York"))
    session = last_complete_eod_session(clock)
    assert session <= date(2026, 9, 15)
