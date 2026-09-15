from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.models import MarketSession
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.t1_priority import (
    T1_MET,
    T1_NOT_APPLICABLE,
    T1_UNAVAILABLE,
    T1_UNMET,
    apply_t1_stable_boost,
    attach_t1_features,
    evaluate_t1_from_daily,
    t1_input_identity,
)
from app.services.market_calendar import prior_trading_sessions
from tests.test_breakout_api_contract import _event, _publish
from tests.test_t1_priority import SESSION, _daily_frame


ET = ZoneInfo("America/New_York")


def _closed() -> datetime:
    return datetime(2026, 9, 14, 16, 5, tzinfo=ET)


def test_incomplete_unavailable_does_not_replace_settled_met(tmp_path: Path) -> None:
    repo = BreakoutRepository(tmp_path / "t1-settled.db")
    repo.initialize()
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    first = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-settled",
    )
    assert first["status"] == T1_MET
    repo.persist_t1_evaluations([{"event_id": "evt-settled", "t1_priority": first}])
    pending_scan = {
        "event_id": "evt-settled",
        "features": {"t1_priority": {"status": "pending", "reason": "session_incomplete"}},
    }
    later = evaluate_t1_from_daily(
        None,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(days=1),
        session=MarketSession.CLOSED,
        previous=pending_scan["features"]["t1_priority"],
        event_id="evt-settled",
    )
    assert later["status"] == T1_UNAVAILABLE
    assert later.get("identity_complete") is False
    repo.persist_t1_evaluations([{"event_id": "evt-settled", "t1_priority": later}])
    overlaid = repo.overlay_t1_evaluations([{"event_id": "evt-settled"}])[0]["t1_priority"]
    assert overlaid["status"] == T1_MET
    assert overlaid["known_at"] == first["known_at"]
    assert overlaid["latest_attempt"]["status"] == T1_UNAVAILABLE
    assert overlaid["latest_attempt"]["reason"] == "daily_unavailable"
    with repo.open_read_connection() as connection:
        versions = connection.execute(
            "SELECT COUNT(*) FROM breakout_t1_evaluations WHERE event_id=?",
            ("evt-settled",),
        ).fetchone()[0]
    assert versions == 1


def test_incomplete_unavailable_does_not_replace_settled_unmet(tmp_path: Path) -> None:
    repo = BreakoutRepository(tmp_path / "t1-unmet.db")
    repo.initialize()
    unmet = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 101, "low": 99, "close": 100.2, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-unmet",
    )
    assert unmet["status"] == T1_UNMET
    repo.persist_t1_evaluations([{"event_id": "evt-unmet", "t1_priority": unmet}])
    later = evaluate_t1_from_daily(
        None,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(days=1),
        session=MarketSession.CLOSED,
        event_id="evt-unmet",
    )
    repo.persist_t1_evaluations([{"event_id": "evt-unmet", "t1_priority": later}])
    overlaid = repo.overlay_t1_evaluations([{"event_id": "evt-unmet"}])[0]["t1_priority"]
    assert overlaid["status"] == T1_UNMET
    assert overlaid["latest_attempt"]["status"] == T1_UNAVAILABLE


def test_complete_revision_can_change_met_and_unmet(tmp_path: Path) -> None:
    repo = BreakoutRepository(tmp_path / "t1-revise.db")
    repo.initialize()
    met = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-rev",
    )
    assert met["status"] == T1_MET
    repo.persist_t1_evaluations([{"event_id": "evt-rev", "t1_priority": met}])
    unmet = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 101, "low": 99, "close": 100.2, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(hours=1),
        session=MarketSession.CLOSED,
        previous=met,
        event_id="evt-rev",
    )
    assert unmet["status"] == T1_UNMET
    assert unmet["eval_version"] == met["eval_version"] + 1
    assert unmet["first_known_at"] == met["known_at"]
    repo.persist_t1_evaluations([{"event_id": "evt-rev", "t1_priority": unmet}])
    back = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(hours=2),
        session=MarketSession.CLOSED,
        previous=unmet,
        event_id="evt-rev",
    )
    assert back["status"] == T1_MET
    repo.persist_t1_evaluations([{"event_id": "evt-rev", "t1_priority": back}])
    overlaid = repo.overlay_t1_evaluations([{"event_id": "evt-rev"}])[0]["t1_priority"]
    assert overlaid["status"] == T1_MET
    assert overlaid["first_known_at"] == met["known_at"]
    assert overlaid["eval_version"] == 3


