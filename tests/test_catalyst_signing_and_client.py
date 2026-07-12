from __future__ import annotations

import hashlib
import hmac
import asyncio
import json
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.services.catalysts.client import MacroLensClient
from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.errors import CatalystError
from app.services.catalysts.models import (
    CatalystBatchRequest,
    HealthResponse,
    NewsImpactAnalysis,
)
from app.services.catalysts.signing import (
    canonical_query,
    canonical_string,
    sha256_hex,
    sign_request,
)
from app.services.catalysts.worker import PINNED_CONTRACT_SHA256


SCHEMA_SHA = "a" * 64
READ_SECRET = "read-secret-0123456789abcdef-0001"
ACTION_SECRET = "action-secret-0123456789abcdef-01"


def test_pinned_contract_copy_has_the_reviewed_byte_digest() -> None:
    path = Path(__file__).resolve().parents[1] / "contracts" / "macrolens-option-pro-v1.json"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PINNED_CONTRACT_SHA256
    assert json.loads(raw)["schema_version"] == "macrolens-option-pro-v1"


def test_remote_models_match_the_pinned_contract_schemas() -> None:
    path = Path(__file__).resolve().parents[1] / "contracts" / "macrolens-option-pro-v1.json"
    models = json.loads(path.read_bytes())["models"]
    assert NewsImpactAnalysis.model_json_schema() == models["NewsImpactAnalysis"]
    assert CatalystBatchRequest.model_json_schema() == models["CatalystBatchRequest"]
    assert HealthResponse.model_json_schema() == models["IntegrationHealthResponse"]


def test_batch_request_uses_contract_defaults_and_bounds() -> None:
    request = CatalystBatchRequest(tickers=[" nvda "])
    assert request.tickers == ["NVDA"]
    assert request.min_confidence == 0
    with pytest.raises(ValidationError):
        CatalystBatchRequest(tickers=["NVDA"], window_hours=721)
    with pytest.raises(ValidationError):
        CatalystBatchRequest(tickers=["NVDA"], min_confidence=None)


def settings(**overrides) -> CatalystSettings:
    values = {
        "MACROLENS_ENABLED": True,
        "MACROLENS_BASE_URL": "http://localhost:9876",
        "MACROLENS_ALLOW_LOCAL_HTTP": True,
        "MACROLENS_READ_KEY_ID": "read-key",
        "MACROLENS_READ_SECRET": READ_SECRET,
        "MACROLENS_SCHEMA_SHA256": SCHEMA_SHA,
        "MACROLENS_REQUEST_MAX_ATTEMPTS": 1,
    }
    values.update(overrides)
    return CatalystSettings(_env_file=None, **values)


def test_canonical_hmac_preserves_repeated_empty_and_rfc3986_values() -> None:
    params = [("b", "hello world"), ("a", ""), ("a", "x/y")]
    assert canonical_query(params) == "a=&a=x%2Fy&b=hello%20world"
    body = b'{"force":false,"news_id":101}'
    message = canonical_string(
        method="post",
        path="/api/integrations/option-pro/v1/analysis-jobs",
        query=canonical_query(params),
        timestamp="1720000000",
        nonce="fixed-nonce",
        body_sha256=sha256_hex(body),
    )
    headers = sign_request(
        method="post",
        path="/api/integrations/option-pro/v1/analysis-jobs",
        params=params,
        body=body,
        key_id="read-key",
        secret=READ_SECRET,
        timestamp=1720000000,
        nonce="fixed-nonce",
    )
    expected = hmac.new(READ_SECRET.encode(), message.encode(), hashlib.sha256).hexdigest()
    assert headers["X-Optix-Signature"] == expected
    assert headers["X-Optix-Content-SHA256"] == hashlib.sha256(body).hexdigest()


def test_remote_configuration_rejects_nonlocal_http_and_disabled_tls(tmp_path) -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        settings(MACROLENS_BASE_URL="http://news.example.com")
    with pytest.raises(ValidationError, match="cannot be disabled"):
        settings(
            MACROLENS_BASE_URL="https://news.example.com",
            MACROLENS_VERIFY_TLS=False,
        )
    with pytest.raises(ValidationError, match="configured together"):
        settings(MACROLENS_ACTION_KEY_ID="action-only")
    with pytest.raises(ValidationError, match="readable file"):
        settings(MACROLENS_CA_BUNDLE=str(tmp_path / "missing-ca.pem"))
    ca_bundle = tmp_path / "private-ca.pem"
    ca_bundle.write_text("fixture CA", encoding="utf-8")
    configured = settings(MACROLENS_CA_BUNDLE=str(ca_bundle))
    assert configured.tls_verify_value == str(ca_bundle)


@pytest.mark.parametrize("key_id", ["bad key", "bad/key", "bad@key", "密钥"])
def test_remote_configuration_rejects_unsupported_key_ids(key_id: str) -> None:
    with pytest.raises(ValidationError, match="unsupported characters"):
        settings(MACROLENS_READ_KEY_ID=key_id)


def test_remote_configuration_rejects_short_or_reused_credentials() -> None:
    with pytest.raises(ValidationError, match="at least 32 bytes"):
        settings(MACROLENS_READ_SECRET="short-secret")
    with pytest.raises(ValidationError, match="must be different"):
        settings(
            MACROLENS_ACTION_KEY_ID="read-key",
            MACROLENS_ACTION_SECRET=ACTION_SECRET,
        )


def test_remote_configuration_counts_hmac_secret_length_in_utf8_bytes() -> None:
    configured = settings(MACROLENS_READ_SECRET="密钥材料" * 4)
    assert len(configured.read_secret.get_secret_value().encode("utf-8")) >= 32


