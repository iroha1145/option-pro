from __future__ import annotations

import asyncio
import random
import json
from datetime import datetime, timezone

from app.services.catalysts.errors import CatalystError
from app.services.catalysts.errors import CatalystRepositoryError
from app.services.catalysts.client import MacroLensClient
import httpx
import pytest
from app.services.catalysts.repository import CatalystRepository
from app.services.catalysts.sync_service import CatalystSyncService
from catalyst_support import catalyst_item, utc
from test_catalyst_sync_worker import LatestClient, page, settings


def _time(day: int, hour: int = 10) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=timezone.utc)


def _seed(repository: CatalystRepository, *, sequence: int = 1) -> dict:
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-old", now=utc(10, 4)
    )
    repository.stage_latest_page(
        run_id,
        [catalyst_item(sequence=sequence, updated_at=utc(10, 4), analysis=False)],
    )
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-old",
        data_through=utc(10, 4),
        next_updated_after=utc(10, 4),
        watermark_sequence=sequence,
        now=utc(10, 4),
    )
    return repository.sync_state("feed")


class RecordingLatestClient(LatestClient):
    def __init__(self, responses):
        super().__init__(responses)
        self.requests: list[dict] = []

    async def latest(self, **kwargs):
        self.requests.append(kwargs)
        return await super().latest(**kwargs)


def _service(repository, client, now):
    return CatalystSyncService(
        settings(repository.path),
        repository,
        client,
        worker_id="worker-resync",
        clock=lambda: now,
        rng=random.Random(1),
    )


