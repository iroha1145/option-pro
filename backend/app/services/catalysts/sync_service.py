from __future__ import annotations

import asyncio
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .client import MacroLensClient
from .config import CatalystSettings
from .errors import CatalystError, CatalystRepositoryError
from .models import (
    ACTIVE_JOB_STATUSES,
    JobStatus,
    RemoteJobResponse,
    RemoteMarketFocusCycle,
)
from .repository import CatalystRepository, _as_utc


class CatalystSyncService:
    """Remote I/O orchestration used only by the dedicated worker process."""

    LOCK_NAME = "catalyst-sync-worker"
    LOW_CONTEXT_MODEL = "low-context-neutral-v2"

    def __init__(
        self,
        settings: CatalystSettings,
        repository: CatalystRepository,
        client: MacroLensClient,
        *,
        worker_id: Optional[str] = None,
        clock: Optional[Callable[[], datetime]] = None,
        rng: Optional[random.Random] = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.client = client
        self.worker_id = worker_id or f"catalyst-{uuid.uuid4().hex[:16]}"
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._rng = rng or random.Random()
        self._fencing_token: Optional[int] = None

    def acquire(self) -> bool:
        token = self.repository.acquire_worker_lock(
            self.LOCK_NAME,
            self.worker_id,
            lease_seconds=self.settings.worker_lease_seconds,
            now=self._clock(),
        )
        self._fencing_token = token
        if token is not None:
            self.repository.heartbeat(
                self.worker_id,
                "starting",
                {"phase": "lease_acquired", "fencing_token": token},
                now=self._clock(),
            )
        return token is not None

    def renew(self) -> None:
        if self._fencing_token is None:
            raise CatalystRepositoryError("worker_lock_lost", "Catalyst worker lock is not held")
        if not self.repository.renew_worker_lock(
            self.LOCK_NAME,
            self.worker_id,
            self._fencing_token,
            lease_seconds=self.settings.worker_lease_seconds,
            now=self._clock(),
        ):
            self._fencing_token = None
            raise CatalystRepositoryError("worker_lock_lost", "Catalyst worker lock was lost")
        self.repository.heartbeat(
            self.worker_id,
            "syncing",
            {"phase": "lease_renewed", "fencing_token": self._fencing_token},
            now=self._clock(),
        )

    def release(self) -> None:
        if self._fencing_token is None:
            return
        self.repository.release_worker_lock(
            self.LOCK_NAME, self.worker_id, self._fencing_token
        )
        self._fencing_token = None

    def ensure_lock(self) -> bool:
        """Renew the current lease or reacquire only when it is unowned.

        ``acquire_worker_lock`` performs the compare under ``BEGIN IMMEDIATE``;
        it therefore cannot overwrite a live lease obtained by another worker.
        """

        if self._fencing_token is None:
            return self.acquire()
        try:
            self.renew()
            return True
        except CatalystRepositoryError as error:
            if error.code != "worker_lock_lost":
                raise
            return self.acquire()

    def _backoff(self, failures: int, retry_after: Optional[int]) -> int:
        if retry_after is not None:
            return max(1, min(3600, retry_after))
        base = min(900, 5 * (2 ** min(max(0, failures), 7)))
        return max(1, int(base * self._rng.uniform(0.8, 1.2)))

    def _record_error(self, stream: str, error: CatalystError) -> None:
        state = self.repository.sync_state(stream)
        next_failures = int(state.get("consecutive_failures") or 0) + 1
        delay = self._backoff(next_failures, error.retry_after_seconds)
        self.repository.record_stream_failure(
            stream,
            error.code,
            retry_after_seconds=delay,
            open_circuit=(
                error.counts_for_circuit
                and next_failures >= self.settings.failure_threshold
            ),
            circuit_seconds=self.settings.circuit_open_seconds,
            now=self._clock(),
        )

    def _abort_run(self, run_id: str, error: CatalystError) -> None:
        state = self.repository.sync_state("feed")
        next_failures = int(state.get("consecutive_failures") or 0) + 1
        delay = self._backoff(next_failures, error.retry_after_seconds)
        observed = self._clock()
        self.repository.abort_sync_run(
            run_id,
            error.code,
            next_attempt_at=observed + timedelta(seconds=delay),
            circuit_open_until=(
                observed + timedelta(seconds=self.settings.circuit_open_seconds)
                if error.counts_for_circuit and next_failures >= self.settings.failure_threshold
                else None
            ),
            now=observed,
        )

    async def sync_health(self) -> bool:
        if not self.ensure_lock():
            return False
        try:
            response = await self.client.health()
            self.renew()
            self.repository.publish_health(
                status=response.status,
                data_through=response.data_through,
                sources=response.sources,
                model=response.model,
                reasoning=response.reasoning,
                execution_mode=response.execution_mode,
                analysis_trigger_enabled=response.analysis_trigger_enabled,
                warnings=response.warnings,
                worker_id=self.worker_id,
                fencing_token=self._fencing_token,
                observed_at=self._clock(),
            )
            return True
        except CatalystError as error:
            if error.code == "worker_lock_lost":
                raise
            if not self.ensure_lock():
                raise CatalystRepositoryError(
                    "worker_lock_lost", "Catalyst worker lock was lost during health sync"
                )
            self._record_error("health", error)
            return False

    async def sync_latest(self) -> bool:
        if not self.ensure_lock():
            return False
        state = self.repository.sync_state("feed")
        if bool(state.get("resync_required")):
            return await self._sync_latest_attempt(resync=True)
        try:
            return await self._sync_latest_attempt(resync=False)
        except CatalystError as error:
            if error.code != "updated_after_too_old":
                self._record_error("feed", error)
                return False
            # Latch recovery before making another remote request. Every later
            # attempt now uses the bounded window and never re-sends the
            # expired incremental watermark.
            self.repository.require_feed_resync(
                resync_from=error.resync_from,
                now=self._clock(),
            )
            return await self._sync_latest_attempt(resync=True)

    async def _sync_latest_attempt(self, *, resync: bool) -> bool:
        state = self.repository.sync_state("feed")
        updated_after = _as_utc(state.get("updated_after"))
        if resync:
            request_updated_after = _as_utc(state.get("resync_from"))
            if request_updated_after is None:
                error = CatalystError(
                    "resync_boundary_missing",
                    "MacroLens did not provide a bounded resync boundary",
                    retryable=True,
                    counts_for_circuit=False,
                )
                self._record_error("feed", error)
                return False
        else:
            request_updated_after = (
                updated_after - timedelta(minutes=5) if updated_after is not None else None
            )
        old_watermark = state.get("watermark_sequence")
        cursor: Optional[str] = None
        snapshot_token: Optional[str] = None
        run_id: Optional[str] = None
        max_sequence = (
            None
            if resync
            else int(old_watermark) if old_watermark is not None else None
        )
        final_data_through = None
        final_updated_after = None if resync else updated_after
        seen_cursors: set[str] = set()
        pages = 0
        try:
            while True:
                self.renew()
                page = await self.client.latest(
                    updated_after=request_updated_after,
                    cursor=cursor,
                    limit=self.settings.latest_page_limit,
                )
                pages += 1
                page_limit = self.settings.resync_max_pages if resync else 10_000
                if pages > page_limit:
                    raise CatalystError(
                        "pagination_limit", "MacroLens pagination exceeded its safety limit", False
                    )
                if snapshot_token is None:
                    snapshot_token = page.snapshot_token
                    run_id = self.repository.begin_sync_run(
                        "feed",
                        snapshot_token=snapshot_token,
                        sync_mode="resync" if resync else "incremental",
                        resync_generation=(
                            int(state.get("resync_generation") or 0) + 1
                            if resync
                            else None
                        ),
                        now=self._clock(),
                    )
                elif snapshot_token != page.snapshot_token:
                    raise CatalystError(
                        "snapshot_changed",
                        "MacroLens snapshot token changed during pagination",
                        False,
                    )
                assert run_id is not None
                self.repository.stage_latest_page(run_id, page.items)
                for item in page.items:
                    max_sequence = (
                        item.change_sequence
                        if max_sequence is None
                        else max(max_sequence, item.change_sequence)
                    )
                final_data_through = page.data_through
                page_updated_after = _as_utc(page.next_updated_after)
                if page_updated_after is not None:
                    if page.has_more:
                        # Overlap pages expose a page-local timestamp that may
                        # precede the already-published watermark. Only the
                        # final page carries the authoritative snapshot time.
                        final_updated_after = (
                            page_updated_after
                            if final_updated_after is None
                            else max(final_updated_after, page_updated_after)
                        )
                    else:
                        if (
                            final_updated_after is not None
                            and page_updated_after < final_updated_after
                        ):
                            raise CatalystError(
                                "watermark_regression",
                                "MacroLens final next_updated_after moved backwards",
                                False,
                            )
                        final_updated_after = page_updated_after
                if not page.has_more:
                    if page.next_cursor is not None:
                        raise CatalystError(
                            "invalid_pagination",
                            "MacroLens returned next_cursor with has_more=false",
                            False,
                        )
                    if resync and page_updated_after is None:
                        raise CatalystError(
                            "resync_watermark_missing",
                            "MacroLens did not return an authoritative resync watermark",
                            False,
                        )
                    if (
                        resync
                        and request_updated_after is not None
                        and page_updated_after is not None
                        and page_updated_after < request_updated_after
                    ):
                        raise CatalystError(
                            "watermark_regression",
                            "MacroLens resync watermark moved behind its requested boundary",
                            False,
                        )
                    break
                if not page.next_cursor or page.next_cursor in seen_cursors:
                    raise CatalystError(
                        "invalid_pagination", "MacroLens returned a missing or repeated cursor", False
                    )
                seen_cursors.add(page.next_cursor)
                cursor = page.next_cursor
            assert run_id is not None and snapshot_token is not None
            self.renew()
            self.repository.publish_latest(
                run_id,
                snapshot_token=snapshot_token,
                data_through=final_data_through,
                next_updated_after=final_updated_after,
                watermark_sequence=max_sequence,
                worker_id=self.worker_id,
                fencing_token=self._fencing_token,
                now=self._clock(),
            )
            return True
        except CatalystError as error:
            if error.code == "worker_lock_lost":
                raise
            if error.code == "projection_payload_conflict":
                if run_id:
                    self._abort_run(run_id, error)
                else:
                    self._record_error("feed", error)
                self.repository.require_feed_resync(
                    resync_from=(
                        self._clock()
                        - timedelta(days=self.settings.latest_window_days)
                    ),
                    error_code=error.code,
                    now=self._clock(),
                )
                if resync:
                    return False
                # Start the bounded generation immediately.  A boundary at
                # the remote retention edge must not sit idle until the next
                # worker interval and expire before its first request.
                return await self._sync_latest_attempt(resync=True)
            if error.code == "updated_after_too_old" and not resync:
                # The wrapper persists recovery mode and starts the bounded
                # generation. Do not let a partial incremental staging run
                # publish before the recovery generation is complete.
                if run_id:
                    self.repository.abort_sync_run(
                        run_id,
                        error.code,
                        now=self._clock(),
                    )
                raise
            if error.code == "updated_after_too_old" and resync:
                if run_id:
                    self.repository.abort_sync_run(
                        run_id,
                        error.code,
                        now=self._clock(),
                    )
                self.repository.require_feed_resync(
                    resync_from=error.resync_from,
                    now=self._clock(),
                )
                return False
            if not self.ensure_lock():
                raise CatalystRepositoryError(
                    "worker_lock_lost", "Catalyst worker lock was lost during feed sync"
                )
            if run_id:
                self._abort_run(run_id, error)
            else:
                self._record_error("feed", error)
            return False
        except Exception as error:
            if not self.ensure_lock():
                raise CatalystRepositoryError(
                    "worker_lock_lost", "Catalyst worker lock was lost during feed sync"
                )
            safe_error = CatalystError("local_sync_error", "Catalyst sync failed locally", True)
            if run_id:
                self._abort_run(run_id, safe_error)
            else:
                self._record_error("feed", safe_error)
            return False

    async def sync_calendar(self) -> bool:
        if not self.ensure_lock():
            return False
        observed = self._clock()
        run_id = self.repository.begin_sync_run("calendar", now=observed)
        try:
            self.renew()
            response = await self.client.calendar(
                date_from=(observed - timedelta(days=self.settings.calendar_lookback_days)).date().isoformat(),
                date_to=(observed + timedelta(days=self.settings.calendar_lookahead_days)).date().isoformat(),
                as_of=observed.isoformat(),
            )
            self.renew()
            self.repository.stage_calendar(run_id, response.items)
            self.repository.publish_calendar(
                run_id,
                data_through=response.data_through,
                worker_id=self.worker_id,
                fencing_token=self._fencing_token,
                now=self._clock(),
            )
            return True
        except CatalystError as error:
            if error.code == "worker_lock_lost":
                raise
            if not self.ensure_lock():
                raise CatalystRepositoryError(
                    "worker_lock_lost", "Catalyst worker lock was lost during calendar sync"
                )
            state = self.repository.sync_state("calendar")
            delay = self._backoff(
                int(state.get("consecutive_failures") or 0) + 1,
                error.retry_after_seconds,
            )
            observed = self._clock()
            self.repository.abort_sync_run(
                run_id,
                error.code,
                next_attempt_at=observed + timedelta(seconds=delay),
                circuit_open_until=(
                    observed + timedelta(seconds=self.settings.circuit_open_seconds)
                    if error.counts_for_circuit
                    and int(state.get("consecutive_failures") or 0) + 1
                    >= self.settings.failure_threshold
                    else None
                ),
                now=observed,
            )
            return False

    async def sync_market_focus(self) -> bool:
        """Pull one coherent focus snapshot and publish it locally once."""

        if not self.ensure_lock():
            return False
        try:
            before = await self.client.hotspot_status()
            self.renew()
            hotspots = await self.client.hotspots(
                limit=self.settings.hotspot_sync_limit,
                as_of=self._clock(),
            )
            self.renew()
            latest = await self.client.latest_market_focus_cycle()
            self.renew()
            after = await self.client.hotspot_status()
            coherent_fields = (
                "prepared_revision",
                "last_consumed_revision",
                "active_cycle_id",
            )
            if any(getattr(before, field) != getattr(after, field) for field in coherent_fields):
                raise CatalystError(
                    "market_focus_snapshot_changed",
                    "MacroLens market focus state changed during synchronization",
                    retryable=True,
                    counts_for_circuit=False,
                )
            self.repository.publish_market_focus_snapshot(
                after,
                hotspots.items,
                latest.cycle,
                worker_id=self.worker_id,
                fencing_token=self._fencing_token,
                now=self._clock(),
            )
            return True
        except CatalystError as error:
            if error.code == "worker_lock_lost":
                raise
            if not self.ensure_lock():
                raise CatalystRepositoryError(
                    "worker_lock_lost",
                    "Catalyst worker lock was lost during market focus sync",
                )
            self._record_error("market_focus", error)
            return False
        except CatalystRepositoryError as error:
            if error.code == "worker_lock_lost":
                raise
            self._record_error(
                "market_focus",
                CatalystError(
                    "market_focus_snapshot_invalid",
                    "MacroLens market focus snapshot could not be published",
                    retryable=True,
                    counts_for_circuit=False,
                ),
            )
            return False

    async def process_jobs(self) -> int:
        if not self.ensure_lock():
            return 0
        processed = 0
        first_error: Optional[CatalystError] = None
        jobs = self.repository.due_jobs(
            self.worker_id,
            lease_seconds=max(self.settings.job_interval_seconds * 4, 30),
            now=self._clock(),
        )
        for job in jobs:
            processed += 1
            local_job_id = job["local_job_id"]
            try:
                self.renew()
                if job.get("cancel_requested_at") and job.get("remote_job_id"):
                    remote = await self.client.cancel_analysis_job(job["remote_job_id"])
                elif not job.get("remote_job_id"):
                    submission = self.repository.begin_remote_submission(
                        local_job_id,
                        self.worker_id,
                        now=self._clock(),
                    )
                    if submission is None:
                        continue
                    remote = await self.client.create_analysis_job(
                        int(job["news_id"]),
                        expected_content_hash=str(job["content_hash"]),
                        expected_change_sequence=int(job["change_sequence"]),
                        force=bool(job["force"]),
                    )
                else:
                    remote = await self.client.get_analysis_job(job["remote_job_id"])
                self.renew()
                self._validate_remote_job(job, remote)
                next_attempt = None
                if remote.status in ACTIVE_JOB_STATUSES:
                    next_attempt = self._clock() + timedelta(
                        seconds=max(self.settings.job_interval_seconds, remote.retry_after or 0)
                    )
                self.repository.apply_remote_job(
                    local_job_id,
                    remote,
                    worker_id=self.worker_id,
                    next_attempt_at=next_attempt,
                    now=self._clock(),
                )
                if remote.status in {
                    JobStatus.COMPLETED,
                    JobStatus.INSUFFICIENT_CONTEXT,
                }:
                    self.repository.enqueue_refresh(("feed",), now=self._clock())
            except CatalystError as error:
                if error.code == "analysis_job_lease_lost":
                    continue
                if error.code == "worker_lock_lost":
                    raise
                if not self.ensure_lock():
                    raise CatalystRepositoryError(
                        "worker_lock_lost", "Catalyst worker lock was lost during job sync"
                    )
                first_error = first_error or error
                terminal = not error.retryable
                self.repository.fail_local_job(
                    local_job_id,
                    error.code,
                    retry_after_seconds=self._backoff(1, error.retry_after_seconds),
                    terminal=terminal,
                    now=self._clock(),
                )
        if first_error is not None:
            self._record_error("job", first_error)
        else:
            self.repository.mark_stream_success("job", now=self._clock())
        return processed

    async def process_market_focus_jobs(self) -> int:
        if not self.ensure_lock():
            return 0
        processed = 0
        jobs = self.repository.due_market_focus_jobs(
            self.worker_id,
            lease_seconds=max(self.settings.job_interval_seconds * 4, 30),
            now=self._clock(),
        )
        for job in jobs:
            processed += 1
            local_cycle_id = str(job["local_cycle_id"])
            try:
                self.renew()
                if job.get("cancel_requested_at") and job.get("remote_cycle_id"):
                    envelope = await self.client.cancel_market_focus_cycle(
                        str(job["remote_cycle_id"])
                    )
                elif not job.get("remote_cycle_id"):
                    if not self.repository.begin_market_focus_submission(
                        local_cycle_id,
                        self.worker_id,
                        now=self._clock(),
                    ):
                        continue
                    envelope = await self.client.create_market_focus_cycle(
                        expected_prepared_revision=(
                            None
                            if job.get("retry_remote_cycle_id")
                            else int(job["expected_prepared_revision"])
                        ),
                        retry_cycle_id=(
                            str(job["retry_remote_cycle_id"])
                            if job.get("retry_remote_cycle_id")
                            else None
                        ),
                    )
                else:
                    envelope = await self.client.get_market_focus_cycle(
                        str(job["remote_cycle_id"])
                    )
                remote = envelope.cycle
                if remote is None:
                    raise CatalystError(
                        "remote_cycle_missing",
                        "MacroLens returned no market focus cycle",
                        retryable=False,
                    )
                self._validate_remote_market_focus_cycle(job, remote)
                self.renew()
                next_attempt = None
                if remote.status in {"pending", "queued", "in_progress"}:
                    next_attempt = self._clock() + timedelta(
                        seconds=self.settings.job_interval_seconds
                    )
                self.repository.apply_remote_market_focus_cycle(
                    local_cycle_id,
                    remote,
                    worker_id=self.worker_id,
                    next_attempt_at=next_attempt,
                    now=self._clock(),
                )
            except CatalystError as error:
                if error.code == "worker_lock_lost":
                    raise
                if not self.ensure_lock():
                    raise CatalystRepositoryError(
                        "worker_lock_lost",
                        "Catalyst worker lock was lost during market focus job sync",
                    )
                self.repository.fail_market_focus_job(
                    local_cycle_id,
                    error.code,
                    retry_after_seconds=self._backoff(1, error.retry_after_seconds),
                    terminal=not error.retryable,
                    now=self._clock(),
                )
        return processed

    @staticmethod
    def _validate_remote_market_focus_cycle(
        job: dict[str, Any],
        remote: RemoteMarketFocusCycle,
    ) -> None:
        if job.get("remote_cycle_id") and remote.cycle_id != job["remote_cycle_id"]:
            raise CatalystError(
                "remote_cycle_identity_mismatch",
                "MacroLens returned a different market focus cycle",
                retryable=False,
            )
        if remote.prepared_revision != int(job["expected_prepared_revision"]):
            raise CatalystError(
                "remote_cycle_revision_mismatch",
                "MacroLens returned a cycle for a different prepared revision",
                retryable=False,
            )
        retry_parent = job.get("retry_remote_cycle_id")
        if retry_parent and remote.retry_of_cycle_id != retry_parent:
            raise CatalystError(
                "remote_cycle_retry_parent_mismatch",
                "MacroLens returned a retry for a different immutable cycle snapshot",
                retryable=False,
            )
        if not retry_parent and remote.retry_of_cycle_id is not None:
            raise CatalystError(
                "remote_cycle_retry_parent_unexpected",
                "MacroLens returned a retry cycle for a new-cycle request",
                retryable=False,
            )
        if remote.execution_number != int(job.get("execution_number") or 1):
            raise CatalystError(
                "remote_cycle_execution_mismatch",
                "MacroLens returned a different market focus execution number",
                retryable=False,
            )
        if remote.model != job["model"] or remote.reasoning_effort != job["reasoning"]:
            raise CatalystError(
                "remote_cycle_runtime_mismatch",
                "MacroLens returned a different market focus runtime",
                retryable=False,
            )
        if remote.result is not None and remote.result.cycle_id != remote.cycle_id:
            raise CatalystError(
                "remote_cycle_result_mismatch",
                "MacroLens returned a result for a different market focus cycle",
                retryable=False,
            )
        if remote.status in {"completed", "insufficient_context"}:
            if remote.result is None:
                raise CatalystError(
                    "remote_cycle_result_missing",
                    "MacroLens completed a market focus cycle without a result",
                    retryable=False,
                )
        elif remote.result is not None:
            raise CatalystError(
                "remote_cycle_result_mismatch",
                "MacroLens returned a result before a publishable terminal state",
                retryable=False,
            )

    def _validate_remote_job(
        self,
        job: dict[str, Any],
        remote: RemoteJobResponse,
    ) -> None:
        """Bind every remote response to the exact cached news revision.

        A valid signature authenticates MacroLens, but it does not prove that a
        response belongs to this local proxy. These checks stop a stale or
        crossed job from publishing against a different revision.
        """

        if remote.news_id != int(job["news_id"]):
            raise CatalystError(
                "remote_job_identity_mismatch",
                "MacroLens returned a job for a different news item",
                retryable=False,
            )
        if job.get("remote_job_id") and remote.job_id != job["remote_job_id"]:
            raise CatalystError(
                "remote_job_identity_mismatch",
                "MacroLens returned a different analysis job",
                retryable=False,
            )
        if (
            remote.content_hash != str(job["content_hash"])
            or remote.change_sequence != int(job["change_sequence"])
        ):
            raise CatalystError(
                "remote_job_input_mismatch",
                "MacroLens returned a job for a different news revision",
                retryable=False,
            )
        if job.get("remote_input_hash") and remote.input_hash != job["remote_input_hash"]:
            raise CatalystError(
                "remote_job_input_mismatch",
                "MacroLens changed the input identity of an existing analysis job",
                retryable=False,
            )

        if remote.status == JobStatus.INSUFFICIENT_CONTEXT:
            result = remote.result
            classification = (
                result.classification.value
                if result is not None and hasattr(result.classification, "value")
                else str(result.classification) if result is not None else ""
            )
            if (
                remote.model != self.LOW_CONTEXT_MODEL
                or remote.reasoning != "none"
                or result is None
                or not result.insufficient_context
                or classification != "neutral"
                or result.overall_sentiment != 0
                or result.confidence != 0
                or result.market_relevance != 0
                or bool(result.affected_stocks)
                or bool(result.affected_sectors)
                or bool(result.affected_commodities)
            ):
                raise CatalystError(
                    "remote_job_runtime_mismatch",
                    "MacroLens returned an invalid low-context terminal result",
                    retryable=False,
                )
        else:
            if remote.model != job["model"] or remote.reasoning != job["reasoning"]:
                raise CatalystError(
                    "remote_job_runtime_mismatch",
                    "MacroLens returned a different model or reasoning configuration",
                    retryable=False,
                )
            if remote.status == JobStatus.COMPLETED and (
                remote.result is None or remote.result.insufficient_context
            ):
                raise CatalystError(
                    "remote_job_result_mismatch",
                    "MacroLens returned an invalid completed analysis result",
                    retryable=False,
                )

        if remote.result is not None and (
            remote.result.model != remote.model
            or remote.result.reasoning != remote.reasoning
        ):
            raise CatalystError(
                "remote_job_result_mismatch",
                "MacroLens result metadata does not match its analysis job",
                retryable=False,
            )
        if remote.status not in {
            JobStatus.COMPLETED,
            JobStatus.INSUFFICIENT_CONTEXT,
        } and remote.result is not None:
            raise CatalystError(
                "remote_job_result_mismatch",
                "MacroLens returned a result before reaching a publishable terminal state",
                retryable=False,
            )

    def _stream_due(self, stream: str, interval_seconds: int) -> bool:
        state = self.repository.sync_state(stream)
        now = self._clock()
        circuit_until = _as_utc(state.get("circuit_open_until"))
        next_attempt = _as_utc(state.get("next_attempt_at"))
        if circuit_until and circuit_until > now:
            return False
        if next_attempt and next_attempt > now:
            return False
        reference = _as_utc(state.get("last_attempt_at") or state.get("last_success_at"))
        if reference is None:
            return True
        jitter = interval_seconds * 0.1
        return (now - reference).total_seconds() >= interval_seconds + self._rng.uniform(-jitter, jitter)

    def _stream_allowed(self, stream: str) -> bool:
        state = self.repository.sync_state(stream)
        now = self._clock()
        circuit_until = _as_utc(state.get("circuit_open_until"))
        next_attempt = _as_utc(state.get("next_attempt_at"))
        return not (
            (circuit_until is not None and circuit_until > now)
            or (next_attempt is not None and next_attempt > now)
        )

    def _stream_retry_at(self, stream: str) -> Optional[datetime]:
        state = self.repository.sync_state(stream)
        deadlines = [
            value
            for value in (
                _as_utc(state.get("next_attempt_at")),
                _as_utc(state.get("circuit_open_until")),
            )
            if value is not None and value > self._clock()
        ]
        return max(deadlines) if deadlines else None

    async def run_once(self) -> dict[str, Any]:
        if not self.ensure_lock():
            return {"status": "standby", "processed": []}
        processed: list[str] = []
        refresh = self.repository.claim_refresh(
            now=self._clock(),
            recovery_after_seconds=max(60, self.settings.worker_lease_seconds * 2),
        )
        forced = set(refresh["streams"] if refresh else [])
        refresh_error: Optional[str] = None
        blocked_until = [
            retry_at
            for stream in forced
            for retry_at in [self._stream_retry_at(stream)]
            if retry_at is not None
        ]
        refresh_deferred = bool(blocked_until)
        if refresh_deferred:
            # Treat a manual refresh as one order. If one requested stream is
            # backing off, no other stream is force-run on every worker tick.
            # Normal interval-based work below remains independent.
            forced.clear()
        try:
            if (
                "health" in forced and self._stream_allowed("health")
            ) or self._stream_due("health", self.settings.health_interval_seconds):
                if await self.sync_health():
                    processed.append("health")
                else:
                    refresh_error = refresh_error or "health_failed"
            if (
                "feed" in forced and self._stream_allowed("feed")
            ) or self._stream_due("feed", self.settings.feed_interval_seconds):
                if await self.sync_latest():
                    processed.append("feed")
                else:
                    refresh_error = refresh_error or "feed_failed"
            if (
                "calendar" in forced and self._stream_allowed("calendar")
            ) or self._stream_due("calendar", self.settings.calendar_interval_seconds):
                if await self.sync_calendar():
                    processed.append("calendar")
                else:
                    refresh_error = refresh_error or "calendar_failed"
            if self._stream_due(
                "market_focus", self.settings.market_focus_interval_seconds
            ):
                if await self.sync_market_focus():
                    processed.append("market_focus")
            jobs = 0
            market_focus_jobs = 0
            if self._stream_due("job", self.settings.job_interval_seconds):
                jobs = await self.process_jobs()
                market_focus_jobs = await self.process_market_focus_jobs()
                processed.append("job")
            if refresh and refresh_deferred:
                self.repository.defer_refresh(
                    refresh["request_id"], not_before=max(blocked_until)
                )
            elif refresh:
                self.repository.complete_refresh(
                    refresh["request_id"], error_code=refresh_error, now=self._clock()
                )
            self.repository.heartbeat(
                self.worker_id,
                "idle",
                {
                    "processed": processed,
                    "jobs": jobs,
                    "market_focus_jobs": market_focus_jobs,
                    "fencing_token": self._fencing_token,
                },
                now=self._clock(),
            )
            return {
                "status": "idle",
                "processed": processed,
                "jobs": jobs,
                "market_focus_jobs": market_focus_jobs,
            }
        except CatalystRepositoryError:
            self.repository.heartbeat(
                self.worker_id,
                "degraded",
                {"error_code": "worker_lock_lost"},
                now=self._clock(),
            )
            raise

    async def run_forever(self, *, stop: Optional[asyncio.Event] = None) -> None:
        stop_event = stop or asyncio.Event()
        if not self.acquire():
            raise CatalystRepositoryError(
                "worker_already_running", "Another Catalyst worker holds the lease"
            )
        try:
            while not stop_event.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=1.0)
                except TimeoutError:
                    pass
        finally:
            self.release()
