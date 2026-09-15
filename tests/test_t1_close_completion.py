from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import MarketSession
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.t1_priority import T1_MET, T1_PENDING
from app.services.breakouts.worker import BreakoutWorker
from app.services.market_calendar import prior_trading_sessions
from tests.test_breakout_paused_sessions import RecordingProvider, Settings


ET = ZoneInfo("America/New_York")
SESSION = date(2026, 9, 14)


def _daily_frame() -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for day in prior_trading_sessions(SESSION, 20):
        rows[pd.Timestamp(day)] = {
            "Open": 100.0,
            "High": 110.0,
            "Low": 90.0,
            "Close": 100.0,
            "Volume": 1_000_000,
        }
    rows[pd.Timestamp(SESSION)] = {
        "Open": 100.0,
        "High": 110.0,
        "Low": 90.0,
        "Close": 108.0,
        "Volume": 2_000_000,
    }
    return pd.DataFrame.from_dict(rows, orient="index").sort_index()


def test_postmarket_completes_pending_t1_without_discovery(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "t1-close.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    event = {
        "event_id": "evt-daily-t1",
        "ticker": "AAA",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "trading_date": SESSION.isoformat(),
        "lifecycle_state": "WATCHING",
        "structure": {"resistance_zone": {"high": 100.0, "low": 90.0}},
        "features": {"t1_priority": {"status": T1_PENDING, "reason": "session_incomplete"}},
        "event_at": "2026-09-14T18:00:00+00:00",
        "alert_priority_score": 80,
    }
    repository.latest_completed_scan = lambda: {"events": [event]}  # type: ignore[method-assign]
    frame = _daily_frame()
    price = SimpleNamespace(calls=0)

    async def daily(tickers, **_kwargs):
        price.calls += 1
        return {str(tickers[0]): SimpleNamespace(frame=frame)}

    price.daily = daily
    provider = RecordingProvider()
    worker = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        scan_service=SimpleNamespace(price_data=price),
        clock=MarketClock(now=lambda: datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)),
        owner_id="worker-t1-close",
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "paused"
    assert result["reason"] == "market_closed"
    assert provider.calls == 0
    assert price.calls == 1
    overlaid = repository.overlay_t1_evaluations([event])[0]
    assert overlaid["features"]["t1_priority"]["status"] == T1_MET
    assert overlaid["features"]["t1_priority"]["data_through"] == SESSION.isoformat()
    assert overlaid["features"]["t1_priority"]["known_at"]
    with repository.open_read_connection() as connection:
        scan_count = connection.execute("SELECT COUNT(*) FROM breakout_scan_runs").fetchone()[0]
    assert scan_count == 0


def test_postmarket_missing_daily_retries_without_discovery(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "t1-retry.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    event = {
        "event_id": "evt-daily-retry",
        "ticker": "BBB",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "trading_date": SESSION.isoformat(),
        "lifecycle_state": "WATCHING",
        "structure": {"resistance_zone": {"high": 100.0, "low": 90.0}},
        "features": {"t1_priority": {"status": T1_PENDING, "reason": "session_incomplete"}},
        "event_at": "2026-09-14T18:00:00+00:00",
        "alert_priority_score": 80,
    }
    repository.latest_completed_scan = lambda: {"events": [event]}  # type: ignore[method-assign]
    price = SimpleNamespace(calls=0)

    async def daily(_tickers, **_kwargs):
        price.calls += 1
        raise RuntimeError("daily bars not ready")

    price.daily = daily
    provider = RecordingProvider()
    worker = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        scan_service=SimpleNamespace(price_data=price),
        clock=MarketClock(now=lambda: datetime(2026, 9, 14, 21, 0, tzinfo=timezone.utc)),
        owner_id="worker-t1-retry",
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "paused"
    assert result["reason"] == "market_closed"
    assert provider.calls == 0
    assert price.calls == 1
    assert result["t1_retry_after_seconds"] == 30.0
    with repository.open_read_connection() as connection:
        scan_count = connection.execute("SELECT COUNT(*) FROM breakout_scan_runs").fetchone()[0]
    assert scan_count == 0


def test_t1_store_does_not_rewrite_same_identity_published_at(tmp_path: Path) -> None:
    repository = BreakoutRepository(tmp_path / "t1-store.db")
    repository.initialize()
    event = {
        "event_id": "evt-same",
        "features": {
            "t1_priority": {
                "status": T1_MET,
                "identity_hash": "abc",
                "known_at": "2026-09-14T20:05:00Z",
                "computed_at": "2026-09-14T20:05:00Z",
            }
        },
    }
    repository.persist_t1_evaluations([event])
    first = repository.overlay_t1_evaluations([{"event_id": "evt-same"}])[0]
    repository.persist_t1_evaluations(
        [
            {
                "event_id": "evt-same",
                "features": {
                    "t1_priority": {
                        "status": T1_MET,
                        "identity_hash": "abc",
                        "known_at": "2026-09-14T20:05:00Z",
                        "computed_at": "2026-09-15T13:00:00Z",
                    }
                },
            }
        ]
    )
    second = repository.overlay_t1_evaluations([{"event_id": "evt-same"}])[0]
    assert first["t1_priority"]["known_at"] == second["t1_priority"]["known_at"]
    assert second["t1_priority"]["computed_at"] == "2026-09-15T13:00:00Z"
    with repository.open_read_connection() as connection:
        versions = connection.execute(
            "SELECT COUNT(*) FROM breakout_t1_evaluations WHERE event_id=?",
            ("evt-same",),
        ).fetchone()[0]
    assert versions == 1