def test_six_day_disconnect_remains_normal_incremental(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    _seed(repository)
    client = RecordingLatestClient(
        [page(token="snapshot-inc", items=[], cursor=None, has_more=False)]
    )
    service = _service(repository, client, _time(17))
    assert service.acquire()
    assert asyncio.run(service.sync_latest())
    assert client.requests[0]["updated_after"] == utc(9, 59)
    state = repository.sync_state("feed")
    assert state["resync_required"] == 0
    assert state["resync_generation"] == 0


def test_eight_day_disconnect_uses_remote_boundary_and_atomically_resyncs(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = _seed(repository, sequence=100)
    boundary = _time(12)
    client = RecordingLatestClient(
        [
            CatalystError(
                "updated_after_too_old",
                "fixture expired watermark",
                False,
                resync_from=boundary,
            ),
            page(
                token="snapshot-resync",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor="resync-2",
                has_more=True,
            ).model_copy(update={"next_updated_after": _time(18)}),
            page(
                token="snapshot-resync", items=[], cursor=None, has_more=False
            ).model_copy(update={"next_updated_after": _time(19, 9)}),
        ]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()
    assert asyncio.run(service.sync_latest())
    assert client.requests[0]["updated_after"] == utc(9, 59)
    assert client.requests[1]["updated_after"] == boundary
    assert client.requests[2]["updated_after"] == boundary
    state = repository.sync_state("feed")
    assert state["resync_required"] == 0
    assert state["resync_generation"] == 1
    assert state["last_resync_at"] == "2026-07-19T10:00:00Z"
    assert state["watermark_sequence"] == 2
    assert state["current_snapshot_id"] != old["current_snapshot_id"]
    with repository.open_read_connection() as connection:
        run = connection.execute(
            "SELECT sync_mode,resync_generation,status FROM catalyst_sync_runs "
            "WHERE snapshot_token='snapshot-resync'"
        ).fetchone()
    assert tuple(run) == ("resync", 1, "completed")


def test_resync_midpage_failure_keeps_old_stale_snapshot_readable(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = _seed(repository)
    boundary = _time(12)
    client = RecordingLatestClient(
        [
            CatalystError("updated_after_too_old", "expired", False, resync_from=boundary),
            page(
                token="snapshot-fails",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor="page-2",
                has_more=True,
            ),
            CatalystError("network_error", "fixture failure", True),
        ]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()
    assert not asyncio.run(service.sync_latest())
    state = repository.sync_state("feed")
    assert state["resync_required"] == 1
    assert state["resync_generation"] == 0
    assert state["current_snapshot_id"] == old["current_snapshot_id"]
    assert state["watermark_sequence"] == 1
    status = repository.status_snapshot(
        stale_ttl_seconds=60,
        feed_interval_seconds=10,
        action_enabled=False,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v2",
        now=_time(28),
    )
    assert status["status"] == "stale"
    assert status["resync_required"] is True
    assert status["streams"]["feed"]["resync_required"] == 1
    assert repository.get_news(101, as_of=_time(28))["change_sequence"] == 1
    with repository.open_read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM catalyst_staging_items").fetchone()[0] == 0


def test_projection_conflict_keeps_its_error_code_while_latching_resync(
    tmp_path,
    monkeypatch,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = _seed(repository)
    client = RecordingLatestClient(
        [
            page(
                token="snapshot-conflict",
                items=[],
                cursor=None,
                has_more=False,
            ).model_copy(update={"next_updated_after": _time(19)}),
            page(
                token="snapshot-conflict-resync",
                items=[],
                cursor=None,
                has_more=False,
            ).model_copy(update={"next_updated_after": _time(19)}),
        ]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()

    def conflict(*args, **kwargs):
        raise CatalystRepositoryError(
            "projection_payload_conflict",
            "fixture immutable projection conflict",
        )

    monkeypatch.setattr(repository, "publish_latest", conflict)
    assert not asyncio.run(service.sync_latest())
    assert client.calls == 2
    assert client.requests[1]["updated_after"] == _time(12)

    state = repository.sync_state("feed")
    assert state["resync_required"] == 1
    assert state["last_error_code"] == "projection_payload_conflict"
    assert state["current_snapshot_id"] == old["current_snapshot_id"]
    status = repository.status_snapshot(
        stale_ttl_seconds=60,
        feed_interval_seconds=10,
        action_enabled=False,
        model="gpt-5.6-terra",
        reasoning="max",
        schema_version="macrolens-option-pro-v2",
        now=_time(19),
    )
    assert "projection_payload_conflict" in status["warnings"]
    assert "updated_after_too_old" not in status["warnings"]


def test_resync_snapshot_change_never_publishes_partial_generation(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = _seed(repository)
    boundary = _time(12)
    client = RecordingLatestClient(
        [
            CatalystError("updated_after_too_old", "expired", False, resync_from=boundary),
            page(
                token="snapshot-a",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor="page-2",
                has_more=True,
            ),
            page(token="snapshot-b", items=[], cursor=None, has_more=False),
        ]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()
    assert not asyncio.run(service.sync_latest())
    state = repository.sync_state("feed")
    assert state["resync_required"] == 1
    assert state["current_snapshot_id"] == old["current_snapshot_id"]
    assert state["watermark_sequence"] == 1


def test_successful_resync_returns_to_incremental_watermark(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    _seed(repository)
    boundary = _time(12)
    resync_client = RecordingLatestClient(
        [
            CatalystError("updated_after_too_old", "expired", False, resync_from=boundary),
            page(
                token="snapshot-resync",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor=None,
                has_more=False,
            ).model_copy(update={"next_updated_after": _time(19, 9)}),
        ]
    )
    first = _service(repository, resync_client, _time(19))
    assert first.acquire()
    assert asyncio.run(first.sync_latest())
    first.release()

    incremental_client = RecordingLatestClient(
        [
            page(token="snapshot-next", items=[], cursor=None, has_more=False).model_copy(
                update={"next_updated_after": _time(19, 10)}
            )
        ]
    )
    second = CatalystSyncService(
        settings(repository.path),
        repository,
        incremental_client,
        worker_id="worker-incremental",
        clock=lambda: _time(19, 11),
    )
    assert second.acquire()
    assert asyncio.run(second.sync_latest())
    assert incremental_client.requests[0]["updated_after"] == datetime(
        2026, 7, 19, 8, 55, tzinfo=timezone.utc
    )
    assert repository.sync_state("feed")["resync_generation"] == 1


def test_missing_remote_resync_boundary_latches_without_reusing_old_watermark(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    _seed(repository)
    client = RecordingLatestClient(
        [CatalystError("updated_after_too_old", "expired", False, resync_from=None)]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()
    assert not asyncio.run(service.sync_latest())
    assert client.calls == 1
    assert repository.sync_state("feed")["resync_required"] == 1
    assert not asyncio.run(service.sync_latest())
    assert client.calls == 1


@pytest.mark.parametrize(
    ("final_watermark", "expected_error"),
    [
        (None, "resync_watermark_missing"),
        (_time(11), "watermark_regression"),
    ],
)
def test_resync_requires_an_authoritative_final_watermark(
    tmp_path,
    final_watermark,
    expected_error,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = _seed(repository)
    boundary = _time(12)
    final_page = page(
        token="snapshot-invalid-watermark",
        items=[],
        cursor=None,
        has_more=False,
    ).model_copy(update={"next_updated_after": final_watermark})
    client = RecordingLatestClient(
        [
            CatalystError(
                "updated_after_too_old",
                "expired",
                False,
                resync_from=boundary,
            ),
            final_page,
        ]
    )
    service = _service(repository, client, _time(19))
    assert service.acquire()

    assert not asyncio.run(service.sync_latest())
    state = repository.sync_state("feed")
    assert state["resync_required"] == 1
    assert state["resync_generation"] == 0
    assert state["current_snapshot_id"] == old["current_snapshot_id"]
    assert state["updated_after"] == old["updated_after"]
    with repository.open_read_connection() as connection:
        failed = connection.execute(
            "SELECT status,error_code FROM catalyst_sync_runs "
            "WHERE snapshot_token='snapshot-invalid-watermark'"
        ).fetchone()
    assert tuple(failed) == ("failed", expected_error)


def test_remote_error_boundary_is_parsed_and_strictly_bounded() -> None:
    response = httpx.Response(400, request=httpx.Request("GET", "https://macro.example/latest"))
    body = json.dumps(
        {
            "code": "updated_after_too_old",
            "retryable": False,
            "server_time": "2026-07-19T10:00:00Z",
            "latest_window_days": 7,
            "resync_from": "2026-07-12T10:00:00Z",
        }
    ).encode()
    error = MacroLensClient._status_error(response, body)
    assert error.code == "updated_after_too_old"
    assert error.resync_from == _time(12)

    unbounded = json.dumps(
        {
            "code": "updated_after_too_old",
            "server_time": "2026-07-19T10:00:00Z",
            "latest_window_days": 7,
            "resync_from": "2026-07-01T10:00:00Z",
        }
    ).encode()
    assert MacroLensClient._status_error(response, unbounded).resync_from is None

    missing = json.dumps(
        {
            "code": "updated_after_too_old",
            "server_time": "2026-07-19T10:00:00Z",
            "latest_window_days": 7,
        }
    ).encode()
    assert MacroLensClient._status_error(response, missing).resync_from is None
