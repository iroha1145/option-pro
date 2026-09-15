from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from app.api import breakouts as breakout_api
from app.services.algorithm_modes import PRODUCTION_ALGORITHM, T1_ALGORITHM
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.repository import BreakoutRepository, InvalidCursorError
from tests.test_breakout_api_contract import _client, _event, _heartbeat, _publish


AT = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)


def _pending(event_id: str, ticker: str, minute: int, status: str = "pending") -> dict:
    event = _event(event_id, ticker, AT.replace(minute=minute), 80.0)
    event["features"]["t1_priority"] = {"status": status}
    event["t1_priority"] = {"status": status}
    return event


def _repo(path: Path) -> BreakoutRepository:
    repo = BreakoutRepository(path)
    repo.initialize()
    return repo


def test_t1_promotion_after_page1_requires_restart_and_covers_all(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "t1-page.db")
    events = [_pending("A", "AAA", 3), _pending("B", "BBB", 2), _pending("C", "CCC", 1)]
    _publish(repo, AT, events)
    page1 = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=1)
    assert [item["event_id"] for item in page1["events"]] == ["A"]
    assert page1["next_cursor"]
    assert page1["t1_view"]
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "C",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "c-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:10:00Z",
                    "computed_at": "2026-09-14T20:10:00Z",
                    "eval_version": 1,
                },
            }
        ]
    )
    stale = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=10, cursor=page1["next_cursor"])
    assert stale["restart_required"] is True
    assert stale["cursor_stale"] is True
    assert stale["events"] == []
    restarted = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=10)
    assert [item["event_id"] for item in restarted["events"]] == ["C", "A", "B"]
    assert restarted["restart_required"] is False


def test_two_successive_completions_do_not_drop_events(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "t1-two.db")
    _publish(repo, AT, [_pending("A", "AAA", 3), _pending("B", "BBB", 2), _pending("C", "CCC", 1)])
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "B",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "b-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:05:00Z",
                    "computed_at": "2026-09-14T20:05:00Z",
                    "eval_version": 1,
                },
            }
        ]
    )
    page1 = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=1)
    assert [item["event_id"] for item in page1["events"]] == ["B"]
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "A",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "a-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:06:00Z",
                    "computed_at": "2026-09-14T20:06:00Z",
                    "eval_version": 1,
                },
            }
        ]
    )
    stale = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=10, cursor=page1["next_cursor"])
    assert stale["restart_required"] is True
    restarted = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=10)
    assert [item["event_id"] for item in restarted["events"]] == ["A", "B", "C"]


def test_production_pagination_contract_stays_scan_bound(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "t1-prod.db")
    _publish(repo, AT, [_pending("A", "AAA", 3), _pending("B", "BBB", 2), _pending("C", "CCC", 1)])
    page1 = repo.list_events(sort_algorithm=PRODUCTION_ALGORITHM, limit=1)
    assert [item["event_id"] for item in page1["events"]] == ["A"]
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "C",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "c-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:10:00Z",
                    "computed_at": "2026-09-14T20:10:00Z",
                },
            }
        ]
    )
    page2 = repo.list_events(sort_algorithm=PRODUCTION_ALGORITHM, limit=10, cursor=page1["next_cursor"])
    assert page2.get("restart_required") in {None, False}
    assert [item["event_id"] for item in page2["events"]] == ["B", "C"]


def test_api_returns_restart_signal_for_stale_t1_cursor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "t1-api.db",
    )
    repo = BreakoutRepository(settings.db_path)
    repo.initialize()
    _publish(repo, AT, [_pending("A", "AAA", 3), _pending("B", "BBB", 2), _pending("C", "CCC", 1)])
    _heartbeat(repo, AT)
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(breakout_api, "_now", lambda: AT)
    client = _client()
    page1 = client.get(
        "/api/breakouts/events",
        params={"sort_algorithm": T1_ALGORITHM, "limit": 1},
    ).json()
    assert [item["event_id"] for item in page1["events"]] == ["A"]
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "C",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "c-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:10:00Z",
                    "computed_at": "2026-09-14T20:10:00Z",
                },
            }
        ]
    )
    stale = client.get(
        "/api/breakouts/events",
        params={"sort_algorithm": T1_ALGORITHM, "limit": 10, "cursor": page1["next_cursor"]},
    ).json()
    assert stale["cursor_stale"] is True
    assert stale["restart_required"] is True
    assert stale["events"] == []
    restarted = client.get(
        "/api/breakouts/events",
        params={"sort_algorithm": T1_ALGORITHM, "limit": 10},
    ).json()
    assert [item["event_id"] for item in restarted["events"]] == ["C", "A", "B"]


def test_algorithm_switch_uses_independent_cursors(tmp_path: Path) -> None:
    repo = _repo(tmp_path / "t1-switch.db")
    _publish(repo, AT, [_pending("A", "AAA", 3, "unmet"), _pending("B", "BBB", 2, "met")])
    repo.persist_t1_evaluations(
        [
            {
                "event_id": "B",
                "t1_priority": {
                    "status": "met",
                    "identity_hash": "b-met",
                    "identity_complete": True,
                    "known_at": "2026-09-14T20:05:00Z",
                    "computed_at": "2026-09-14T20:05:00Z",
                },
            }
        ]
    )
    t1_page = repo.list_events(sort_algorithm=T1_ALGORITHM, limit=1)
    prod_page = repo.list_events(sort_algorithm=PRODUCTION_ALGORITHM, limit=1)
    assert [item["event_id"] for item in t1_page["events"]] == ["B"]
    assert [item["event_id"] for item in prod_page["events"]] == ["A"]
    with pytest.raises(InvalidCursorError):
        repo.list_events(
            sort_algorithm=T1_ALGORITHM,
            limit=1,
            cursor=prod_page["next_cursor"],
        )
