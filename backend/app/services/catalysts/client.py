from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional, TypeVar, Union

import httpx
from pydantic import BaseModel, ValidationError

from .config import CatalystSettings
from .errors import CatalystError, CatalystSchemaError
from .models import (
    CalendarResponse,
    CatalystBatchRequest,
    CatalystItem,
    ContractEnvelope,
    FeedResponse,
    HealthResponse,
    HotspotPreparationResponse,
    HotspotPreparationStatus,
    LatestResponse,
    MarketFocusCycleCreateRequest,
    MarketFocusCycleResponse,
    RemoteJobResponse,
    SCHEMA_VERSION,
    TickerResponse,
)
from .signing import canonical_query, sign_request


INTEGRATION_PREFIX = "/api/integrations/option-pro/v1"
_ModelT = TypeVar("_ModelT", bound=BaseModel)


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0
    half_open_probe: bool = False


class CircuitBreakerRegistry:
    """Per endpoint-family and credential-scope in-process circuit breaker."""

    def __init__(self, *, threshold: int, open_seconds: int) -> None:
        self._threshold = threshold
        self._open_seconds = open_seconds
        self._states: dict[tuple[str, str], _CircuitState] = {}
        self._lock = asyncio.Lock()

    async def before_request(self, family: str, scope: str) -> None:
        async with self._lock:
            state = self._states.setdefault((family, scope), _CircuitState())
            now = time.monotonic()
            if state.open_until > now:
                raise CatalystError(
                    code="circuit_open",
                    message=f"MacroLens {family} circuit is open",
                    retryable=True,
                    retry_after_seconds=max(1, int(state.open_until - now)),
                    counts_for_circuit=False,
                )
            if state.open_until and state.open_until <= now:
                if state.half_open_probe:
                    raise CatalystError(
                        code="circuit_half_open",
                        message=f"MacroLens {family} circuit probe is already running",
                        retryable=True,
                        retry_after_seconds=1,
                        counts_for_circuit=False,
                    )
                state.half_open_probe = True

    async def success(self, family: str, scope: str) -> None:
        async with self._lock:
            self._states[(family, scope)] = _CircuitState()

    async def failure(self, family: str, scope: str, *, count: bool) -> None:
        async with self._lock:
            state = self._states.setdefault((family, scope), _CircuitState())
            state.half_open_probe = False
            if not count:
                return
            state.failures += 1
            if state.failures >= self._threshold:
                state.open_until = time.monotonic() + self._open_seconds