def test_atr_history_price_change_alters_identity_and_conclusion() -> None:
    prior = prior_trading_sessions(SESSION, 20)
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for day in prior:
        rows[pd.Timestamp(day)] = {
            "Open": 100.0,
            "High": 101.0,
            "Low": 99.0,
            "Close": 100.0,
            "Volume": 1_000_000,
        }
    rows[pd.Timestamp(SESSION)] = {
        "Open": 100.0,
        "High": 106.0,
        "Low": 99.0,
        "Close": 105.8,
        "Volume": 2_000_000,
    }
    old = pd.DataFrame.from_dict(rows, orient="index").sort_index()
    revised = old.copy()
    for day in prior:
        revised.loc[pd.Timestamp(day), ["High", "Low"]] = (115.0, 85.0)
    first = evaluate_t1_from_daily(
        old,
        session_date=SESSION,
        resistance_high=105,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-atr",
    )
    second = evaluate_t1_from_daily(
        revised,
        session_date=SESSION,
        resistance_high=105,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-atr",
    )
    cached = evaluate_t1_from_daily(
        revised,
        session_date=SESSION,
        resistance_high=105,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-atr",
    )
    assert first["identity_hash"] != second["identity_hash"]
    assert first["status"] == T1_MET
    assert second["status"] == T1_UNMET
    assert second["identity_hash"] == cached["identity_hash"]
    assert second["status"] == cached["status"]
    assert second.get("reused") is not True


def test_future_bars_do_not_change_event_t_identity() -> None:
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    first = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-future",
    )
    later_frame = _daily_frame(
        {"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000},
        extra_days=[date(2026, 9, 15)],
    )
    later_frame.loc[pd.Timestamp(date(2026, 9, 15)), ["High", "Low", "Close"]] = (200.0, 50.0, 50.0)
    later = evaluate_t1_from_daily(
        later_frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=datetime(2026, 9, 15, 16, 5, tzinfo=ET),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-future",
    )
    assert later["identity_hash"] == first["identity_hash"]
    assert later["status"] == first["status"]
    assert later["known_at"] == first["known_at"]
    assert later.get("reused") is True


def test_orb_stays_not_applicable_and_failed_met_is_not_boosted() -> None:
    attached = attach_t1_features(
        {
            "event_id": "evt-orb",
            "setup_type": "OPENING_RANGE_BREAKOUT",
            "trading_date": SESSION.isoformat(),
            "structure": {"resistance_zone": {"high": 100.0, "low": 90.0}},
        },
        _daily_frame(),
        as_of=_closed(),
        session=MarketSession.CLOSED,
    )
    assert attached["t1_priority"]["status"] == T1_NOT_APPLICABLE
    boosted = apply_t1_stable_boost(
        [
            {
                "event_id": "A",
                "trading_date": SESSION.isoformat(),
                "event_at": "2026-09-14T19:03:00Z",
                "alert_priority_score": 80,
                "t1_priority": {"status": T1_UNMET},
                "lifecycle_state": "WATCHING",
            },
            {
                "event_id": "B",
                "trading_date": SESSION.isoformat(),
                "event_at": "2026-09-14T19:02:00Z",
                "alert_priority_score": 80,
                "t1_priority": {"status": T1_MET},
                "lifecycle_state": "FAILED",
            },
        ]
    )
    assert [item["event_id"] for item in boosted] == ["A", "B"]


def test_regular_scan_carryover_overlay_keeps_sidecar_settled(tmp_path: Path) -> None:
    repo = BreakoutRepository(tmp_path / "t1-carryover.db")
    repo.initialize()
    at = datetime(2026, 9, 14, 20, 0, tzinfo=ET)
    event = _event("evt-carry", "AAA", at, 80.0)
    event["features"]["t1_priority"] = {"status": "pending", "reason": "session_incomplete"}
    _publish(repo, at, [event])
    met = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-carry",
    )
    repo.persist_t1_evaluations([{"event_id": "evt-carry", "t1_priority": met}])
    snapshot = repo.latest_completed_scan()
    raw = [item for item in snapshot["events"] if item["event_id"] == "evt-carry"][0]
    assert raw["features"]["t1_priority"]["status"] == "pending"
    overlaid = repo.overlay_t1_evaluations([raw])[0]
    assert overlaid["t1_priority"]["status"] == T1_MET
    later = evaluate_t1_from_daily(
        None,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(days=1),
        session=MarketSession.CLOSED,
        previous=overlaid["t1_priority"],
        event_id="evt-carry",
    )
    repo.persist_t1_evaluations([{"event_id": "evt-carry", "t1_priority": later}])
    kept = repo.overlay_t1_evaluations([raw])[0]["t1_priority"]
    assert kept["status"] == T1_MET
    assert kept["latest_attempt"]["status"] == T1_UNAVAILABLE


def _store_then_overlay(tmp_path: Path, name: str, first: dict, later: dict) -> dict:
    repo = BreakoutRepository(tmp_path / name)
    repo.initialize()
    event_id = str(first.get("event_id") or later.get("event_id") or "evt")
    repo.persist_t1_evaluations([{"event_id": event_id, "t1_priority": first}])
    repo.persist_t1_evaluations([{"event_id": event_id, "t1_priority": later}])
    return repo.overlay_t1_evaluations([{"event_id": event_id}])[0]["t1_priority"]


