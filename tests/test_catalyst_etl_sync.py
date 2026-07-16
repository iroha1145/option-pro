from __future__ import annotations

import sqlite3

import httpx
import pytest

from app.services.catalysts.etl_client import (
    CalendarPage,
    EtlClientConfig,
    MacroLensEtlClient,
    NewsChangesPage,
)
from app.services.catalysts.etl_repository import (
    CatalystEtlRepository,
    EPOCH,
    SCHEMA_VERSION,
    EtlCheckpointConflict,
    EtlWatermarkConflict,
)
from app.services.catalysts.etl_sync import MacroLensIncrementalSync


AS_OF = "2026-07-15T12:00:00.123456Z"
NEXT_AS_OF = "2026-07-15T12:01:00.654321Z"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _news(news_id: int, *, sources: tuple[str, ...] = ("finnhub", "massive")) -> dict:
    return {
        "id": news_id,
        "source": sources[0],
        "title": f"Raw headline {news_id}",
        "summary": f"Raw summary {news_id}",
        "url": f"https://example.com/news/{news_id}",
        "image_url": None,
        "published_at": "2026-07-15T10:00:00Z",
        "fetched_at": "2026-07-15T10:01:00Z",
        "updated_at": "2020-01-01T00:00:00Z",
        "source_tickers": ["AMD", "NVDA"],
        "sources": list(sources),
        "source_count": len(sources),
        "content_hash": f"hash-{news_id}",
    }


def _upsert(sequence: int, news_id: int) -> dict:
    return {
        "sequence": sequence,
        "operation": "upsert",
        "changed_at": f"2026-07-15T11:0{sequence}:00Z",
        "source_updated_at": "2020-01-01T00:00:00Z",
        "available_at": f"2026-07-15T11:0{sequence}:00Z",
        "news": _news(news_id),
        "news_id": news_id,
    }


def _delete(sequence: int, news_id: int) -> dict:
    return {
        "sequence": sequence,
        "operation": "delete",
        "changed_at": f"2026-07-15T11:0{sequence}:00Z",
        "source_updated_at": f"2026-07-15T11:0{sequence}:00Z",
        "available_at": f"2026-07-15T11:0{sequence}:00Z",
        "news": None,
        "news_id": news_id,
    }


def _first_news_page() -> dict:
    return {
        "items": [_upsert(1, 7)],
        "has_more": True,
        "next_cursor": "news-page-2",
        "watermark": {"sequence": 2, "as_of": AS_OF},
        "next_updated_after": None,
        "next_after_sequence": None,
    }


def _second_news_page() -> dict:
    return {
        "items": [_delete(2, 7)],
        "has_more": False,
        "next_cursor": None,
        "watermark": {"sequence": 2, "as_of": AS_OF},
        "next_updated_after": AS_OF,
        "next_after_sequence": 2,
    }


@pytest.mark.anyio
async def test_news_sync_resumes_at_page_cursor_and_persists_delete_tombstone(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        cursor = request.url.params.get("cursor")
        return httpx.Response(
            200,
            json=_second_news_page() if cursor else _first_news_page(),
        )

    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-token"),
        transport=httpx.MockTransport(handler),
    ) as client:
        sync = MacroLensIncrementalSync(client, repository)
        interrupted = await sync.sync_news(max_pages=1)
        checkpoint = repository.state("news")
        resumed = await sync.sync_news()

    assert interrupted.complete is False
    assert checkpoint.cursor == "news-page-2"
    assert checkpoint.updated_after == EPOCH
    assert resumed.complete is True
    assert resumed.watermark_sequence == 2
    assert repository.state("news").updated_after == AS_OF
    assert requests[0].url.params["updated_after"] == EPOCH
    assert requests[0].url.params["after_sequence"] == "0"
    assert "cursor" not in requests[0].url.params
    assert requests[1].url.params["cursor"] == "news-page-2"
    assert "updated_after" not in requests[1].url.params
    assert "after_sequence" not in requests[1].url.params
    assert all(request.url.params["limit"] == "50" for request in requests)

    deleted = repository.get_news(7, include_deleted=True)
    assert deleted is not None
    assert deleted["deleted"] == 1
    assert deleted["title"] == "Raw headline 7"
    assert deleted["summary"] == "Raw summary 7"
    assert deleted["sources"] == ["finnhub", "massive"]
    assert deleted["source_observations"] == []
    assert deleted["raw"]["sources"] == ["finnhub", "massive"]
    assert repository.get_news(7) is None
    assert [row["change_sequence"] for row in repository.tombstones(7)] == [2]


