from __future__ import annotations

import asyncio
import random
from datetime import timedelta

import pytest

from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.errors import CatalystError
from app.services.catalysts.models import LatestResponse, RemoteJobResponse
from app.services.catalysts.repository import CatalystRepository
from app.services.catalysts.sync_service import CatalystSyncService
from catalyst_support import catalyst_item, utc


SCHEMA_SHA = "a" * 64
READ_SECRET = "read-secret-0123456789abcdef-0001"
ACTION_SECRET = "action-secret-0123456789abcdef-01"


def settings(path, **overrides) -> CatalystSettings:
    values = {
        "MACROLENS_ENABLED": True,
        "MACROLENS_BASE_URL": "http://localhost:9876",
        "MACROLENS_ALLOW_LOCAL_HTTP": True,
        "MACROLENS_READ_KEY_ID": "read-key",
        "MACROLENS_READ_SECRET": READ_SECRET,
        "MACROLENS_ACTION_KEY_ID": "action-key",
        "MACROLENS_ACTION_SECRET": ACTION_SECRET,
        "MACROLENS_CACHE_DB_PATH": path,
        "MACROLENS_SCHEMA_SHA256": SCHEMA_SHA,
        "MACROLENS_LATEST_PAGE_LIMIT": 1,
    }
    values.update(overrides)
    return CatalystSettings(_env_file=None, **values)


def page(*, token: str, items: list, cursor: str | None, has_more: bool) -> LatestResponse:
    return LatestResponse(
        schema_sha256=SCHEMA_SHA,
        request_id="request-1234",
        snapshot_token=token,
        data_through=utc(10, 8),
        next_updated_after=utc(10, 8),
        next_cursor=cursor,
        has_more=has_more,
        items=items,
    )


class LatestClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def latest(self, **_kwargs):
        value = self.responses[self.calls]
        self.calls += 1
        if isinstance(value, Exception):
            raise value
        return value


def test_incremental_worker_publishes_only_after_all_snapshot_pages(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    client = LatestClient(
        [
            page(
                token="snapshot-good",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor="cursor-2",
                has_more=True,
            ),
            page(
                token="snapshot-good",
                items=[
                    catalyst_item(
                        sequence=3,
                        updated_at=utc(10, 7),
                        analysis=False,
                        news_id=102,
                        ticker="AMD",
                    )
                ],
                cursor=None,
                has_more=False,
            ),
        ]
    )
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-good",
        clock=lambda: utc(10, 8),
        rng=random.Random(1),
    )
    assert service.acquire()
    assert asyncio.run(service.sync_latest())
    state = repository.sync_state("feed")
    assert state["watermark_sequence"] == 3
    assert state["updated_after"] == "2026-07-11T10:08:00Z"
    assert repository.get_news(101, as_of=utc(10, 8))["analysis"] is not None
    assert repository.get_news(102, as_of=utc(10, 8))["analysis"] is None


def test_incremental_overlap_page_keeps_watermark_monotonic_until_final_as_of(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    initial = LatestClient(
        [
            page(
                token="snapshot-old",
                items=[catalyst_item(sequence=1, updated_at=utc(10, 8), analysis=False)],
                cursor=None,
                has_more=False,
            )
        ]
    )
    first = CatalystSyncService(
        settings(repository.path),
        repository,
        initial,  # type: ignore[arg-type]
        worker_id="worker-old",
        clock=lambda: utc(10, 8),
    )
    assert first.acquire()
    assert asyncio.run(first.sync_latest())
    first.release()

    overlap_watermark = utc(10, 8) - timedelta(seconds=43)
    client = LatestClient(
        [
            page(
                token="snapshot-overlap",
                items=[
                    catalyst_item(
                        sequence=2,
                        updated_at=overlap_watermark,
                        analysis=True,
                        news_id=102,
                    )
                ],
                cursor="cursor-final",
                has_more=True,
            ).model_copy(update={"next_updated_after": overlap_watermark}),
            page(
                token="snapshot-overlap",
                items=[],
                cursor=None,
                has_more=False,
            ).model_copy(update={"next_updated_after": utc(10, 9)}),
        ]
    )
    second = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-overlap",
        clock=lambda: utc(10, 9),
        rng=random.Random(1),
    )
    assert second.acquire()

    assert asyncio.run(second.sync_latest())
    state = repository.sync_state("feed")
    assert state["updated_after"] == "2026-07-11T10:09:00Z"
    assert state["watermark_sequence"] == 2
    assert state["last_error_code"] is None
    assert repository.get_news(102, as_of=utc(10, 9))["analysis"] is not None


