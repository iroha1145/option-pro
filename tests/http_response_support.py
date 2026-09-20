"""Helpers for tests that invoke route handlers directly.

Snapshot GET endpoints now take the Request (for If-None-Match) and return a
plain Response with pre-serialized bytes (so FastAPI never re-encodes 4MB
payloads). Tests that call the handlers as functions use these to build a
minimal request and to read the JSON body back regardless of return shape.
"""

from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import Request, Response


def lock_screener_admin_production(monkeypatch: Any) -> None:
    """Keep scheduled strength_refresh on the original production snapshot identity."""

    settings = type(
        "Settings",
        (),
        {
            "algorithms": type(
                "Algos",
                (),
                {
                    "screener_ranking_algorithm": "production",
                    "radar_sort_algorithm": "production",
                },
            )()
        },
    )()
    monkeypatch.setattr("app.api.strength.get_effective_runtime_settings", lambda: settings)


def anonymous_get_request(
    path: str = "/api/test",
    headers: Mapping[str, str] | None = None,
) -> Request:
    raw_headers = [
        (key.lower().encode("latin-1"), value.encode("latin-1"))
        for key, value in (headers or {}).items()
    ]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "headers": raw_headers,
            "query_string": b"",
        }
    )


def response_payload(result: Any) -> Any:
    """Return the JSON payload whether the handler returned dict or Response."""

    if isinstance(result, Response):
        if not result.body:
            return None
        body = result.body
        if result.headers.get("content-encoding") == "gzip":
            import gzip

            body = gzip.decompress(body)
        return json.loads(body)
    return result
