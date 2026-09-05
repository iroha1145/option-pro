"""Bound API bodies after gateway admission, before routing or JSON parsing."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


API_BODY_TIMEOUT_SECONDS = 10.0
MAX_BODY_MESSAGES = 16_384
Receive = Callable[[], Awaitable[dict[str, Any]]]

_REJECTION_MESSAGES = {
    "invalid_content_length": "Invalid Content-Length",
    "request_body_too_large": "Request body is too large",
    "request_body_too_fragmented": "Request body has too many fragments",
    "request_body_timeout": "Request body did not arrive in time",
    "invalid_request_body": "Invalid request body",
    "content_length_mismatch": "Request body does not match Content-Length",
}


class BodyRejected(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.message = _REJECTION_MESSAGES[code]


class ClientDisconnected(Exception):
    """The gateway must not send a response after the client disconnects."""


def _declared_length(headers: list[tuple[bytes, bytes]], limit: int) -> int | None:
    lengths = [value for key, value in headers if key.lower() == b"content-length"]
    if not lengths:
        return None
    if len(lengths) != 1:
        raise BodyRejected(400, "invalid_content_length")
    raw = lengths[0].strip()
    # Bound integer parsing; signed, comma-joined and non-ASCII values are invalid.
    if not raw or len(raw) > 19 or any(c < 48 or c > 57 for c in raw):
        raise BodyRejected(400, "invalid_content_length")
    length = int(raw)
    if length > limit:
        raise BodyRejected(413, "request_body_too_large")
    return length


async def bounded_api_receive(
    scope: dict[str, Any],
    receive: Receive,
    *,
    limit: int,
    timeout: float | None = None,
    max_messages: int | None = None,
) -> Receive:
    """Buffer within one total deadline, then replay once and retain disconnects.

    The route-specific byte limit is mandatory: credentials must not inherit the
    larger limit needed for a full drawing import. The deadline covers the FIRST
    read from the server and is never renewed when another chunk arrives.
    """
    timeout = API_BODY_TIMEOUT_SECONDS if timeout is None else timeout
    max_messages = MAX_BODY_MESSAGES if max_messages is None else max_messages
    if limit <= 0 or timeout <= 0 or max_messages <= 0:
        raise ValueError("body limits must be positive")
    declared = _declared_length(scope.get("headers", []), limit)
    body = bytearray()
    count = 0
    try:
        async with asyncio.timeout(timeout):
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    raise ClientDisconnected
                if message.get("type") != "http.request":
                    raise BodyRejected(400, "invalid_request_body")
                count += 1
                if count > max_messages:
                    raise BodyRejected(413, "request_body_too_fragmented")
                chunk = message.get("body", b"")
                if not isinstance(chunk, bytes):
                    raise BodyRejected(400, "invalid_request_body")
                if len(chunk) > limit - len(body):
                    raise BodyRejected(413, "request_body_too_large")
                body.extend(chunk)
                if not message.get("more_body", False):
                    break
    except TimeoutError as exc:
        raise BodyRejected(408, "request_body_timeout") from exc
    if declared is not None and len(body) != declared:
        raise BodyRejected(400, "content_length_mismatch")

    buffered = bytes(body)
    del body
    pending = True

    async def replay() -> dict[str, Any]:
        nonlocal pending, buffered
        if pending:
            pending = False
            content, buffered = buffered, b""
            return {"type": "http.request", "body": content, "more_body": False}
        # Real disconnect monitoring remains available to streaming responses.
        return await receive()

    return replay
