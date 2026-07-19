from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any, Sequence

from app.config import get_settings
from app.services.ai_jobs import runtime
from app.services.ai_jobs.repository import AIJobRepository


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Revalidate already-paid provider results that failed only the "
            "local schema contract. No new model request is submitted."
        )
    )
    parser.add_argument(
        "--job-id",
        action="append",
        required=True,
        dest="job_ids",
        help="Exact failed AI job id; repeat for more than one job.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Persist recovered results. Without this flag the command is read-only.",
    )
    return parser


async def recover(job_ids: Sequence[str], *, apply: bool) -> list[dict[str, Any]]:
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    output: list[dict[str, Any]] = []
    for raw_job_id in dict.fromkeys(job_ids):
        job_id = str(raw_job_id).strip()
        row = repository.get_job(job_id)
        if row is None:
            output.append({"job_id": job_id, "status": "not_found"})
            continue
        response_id = str(row.get("openai_response_id") or "")
        if (
            row.get("status") != "failed"
            or row.get("error_code") != "schema_validation_failed"
            or row.get("result_json") is not None
            or not response_id
        ):
            output.append({"job_id": job_id, "status": "not_recoverable"})
            continue
        response = await runtime.retrieve(settings, response_id)
        if str(getattr(response, "status", "") or "") != "completed":
            output.append({"job_id": job_id, "status": "provider_not_completed"})
            continue
        terminal_error = runtime.response_terminal_error(response)
        if terminal_error:
            output.append(
                {
                    "job_id": job_id,
                    "status": "provider_terminal_error",
                    "error_code": terminal_error,
                }
            )
            continue
        payload = json.loads(str(row["payload_json"]))
        result = runtime.response_result(response, str(row["job_type"]), payload)
        if apply:
            recovered = repository.recover_schema_validation_failure(
                job_id,
                response_id,
                result,
            )
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
    args = _parser().parse_args(argv)
    results = asyncio.run(recover(args.job_ids, apply=bool(args.apply)))
    print(json.dumps(results, ensure_ascii=False, separators=(",", ":")))
    successful = {"validated", "recovered"}
    return 0 if results and all(item["status"] in successful for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
