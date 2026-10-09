from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.access import request_owner_access_context
from app.services.catalysts import calendar_source
from app.services.catalysts.calendar_source import (
    CALENDAR_URL,
    CalendarBatch,
    calendar_stale,
    content_token,
    fetch_forexfactory,
    normalize_events,
    prune_snapshots,
    store_calendar,
)
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.news_sources import SourceError, SourceRequest, http_client
from test_catalyst_local_intelligence import _stack


FIXTURE = Path(__file__).parent / "fixtures" / "catalyst_sources" / "forexfactory_thisweek.json"
NOW = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)


def _raw_events() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _repository(tmp_path) -> CatalystEtlRepository:
    repository = CatalystEtlRepository(tmp_path / "catalyst-cache.db")
    repository.initialize()
    return repository


def _store(repository, events, *, now=NOW, not_modified=False):
    with repository.transaction() as connection:
        return store_calendar(
            repository,
            connection,
            CalendarBatch(events=events, not_modified=not_modified),
            fetched_at=now - timedelta(seconds=1),
            now=now,
        )


def _snapshots(repository) -> list[sqlite3.Row]:
    with sqlite3.connect(repository.path) as connection:
        connection.row_factory = sqlite3.Row
        return connection.execute(
            "SELECT * FROM macrolens_etl_calendar_snapshots ORDER BY snapshot_sequence"
        ).fetchall()


def _event_rows(repository) -> int:
    with sqlite3.connect(repository.path) as connection:
        return connection.execute("SELECT COUNT(*) FROM macrolens_etl_calendar_events").fetchone()[0]


def test_weekly_file_keeps_major_high_and_medium_events_with_news_feed_ids():
    raw = _raw_events()
    events = normalize_events(raw)

    assert len(raw) == 83
    assert len(events) == 12
    assert {event["impact"] for event in events} <= {"high", "medium"}
    assert {event["country_code"] for event in events} <= calendar_source.MAJOR_CURRENCIES
    first = events[0]
    source = next(
        item
        for item in raw
        if item["title"] == first["title"] and item["country"] == first["country_code"]
    )
    # News-feed models/database.py:1091-1099：原始日期串、币种大写、去空白标题以换行相连。
    expected = hashlib.sha256(
        "\n".join((source["date"], source["country"].upper(), source["title"].strip())).encode()
    ).hexdigest()
    assert first["event_id"] == expected
    assert first["currency"] == first["country_code"]
    assert len(first["scheduled_at_utc"]) == 20
    assert first["scheduled_at"] == first["scheduled_at_utc"]
    # 周文件不带实际值，实际值仍由读接口用 TradingView 补。
    assert all(event["actual"] is None for event in events)
    assert [event["scheduled_at_utc"] for event in events] == sorted(
        event["scheduled_at_utc"] for event in events
    )
    usd = next(event for event in events if event["country_code"] == "USD")
    assert usd["country"] == "🇺🇸 美国"
    assert usd["impact_zh"] in {"高", "中"}


def test_events_with_actual_values_are_kept_and_bad_dates_fail_the_fetch():
    rows = [
        {"title": "CPI m/m", "country": "usd", "date": "2026-10-14T08:30:00-04:00",
         "impact": "Low", "forecast": "", "previous": "0.3%", "actual": "0.2%"},
        {"title": "CPI m/m", "country": "usd", "date": "2026-10-14T08:30:00-04:00",
         "impact": "Low", "forecast": "", "previous": "0.3%", "actual": "0.2%"},
        {"title": "Retail Sales", "country": "NZD", "date": "2026-10-14T21:45:00-04:00",
         "impact": "High"},
    ]
    events = normalize_events(rows)
    assert len(events) == 1
    assert events[0]["actual"] == "0.2%"
    assert events[0]["forecast"] is None
    assert events[0]["scheduled_at_utc"] == "2026-10-14T12:30:00Z"

    with pytest.raises(SourceError, match="invalid_response"):
        normalize_events([{"title": "GDP", "country": "USD", "date": "2026-10-14 08:30", "impact": "High"}])
    with pytest.raises(SourceError, match="invalid_response"):
        normalize_events([])
    with pytest.raises(SourceError, match="invalid_response"):
        normalize_events({"events": []})