def test_failed_second_page_keeps_previous_watermark_and_snapshot(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    initial = LatestClient(
        [
            page(
                token="snapshot-old",
                items=[catalyst_item(sequence=1, updated_at=utc(10, 4), analysis=False)],
                cursor=None,
                has_more=False,
            )
        ]
    )
    first = CatalystSyncService(
        settings(repository.path),
        repository,
        initial,  # type: ignore[arg-type]
        worker_id="worker-old",
        clock=lambda: utc(10, 4),
    )
    assert first.acquire()
    assert asyncio.run(first.sync_latest())
    first_state = repository.sync_state("feed")
    first.release()

    failing = LatestClient(
        [
            page(
                token="snapshot-new",
                items=[catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True)],
                cursor="cursor-next",
                has_more=True,
            ),
            CatalystError("network_error", "fixture network failure", True),
        ]
    )
    second = CatalystSyncService(
        settings(repository.path),
        repository,
        failing,  # type: ignore[arg-type]
        worker_id="worker-new",
        clock=lambda: utc(10, 7),
        rng=random.Random(1),
    )
    assert second.acquire()
    assert not asyncio.run(second.sync_latest())
    state = repository.sync_state("feed")
    assert state["watermark_sequence"] == 1
    assert state["current_snapshot_id"] == first_state["current_snapshot_id"]
    assert repository.get_news(101, as_of=utc(10, 8))["analysis"] is None
    with repository.open_read_connection() as connection:
        assert connection.execute("SELECT count(*) FROM catalyst_staging_items").fetchone()[0] == 0


class JobClient:
    def __init__(self) -> None:
        self.cancelled = False

    @staticmethod
    def response(
        status: str,
        *,
        model: str = "gpt-5.6-terra",
        reasoning: str = "max",
        result=None,
    ) -> RemoteJobResponse:
        return RemoteJobResponse(
            schema_sha256=SCHEMA_SHA,
            request_id="request-1234",
            job_id="remote-job-1234",
            news_id=101,
            content_hash="content-hash-101",
            input_hash="b" * 64,
            change_sequence=1,
            status=status,
            model=model,
            reasoning=reasoning,
            submitted_at=utc(10, 7),
            updated_at=utc(10, 7),
            completed_at=utc(10, 8) if status == "cancelled" else None,
            error_code=None,
            retry_after=None,
            result=result,
        )

    async def create_analysis_job(
        self,
        _news_id: int,
        *,
        expected_content_hash: str,
        expected_change_sequence: int,
        force: bool,
    ):
        assert not force
        assert expected_content_hash == "content-hash-101"
        assert expected_change_sequence == 1
        return self.response("queued")

    async def get_analysis_job(self, _job_id: str):
        return self.response("in_progress")

    async def cancel_analysis_job(self, _job_id: str):
        self.cancelled = True
        return self.response("cancelled")


def test_local_job_id_is_opaque_and_remote_job_id_never_enters_public_result(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-jobs", now=utc(10, 4)
    )
    repository.stage_latest_page(
        run_id, [catalyst_item(sequence=1, updated_at=utc(10, 4), analysis=False)]
    )
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-jobs",
        data_through=utc(10, 4),
        next_updated_after=utc(10, 4),
        watermark_sequence=1,
        now=utc(10, 4),
    )
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )
    assert len(local["job_id"]) == 32
    client = JobClient()
    clock_value = [utc(10, 7)]
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-jobs",
        clock=lambda: clock_value[0],
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    public = repository.get_analysis_job(local["job_id"])
    assert public["status"] == "queued"
    assert "remote_job_id" not in public
    assert "remote-job-1234" not in str(public)

    repository.request_job_cancel(local["job_id"], now=utc(10, 7))
    clock_value[0] = utc(10, 8)
    assert asyncio.run(service.process_jobs()) == 1
    cancelled = repository.get_analysis_job(local["job_id"])
    assert cancelled["status"] == "cancelled"
    assert client.cancelled


