from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import uuid
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.services.ai_jobs import runtime
from app.services.ai_jobs.repository import AIJobRepository


logger = logging.getLogger(__name__)


def _poll_delay(settings: Any, poll_count: int) -> float:
    initial = float(settings.openai_background_initial_poll_seconds)
    maximum = float(settings.openai_background_max_poll_seconds)
    return min(maximum, initial * (2 ** min(max(0, poll_count), 4)))


def _submitted_age_seconds(job: dict[str, Any]) -> float:
    for field in ("submitted_at", "submission_started_at", "created_at"):
        submitted = str(job.get(field) or "")
        if not submitted:
            continue
        try:
            submitted_at = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        except ValueError:
            continue
        return max(
            0.0,
            (datetime.now(timezone.utc) - submitted_at).total_seconds(),
        )
    return 0.0


def _public_error(
    exc: Exception,
    *,
    submitted: bool,
    response_id: str | None,
) -> str:
    status_code = getattr(exc, "status_code", None)
    if submitted and not response_id and not isinstance(status_code, int):
        return "submission_outcome_unknown"
    if isinstance(status_code, int):
        if status_code in {401, 403}:
            return "provider_auth_failed"
        if status_code == 429:
            return "provider_rate_limited"
        if status_code >= 500:
            return "provider_server_error"
        return "provider_request_rejected"
    if isinstance(exc, ValueError):
        code = str(exc)
        if code in {
            "ai_input_too_large",
            "ai_empty_response",
            "earnings_ticker_mismatch",
            "earnings_impacted_count_invalid",
            "news_identity_mismatch",
            "news_ticker_binding_mismatch",
            "market_focus_cycle_mismatch",
            "market_focus_as_of_mismatch",
            "market_focus_input_hash_mismatch",
            "market_focus_event_binding_mismatch",
            "market_focus_ticker_binding_mismatch",
        }:
            return code
        return "schema_validation_failed"
    if isinstance(exc, RuntimeError):
        code = str(exc)
        if code in {
            "ai_not_configured",
            "ai_sdk_unavailable",
            "runtime_configuration_invalid",
        }:
            return code
    return "provider_unavailable"


