from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from collections import Counter
from datetime import datetime
from typing import Any, Sequence

from app.config import get_settings
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import (
    RECOVERABLE_FAILURE_CODES,
    AIJobRepository,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate already-paid provider results that failed only a "
            "local step: the schema contract, or a local write recorded as "
            "provider_unavailable/local_storage_error after the provider "
            "accepted the job. No new model request is submitted."
        )
    )
    parser.add_argument(
        "--job-id",
        action="append",
        default=[],
        dest="job_ids",
        help="Exact failed AI job id; repeat for more than one job.",
    )
    parser.add_argument(
        "--failed-since",
        type=_aware_timestamp,
        help=(
            "Also select every recoverable failed job updated at or after this "
            "ISO-8601 time whose paid receipt is stored locally."
        ),
    )
    parser.add_argument(
        "--job-type",
        action="append",
        default=[],
        dest="job_types",
        choices=sorted(runtime.AI_TASK_MAX_OUTPUT_TOKENS),
        help="Limit --failed-since to this job type; repeatable.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=2000,
        help="Maximum number of rows --failed-since selects (default 2000).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist recovered results. Without this flag the command is read-only.",
    )
    return parser


def _aware_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("timestamp must include a timezone")
    return parsed


async def recover(
    job_ids: Sequence[str],
    *,
    apply: bool,
    failed_since: datetime | None = None,
    job_types: Sequence[str] = (),
    limit: int = 2000,
) -> list[dict[str, Any]]:
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    selected = list(job_ids)
    if failed_since is not None:
        selected.extend(
            repository.recoverable_receipt_failures(
                since=failed_since, job_types=job_types, limit=limit,
            )
        )
    output: list[dict[str, Any]] = []
    for raw_job_id in dict.fromkeys(selected):
        job_id = str(raw_job_id).strip()
        row = repository.get_job(job_id)
        if row is None:
            output.append({"job_id": job_id, "status": "not_found"})
            continue
        claude_result = runtime.uses_claude(row.get("model"))
        response_id = str(row.get(
            "anthropic_message_id" if claude_result else "openai_response_id"
        ) or "")
        if (
            row.get("status") != "failed"
            or row.get("error_code") not in RECOVERABLE_FAILURE_CODES
            or row.get("result_json") is not None
            or not response_id
        ):
            output.append({"job_id": job_id, "status": "not_recoverable"})
            continue
        try:
            receipt = repository.get_provider_result(job_id) if claude_result or row.get("provider_result_json") else None
            response = None if receipt or claude_result else await runtime.retrieve(settings, response_id)
        except Exception as error:
            output.append(
                {
                    "job_id": job_id,
                    "status": "retrieve_failed",
                    "error_type": type(error).__name__,
                }
            )
            continue
        if (claude_result and receipt is None) or (
            not claude_result and not receipt and str(getattr(response, "status", "") or "") != "completed"
        ):
            output.append({"job_id": job_id, "status": "provider_not_completed"})
            continue
        terminal_error = receipt.get("terminal_error") if receipt else runtime.response_terminal_error(response)
        if terminal_error:
            output.append(
                {
                    "job_id": job_id,
                    "status": "provider_terminal_error",
                    "error_code": terminal_error,
                }
            )
            continue
        try:
            payload = json.loads(str(row["payload_json"]))
            result = (
                runtime.receipt_result(receipt, str(row["job_type"]), payload)
                if receipt else runtime.response_result(response, str(row["job_type"]), payload)
            )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            output.append(
                {
                    "job_id": job_id,
                    "status": "validation_failed",
                    "error_type": type(error).__name__,
                    "error": str(error)[:500],
                }
            )
            continue
        if apply:
            try:
                recovered = repository.recover_schema_validation_failure(
                    job_id,
                    response_id,
                    result,
                )
            except (OSError, RuntimeError, sqlite3.Error, TypeError, ValueError) as error:
                output.append(
                    {
                        "job_id": job_id,
                        "status": "persistence_failed",
                        "error_type": type(error).__name__,
                    }
                )
                continue
            output.append(
                {
                    "job_id": job_id,
                    "status": "recovered",
                    "completed_at": recovered.get("completed_at"),
                }
            )
        else:
            output.append({"job_id": job_id, "status": "validated"})
    return output


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.job_ids and args.failed_since is None:
        parser.error("give --job-id or --failed-since")
    results = asyncio.run(
        recover(
            args.job_ids,
            apply=bool(args.apply),
            failed_since=args.failed_since,
            job_types=args.job_types,
            limit=args.limit,
        )
    )
    print(json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    print(
        json.dumps(dict(Counter(item["status"] for item in results)), sort_keys=True),
        file=sys.stderr,
    )
    successful = {"validated", "recovered"}
    return 0 if results and all(item["status"] in successful for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