class MacroLensClient:
    """Fixed-origin, signed, bounded remote client.

    The client never inherits proxy environment variables, never follows a
    redirect, and validates every success response before returning it.
    """

    def __init__(
        self,
        settings: CatalystSettings,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        now: Optional[Callable[[], float]] = None,
    ) -> None:
        if not settings.enabled:
            raise ValueError("MacroLensClient cannot be created while disabled")
        self.settings = settings
        self._now = now or time.time
        timeout = httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.connect_timeout_seconds,
            pool=settings.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            timeout=timeout,
            verify=settings.tls_verify_value,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "Optix-Catalyst-Sync/1"},
        )
        self._circuit = CircuitBreakerRegistry(
            threshold=settings.failure_threshold,
            open_seconds=settings.circuit_open_seconds,
        )
        self._expected_schema_sha = self._load_contract_sha()

    async def __aenter__(self) -> "MacroLensClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    def _load_contract_sha(self) -> str:
        if self.settings.schema_sha256:
            return self.settings.schema_sha256
        contract = Path(__file__).resolve().parents[4] / "contracts" / "macrolens-option-pro-v2.json"
        if not contract.is_file():
            return ""
        return hashlib.sha256(contract.read_bytes()).hexdigest()

    def _validate_envelope(self, value: BaseModel) -> None:
        schema_version = getattr(value, "schema_version", None)
        schema_sha256 = getattr(value, "schema_sha256", None)
        if schema_version != self.settings.schema_version or schema_version != SCHEMA_VERSION:
            raise CatalystSchemaError("MacroLens schema_version does not match the pinned v2 contract")
        if self._expected_schema_sha and schema_sha256 != self._expected_schema_sha:
            raise CatalystSchemaError("MacroLens schema_sha256 does not match the pinned contract")

    def _credentials(self, scope: str) -> tuple[str, str]:
        if scope == "action":
            key_id = self.settings.action_key_id
            secret = self.settings.action_secret.get_secret_value()
            if not key_id or not secret:
                raise CatalystError(
                    code="capability_disabled",
                    message="MacroLens action credentials are not configured",
                    retryable=False,
                    counts_for_circuit=False,
                )
            return key_id, secret
        return self.settings.read_key_id, self.settings.read_secret.get_secret_value()

    @staticmethod
    def _json_bytes(payload: Optional[Any]) -> bytes:
        if payload is None:
            return b""
        return json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        raw_length = response.headers.get("content-length", "")
        if raw_length:
            try:
                if int(raw_length) > self.settings.max_response_bytes:
                    raise CatalystError(
                        code="response_too_large",
                        message="MacroLens response exceeded the configured size limit",
                        retryable=False,
                    )
            except ValueError:
                pass
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.settings.max_response_bytes:
                raise CatalystError(
                    code="response_too_large",
                    message="MacroLens response exceeded the configured size limit",
                    retryable=False,
                )
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _classify_transport(exc: Exception) -> CatalystError:
        if isinstance(exc, httpx.TimeoutException):
            return CatalystError("remote_timeout", "MacroLens request timed out", True)
        current: Optional[BaseException] = exc
        visited = set()
        while current is not None and id(current) not in visited:
            visited.add(id(current))
            if isinstance(current, ssl.SSLError):
                return CatalystError("tls_error", "MacroLens TLS verification failed", False)
            if isinstance(current, socket.gaierror):
                return CatalystError("dns_error", "MacroLens DNS lookup failed", True)
            current = current.__cause__ or current.__context__
        if isinstance(exc, httpx.NetworkError):
            return CatalystError("network_error", "MacroLens network request failed", True)
        return CatalystError("remote_transport_error", "MacroLens transport failed", True)

    @staticmethod
    def _status_error(response: httpx.Response, body: bytes) -> CatalystError:
        retry_after: Optional[int] = None
        try:
            retry_after = max(0, min(86_400, int(response.headers.get("retry-after", ""))))
        except (TypeError, ValueError):
            retry_after = None
        safe_code = ""
        safe_retryable: Optional[bool] = None
        safe_retry_after: Optional[int] = None
        safe_resync_from: Optional[datetime] = None
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                candidate = parsed.get("code")
                if isinstance(candidate, str) and 0 < len(candidate) <= 100:
                    safe_code = candidate
                if isinstance(parsed.get("retryable"), bool):
                    safe_retryable = parsed["retryable"]
                candidate_retry = parsed.get("retry_after_seconds")
                if isinstance(candidate_retry, int) and 0 <= candidate_retry <= 86_400:
                    safe_retry_after = candidate_retry
                if safe_code == "updated_after_too_old":
                    try:
                        server_time = datetime.fromisoformat(
                            str(parsed.get("server_time") or "").replace("Z", "+00:00")
                        )
                        if server_time.tzinfo is None or server_time.utcoffset() is None:
                            raise ValueError
                        server_time = server_time.astimezone(timezone.utc)
                        window_days = parsed.get("latest_window_days")
                        if not isinstance(window_days, int) or not 1 <= window_days <= 7:
                            raise ValueError
                        raw_boundary = parsed.get("resync_from")
                        if raw_boundary is None:
                            raise ValueError
                        safe_resync_from = datetime.fromisoformat(
                            str(raw_boundary).replace("Z", "+00:00")
                        )
                        if (
                            safe_resync_from.tzinfo is None
                            or safe_resync_from.utcoffset() is None
                        ):
                            raise ValueError
                        safe_resync_from = safe_resync_from.astimezone(timezone.utc)
                        boundary_age = server_time - safe_resync_from
                        if not timedelta(0) <= boundary_age <= timedelta(
                            days=window_days, seconds=5
                        ):
                            raise ValueError
                    except (TypeError, ValueError, OverflowError):
                        safe_resync_from = None
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        if response.status_code in {401, 403}:
            return CatalystError(
                code="remote_unauthorized" if response.status_code == 401 else "remote_forbidden",
                message="MacroLens rejected the service credentials or scope",
                retryable=False,
            )
        if response.status_code == 429:
            return CatalystError(
                code=safe_code or "remote_rate_limited",
                message="MacroLens rate limit was reached",
                retryable=True,
                retry_after_seconds=safe_retry_after or retry_after,
            )
        if 500 <= response.status_code <= 599:
            return CatalystError(
                code=safe_code or "remote_server_error",
                message="MacroLens returned a server error",
                retryable=True if safe_retryable is None else safe_retryable,
                retry_after_seconds=safe_retry_after or retry_after,
            )
        return CatalystError(
            code=safe_code or "remote_request_rejected",
            message=f"MacroLens rejected the request with status {response.status_code}",
            retryable=False if safe_retryable is None else safe_retryable,
            retry_after_seconds=safe_retry_after or retry_after,
            resync_from=safe_resync_from,
        )

    async def _request(
        self,
        model: type[_ModelT],
        *,
        method: str,
        endpoint: str,
        family: str,
        scope: str = "read",
        params: Optional[Union[Mapping[str, Any], list[tuple[str, Any]]]] = None,
        payload: Optional[Any] = None,
    ) -> _ModelT:
        await self._circuit.before_request(family, scope)
        key_id, secret = self._credentials(scope)
        path = f"{INTEGRATION_PREFIX}{endpoint}"
        body = self._json_bytes(payload)
        query = canonical_query(params)
        url = path + (f"?{query}" if query else "")
        final_error: Optional[CatalystError] = None
        try:
            for attempt in range(self.settings.request_max_attempts):
                response: Optional[httpx.Response] = None
                try:
                    # Every transport attempt is a distinct signed request.
                    # Reusing a nonce after a network retry would correctly be
                    # rejected by MacroLens replay protection.
                    headers = sign_request(
                        method=method,
                        path=path,
                        params=params,
                        body=body,
                        key_id=key_id,
                        secret=secret,
                        timestamp=int(self._now()),
                    )
                    if body:
                        headers["Content-Type"] = "application/json"
                    request = self._client.build_request(method, url, headers=headers, content=body)
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + self.settings.total_timeout_seconds
                    response = await asyncio.wait_for(
                        self._client.send(request, stream=True),
                        timeout=self.settings.total_timeout_seconds,
                    )
                    remaining = max(0.001, deadline - loop.time())
                    response_body = await asyncio.wait_for(
                        self._bounded_body(response), timeout=remaining
                    )
                    if response.is_redirect:
                        raise CatalystError(
                            code="remote_redirect_rejected",
                            message="MacroLens returned a redirect",
                            retryable=False,
                        )
                    if response.status_code < 200 or response.status_code >= 300:
                        raise self._status_error(response, response_body)
                    try:
                        value = model.model_validate_json(response_body)
                    except ValidationError as exc:
                        raise CatalystSchemaError() from exc
                    self._validate_envelope(value)
                    await self._circuit.success(family, scope)
                    return value
                except CatalystError as exc:
                    final_error = exc
                except (httpx.HTTPError, TimeoutError) as exc:
                    final_error = self._classify_transport(exc)
                finally:
                    if response is not None:
                        await response.aclose()
                if (
                    final_error is None
                    or not final_error.retryable
                    or final_error.code == "remote_rate_limited"
                    or attempt + 1 >= self.settings.request_max_attempts
                ):
                    break
                await asyncio.sleep(0.2 * (2**attempt))
        except BaseException:
            # A cancelled half-open probe must not leave the circuit locked.
            await self._circuit.failure(family, scope, count=False)
            raise
        assert final_error is not None
        await self._circuit.failure(
            family, scope, count=final_error.counts_for_circuit
        )
        raise final_error

    async def health(self) -> HealthResponse:
        return await self._request(
            HealthResponse, method="GET", endpoint="/health", family="health"
        )

    async def feed(self, **params: Any) -> FeedResponse:
        return await self._request(
            FeedResponse,
            method="GET",
            endpoint="/feed",
            family="feed",
            params={key: value for key, value in params.items() if value is not None},
        )

    async def latest(
        self,
        *,
        updated_after: Optional[datetime],
        cursor: Optional[str],
        limit: int,
    ) -> LatestResponse:
        params: dict[str, Any] = {"limit": limit}
        if updated_after is not None:
            params["updated_after"] = updated_after.isoformat()
        if cursor:
            params["cursor"] = cursor
        return await self._request(
            LatestResponse,
            method="GET",
            endpoint="/latest",
            family="feed",
            params=params,
        )

    async def calendar(self, **params: Any) -> CalendarResponse:
        return await self._request(
            CalendarResponse,
            method="GET",
            endpoint="/calendar",
            family="calendar",
            params={key: value for key, value in params.items() if value is not None},
        )

    async def ticker_catalysts(self, ticker: str, **params: Any) -> TickerResponse:
        return await self._request(
            TickerResponse,
            method="GET",
            endpoint=f"/catalysts/{ticker}",
            family="feed",
            params={key: value for key, value in params.items() if value is not None},
        )

    async def catalyst_batch(self, request: CatalystBatchRequest) -> dict[str, Any]:
        # This endpoint is available for contract diagnostics.  Normal Option
        # Pro page reads use the local cache and never call it.
        value = await self._request(
            _BatchEnvelope,
            method="POST",
            endpoint="/catalysts/batch",
            family="feed",
            scope="read",
            payload=request.model_dump(mode="json", exclude_none=True),
        )
        return value.model_dump(mode="json")

    async def create_analysis_job(
        self,
        news_id: int,
        *,
        expected_content_hash: str,
        expected_change_sequence: Optional[int],
        force: bool,
    ) -> RemoteJobResponse:
        return await self._request(
            RemoteJobResponse,
            method="POST",
            endpoint="/analysis-jobs",
            family="job",
            scope="action",
            payload={
                "news_id": news_id,
                "expected_content_hash": expected_content_hash,
                "expected_change_sequence": expected_change_sequence,
                "force": force,
            },
        )

    async def get_analysis_job(self, remote_job_id: str) -> RemoteJobResponse:
        return await self._request(
            RemoteJobResponse,
            method="GET",
            endpoint=f"/analysis-jobs/{remote_job_id}",
            family="job",
            scope="read",
        )

    async def cancel_analysis_job(self, remote_job_id: str) -> RemoteJobResponse:
        return await self._request(
            RemoteJobResponse,
            method="POST",
            endpoint=f"/analysis-jobs/{remote_job_id}/cancel",
            family="job",
            scope="action",
            payload={},
        )

    async def hotspot_status(self) -> HotspotPreparationStatus:
        return await self._request(
            HotspotPreparationStatus,
            method="GET",
            endpoint="/hotspots/status",
            family="market_focus",
        )

    async def hotspots(
        self,
        *,
        limit: int,
        as_of: Optional[datetime] = None,
    ) -> HotspotPreparationResponse:
        params: dict[str, Any] = {"limit": limit}
        if as_of is not None:
            params["as_of"] = as_of.isoformat()
        return await self._request(
            HotspotPreparationResponse,
            method="GET",
            endpoint="/hotspots",
            family="market_focus",
            params=params,
        )

    async def latest_market_focus_cycle(self) -> MarketFocusCycleResponse:
        return await self._request(
            MarketFocusCycleResponse,
            method="GET",
            endpoint="/market-focus-cycles/latest",
            family="market_focus",
        )

    async def create_market_focus_cycle(
        self,
        *,
        expected_prepared_revision: int | None = None,
        retry_cycle_id: str | None = None,
    ) -> MarketFocusCycleResponse:
        if (expected_prepared_revision is None) == (retry_cycle_id is None):
            raise ValueError(
                "exactly one of expected_prepared_revision or retry_cycle_id is required"
            )
        request = MarketFocusCycleCreateRequest(
            trigger="manual",
            expected_prepared_revision=expected_prepared_revision,
            retry_cycle_id=retry_cycle_id,
        )
        return await self._request(
            MarketFocusCycleResponse,
            method="POST",
            endpoint="/market-focus-cycles",
            family="market_focus_job",
            scope="action",
            payload=request.model_dump(mode="json", exclude_none=True),
        )

    async def get_market_focus_cycle(
        self,
        remote_cycle_id: str,
    ) -> MarketFocusCycleResponse:
        return await self._request(
            MarketFocusCycleResponse,
            method="GET",
            endpoint=f"/market-focus-cycles/{remote_cycle_id}",
            family="market_focus_job",
        )

    async def cancel_market_focus_cycle(
        self,
        remote_cycle_id: str,
    ) -> MarketFocusCycleResponse:
        return await self._request(
            MarketFocusCycleResponse,
            method="POST",
            endpoint=f"/market-focus-cycles/{remote_cycle_id}/cancel",
            family="market_focus_job",
            scope="action",
            payload={},
        )


class _BatchTickerResult(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    status: Literal["active", "empty", "stale", "unavailable"]
    data_through: Optional[datetime] = None
    items: list[CatalystItem]
    next_cursor: Optional[str] = None


class _BatchEnvelope(ContractEnvelope):
    as_of: datetime
    results: dict[str, _BatchTickerResult]