@pytest.mark.anyio
async def test_expired_cursor_replays_from_last_completed_time_without_losing_late_data(
    tmp_path,
):
    phase = "partial"
    seen_queries: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal phase
        seen_queries.append(dict(request.url.params))
        if phase == "partial":
            phase = "resume"
            return httpx.Response(200, json=_first_news_page())
        if request.url.params.get("cursor") == "news-page-2":
            return httpx.Response(
                400,
                json={"detail": {"code": "invalid_cursor"}},
            )
        # The source timestamp is from 2020, but the local availability falls
        # after the saved 1970 cutoff and therefore survives the reset replay.
        return httpx.Response(
            200,
            json={
                "items": [_upsert(1, 7), _upsert(2, 8)],
                "has_more": False,
                "next_cursor": None,
                "watermark": {"sequence": 2, "as_of": NEXT_AS_OF},
                "next_updated_after": NEXT_AS_OF,
                "next_after_sequence": 2,
            },
        )

    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-token"),
        transport=httpx.MockTransport(handler),
    ) as client:
        sync = MacroLensIncrementalSync(client, repository)
        partial = await sync.sync_news(max_pages=1)
        resumed = await sync.sync_news()

    assert partial.complete is False
    assert partial.records == 1
    assert partial.replayed == 0
    assert resumed.complete is True
    assert resumed.cursor_resets == 1
    assert resumed.records == 1
    assert resumed.replayed == 1
    assert repository.state("news").reset_count == 1
    assert repository.state("news").updated_after == NEXT_AS_OF
    assert repository.get_news(8)["source_updated_at"] == "2020-01-01T00:00:00Z"
    assert seen_queries[1] == {"cursor": "news-page-2", "limit": "50"}
    assert seen_queries[2] == {
        "after_sequence": "0",
        "limit": "50",
        "updated_after": EPOCH,
    }


def _event(ordinal: int) -> dict:
    return {
        "event_id": f"event-{ordinal}",
        "country_code": "USD",
        "country": "美国",
        "title": f"经济事件 {ordinal}",
        "impact": "high",
        "impact_zh": "高",
        "scheduled_at": f"2026-07-16T0{ordinal}:00:00-04:00",
        "scheduled_at_utc": f"2026-07-16T0{ordinal + 4}:00:00Z",
        "forecast": "1",
        "previous": "0",
        "actual": None,
        "is_stale": False,
        "source_fetched_at": "2026-07-15T11:00:00Z",
        "available_at": "2026-07-15T11:01:00Z",
        "ordinal": ordinal,
    }


@pytest.mark.anyio
async def test_calendar_sync_keeps_frozen_snapshot_across_pages(tmp_path):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        is_second = request.url.params.get("cursor") == "calendar-page-2"
        return httpx.Response(
            200,
            json={
                "items": [_event(2)] if is_second else [_event(1)],
                "has_more": not is_second,
                "next_cursor": None if is_second else "calendar-page-2",
                "watermark": {
                    "sequence": 4,
                    "snapshot_token": "cal_" + "a" * 40,
                    "as_of": AS_OF,
                },
                "data_through": "2026-07-15T11:00:00Z",
                "is_stale": False,
                "next_updated_after": AS_OF if is_second else None,
                "next_after_sequence": 4 if is_second else None,
            },
        )

    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-token"),
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await MacroLensIncrementalSync(client, repository).sync_calendar()

    assert result.complete is True
    assert result.records == 2
    assert [item["event_id"] for item in repository.calendar_events()] == [
        "event-1",
        "event-2",
    ]
    assert requests[0].url.params["after_sequence"] == "0"
    assert requests[1].url.params["cursor"] == "calendar-page-2"
    assert "after_sequence" not in requests[1].url.params


