"""Bounded, replaceable TradingView discovery Provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

import httpx

from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.models import (
    DiscoveryProfile,
    DiscoverySnapshot,
    MarketSession,
    ProviderStatus,
)
from app.services.breakouts.normalizer import (
    filter_and_deduplicate,
    normalize_provider_row,
)
from app.services.breakouts.providers.base import (
    ProviderError,
    ProviderPayloadTooLarge,
    ProviderRateLimited,
    ProviderSchemaError,
    ProviderServerError,
    ProviderTimeout,
    ProviderTransportError,
)


TRADINGVIEW_SCAN_URL = "https://scanner.tradingview.com/america/scan"
REGULAR_COLUMNS = (
    "name",
    "exchange",
    "description",
    "type",
    "typespecs",
    "close",
    "change",
    "volume",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "sector",
)
PREMARKET_COLUMNS = (
    "name",
    "exchange",
    "description",
    "type",
    "typespecs",
    "close",
    "premarket_close",
    "premarket_change",
    "premarket_volume",
    "volume",
    "relative_volume_10d_calc",
    "market_cap_basic",
    "sector",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class TradingViewDiscoveryProvider:
    """Use TradingView only for coarse discovery, never confirmation."""

    def __init__(
        self,
        settings: BreakoutSettings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings or get_breakout_settings()
        self._monotonic = monotonic
        self._sleep = sleeper
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(
                self.settings.provider_timeout_seconds,
                connect=self.settings.provider_connect_timeout_seconds,
                read=self.settings.provider_read_timeout_seconds,
                write=self.settings.provider_read_timeout_seconds,
                pool=self.settings.provider_connect_timeout_seconds,
            ),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": "Optix-Pro-Breakout-Radar/1"},
        )
        self._owns_client = client is None
        self._cache: OrderedDict[str, tuple[float, DiscoverySnapshot]] = OrderedDict()
        self._cache_safety: dict[str, str] = {}
        self._inflight: dict[str, asyncio.Future[DiscoverySnapshot]] = {}
        self._inflight_loop: asyncio.AbstractEventLoop | None = None
        self._request_semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None
        self._consecutive_failures = 0
        self._circuit_open_until = 0.0
        self._last_error_code: str | None = None
        self._last_success_at: datetime | None = None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    @property
    def health(self) -> dict[str, Any]:
        now = self._monotonic()
        stale_available = any(
            now - fetched_at <= self.settings.provider_stale_ttl_seconds
            for fetched_at, _snapshot in self._cache.values()
        )
        return {
            "provider": "tradingview",
            "status": "unavailable" if self._circuit_open_until > now else (
                "active" if self._last_success_at else "degraded"
            ),
            "last_success_at": self._last_success_at.isoformat()
            if self._last_success_at
            else None,
            "consecutive_failures": self._consecutive_failures,
            "circuit_open": self._circuit_open_until > now,
            "circuit_open_remaining_seconds": max(0.0, self._circuit_open_until - now),
            "last_error_code": self._last_error_code,
            "stale_snapshot_available": stale_available,
        }

    def _columns(self, session: MarketSession) -> tuple[str, ...]:
        return PREMARKET_COLUMNS if session is MarketSession.PREMARKET else REGULAR_COLUMNS

    def _payload(self, session: MarketSession) -> dict[str, Any]:
        columns = list(self._columns(session))
        if session is MarketSession.PREMARKET:
            filters = [
                {"left": "premarket_close", "operation": "egreater", "right": self.settings.min_price},
                {
                    "left": "premarket_change",
                    "operation": "egreater",
                    "right": self.settings.premarket_min_change_pct,
                },
                {"left": "premarket_volume", "operation": "greater", "right": 0},
            ]
            sort = {"sortBy": "premarket_change", "sortOrder": "desc"}
        else:
            filters = [
                {"left": "close", "operation": "egreater", "right": self.settings.min_price},
                {
                    "left": "change",
                    "operation": "egreater",
                    "right": self.settings.regular_min_change_pct,
                },
                {
                    "left": "relative_volume_10d_calc",
                    "operation": "egreater",
                    "right": self.settings.regular_min_relative_volume,
                },
            ]
            sort = {"sortBy": "change", "sortOrder": "desc"}
        return {
            "filter": filters,
            "options": {"lang": "en"},
            "markets": ["america"],
            "symbols": {"query": {"types": []}, "tickers": []},
            "columns": columns,
            "sort": sort,
            "range": [0, self.settings.provider_result_limit],
        }

    def _cache_key(
        self,
        *,
        session: MarketSession,
        profile: DiscoveryProfile,
        as_of: datetime,
    ) -> str:
        source = {
            "provider": "tradingview",
            "session": session.value,
            "profile": profile.value,
            "source_date": as_of.date().isoformat(),
            "source_time_bucket": int(as_of.timestamp())
            // max(1, min(self.settings.provider_cache_ttl_seconds, 60)),
            "schema": self.settings.provider_schema_version,
            "payload": self._payload(session),
        }
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def _safety_boundary(
        self,
        *,
        session: MarketSession,
        profile: DiscoveryProfile,
    ) -> str:
        """Fingerprint fields that must never share stale or inflight work."""

        source = {
            "provider": "tradingview",
            "session": session.value,
            "profile": profile.value,
            "schema": self.settings.provider_schema_version,
            "payload": self._payload(session),
        }
        encoded = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    async def _read_response(self, response: httpx.Response) -> bytes:
        if response.status_code == 429:
            raw = response.headers.get("retry-after", "0")
            try:
                retry_after = float(raw)
            except (TypeError, ValueError):
                retry_after = 0.0
            raise ProviderRateLimited(retry_after)
        if 500 <= response.status_code <= 599:
            raise ProviderServerError(f"provider_status_{response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(f"provider_status_{response.status_code}")
        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > self.settings.provider_max_response_bytes:
                    raise ProviderPayloadTooLarge("provider_content_length_exceeded")
            except ValueError:
                pass
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > self.settings.provider_max_response_bytes:
                raise ProviderPayloadTooLarge("provider_body_exceeded")
            chunks.append(chunk)
        return b"".join(chunks)

    async def _fetch(self, session: MarketSession) -> bytes:
        running_loop = asyncio.get_running_loop()
        if (
            self._request_semaphore is None
            or self._semaphore_loop is not running_loop
        ):
            self._request_semaphore = asyncio.Semaphore(
                self.settings.provider_max_concurrency
            )
            self._semaphore_loop = running_loop
        try:
            async def request() -> bytes:
                async with self._client.stream(
                    "POST",
                    TRADINGVIEW_SCAN_URL,
                    json=self._payload(session),
                ) as response:
                    return await self._read_response(response)

            async with self._request_semaphore:
                return await asyncio.wait_for(
                    request(),
                    timeout=self.settings.provider_timeout_seconds,
                )
        except (asyncio.TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderTimeout("provider_timeout") from exc
        except httpx.RequestError as exc:
            raise ProviderTransportError("provider_transport_error") from exc

    def _parse(
        self,
        body: bytes,
        *,
        session: MarketSession,
        as_of: datetime,
        cache_key: str,
    ) -> tuple[DiscoverySnapshot, bool]:
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProviderSchemaError("provider_invalid_json") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data", []), list):
            raise ProviderSchemaError("provider_invalid_root")
        expected = list(self._columns(session))
        returned = payload.get("columns")
        warnings: list[str] = []
        cacheable = True
        columns = expected
        if returned is not None:
            if not isinstance(returned, list) or any(not isinstance(item, str) for item in returned):
                raise ProviderSchemaError("provider_invalid_columns")
            if set(returned) != set(expected):
                warnings.append("provider_column_schema_changed")
                cacheable = False
            elif returned != expected:
                warnings.append("provider_column_order_changed")
            columns = returned

        candidates = []
        for item in payload.get("data", [])[: self.settings.provider_result_limit]:
            if not isinstance(item, dict) or not isinstance(item.get("d"), list):
                warnings.append("provider_invalid_row")
                cacheable = False
                continue
            candidate, row_warnings = normalize_provider_row(
                symbol=item.get("s"),
                row=item["d"],
                columns=columns,
                session=session,
                as_of=as_of,
            )
            if row_warnings:
                warnings.extend(row_warnings)
            if candidate is not None:
                candidates.append(candidate)
        normalized, filter_warnings = filter_and_deduplicate(
            candidates,
            settings=self.settings,
            session=session,
        )
        warnings.extend(filter_warnings)
        unique_warnings = list(dict.fromkeys(warnings))[:64]
        status = ProviderStatus.DEGRADED if unique_warnings else ProviderStatus.ACTIVE
        return (
            DiscoverySnapshot(
                provider="tradingview",
                status=status,
                as_of=as_of,
                session=session,
                schema_version=self.settings.provider_schema_version,
                candidate_count=len(normalized),
                warnings=unique_warnings,
                candidates=normalized,
                cache_key=cache_key,
            ),
            cacheable,
        )

    def _stale(
        self,
        cached: tuple[float, DiscoverySnapshot] | None,
        *,
        warning: str,
        requested_as_of: datetime,
    ) -> DiscoverySnapshot | None:
        if cached is None:
            return None
        fetched_at, snapshot = cached
        if snapshot.as_of > requested_as_of:
            return None
        age = self._monotonic() - fetched_at
        if age > self.settings.provider_stale_ttl_seconds:
            return None
        return snapshot.model_copy(
            update={
                "status": ProviderStatus.STALE,
                "warnings": list(dict.fromkeys([*snapshot.warnings, warning]))[:64],
            }
        )

    def _latest_stale_candidate(
        self,
        *,
        session: MarketSession,
        requested_as_of: datetime,
        safety_boundary: str,
    ) -> tuple[float, DiscoverySnapshot] | None:
        """Return the newest compatible snapshot across cache time buckets.

        The exact cache key intentionally includes a short source-time bucket so
        successful reads stay point-in-time.  On a refresh failure, however, a
        still-bounded snapshot from the immediately preceding bucket remains a
        valid stale fallback.  Never cross sessions, schemas, the requested
        cutoff, or the configured stale TTL.
        """

        now = self._monotonic()
        eligible = [
            cached
            for key, cached in self._cache.items()
            if (
                self._cache_safety.get(key) == safety_boundary
                and
                cached[1].session is session
                and cached[1].schema_version == self.settings.provider_schema_version
                and cached[1].as_of <= requested_as_of
                and now - cached[0] <= self.settings.provider_stale_ttl_seconds
            )
        ]
        if not eligible:
            return None
        return max(eligible, key=lambda item: (item[1].as_of, item[0]))

    def _remember(
        self,
        key: str,
        snapshot: DiscoverySnapshot,
        *,
        safety_boundary: str,
    ) -> None:
        existing = self._cache.get(key)
        if existing is not None and existing[1].as_of > snapshot.as_of:
            return
        self._cache[key] = (self._monotonic(), snapshot)
        self._cache_safety[key] = safety_boundary
        self._cache.move_to_end(key)
        while len(self._cache) > 8:
            expired_key, _ = self._cache.popitem(last=False)
            self._cache_safety.pop(expired_key, None)

    def _fresh_cached(
        self,
        key: str,
        *,
        requested_as_of: datetime,
    ) -> DiscoverySnapshot | None:
        cached = self._cache.get(key)
        if (
            cached is not None
            and cached[1].as_of <= requested_as_of
            and self._monotonic() - cached[0]
            <= self.settings.provider_cache_ttl_seconds
        ):
            return cached[1]
        return None

    async def _scan_uncached(
        self,
        *,
        key: str,
        safety_boundary: str,
        session: MarketSession,
        as_of: datetime,
    ) -> DiscoverySnapshot:
        """Leader-only Provider work; followers await its shielded Future."""

        cached = self._fresh_cached(key, requested_as_of=as_of)
        if cached is not None:
            return cached
        stale_candidate = self._latest_stale_candidate(
            session=session,
            requested_as_of=as_of,
            safety_boundary=safety_boundary,
        )
        now = self._monotonic()
        if self._circuit_open_until > now:
            stale = self._stale(
                stale_candidate,
                warning="provider_circuit_open",
                requested_as_of=as_of,
            )
            if stale is not None:
                return stale
            return DiscoverySnapshot(
                provider="tradingview",
                status=ProviderStatus.UNAVAILABLE,
                as_of=as_of,
                session=session,
                schema_version=self.settings.provider_schema_version,
                candidate_count=0,
                warnings=["provider_circuit_open"],
                candidates=[],
                cache_key=key,
            )

        last_error: ProviderError | None = None
        for attempt in range(self.settings.provider_retry_attempts):
            try:
                body = await self._fetch(session)
                snapshot, cacheable = self._parse(
                    body,
                    session=session,
                    as_of=as_of,
                    cache_key=key,
                )
                self._consecutive_failures = 0
                self._circuit_open_until = 0.0
                self._last_error_code = None
                self._last_success_at = _utc_now()
                if cacheable:
                    self._remember(
                        key,
                        snapshot,
                        safety_boundary=safety_boundary,
                    )
                return snapshot
            except ProviderError as exc:
                last_error = exc
                if not exc.retryable or attempt + 1 >= self.settings.provider_retry_attempts:
                    break
                exponential_backoff = 0.25 * (2**attempt)
                requested_delay = (
                    max(exc.retry_after, exponential_backoff)
                    if isinstance(exc, ProviderRateLimited)
                    else exponential_backoff
                )
                delay = min(
                    requested_delay,
                    self.settings.provider_retry_after_cap_seconds,
                )
                await self._sleep(delay)

        self._consecutive_failures += 1
        self._last_error_code = (
            last_error.code if last_error else "provider_unknown_error"
        )
        if self._consecutive_failures >= self.settings.provider_failure_threshold:
            self._circuit_open_until = (
                self._monotonic() + self.settings.provider_circuit_open_seconds
            )
        stale = self._stale(
            stale_candidate,
            warning=self._last_error_code,
            requested_as_of=as_of,
        )
        if stale is not None:
            return stale
        return DiscoverySnapshot(
            provider="tradingview",
            status=ProviderStatus.UNAVAILABLE,
            as_of=as_of,
            session=session,
            schema_version=self.settings.provider_schema_version,
            candidate_count=0,
            warnings=[self._last_error_code],
            candidates=[],
            cache_key=key,
        )

    async def scan(
        self,
        *,
        session: MarketSession,
        as_of: datetime,
        profile: DiscoveryProfile,
    ) -> DiscoverySnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        if session not in (MarketSession.REGULAR, MarketSession.PREMARKET):
            return DiscoverySnapshot(
                provider="tradingview",
                status=ProviderStatus.UNAVAILABLE,
                as_of=as_of,
                session=session,
                schema_version=self.settings.provider_schema_version,
                candidate_count=0,
                warnings=["session_not_supported"],
                candidates=[],
            )
        expected_profile = (
            DiscoveryProfile.PREMARKET_GAPPERS
            if session is MarketSession.PREMARKET
            else DiscoveryProfile.REGULAR_MOVERS
        )
        if profile is not expected_profile:
            raise ValueError("discovery profile does not match market session")

        key = self._cache_key(session=session, profile=profile, as_of=as_of)
        cached = self._fresh_cached(key, requested_as_of=as_of)
        if cached is not None:
            return cached

        safety_boundary = self._safety_boundary(
            session=session,
            profile=profile,
        )
        running_loop = asyncio.get_running_loop()
        if self._inflight_loop is not running_loop:
            self._inflight = {}
            self._inflight_loop = running_loop
        inflight_key = "|".join(
            [
                key,
                safety_boundary,
                as_of.astimezone(timezone.utc).isoformat(timespec="microseconds"),
            ]
        )
        task = self._inflight.get(inflight_key)
        if task is None or task.done():
            task = running_loop.create_task(
                self._scan_uncached(
                    key=key,
                    safety_boundary=safety_boundary,
                    session=session,
                    as_of=as_of,
                )
            )
            self._inflight[inflight_key] = task

            def cleanup(done: asyncio.Future[DiscoverySnapshot]) -> None:
                if self._inflight.get(inflight_key) is done:
                    self._inflight.pop(inflight_key, None)
                if not done.cancelled():
                    done.exception()

            task.add_done_callback(cleanup)
        # A follower timeout/cancellation must not cancel the leader or the
        # request shared by the remaining followers.
        return await asyncio.shield(task)
