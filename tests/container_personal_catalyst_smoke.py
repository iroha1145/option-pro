from __future__ import annotations

import asyncio
import json

import httpx

from app.config import Settings
from app.personal_config import get_personal_config
from app.worker.tasks import CatalystSyncTask


AS_OF = "2026-07-16T00:00:00Z"


def _page(path: str) -> dict:
    payload = {
        "items": [],
        "has_more": False,
        "next_cursor": None,
        "next_updated_after": AS_OF,
        "next_after_sequence": 0,
        "watermark": {"sequence": 0, "as_of": AS_OF},
    }
    if path.endswith("/calendar"):
        payload["watermark"]["snapshot_token"] = None
        payload["data_through"] = None
        payload["is_stale"] = False
    return payload


async def _run() -> dict:
    settings = Settings()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get_list("authorization") == [
            "Bearer container-fixture-owner"
        ]
        assert request.url.path in {
            "/internal/v1/news/changes",
            "/internal/v1/calendar",
        }
        requests.append(request.url.path)
        return httpx.Response(200, json=_page(request.url.path))

    task = CatalystSyncTask(
        "container-catalyst-smoke",
        settings=settings,
        personal_config=get_personal_config(),
        etl_transport=httpx.MockTransport(handler),
    )
    try:
        result = await task()
    finally:
        await task.aclose()
    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_reasoning == "max"
    assert settings.openai_max_concurrency == 1
    assert result.status == "idle"
    assert requests == [
        "/internal/v1/news/changes",
        "/internal/v1/calendar",
    ]
    return {
        "status": result.status,
        "processed": result.details["processed"],
        "model": settings.openai_model,
        "reasoning": settings.openai_reasoning,
        "max_concurrency": settings.openai_max_concurrency,
        "authorization_checked": True,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(_run()),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )
