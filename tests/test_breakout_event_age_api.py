from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api import breakouts as breakout_api
from app.services.breakouts.config import BreakoutSettings


START = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)


def _settings(path) -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        db_path=path,
    )


def _stored_event(**times) -> dict:
    return {
        "event_id": "event-age-api",
        "ticker": "AAPL",
        "asset_type": "common_stock",
        "session": "regular",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "CONFIRMED",
        "pivot_id": "pivot-AAPL",
        "source_snapshot_id": "snapshot-age-api",
        "event_price": 100.0,
        "features": {},
        "scores": {},
        **times,
    }


def test_api_reports_independent_event_state_and_observation_ages(tmp_path) -> None:
    first_seen_at = START
    triggered_at = START + timedelta(minutes=5)
    state_changed_at = START + timedelta(minutes=10)
    last_seen_at = START + timedelta(minutes=15)
    now = START + timedelta(minutes=20)
    stored = _stored_event(
        # A later event_at must not override the explicit trigger anchor.
        event_at=last_seen_at,
        first_seen_at=first_seen_at,
        triggered_at=triggered_at,
        state_changed_at=state_changed_at,
        last_seen_at=last_seen_at,
    )

    response = breakout_api._public_event(
        _settings(tmp_path / "breakouts.db"),
        stored,
        observed_at=now,
    )
    assert response.event_at == triggered_at
    assert response.first_seen_at == first_seen_at
    assert response.triggered_at == triggered_at
    assert response.state_changed_at == state_changed_at
    assert response.last_seen_at == last_seen_at
    assert response.event_age_seconds == 15 * 60
    assert response.state_age_seconds == 10 * 60
    assert response.observation_age_seconds == 5 * 60

    later = breakout_api._public_event(
        _settings(tmp_path / "breakouts.db"),
        stored,
        observed_at=now + timedelta(minutes=3),
    )
    assert later.event_age_seconds == response.event_age_seconds + 3 * 60
    assert later.observation_age_seconds == response.observation_age_seconds + 3 * 60


def test_untriggered_api_event_uses_first_seen_anchor(tmp_path) -> None:
    now = START + timedelta(minutes=12)
    stored = _stored_event(
        lifecycle_state="WATCHING",
        event_at=START,
        first_seen_at=START,
        triggered_at=None,
        state_changed_at=START + timedelta(minutes=2),
        last_seen_at=START + timedelta(minutes=10),
    )

    response = breakout_api._public_event(
        _settings(tmp_path / "breakouts.db"),
        stored,
        observed_at=now,
    )
    assert response.triggered_at is None
    assert response.event_at == START
    assert response.event_age_seconds == 12 * 60
    assert response.state_age_seconds == 10 * 60
    assert response.observation_age_seconds == 2 * 60