def test_unchanged_event_set_confirms_the_snapshot_without_moving_as_of(tmp_path):
    repository = _repository(tmp_path)
    events = normalize_events(_raw_events())

    first = _store(repository, events)
    later = NOW + timedelta(minutes=10)
    second = _store(repository, events, now=later)
    third = _store(repository, (), now=later + timedelta(minutes=10), not_modified=True)

    assert first.written is True and first.sequence == 1
    assert second.written is False and second.sequence == 1
    assert third.written is False
    snapshots = _snapshots(repository)
    assert len(snapshots) == 1
    assert snapshots[0]["as_of"] == "2026-10-09T14:00:00.000000Z"
    assert snapshots[0]["snapshot_token"] == content_token(events)
    assert snapshots[0]["data_through"] == "2026-10-09T14:19:59.000000Z"
    state = repository.state("calendar")
    assert state.completed_watermark_sequence == 1
    assert state.completed_as_of == "2026-10-09T14:20:00.000000Z"
    assert state.updated_after == state.completed_as_of
    assert state.last_error_code is None

    changed = list(events)
    changed[0] = {**changed[0], "forecast": "0.4%"}
    fourth = _store(repository, tuple(changed), now=later + timedelta(minutes=20))
    assert fourth.written is True and fourth.sequence == 2
    assert repository.state("calendar").completed_watermark_sequence == 2
    assert len(_snapshots(repository)) == 2


def test_large_event_sets_are_written_as_one_complete_snapshot(tmp_path):
    repository = _repository(tmp_path)
    rows = [
        {
            "title": f"Indicator {index:03d}",
            "country": "USD",
            "date": (datetime(2026, 10, 12, tzinfo=timezone.utc) + timedelta(hours=index)).isoformat(),
            "impact": "High",
            "forecast": "1.0%",
            "previous": "0.9%",
        }
        for index in range(120)
    ]
    stored = _store(repository, normalize_events(rows))

    assert stored.written is True and stored.events == 120
    snapshot = _snapshots(repository)[0]
    assert snapshot["complete"] == 1
    assert snapshot["last_ordinal"] == 120
    assert repository.state("calendar").cursor is None
    assert [event["ordinal"] for event in repository.calendar_events()] == list(range(1, 121))


def test_a_regressed_clock_never_moves_the_calendar_checkpoint_back(tmp_path):
    repository = _repository(tmp_path)
    events = normalize_events(_raw_events())
    _store(repository, events)
    _store(repository, events, now=NOW - timedelta(hours=1))

    assert repository.state("calendar").completed_as_of == "2026-10-09T14:00:00.000000Z"


def test_old_snapshots_are_pruned_in_bounded_batches_and_the_latest_stays(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    base = normalize_events(_raw_events())
    start = NOW - timedelta(days=12)
    for index in range(6):
        events = tuple({**event, "previous": f"{index}%"} for event in base)
        _store(repository, events, now=start + timedelta(days=index))
    assert len(_snapshots(repository)) == 6
    assert _event_rows(repository) == 6 * len(base)

    monkeypatch.setattr(calendar_source, "PRUNE_LIMIT_PER_REFRESH", 2)
    assert prune_snapshots(repository, now=NOW) == 2
    assert prune_snapshots(repository, now=NOW) == 2
    # 第 6 份是最新快照，即使早于 7 天也保留。
    assert prune_snapshots(repository, now=NOW) == 1
    assert prune_snapshots(repository, now=NOW) == 0
    remaining = _snapshots(repository)
    assert [row["snapshot_sequence"] for row in remaining] == [6]
    assert _event_rows(repository) == len(base)


def test_stored_calendar_reads_back_through_the_local_calendar_view(tmp_path):
    with request_owner_access_context(True):
        etl, _ai, intelligence = _stack(tmp_path)
    events = normalize_events(_raw_events())
    _store(etl, events)

    payload = intelligence.calendar(
        date_from=date(2026, 10, 3),
        date_to=date(2026, 10, 12),
        as_of=NOW + timedelta(minutes=1),
        currencies=None,
        min_impact=None,
    )

    assert len(payload["items"]) == len(events)
    usd = next(item for item in payload["items"] if item["currency"] == "USD")
    assert usd["country"] == "🇺🇸 美国"
    assert payload["data_through"] == "2026-10-09T13:59:59.000000Z"
    assert {item["event_id"] for item in payload["items"]} == {
        event["event_id"] for event in events
    }


def test_calendar_is_stale_only_after_a_day_without_success():
    assert calendar_stale(None, now=NOW) is True
    assert calendar_stale("2026-10-09T00:00:00.000000Z", now=NOW) is False
    assert calendar_stale("2026-10-08T13:59:00.000000Z", now=NOW) is True


def test_forexfactory_fetch_uses_validators_and_classifies_bad_payloads():
    requests: list[httpx.Request] = []
    body = FIXTURE.read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert str(request.url) == CALENDAR_URL
        if request.headers.get("if-none-match") == 'W/"6ac8ef37-2bbf"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            headers={"ETag": 'W/"6ac8ef37-2bbf"', "Content-Type": "application/json"},
            content=body,
        )

    async def scenario():
        async with http_client(transport=httpx.MockTransport(handler)) as client:
            first = await fetch_forexfactory(client, SourceRequest(now=NOW))
            second = await fetch_forexfactory(client, SourceRequest(now=NOW, cursor=first.cursor))
            return first, second

    first, second = asyncio.run(scenario())
    assert len(first.events) == 12
    assert json.loads(first.cursor)["etag"] == 'W/"6ac8ef37-2bbf"'
    assert second.not_modified is True
    assert second.cursor == first.cursor

    def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>Request denied</html>")

    async def broken_scenario():
        async with http_client(transport=httpx.MockTransport(broken)) as client:
            return await fetch_forexfactory(client, SourceRequest(now=NOW))

    with pytest.raises(SourceError, match="invalid_response"):
        asyncio.run(broken_scenario())


def test_confirming_a_snapshot_refuses_a_moved_checkpoint_or_an_earlier_time(tmp_path):
    from app.services.catalysts.etl_repository import EtlWatermarkConflict

    repository = _repository(tmp_path)
    _store(repository, normalize_events(_raw_events()))

    with repository.transaction() as connection:
        with pytest.raises(EtlWatermarkConflict, match="watermark_changed"):
            repository.touch_calendar_snapshot(
                connection,
                sequence=2,
                data_through="2026-10-09T14:05:00.000000Z",
                checked_at="2026-10-09T14:05:00.000000Z",
            )
        with pytest.raises(EtlWatermarkConflict, match="checkpoint_time_regressed"):
            repository.touch_calendar_snapshot(
                connection,
                sequence=1,
                data_through="2026-10-09T13:00:00.000000Z",
                checked_at="2026-10-09T13:00:00.000000Z",
            )


def test_clearing_pending_checkpoints_drops_a_remote_pagination_window(tmp_path):
    repository = _repository(tmp_path)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            """UPDATE macrolens_etl_state SET cursor='remote-cursor',
                   pending_watermark_sequence=9,pending_watermark_as_of='2026-10-09T10:00:00Z'
               WHERE stream='news'"""
        )
    before = repository.state("news")

    with repository.transaction() as connection:
        assert repository.clear_pending_checkpoints(connection) == 1

    after = repository.state("news")
    assert after.cursor is None
    assert after.pending_watermark_sequence is None
    assert after.pending_watermark_as_of is None
    assert after.generation == before.generation + 1
    assert after.completed_watermark_sequence == before.completed_watermark_sequence