def test_missing_event_volume_does_not_replace_settled_met(tmp_path: Path) -> None:
    first = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-vol-missing",
    )
    assert first["status"] == T1_MET
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000})
    frame.loc[pd.Timestamp(SESSION), "Volume"] = np.nan
    later = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(hours=1),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-vol-missing",
    )
    assert later["status"] == T1_UNAVAILABLE
    assert later["reason"] == "missing_event_volume"
    assert later.get("identity_complete") is False
    kept = _store_then_overlay(tmp_path, "t1-vol-missing.db", first, later)
    assert kept["status"] == T1_MET
    assert kept["known_at"] == first["known_at"]
    assert kept["eval_version"] == first["eval_version"]
    assert kept["latest_attempt"]["reason"] == "missing_event_volume"


def test_negative_event_volume_does_not_become_a_signal(tmp_path: Path) -> None:
    first = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-vol-neg",
    )
    frame = _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": -1})
    later = evaluate_t1_from_daily(
        frame,
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(hours=1),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-vol-neg",
    )
    assert later["status"] == T1_UNAVAILABLE
    assert later["reason"] == "missing_event_volume"
    assert later.get("identity_complete") is False
    kept = _store_then_overlay(tmp_path, "t1-vol-neg.db", first, later)
    assert kept["status"] == T1_MET
    assert kept["latest_attempt"]["status"] == T1_UNAVAILABLE


def test_missing_resistance_is_not_a_complete_revision(tmp_path: Path) -> None:
    first = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-res",
    )
    later = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=None,
        as_of=_closed() + timedelta(hours=1),
        session=MarketSession.CLOSED,
        previous=first,
        event_id="evt-res",
    )
    assert later["status"] == T1_UNAVAILABLE
    assert later["reason"] == "t1_inputs_unavailable"
    assert later.get("identity_complete") is False
    kept = _store_then_overlay(tmp_path, "t1-res.db", first, later)
    assert kept["status"] == T1_MET
    assert kept["known_at"] == first["known_at"]
    assert kept["latest_attempt"]["reason"] == "t1_inputs_unavailable"


def test_zero_event_volume_is_unmet_not_missing(tmp_path: Path) -> None:
    zero = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 0}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-zero",
    )
    assert zero["status"] == T1_UNMET
    assert zero.get("identity_complete") is True
    met = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-zero",
    )
    revised = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 0}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed() + timedelta(hours=1),
        session=MarketSession.CLOSED,
        previous=met,
        event_id="evt-zero",
    )
    assert revised["status"] == T1_UNMET
    assert revised["eval_version"] == met["eval_version"] + 1
    kept = _store_then_overlay(tmp_path, "t1-zero.db", met, revised)
    assert kept["status"] == T1_UNMET
    assert kept["first_known_at"] == met["known_at"]


def test_store_rejects_claimed_complete_unavailable(tmp_path: Path) -> None:
    repo = BreakoutRepository(tmp_path / "t1-claimed.db")
    repo.initialize()
    first = evaluate_t1_from_daily(
        _daily_frame({"open_": 100, "high": 110, "low": 90, "close": 108, "volume": 2_000_000}),
        session_date=SESSION,
        resistance_high=100,
        as_of=_closed(),
        session=MarketSession.CLOSED,
        event_id="evt-claimed",
    )
    repo.persist_t1_evaluations([{"event_id": "evt-claimed", "t1_priority": first}])
    forged = {
        "status": T1_UNAVAILABLE,
        "reason": "missing_event_volume",
        "identity_hash": "forged-complete-hash",
        "identity_complete": True,
        "computed_at": "2026-09-14T21:00:00Z",
        "known_at": None,
    }
    repo.persist_t1_evaluations([{"event_id": "evt-claimed", "t1_priority": forged}])
    kept = repo.overlay_t1_evaluations([{"event_id": "evt-claimed"}])[0]["t1_priority"]
    assert kept["status"] == T1_MET
    assert kept["known_at"] == first["known_at"]
    assert kept["latest_attempt"]["reason"] == "missing_event_volume"


def test_t1_input_identity_includes_prior_ohlc() -> None:
    prior = [
        {
            "session_date": day.isoformat(),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1_000_000,
        }
        for day in prior_trading_sessions(SESSION, 20)
    ]
    session_bar = {"open": 100.0, "high": 106.0, "low": 99.0, "close": 105.8, "volume": 2_000_000}
    first = t1_input_identity(
        event_id="E",
        session_date=SESSION,
        resistance_high=105,
        session_bar=session_bar,
        prior_bars=prior,
    )
    changed = [dict(item) for item in prior]
    changed[0]["high"] = 115.0
    changed[0]["low"] = 85.0
    second = t1_input_identity(
        event_id="E",
        session_date=SESSION,
        resistance_high=105,
        session_bar=session_bar,
        prior_bars=changed,
    )
    assert first != second
