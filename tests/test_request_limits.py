"""Exercise body deadlines and admission through the real gateway, without I/O."""
from __future__ import annotations

import asyncio
import json
from collections import deque

import pytest

from app import main, request_limits


class _Runtime:
    mode = "password"
    visitor_ai_actions = False
    visitor_live_pulls = False

    def __init__(self, owner: bool = True):
        self.owner = owner

    def request_is_owner(self, _request):
        return self.owner


@pytest.fixture(autouse=True)
def _clear_rate_buckets():
    main._rl_buckets.clear()
    yield
    main._rl_buckets.clear()


async def _echo(_scope, receive, send):
    message = await receive()
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": message["body"]})


async def _request(
    receive, *, headers=(), method="POST", version="1.1",
    path="/api/account/register", application=_echo, owner=True,
):
    sent = []

    async def send(message):
        sent.append(message)

    await main._GatewayMiddleware(application, access_runtime=_Runtime(owner))(
        {
            "type": "http", "http_version": version, "method": method,
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "scheme": "https", "server": ("testserver", 443),
            "client": ("203.0.113.77", 45000),
            "headers": [(b"host", b"testserver"), *headers],
        },
        receive,
        send,
    )
    return sent


def _messages(*messages):
    remaining = deque(messages)

    async def receive():
        assert remaining, "must not read beyond the final chunk or rejection"
        return remaining.popleft()

    return receive


def _error(sent):
    return sent[0]["status"], json.loads(sent[1]["body"])["error"]


def test_gateway_body_deadline_covers_the_first_receive(monkeypatch):
    monkeypatch.setattr(request_limits, "API_BODY_TIMEOUT_SECONDS", 0.025)

    async def receive():
        await asyncio.Future()

    sent = asyncio.run(_request(receive))
    assert _error(sent) == (408, "request_body_timeout")
    assert dict(sent[0]["headers"])[b"connection"] == b"close"


def test_gateway_deadline_is_total_not_reset_by_each_chunk(monkeypatch):
    monkeypatch.setattr(request_limits, "API_BODY_TIMEOUT_SECONDS", 0.05)
    reads = 0

    async def receive():
        nonlocal reads
        await asyncio.sleep(0.01)
        reads += 1
        return {"type": "http.request", "body": b"x", "more_body": reads < 50}

    sent = asyncio.run(_request(receive))
    assert _error(sent) == (408, "request_body_timeout")
    assert 1 <= reads < 50


def test_empty_fragments_are_bounded_even_without_body_growth(monkeypatch):
    monkeypatch.setattr(request_limits, "MAX_BODY_MESSAGES", 3)
    reads = 0

    async def receive():
        nonlocal reads
        reads += 1
        return {"type": "http.request", "body": b"", "more_body": True}

    sent = asyncio.run(_request(receive))
    assert _error(sent) == (413, "request_body_too_fragmented")
    assert reads == 4


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"])
def test_all_api_methods_keep_the_existing_body_admission(method, monkeypatch):
    monkeypatch.setattr(request_limits, "MAX_BODY_MESSAGES", 1)
    receive = _messages(
        {"type": "http.request", "body": b"", "more_body": True},
        {"type": "http.request", "body": b"", "more_body": False},
    )
    assert _error(asyncio.run(_request(receive, method=method))) == (413, "request_body_too_fragmented")


@pytest.mark.parametrize("length", [b"1", b"3"])
def test_actual_length_must_match_declared_length(length):
    sent = asyncio.run(_request(
        _messages({"type": "http.request", "body": b"{}", "more_body": False}),
        headers=[(b"content-length", length)],
    ))
    assert _error(sent) == (400, "content_length_mismatch")


@pytest.mark.parametrize("headers", [[], [(b"content-length", b"2")], [(b"Content-Length", b" 0002 ")]])
def test_valid_body_is_replayed_once_then_uses_real_disconnect(headers):
    received = []

    async def app(_scope, receive, send):
        received.append(await receive())
        received.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    disconnect = {"type": "http.disconnect"}
    sent = asyncio.run(_request(_messages(
        {"type": "http.request", "body": b"{", "more_body": True},
        {"type": "http.request", "body": b"}", "more_body": False},
        disconnect,
    ), headers=headers, application=app))
    assert sent[0]["status"] == 200
    assert received == [{"type": "http.request", "body": b"{}", "more_body": False}, disconnect]


@pytest.mark.parametrize("values", [[b""], [b"-1"], [b"+2"], [b"1,2"], [b"2", b"2"], [b"9" * 20]])
def test_invalid_length_is_rejected_before_receiving(values):
    sent = asyncio.run(_request(_messages(), headers=[(b"content-length", value) for value in values]))
    assert _error(sent) == (400, "invalid_content_length")


@pytest.mark.parametrize("version, closes_connection", [("1.0", True), ("1.1", True), ("2", False), ("3", False)])
def test_body_rejection_closes_only_http1_connections(version, closes_connection):
    sent = asyncio.run(_request(_messages(), version=version, headers=[(b"content-length", b"-1")]))
    assert (dict(sent[0]["headers"]).get(b"connection") == b"close") is closes_connection


def test_disconnected_upload_does_not_attempt_a_response():
    sent = asyncio.run(_request(_messages(
        {"type": "http.request", "body": b"partial", "more_body": True},
        {"type": "http.disconnect"},
    )))
    assert sent == []


@pytest.mark.parametrize("message", [{"type": "unknown"}, {"type": "http.request", "body": "secret"}])
def test_invalid_receive_messages_do_not_reach_the_router(message):
    sent = asyncio.run(_request(_messages(message)))
    assert _error(sent) == (400, "invalid_request_body")
    assert b"secret" not in sent[1]["body"]


def test_authentication_still_precedes_body_reading_and_validation():
    sent = asyncio.run(_request(
        _messages(), path="/api/settings", owner=False,
        headers=[(b"content-length", b"-1")],
    ))
    assert sent[0]["status"] == 401
    assert json.loads(sent[1]["body"])["error"] != "invalid_content_length"


def test_rate_limit_still_precedes_body_reading_and_validation(monkeypatch):
    monkeypatch.setattr(main, "_RL_LIGHT_LIMIT", 1)
    first = asyncio.run(_request(_messages({"type": "http.request", "body": b"", "more_body": False})))
    assert first[0]["status"] == 200
    blocked = asyncio.run(_request(_messages(), headers=[(b"content-length", b"-1")]))
    assert _error(blocked) == (429, "rate_limited")