def test_remote_job_with_wrong_news_id_is_rejected_without_persisting_result(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )

    class WrongNewsClient(JobClient):
        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            return self.response("queued").model_copy(update={"news_id": 999})

    service = CatalystSyncService(
        settings(repository.path),
        repository,
        WrongNewsClient(),  # type: ignore[arg-type]
        worker_id="worker-wrong-news",
        clock=lambda: utc(10, 7),
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    failed = repository.get_analysis_job(local["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "remote_job_identity_mismatch"
    assert failed["result"] is None


def test_claimed_proxy_cancel_before_submission_never_calls_remote_create(
    monkeypatch,
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )

    class CountingClient(JobClient):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls = 0

        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            self.create_calls += 1
            return await super().create_analysis_job(
                _news_id,
                expected_content_hash=expected_content_hash,
                expected_change_sequence=expected_change_sequence,
                force=force,
            )

    client = CountingClient()
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-pre-submit-cancel",
        clock=lambda: utc(10, 7),
    )
    assert service.acquire()
    stale_claim = repository.due_jobs(
        service.worker_id,
        limit=1,
        lease_seconds=30,
        now=utc(10, 7),
    )[0]
    first = repository.request_job_cancel(local["job_id"], now=utc(10, 7))
    second = repository.request_job_cancel(local["job_id"], now=utc(10, 7))
    monkeypatch.setattr(repository, "due_jobs", lambda *_args, **_kwargs: [stale_claim])

    assert asyncio.run(service.process_jobs()) == 1
    final = repository.get_analysis_job(local["job_id"])
    assert first["status"] == "cancelled"
    assert second["status"] == "cancelled"
    assert final["status"] == "cancelled"
    assert client.create_calls == 0


def test_cancel_during_remote_create_persists_id_then_cancels_idempotently(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )

    class CancelDuringCreateClient(JobClient):
        def __init__(self) -> None:
            super().__init__()
            self.create_calls = 0
            self.cancel_calls = 0

        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            self.create_calls += 1
            cancelled = repository.request_job_cancel(local["job_id"], now=utc(10, 7))
            assert cancelled["status"] == "in_progress"
            assert cancelled["cancel_requested"] is True
            return await super().create_analysis_job(
                _news_id,
                expected_content_hash=expected_content_hash,
                expected_change_sequence=expected_change_sequence,
                force=force,
            )

        async def cancel_analysis_job(self, _job_id: str):
            self.cancel_calls += 1
            return await super().cancel_analysis_job(_job_id)

    client = CancelDuringCreateClient()
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-mid-submit-cancel",
        clock=lambda: utc(10, 7),
    )
    assert service.acquire()

    assert asyncio.run(service.process_jobs()) == 1
    after_create = repository.get_analysis_job(local["job_id"])
    assert after_create["status"] == "in_progress"
    assert after_create["cancel_requested"] is True
    with repository.open_read_connection() as connection:
        stored = connection.execute(
            "SELECT remote_job_id,next_attempt_at FROM catalyst_analysis_jobs "
            "WHERE local_job_id=?",
            (local["job_id"],),
        ).fetchone()
    assert stored["remote_job_id"] == "remote-job-1234"
    assert stored["next_attempt_at"] == "2026-07-11T10:07:00Z"
    assert client.create_calls == 1
    assert client.cancel_calls == 0

    assert asyncio.run(service.process_jobs()) == 1
    cancelled = repository.get_analysis_job(local["job_id"])
    assert cancelled["status"] == "cancelled"
    assert client.create_calls == 1
    assert client.cancel_calls == 1

    repeated = repository.request_job_cancel(local["job_id"], now=utc(10, 7))
    assert repeated["status"] == "cancelled"
    assert asyncio.run(service.process_jobs()) == 0
    assert client.create_calls == 1
    assert client.cancel_calls == 1


@pytest.mark.parametrize(
    ("remote_update", "error_code"),
    [
        ({"content_hash": "different-content-hash"}, "remote_job_input_mismatch"),
        ({"change_sequence": 2}, "remote_job_input_mismatch"),
        ({"model": "gpt-4o-mini", "reasoning": "low"}, "remote_job_runtime_mismatch"),
    ],
)
def test_crossed_remote_revision_or_runtime_is_rejected(
    tmp_path, remote_update: dict, error_code: str
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )

    class CrossedClient(JobClient):
        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            return self.response("queued").model_copy(update=remote_update)

    service = CatalystSyncService(
        settings(repository.path),
        repository,
        CrossedClient(),  # type: ignore[arg-type]
        worker_id="worker-crossed-response",
        clock=lambda: utc(10, 7),
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    failed = repository.get_analysis_job(local["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == error_code
    assert failed["result"] is None


def test_completed_result_metadata_must_match_its_remote_job(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )
    analysis = catalyst_item(
        sequence=1, updated_at=utc(10, 7), analysis=True
    ).analysis.model_copy(update={"model": "gpt-4o-mini", "reasoning": "low"})

    class CrossedResultClient(JobClient):
        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            return self.response("completed", result=analysis)

    service = CatalystSyncService(
        settings(repository.path),
        repository,
        CrossedResultClient(),  # type: ignore[arg-type]
        worker_id="worker-crossed-result",
        clock=lambda: utc(10, 7),
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    failed = repository.get_analysis_job(local["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "remote_job_result_mismatch"
    assert failed["result"] is None


def test_remote_poll_with_wrong_job_id_is_rejected(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 7),
    )

    class WrongJobClient(JobClient):
        def __init__(self) -> None:
            super().__init__()
            self.created = False

        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            self.created = True
            return self.response("queued")

        async def get_analysis_job(self, _job_id: str):
            return self.response("in_progress").model_copy(
                update={"job_id": "remote-job-different"}
            )

    client = WrongJobClient()
    clock = [utc(10, 7)]
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-wrong-job",
        clock=lambda: clock[0],
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    assert repository.get_analysis_job(local["job_id"])["status"] == "queued"

    clock[0] = utc(10, 8)
    assert asyncio.run(service.process_jobs()) == 1
    failed = repository.get_analysis_job(local["job_id"])
    assert failed["status"] == "failed"
    assert failed["error_code"] == "remote_job_identity_mismatch"
    assert failed["result"] is None


def test_slow_remote_failure_refreshes_local_lease_heartbeat_and_stays_healthy(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    clock = [utc(9)]

    class SlowFailureClient:
        async def health(self):
            # Simulate a request lasting longer than both the original lease
            # and the local health heartbeat threshold without sleeping in CI.
            clock[0] = clock[0] + timedelta(seconds=35)
            raise CatalystError("remote_timeout", "fixture timeout", True)

    service = CatalystSyncService(
        settings(repository.path, MACROLENS_WORKER_LEASE_SECONDS=10),
        repository,
        SlowFailureClient(),  # type: ignore[arg-type]
        worker_id="worker-slow",
        clock=lambda: clock[0],
    )
    assert service.acquire()
    assert not asyncio.run(service.sync_health())
    local_health = repository.worker_health(
        heartbeat_ttl_seconds=30, now=clock[0]
    )
    assert local_health["healthy"] is True
    assert local_health["lock_live"] is True
    assert repository.sync_state("health")["last_error_code"] == "remote_timeout"


def test_manual_refresh_waits_as_one_order_without_force_retry_storm(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    clock = [utc(10)]
    for stream in ("health", "feed", "calendar", "job", "market_focus"):
        repository.mark_stream_success(stream, now=clock[0])
    repository.record_stream_failure(
        "health", "rate_limited", retry_after_seconds=120, now=clock[0]
    )
    request_id = repository.enqueue_refresh(now=clock[0])
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        object(),  # type: ignore[arg-type]
        worker_id="worker-refresh",
        clock=lambda: clock[0],
    )
    calls = {"health": 0, "feed": 0, "calendar": 0}

    async def counted(stream: str) -> bool:
        calls[stream] += 1
        return True

    service.sync_health = lambda: counted("health")  # type: ignore[method-assign]
    service.sync_latest = lambda: counted("feed")  # type: ignore[method-assign]
    service.sync_calendar = lambda: counted("calendar")  # type: ignore[method-assign]

    async def focus_ok() -> bool:
        return True

    service.sync_market_focus = focus_ok  # type: ignore[method-assign]

    assert service.acquire()
    for _ in range(3):
        asyncio.run(service.run_once())
    assert calls == {"health": 0, "feed": 0, "calendar": 0}
    with repository.open_read_connection() as connection:
        deferred = connection.execute(
            "SELECT status,requested_at FROM catalyst_refresh_outbox WHERE request_id=?",
            (request_id,),
        ).fetchone()
    assert tuple(deferred) == ("pending", "2026-07-11T10:02:00Z")

    clock[0] = utc(10, 2)
    result = asyncio.run(service.run_once())
    assert result["status"] == "idle"
    assert calls == {"health": 1, "feed": 1, "calendar": 1}
    with repository.open_read_connection() as connection:
        status = connection.execute(
            "SELECT status FROM catalyst_refresh_outbox WHERE request_id=?",
            (request_id,),
        ).fetchone()[0]
    assert status == "completed"


def test_stale_processing_refresh_claim_is_recovered(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    request_id = repository.enqueue_refresh(("feed",), now=utc(9))
    assert repository.claim_refresh(now=utc(9))["request_id"] == request_id
    assert repository.claim_refresh(
        now=utc(9, 4), recovery_after_seconds=300
    ) is None
    recovered = repository.claim_refresh(
        now=utc(9, 6), recovery_after_seconds=300
    )
    assert recovered == {"request_id": request_id, "streams": ["feed"]}


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled"])
def test_force_retry_safely_rebinds_reused_terminal_remote_job(
    tmp_path, terminal_status: str
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    old = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    reused = JobClient.response(terminal_status)
    repository.apply_remote_job(old["job_id"], reused, now=utc(10))
    not_forced = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )
    assert not_forced["job_id"] == old["job_id"]
    retry = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=True,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 2),
    )

    class ReusedTerminalClient(JobClient):
        async def create_analysis_job(
            self,
            _news_id: int,
            *,
            expected_content_hash: str,
            expected_change_sequence: int,
            force: bool,
        ):
            assert force is True
            assert expected_content_hash == "content-hash-101"
            assert expected_change_sequence == 1
            return reused

    service = CatalystSyncService(
        settings(repository.path),
        repository,
        ReusedTerminalClient(),  # type: ignore[arg-type]
        worker_id="worker-force-retry",
        clock=lambda: utc(10, 2),
    )
    assert service.acquire()
    assert asyncio.run(service.process_jobs()) == 1
    assert repository.get_analysis_job(retry["job_id"])["status"] == terminal_status
    assert repository.get_analysis_job(old["job_id"])["status"] == terminal_status
    with repository.open_read_connection() as connection:
        mappings = connection.execute(
            "SELECT local_job_id,remote_job_id FROM catalyst_analysis_jobs ORDER BY created_at"
        ).fetchall()
    assert mappings[0][1] is None
    assert mappings[1][1] == "remote-job-1234"


def test_low_context_actual_runtime_does_not_break_request_idempotency(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    local = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    base = catalyst_item(
        sequence=1, updated_at=utc(10), analysis=True
    ).analysis
    low_context = base.model_copy(
        update={
            "title_zh": "信息不足",
            "headline_summary": "原文信息不足，未调用模型。",
            "overall_sentiment": 0,
            "classification": base.classification.__class__.NEUTRAL,
            "confidence": 0,
            "market_relevance": 0,
            "affected_stocks": [],
            "affected_sectors": [],
            "affected_commodities": [],
            "insufficient_context": True,
            "model": "low-context-neutral-v2",
            "reasoning": "none",
        }
    )
    remote = JobClient.response(
        "insufficient_context",
        model="low-context-neutral-v2",
        reasoning="none",
        result=low_context,
    )
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        JobClient(),  # type: ignore[arg-type]
        worker_id="worker-low-context",
        clock=lambda: utc(10),
    )
    service._validate_remote_job(  # noqa: SLF001 - regression boundary
        repository.due_jobs("validator", now=utc(10))[0], remote
    )
    repository.apply_remote_job(local["job_id"], remote, now=utc(10))
    duplicate = repository.enqueue_analysis(
        101,
        content_hash="content-hash-101",
        change_sequence=1,
        contract_schema_version="macrolens-option-pro-v2",
        force=False,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )
    assert duplicate["job_id"] == local["job_id"]
    assert duplicate["model"] == "low-context-neutral-v2"
    assert duplicate["reasoning"] == "none"
    assert duplicate["requested_model"] == "gpt-5.6-terra"
    assert duplicate["requested_reasoning"] == "max"
    with repository.open_read_connection() as connection:
        row = connection.execute(
            "SELECT model,reasoning,actual_model,actual_reasoning FROM catalyst_analysis_jobs"
        ).fetchone()
    assert tuple(row) == (
        "gpt-5.6-terra",
        "max",
        "low-context-neutral-v2",
        "none",
    )