@pytest.mark.anyio
async def test_changed_watermark_rejects_page_without_advancing_checkpoint(tmp_path):
    first_round = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_round
        if first_round:
            first_round = False
            return httpx.Response(200, json=_first_news_page())
        changed = _second_news_page()
        changed["watermark"] = {"sequence": 2, "as_of": NEXT_AS_OF}
        changed["next_updated_after"] = NEXT_AS_OF
        return httpx.Response(200, json=changed)

    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-token"),
        transport=httpx.MockTransport(handler),
    ) as client:
        sync = MacroLensIncrementalSync(client, repository)
        await sync.sync_news(max_pages=1)
        with pytest.raises(EtlWatermarkConflict):
            await sync.sync_news()

    checkpoint = repository.state("news")
    assert checkpoint.cursor == "news-page-2"
    assert checkpoint.updated_after == EPOCH
    assert repository.get_news(7, include_deleted=True)["deleted"] == 0


def test_each_page_and_checkpoint_are_one_sqlite_transaction(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    repository.initialize()
    with sqlite3.connect(repository.path) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {
        "macrolens_etl_state",
        "macrolens_etl_news",
        "macrolens_etl_news_changes",
        "macrolens_etl_news_tombstones",
        "macrolens_etl_calendar_snapshots",
        "macrolens_etl_calendar_events",
    } <= tables


def test_initialize_migrates_legacy_state_rows_with_a_generation(tmp_path):
    path = tmp_path / "legacy-catalyst.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE macrolens_etl_schema (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            INSERT INTO macrolens_etl_schema VALUES(
                'macrolens-etl-local-v1','legacy-checksum','2026-07-15T00:00:00Z'
            );
            CREATE TABLE macrolens_etl_state (
                stream TEXT PRIMARY KEY,
                cursor TEXT,
                updated_after TEXT NOT NULL,
                pending_watermark_sequence INTEGER,
                pending_watermark_as_of TEXT,
                pending_snapshot_token TEXT,
                completed_watermark_sequence INTEGER NOT NULL DEFAULT 0,
                completed_as_of TEXT,
                reset_count INTEGER NOT NULL DEFAULT 0,
                last_success_at TEXT,
                last_error_code TEXT,
                updated_at TEXT NOT NULL
            );
            INSERT INTO macrolens_etl_state(stream,cursor,updated_after,updated_at)
            VALUES
                ('news',NULL,'1970-01-01T00:00:00Z','2026-07-15T00:00:00Z'),
                ('calendar',NULL,'1970-01-01T00:00:00Z','2026-07-15T00:00:00Z');
            """
        )

    repository = CatalystEtlRepository(path)
    repository.initialize()

    assert repository.state("news").generation == 0
    with sqlite3.connect(path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(macrolens_etl_state)"
            ).fetchall()
        }
        versions = {
            row[0]
            for row in connection.execute(
                "SELECT version FROM macrolens_etl_schema"
            ).fetchall()
        }
    assert "generation" in columns
    assert SCHEMA_VERSION in versions


def test_generation_rejects_a_stale_none_cursor_page_without_regressing_state(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    repository.initialize()
    initial = repository.state("news")
    newer = NewsChangesPage.model_validate(
        {
            "items": [_upsert(1, 7), _upsert(2, 8)],
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": 2, "as_of": NEXT_AS_OF},
            "next_updated_after": NEXT_AS_OF,
            "next_after_sequence": 2,
        }
    )
    stale = NewsChangesPage.model_validate(
        {
            "items": [_upsert(1, 7)],
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": 1, "as_of": AS_OF},
            "next_updated_after": AS_OF,
            "next_after_sequence": 1,
        }
    )

    repository.apply_news_page(
        newer,
        expected_cursor=initial.cursor,
        expected_generation=initial.generation,
    )
    with pytest.raises(EtlCheckpointConflict):
        repository.apply_news_page(
            stale,
            expected_cursor=initial.cursor,
            expected_generation=initial.generation,
        )

    current = repository.state("news")
    assert current.generation == initial.generation + 1
    assert current.completed_watermark_sequence == 2
    assert current.updated_after == NEXT_AS_OF
    assert repository.get_news(8) is not None


def test_sequence_regression_rolls_back_the_whole_news_page(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    repository.initialize()
    initial = repository.state("news")
    current_page = NewsChangesPage.model_validate(
        {
            "items": [_upsert(1, 7), _upsert(2, 8)],
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": 2, "as_of": NEXT_AS_OF},
            "next_updated_after": NEXT_AS_OF,
            "next_after_sequence": 2,
        }
    )
    repository.apply_news_page(
        current_page,
        expected_cursor=initial.cursor,
        expected_generation=initial.generation,
    )
    before = repository.state("news")
    regression = NewsChangesPage.model_validate(
        {
            "items": [_delete(1, 7)],
            "has_more": False,
            "next_cursor": None,
            "watermark": {"sequence": 1, "as_of": AS_OF},
            "next_updated_after": AS_OF,
            "next_after_sequence": 1,
        }
    )

    with pytest.raises(EtlWatermarkConflict, match="sequence_regressed"):
        repository.apply_news_page(
            regression,
            expected_cursor=before.cursor,
            expected_generation=before.generation,
        )

    after = repository.state("news")
    assert after.generation == before.generation
    assert after.completed_watermark_sequence == 2
    assert repository.get_news(7) is not None
    assert repository.tombstones(7) == []


def _calendar_page(sequence: int, *, as_of: str) -> CalendarPage:
    return CalendarPage.model_validate(
        {
            "items": [_event(1)],
            "has_more": False,
            "next_cursor": None,
            "watermark": {
                "sequence": sequence,
                "snapshot_token": "cal_" + str(sequence) * 40,
                "as_of": as_of,
            },
            "data_through": "2026-07-15T11:00:00Z",
            "is_stale": False,
            "next_updated_after": as_of,
            "next_after_sequence": sequence,
        }
    )


def test_calendar_generation_rejects_a_stale_none_cursor_page(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    repository.initialize()
    initial = repository.state("calendar")

    repository.apply_calendar_page(
        _calendar_page(2, as_of=NEXT_AS_OF),
        expected_cursor=initial.cursor,
        expected_generation=initial.generation,
    )
    with pytest.raises(EtlCheckpointConflict):
        repository.apply_calendar_page(
            _calendar_page(1, as_of=AS_OF),
            expected_cursor=initial.cursor,
            expected_generation=initial.generation,
        )

    current = repository.state("calendar")
    assert current.generation == initial.generation + 1
    assert current.completed_watermark_sequence == 2
    assert current.updated_after == NEXT_AS_OF
    assert [item["event_id"] for item in repository.calendar_events()] == [
        "event-1"
    ]
    assert repository.calendar_events(snapshot_sequence=1) == []


def test_calendar_sequence_regression_rolls_back_the_whole_page(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    repository.initialize()
    initial = repository.state("calendar")
    repository.apply_calendar_page(
        _calendar_page(2, as_of=NEXT_AS_OF),
        expected_cursor=initial.cursor,
        expected_generation=initial.generation,
    )
    before = repository.state("calendar")

    with pytest.raises(EtlWatermarkConflict, match="sequence_regressed"):
        repository.apply_calendar_page(
            _calendar_page(1, as_of=AS_OF),
            expected_cursor=before.cursor,
            expected_generation=before.generation,
        )

    after = repository.state("calendar")
    assert after.generation == before.generation
    assert after.completed_watermark_sequence == 2
    assert [item["event_id"] for item in repository.calendar_events()] == [
        "event-1"
    ]
    assert repository.calendar_events(snapshot_sequence=1) == []


@pytest.mark.anyio
async def test_delete_without_prior_upsert_still_creates_durable_tombstone(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [_delete(1, 99)],
                "has_more": False,
                "next_cursor": None,
                "watermark": {"sequence": 1, "as_of": AS_OF},
                "next_updated_after": AS_OF,
                "next_after_sequence": 1,
            },
        )

    repository = CatalystEtlRepository(tmp_path / "catalyst.db")
    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-token"),
        transport=httpx.MockTransport(handler),
    ) as client:
        await MacroLensIncrementalSync(client, repository).sync_news()

    deleted = repository.get_news(99, include_deleted=True)
    assert deleted is not None
    assert deleted["deleted"] == 1
    assert deleted["title"] is None
    assert [row["news_id"] for row in repository.tombstones()] == [99]
