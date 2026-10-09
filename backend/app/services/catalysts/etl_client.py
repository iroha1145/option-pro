from __future__ import annotations

import asyncio
import json
import socket
import ssl
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError, field_validator

from app.json_validation import reject_duplicate_json_keys, reject_non_finite_json

from .ingest_models import (
    CALENDAR_PAGE_LIMIT,
    NEWS_PAGE_LIMIT,
    CalendarPage,
    NewsChangesPage,
    NewsWatermark,
    RawNewsItem,
    _require_utc_text,
    _WireModel,
)


# Keep protocol error causes independent of untrusted upstream keys and values.
_reject_duplicate = partial(reject_duplicate_json_keys, error_message="duplicate JSON key")
_reject_constant = partial(reject_non_finite_json, error_message="non-finite JSON number")

INTERNAL_API_PREFIX = "/internal/v1"
DEFAULT_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_Sleep = Callable[[float], Awaitable[None]]


class EtlClientError(RuntimeError):
    """A bounded error that never includes an upstream body or credential."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
        remote_reached: bool = False,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
        self.remote_reached = remote_reached


class EtlAuthenticationError(EtlClientError):
    def __init__(self, status_code: int) -> None:
        super().__init__(
            "authentication_failed",
            "MacroLens rejected the owner token",
            retryable=False,
            status_code=status_code,
            remote_reached=True,
        )


class EtlCursorResetRequired(EtlClientError):
    def __init__(self, code: str) -> None:
        super().__init__(
            code,
            "MacroLens rejected or expired the saved cursor",
            retryable=False,
            remote_reached=True,
        )


class EtlResponseTooLarge(EtlClientError):
    def __init__(self) -> None:
        super().__init__(
            "response_too_large",
            "MacroLens response exceeded the configured byte limit",
            retryable=False,
            remote_reached=True,
        )


class EtlProtocolError(EtlClientError):
    def __init__(self, message: str) -> None:
        super().__init__(
            "invalid_response",
            message,
            retryable=False,
            remote_reached=True,
        )


class NewsDetail(_WireModel):
    item: RawNewsItem
    watermark: NewsWatermark
    available_at: str

    @field_validator("available_at")
    @classmethod
    def validate_available_at(cls, value: str) -> str:
        return _require_utc_text(value, field="available_at")


class InternalHealth(_WireModel):
    status: Literal["ok", "degraded"]
    service: Literal["macrolens-etl"]
    as_of: str
    data_through: str | None
    database: Literal["ok"]
    scheduler: Literal["running", "stopped"]
    watermarks: dict[str, int]
    sources: list[dict[str, Any]]

    @field_validator("as_of", "data_through")
    @classmethod
    def validate_timestamps(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_utc_text(value, field=info.field_name)

    @field_validator("watermarks")
    @classmethod
    def validate_watermarks(cls, value: dict[str, int]) -> dict[str, int]:
        if set(value) != {"news_sequence", "calendar_sequence"}:
            raise ValueError("health watermarks are incomplete")
        if any(sequence < 0 for sequence in value.values()):
            raise ValueError("health watermark cannot be negative")
        return value


@dataclass(frozen=True)
class HealthProbe:
    reachable: bool
    authenticated: bool
    status: str
    error_code: str | None = None
    health: InternalHealth | None = None


@dataclass(frozen=True)
class EtlClientConfig:
    base_url: str
    owner_token: str = field(repr=False)
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 10.0
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_attempts: int = 3
    ca_bundle: str | Path | None = None

    def __post_init__(self) -> None:
        split = urlsplit(self.base_url)
        if (
            split.scheme != "https"
            or not split.hostname
            or split.username is not None
            or split.password is not None
            or split.query
            or split.fragment
            or split.path not in {"", "/"}
        ):
            raise ValueError("MacroLens base_url must be an origin using HTTPS")
        normalized = self.base_url.rstrip("/")
        object.__setattr__(self, "base_url", normalized)
        if (
            not self.owner_token
            or self.owner_token != self.owner_token.strip()
            or "\r" in self.owner_token
            or "\n" in self.owner_token
            or len(self.owner_token) > 4_096
        ):
            raise ValueError("MacroLens owner token is invalid")
        if self.connect_timeout_seconds <= 0 or self.read_timeout_seconds <= 0:
            raise ValueError("MacroLens timeouts must be positive")
        if not 1_024 <= self.max_response_bytes <= DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("MacroLens response byte limit is invalid")
        if not 1 <= self.max_attempts <= 5:
            raise ValueError("MacroLens max_attempts must be between 1 and 5")
        if self.ca_bundle is not None:
            bundle = Path(self.ca_bundle)
            if not bundle.is_file():
                raise ValueError("MacroLens CA bundle must be a readable file")
            object.__setattr__(self, "ca_bundle", bundle)


class MacroLensEtlClient:
    """One-way, bearer-authenticated reader for the MacroLens ETL surface."""

    def __init__(
        self,
        config: EtlClientConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: _Sleep = asyncio.sleep,
    ) -> None:
        self.config = config
        self._sleep = sleep
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=config.connect_timeout_seconds,
            pool=config.connect_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=config.base_url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {config.owner_token}",
                "User-Agent": "Optix-Personal-Catalyst-ETL/1",
            },
            timeout=timeout,
            verify=str(config.ca_bundle) if config.ca_bundle is not None else True,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    async def __aenter__(self) -> "MacroLensEtlClient":
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _validate_limit(limit: int, *, maximum: int) -> int:
        if isinstance(limit, bool) or not 1 <= limit <= maximum:
            raise ValueError(f"page limit must be between 1 and {maximum}")
        return limit

    @staticmethod
    def _validate_after_sequence(after_sequence: int) -> int:
        if (
            isinstance(after_sequence, bool)
            or not isinstance(after_sequence, int)
            or after_sequence < 0
        ):
            raise ValueError("after_sequence must be a non-negative integer")
        return after_sequence

    async def health(self) -> InternalHealth:
        payload = await self._request_json("/health")
        return self._parse_model(InternalHealth, payload)

    async def probe_health(self) -> HealthProbe:
        try:
            health = await self.health()
        except EtlAuthenticationError as exc:
            return HealthProbe(True, False, "unavailable", exc.code)
        except EtlClientError as exc:
            return HealthProbe(
                exc.remote_reached,
                exc.remote_reached,
                "unavailable",
                exc.code,
            )
        return HealthProbe(True, True, health.status, health=health)

    async def news_changes(
        self,
        *,
        cursor: str | None = None,
        updated_after: str | None = None,
        as_of: str | None = None,
        after_sequence: int | None = None,
        limit: int = NEWS_PAGE_LIMIT,
    ) -> NewsChangesPage:
        if cursor and (
            updated_after is not None
            or as_of is not None
            or after_sequence is not None
        ):
            raise ValueError("cursor cannot be combined with explicit checkpoints")
        params: dict[str, Any] = {
            "limit": self._validate_limit(limit, maximum=NEWS_PAGE_LIMIT)
        }
        if cursor:
            if len(cursor) > 2_048:
                raise ValueError("cursor is too long")
            params["cursor"] = cursor
        else:
            if updated_after is not None:
                _require_utc_text(updated_after, field="updated_after")
                params["updated_after"] = updated_after
            if as_of is not None:
                _require_utc_text(as_of, field="as_of")
                params["as_of"] = as_of
            if after_sequence is not None:
                params["after_sequence"] = self._validate_after_sequence(after_sequence)
        payload = await self._request_json("/news/changes", params=params)
        return self._parse_model(NewsChangesPage, payload)

    async def news_item(self, news_id: int, *, as_of: str | None = None) -> NewsDetail:
        if isinstance(news_id, bool) or news_id < 1:
            raise ValueError("news_id must be positive")
        params: dict[str, Any] = {}
        if as_of is not None:
            _require_utc_text(as_of, field="as_of")
            params["as_of"] = as_of
        payload = await self._request_json(f"/news/{news_id}", params=params)
        return self._parse_model(NewsDetail, payload)

    async def calendar(
        self,
        *,
        cursor: str | None = None,
        updated_after: str | None = None,
        as_of: str | None = None,
        after_sequence: int | None = None,
        limit: int = CALENDAR_PAGE_LIMIT,
    ) -> CalendarPage:
        if cursor and (
            updated_after is not None
            or as_of is not None
            or after_sequence is not None
        ):
            raise ValueError("cursor cannot be combined with explicit checkpoints")
        params: dict[str, Any] = {
            "limit": self._validate_limit(limit, maximum=CALENDAR_PAGE_LIMIT)
        }
        if cursor:
            if len(cursor) > 2_048:
                raise ValueError("cursor is too long")
            params["cursor"] = cursor
        else:
            if updated_after is not None:
                _require_utc_text(updated_after, field="updated_after")
                params["updated_after"] = updated_after
            if as_of is not None:
                _require_utc_text(as_of, field="as_of")
                params["as_of"] = as_of
            if after_sequence is not None:
                params["after_sequence"] = self._validate_after_sequence(after_sequence)
        payload = await self._request_json("/calendar", params=params)
        return self._parse_model(CalendarPage, payload)

    @staticmethod
    def _parse_model(model_type: type[_WireModel], payload: dict[str, Any]) -> Any:
        try:
            return model_type.model_validate(payload)
        except ValidationError as exc:
            raise EtlProtocolError("MacroLens returned a response with an invalid shape") from exc

    @staticmethod
    def _json_object(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(
                raw,
                object_pairs_hook=_reject_duplicate,
                parse_constant=_reject_constant,
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise EtlProtocolError("MacroLens returned malformed JSON") from exc
        if not isinstance(value, dict):
            raise EtlProtocolError("MacroLens response root must be an object")
        return value

    async def _bounded_body(self, response: httpx.Response) -> bytes:
        declared = response.headers.get("content-length")
        if declared:
            try:
                if int(declared) > self.config.max_response_bytes:
                    raise EtlResponseTooLarge()
            except ValueError:
                pass
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > self.config.max_response_bytes:
                raise EtlResponseTooLarge()
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _error_code(payload: dict[str, Any]) -> str | None:
        candidate: Any = payload.get("code")
        if not isinstance(candidate, str):
            detail = payload.get("detail")
            candidate = detail.get("code") if isinstance(detail, dict) else None
        return candidate if isinstance(candidate, str) and 0 < len(candidate) <= 100 else None

    @staticmethod
    def _transport_error(exc: httpx.TransportError) -> EtlClientError:
        if isinstance(exc, httpx.TimeoutException):
            reached = isinstance(exc, (httpx.ReadTimeout, httpx.WriteTimeout))
            return EtlClientError(
                "remote_timeout",
                "MacroLens request timed out",
                retryable=True,
                remote_reached=reached,
            )
        current: BaseException | None = exc
        seen: set[int] = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, ssl.SSLError):
                return EtlClientError("tls_error", "MacroLens TLS verification failed")
            if isinstance(current, socket.gaierror):
                return EtlClientError("dns_error", "MacroLens DNS lookup failed", retryable=True)
            current = current.__cause__ or current.__context__
        return EtlClientError("network_error", "MacroLens network request failed", retryable=True)

    @staticmethod
    def _status_error(response: httpx.Response, body: bytes) -> EtlClientError:
        payload: dict[str, Any] = {}
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                payload = parsed
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
        code = MacroLensEtlClient._error_code(payload)
        if response.status_code in {401, 403}:
            return EtlAuthenticationError(response.status_code)
        if code in {"invalid_cursor", "cursor_filter_mismatch", "calendar_snapshot_expired"}:
            return EtlCursorResetRequired(code)
        if response.status_code == 404:
            return EtlClientError(
                code or "not_found",
                "MacroLens resource was not found",
                status_code=response.status_code,
                remote_reached=True,
            )
        retryable = response.status_code in {408, 425, 429} or response.status_code >= 500
        return EtlClientError(
            code or f"remote_http_{response.status_code}",
            "MacroLens returned an unsuccessful status",
            retryable=retryable,
            status_code=response.status_code,
            remote_reached=True,
        )

    async def _request_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        endpoint = INTERNAL_API_PREFIX + path
        last_error: EtlClientError | None = None
        for attempt in range(1, self.config.max_attempts + 1):
            response: httpx.Response | None = None
            try:
                request = self._client.build_request("GET", endpoint, params=params)
                response = await self._client.send(request, stream=True)
                body = await self._bounded_body(response)
                if response.status_code >= 400:
                    error = self._status_error(response, body)
                    if not error.retryable:
                        raise error
                    last_error = error
                else:
                    content_type = response.headers.get("content-type", "").lower()
                    if not content_type.startswith("application/json"):
                        raise EtlProtocolError("MacroLens response was not JSON")
                    return self._json_object(body)
            except httpx.TransportError as exc:
                error = self._transport_error(exc)
                if not error.retryable:
                    raise error from exc
                last_error = error
            finally:
                if response is not None:
                    await response.aclose()
            if attempt < self.config.max_attempts:
                await self._sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
        assert last_error is not None
        raise last_error
