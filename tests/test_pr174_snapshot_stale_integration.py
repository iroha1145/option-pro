"""End-to-end snapshot pool tests for PR #174 closeout.

Must run against the real repository entry, not an isolated snapshot blob.
Covers >=420 bars, missing last/internal T, late source, and ETF track mismatch.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np

from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.residual import residual_raw_momentum
from app.services.research_eod_v1.snapshot import compute_snapshot


def _long_panel():
    days = trading_days(date(2018, 1, 2), 421)
    assert len(days) >= 420
    nvda = make_series("NVDA", days, trending_close(len(days), 40, 0.12))
    amd = make_series("AMD", days, trending_close(len(days), 30, 0.10))
    avgo = make_series("AVGO", days, trending_close(len(days), 35, 0.09))
    hole_days = days[:200] + days[201:]
    hole = make_series("HOLE", hole_days, trending_close(len(hole_days), 41, 0.11))
    stale = make_series("STALE", days[:-1], trending_close(len(days) - 1, 22, 0.06))
    late = make_series("LATE", days, trending_close(len(days), 28, 0.07))
    qqq = make_series(
        "QQQ",
        days,
        trending_close(len(days), 180, 0.05),
        asset_track="etf",
        security_type="ETF",
        theme_ids=("etfs",),
    )
    spy = make_series(
        "SPY",
        days,
        trending_close(len(days), 210, 0.04),
        asset_track="etf",
        security_type="ETF",
        theme_ids=("etfs",),
    )
    return days, {
        "NVDA": nvda,
        "AMD": amd,
        "AVGO": avgo,
        "HOLE": hole,
        "STALE": stale,
        "LATE": late,
        "QQQ": qqq,
        "SPY": spy,
    }


def test_snapshot_rejects_missing_last_bar_without_keyerror() -> None:
    days, panel = _long_panel()
    assert len(panel["STALE"].dates) >= 420
    assert not has_complete_session_bar(panel["STALE"], days[-1])
    as_of = as_of_after_close(days[-1])
    panel["LATE"].source_available_at = as_of + timedelta(hours=6)
    try:
        payload = compute_snapshot(
            as_of,
            panel,
            "u_closeout_stale",
            load_registry(),
            sector_id="semiconductors",
            algorithm="D_residual_momentum",
            extra_members={"STALE", "HOLE", "LATE", "QQQ"},
            late_securities=("LATE",),
        )
    except KeyError as exc:
        raise AssertionError(f"complete-T pool leaked a residual KeyError: {exc}") from exc
    by_id = {row["security_id"]: row for row in payload["rows"]}
    assert "STALE" not in payload["candidate_ids"]
    assert "LATE" not in payload["candidate_ids"]
    assert "LATE" not in payload["reference_ids"]
    assert "QQQ" not in payload["candidate_ids"]
    assert "STALE" in by_id
    assert "MISSING_T_BAR" in by_id["STALE"]["rejection_reasons"]
    assert by_id["STALE"]["status"] == "rejected"
    assert "LATE" in by_id
    assert "LATE_SOURCE" in by_id["LATE"]["rejection_reasons"]
    assert "QQQ" in by_id
    assert "TRACK_MISMATCH" in by_id["QQQ"]["rejection_reasons"]
    assert has_complete_session_bar(panel["HOLE"], days[-1])
    hole = by_id.get("HOLE")
    assert hole is not None
    if hole.get("residual_status"):
        assert hole["residual_status"] != "CRASH"
    assert payload["session_date"] == days[-1].isoformat()


def test_snapshot_missing_internal_bar_does_not_crash_residual() -> None:
    days, panel = _long_panel()
    as_of = as_of_after_close(days[-1])
    panel["LATE"].source_available_at = as_of
    payload = compute_snapshot(
        as_of,
        panel,
        "u_closeout_hole",
        load_registry(),
        sector_id="semiconductors",
        algorithm="D_residual_momentum",
        extra_members={"HOLE"},
    )
    hole = next(row for row in payload["rows"] if row["security_id"] == "HOLE")
    assert hole["residual_status"] in {
        "OK",
        "MISSING_DAY_RETURN",
        "UNALIGNED_BENCHMARK",
        "SHORT_HISTORY",
        "SINGULAR_OR_THIN_REGRESSION",
        "RESIDUAL_WINDOW_INVALID",
        "ZERO_RESIDUAL_VOL",
        "INSUFFICIENT_MATCHED_BENCHMARK",
    }


def test_residual_api_accepts_target_absent_from_peer_panel() -> None:
    days, panel = _long_panel()
    stale = panel["STALE"]
    peers = {sid: series for sid, series in panel.items() if sid != "STALE"}
    result = residual_raw_momentum(stale, panel["SPY"], peers)
    assert result.status in {
        "OK",
        "SHORT_HISTORY",
        "UNALIGNED_BENCHMARK",
        "MISSING_DAY_RETURN",
        "SINGULAR_OR_THIN_REGRESSION",
        "RESIDUAL_WINDOW_INVALID",
        "ZERO_RESIDUAL_VOL",
        "INSUFFICIENT_MATCHED_BENCHMARK",
    }


def test_etf_track_mismatch_is_structured_not_scored_into_stock_theme() -> None:
    days, panel = _long_panel()
    payload = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u_closeout_etf",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
        extra_members={"QQQ"},
    )
    qqq = next(row for row in payload["rows"] if row["security_id"] == "QQQ")
    assert qqq["status"] == "rejected"
    assert qqq["score"] is None or "TRACK_MISMATCH" in qqq["rejection_reasons"]
    assert "TRACK_MISMATCH" in qqq["rejection_reasons"]
    assert qqq["stock_or_etf_track"] == "etf"