def test_client_signs_actual_empty_body_and_validates_pinned_envelope() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.headers)
        return httpx.Response(
            200,
            json={
                "schema_version": "macrolens-option-pro-v1",
                "schema_sha256": SCHEMA_SHA,
                "request_id": "request-1234",
                "status": "ok",
                "as_of": "2026-07-12T10:00:00Z",
                "data_through": None,
                "database": {"status": "ok"},
                "scheduler": {"status": "ok"},
                "analysis_queue": {
                    "status": "ok",
                    "pending": 0,
                    "queued": 0,
                    "in_progress": 0,
                    "budget_status": "budget_unbounded",
                },
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "execution_mode": "background",
                "analysis_trigger_enabled": False,
                "sources": {},
                "warnings": [],
            },
        )

    async def scenario():
        client = MacroLensClient(
            settings(), transport=httpx.MockTransport(handler), now=lambda: 1720000000
        )
        try:
            return await client.health()
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.model == "gpt-5.6-terra"
    assert seen["x-optix-content-sha256"] == hashlib.sha256(b"").hexdigest()
    assert seen["x-optix-key-id"] == "read-key"
    assert len(seen["x-optix-signature"]) == 64


def test_analysis_create_pins_the_exact_cached_news_revision() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            202,
            json={
                "schema_version": "macrolens-option-pro-v1",
                "schema_sha256": SCHEMA_SHA,
                "request_id": "request-1234",
                "job_id": "remote-job-1234",
                "news_id": 101,
                "content_hash": "content-hash-101",
                "input_hash": "b" * 64,
                "change_sequence": 7,
                "status": "failed",
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "submitted_at": None,
                "updated_at": "2026-07-12T10:00:00Z",
                "completed_at": "2026-07-12T10:00:00Z",
                "error_code": "fixture_failure",
                "retry_after": None,
                "result": None,
            },
        )

    async def scenario():
        client = MacroLensClient(
            settings(
                MACROLENS_ACTION_KEY_ID="action-key",
                MACROLENS_ACTION_SECRET=ACTION_SECRET,
            ),
            transport=httpx.MockTransport(handler),
        )
        try:
            return await client.create_analysis_job(
                101,
                expected_content_hash="content-hash-101",
                expected_change_sequence=7,
                force=True,
            )
        finally:
            await client.aclose()

    response = asyncio.run(scenario())
    assert response.content_hash == "content-hash-101"
    assert seen == {
        "news_id": 101,
        "expected_content_hash": "content-hash-101",
        "expected_change_sequence": 7,
        "force": True,
    }


def test_client_classifies_server_errors_and_opens_per_family_circuit() -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            503,
            json={
                "code": "upstream_unavailable",
                "message": "safe",
                "retryable": True,
                "retry_after_seconds": 1,
                "request_id": "request-1234",
            },
        )

    async def scenario():
        client = MacroLensClient(
            settings(MACROLENS_FAILURE_THRESHOLD=3),
            transport=httpx.MockTransport(handler),
        )
        try:
            for _ in range(3):
                with pytest.raises(CatalystError) as caught:
                    await client.health()
                assert caught.value.code == "upstream_unavailable"
            with pytest.raises(CatalystError) as caught:
                await client.health()
            assert caught.value.code == "circuit_open"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert calls == 3


def test_client_rejects_schema_digest_mismatch_without_exposing_body() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "schema_version": "macrolens-option-pro-v1",
                "schema_sha256": "b" * 64,
                "request_id": "request-1234",
                "snapshot_token": "snapshot-1234",
                "data_through": None,
                "next_updated_after": None,
                "next_cursor": None,
                "has_more": False,
                "items": [],
            },
        )

    async def scenario():
        client = MacroLensClient(settings(), transport=httpx.MockTransport(handler))
        try:
            with pytest.raises(CatalystError) as caught:
                await client.latest(updated_after=None, cursor=None, limit=100)
            assert caught.value.code == "schema_mismatch"
            assert "bbbb" not in str(caught.value)
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_each_transport_retry_uses_a_fresh_nonce() -> None:
    nonces = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonces.append(request.headers["x-optix-nonce"])
        if len(nonces) == 1:
            return httpx.Response(
                503,
                json={
                    "code": "temporary_remote_failure",
                    "message": "safe",
                    "retryable": True,
                    "retry_after_seconds": None,
                    "request_id": "request-1234",
                },
            )
        return httpx.Response(
            200,
            json={
                "schema_version": "macrolens-option-pro-v1",
                "schema_sha256": SCHEMA_SHA,
                "request_id": "request-1234",
                "status": "ok",
                "as_of": "2026-07-12T10:00:00Z",
                "data_through": None,
                "database": {"status": "ok"},
                "scheduler": {"status": "ok"},
                "analysis_queue": {
                    "status": "ok",
                    "pending": 0,
                    "queued": 0,
                    "in_progress": 0,
                    "budget_status": "budget_unbounded",
                },
                "model": "gpt-5.6-terra",
                "reasoning": "max",
                "execution_mode": "background",
                "analysis_trigger_enabled": False,
                "sources": {},
                "warnings": [],
            },
        )

    async def scenario():
        client = MacroLensClient(
            settings(MACROLENS_REQUEST_MAX_ATTEMPTS=2),
            transport=httpx.MockTransport(handler),
            now=lambda: 1720000000,
        )
        try:
            result = await client.health()
            assert result.status == "ok"
        finally:
            await client.aclose()

    asyncio.run(scenario())
    assert len(nonces) == 2
    assert nonces[0] != nonces[1]