async def _lease_heartbeat(
    repository: AIJobRepository,
    job_id: str,
    owner: str,
    lease_seconds: int,
    stop: asyncio.Event,
) -> None:
    interval = max(5.0, lease_seconds / 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            renewed = await asyncio.to_thread(
                repository.renew_lease,
                job_id,
                owner,
                lease_seconds,
            )
            if not renewed:
                return


async def _finish_response(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
    response: Any,
) -> None:
    status = str(getattr(response, "status", "") or "")
    response_id = str(getattr(response, "id", "") or job.get("openai_response_id") or "")
    if status in {"queued", "in_progress"}:
        if not response_id:
            repository.fail(job["job_id"], owner, "provider_response_id_missing")
            return
        repository.record_background_response(
            job["job_id"],
            owner,
            response_id,
            status,
            delay_seconds=_poll_delay(settings, int(job.get("poll_count") or 0)),
            error_code=(
                "poll_window_elapsed"
                if _submitted_age_seconds(job)
                > float(settings.openai_background_poll_timeout_seconds)
                else None
            ),
        )
        return
    terminal_error = runtime.response_terminal_error(response)
    if status == "completed":
        if terminal_error:
            repository.fail(job["job_id"], owner, terminal_error)
            return
        payload = json.loads(job["payload_json"])
        result = runtime.response_result(response, job["job_type"], payload)
        repository.complete(
            job["job_id"],
            owner,
            result,
            runtime.response_usage(response),
        )
        return
    if status == "cancelled":
        repository.mark_cancelled(job["job_id"], owner)
        return
    if status in {"failed", "incomplete"}:
        repository.fail(
            job["job_id"],
            owner,
            terminal_error or f"provider_{status}",
        )
        return
    repository.fail(job["job_id"], owner, "provider_status_unsupported")


async def process_job(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
) -> None:
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(
        _lease_heartbeat(
            repository,
            job["job_id"],
            owner,
            int(settings.openai_job_lease_seconds),
            stop,
        )
    )
    submitted = bool(job.get("submission_started_at"))
    response_id = job.get("openai_response_id")
    try:
        if job.get("cancel_requested_at"):
            if response_id:
                if _submitted_age_seconds(job) > int(
                    settings.openai_job_max_age_seconds
                ):
                    repository.fail(job["job_id"], owner, "provider_response_expired")
                    return
                try:
                    response = await runtime.cancel(settings, response_id)
                except Exception as exc:
                    logger.warning(
                        "AI response cancellation deferred (%s)",
                        type(exc).__name__,
                    )
                    repository.defer(
                        job["job_id"],
                        owner,
                        delay_seconds=_poll_delay(
                            settings,
                            int(job.get("poll_count") or 0),
                        ),
                        error_code="provider_cancel_deferred",
                    )
                    return
                await _finish_response(
                    repository,
                    settings,
                    job,
                    owner,
                    response,
                )
            else:
                repository.mark_cancelled(job["job_id"], owner)
            return

        if response_id:
            if _submitted_age_seconds(job) > int(
                settings.openai_job_max_age_seconds
            ):
                repository.fail(job["job_id"], owner, "provider_response_expired")
                return
            try:
                response = await runtime.retrieve(settings, response_id)
            except Exception as exc:
                logger.warning(
                    "AI response retrieve deferred (%s)",
                    type(exc).__name__,
                )
                repository.defer(
                    job["job_id"],
                    owner,
                    delay_seconds=_poll_delay(
                        settings,
                        int(job.get("poll_count") or 0),
                    ),
                    error_code="provider_poll_deferred",
                )
                return
            await _finish_response(repository, settings, job, owner, response)
            return

        if submitted:
            repository.fail(
                job["job_id"],
                owner,
                "submission_outcome_unknown",
            )
            return

        current_schema_version, current_schema_sha256 = runtime.schema_identity(
            job["job_type"]
        )
        if (
            job["model"] != runtime.OFFICIAL_OPENAI_MODEL
            or job["reasoning"] != runtime.OFFICIAL_REASONING_EFFORT
            or job["execution_mode"] != runtime.OFFICIAL_EXECUTION_MODE
            or not runtime.runtime_configuration_valid(settings)
            or job["schema_version"] != current_schema_version
            or job["schema_sha256"] != current_schema_sha256
        ):
            repository.fail(
                job["job_id"],
                owner,
                "runtime_configuration_changed",
            )
            return

        payload = json.loads(job["payload_json"])
        prepared = runtime.prepare_background(
            settings,
            job["job_type"],
            payload,
        )
        try:
            submission_state = repository.mark_submission_started(
                job["job_id"],
                owner,
                daily_limit=int(settings.openai_daily_max_jobs),
            )
        except RuntimeError as exc:
            if str(exc) == "ai_job_not_submittable":
                return
            raise
        if submission_state != "started":
            return
        # Every local validation and SDK construction step has completed. From
        # this point an exception can represent a request whose upstream
        # outcome is unknown, so it must consume both budget and concurrency.
        submitted = True
        response = await runtime.submit_background(
            settings,
            job["job_type"],
            payload,
            prepared=prepared,
        )
        submitted_response_id = str(getattr(response, "id", "") or "") or None
        if not submitted_response_id:
            repository.fail(
                job["job_id"],
                owner,
                "submission_outcome_unknown",
            )
            return
        repository.link_background_response(
            job["job_id"],
            owner,
            submitted_response_id,
        )
        # Treat the upstream identity as recoverable only after it is
        # durably linked. A failed link otherwise makes an untracked paid
        # response look retryable and can submit the same job twice.
        response_id = submitted_response_id
        job["openai_response_id"] = response_id
        await _finish_response(repository, settings, job, owner, response)
    except Exception as exc:
        code = _public_error(
            exc,
            submitted=submitted,
            response_id=response_id,
        )
        logger.warning("AI job failed (%s, %s)", code, type(exc).__name__)
        with suppress(Exception):
            repository.fail(job["job_id"], owner, code)
    finally:
        stop.set()
        heartbeat.cancel()
        with suppress(asyncio.CancelledError):
            await heartbeat


async def run_once(
    repository: AIJobRepository,
    settings: Any,
    owner: str,
) -> int:
    job = await asyncio.to_thread(
        repository.claim_due,
        owner,
        int(settings.openai_job_lease_seconds),
    )
    if not job:
        return 0
    await process_job(repository, settings, job, owner)
    return 1


def health_payload(repository: AIJobRepository, settings: Any) -> dict[str, Any]:
    payload = repository.health()
    configured = bool(settings.openai_api_key.get_secret_value().strip())
    capability = runtime.capability_status(settings)
    payload.update(
        {
            "status": (
                payload["status"]
                if not payload["healthy"]
                else capability["status"]
                if configured
                else "disabled"
            ),
            "configured": configured,
            "provider_capability_supported": bool(capability.get("supported")),
            "sdk_capability_supported": bool(capability.get("sdk_supported")),
            "methods": capability.get("methods", {}),
            "model": runtime.OFFICIAL_OPENAI_MODEL,
            "reasoning": runtime.OFFICIAL_REASONING_EFFORT,
            "execution_mode": runtime.OFFICIAL_EXECUTION_MODE,
        }
    )
    if not configured and payload["healthy"]:
        payload["healthy"] = True
    return payload


async def run_forever() -> None:
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    repository.initialize()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    while True:
        if not settings.openai_api_key.get_secret_value().strip():
            await asyncio.sleep(30)
            continue
        processed = await run_once(repository, settings, owner)
        await asyncio.sleep(0.5 if processed else 2.0)


def main() -> None:
    parser = argparse.ArgumentParser(description="Option Pro persistent AI worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    if args.healthcheck:
        payload = health_payload(repository, settings)
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(0 if payload["healthy"] else 1)
    if args.once:
        if not settings.openai_api_key.get_secret_value().strip():
            print(
                json.dumps(
                    {
                        "status": "disabled",
                        "processed": 0,
                        "as_of": datetime.now(timezone.utc).isoformat(),
                    },
                    separators=(",", ":"),
                )
            )
            return
        owner = f"once-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        processed = asyncio.run(run_once(repository, settings, owner))
        print(
            json.dumps(
                {"status": "completed", "processed": processed},
                separators=(",", ":"),
            )
        )
        return
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
