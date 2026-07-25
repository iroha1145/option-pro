"""Minimal FRED client for Optix Macro Conditions.

Deliberate constraints:

* the origin is fixed to the official API — there is no ``FRED_BASE_URL``
  setting, no proxy switch and no provider abstraction to configure;
* one request at a time, bounded response bodies, bounded exponential backoff,
  ``Retry-After`` honoured on 429, and no retry for non-throttling 4xx or for
  malformed payloads;
* logs record a series id and a safe error code only — never the API key, never
  the request URL, never an upstream body;
* tests replace the network by injecting an ``httpx`` transport.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

import httpx

from .models import MacroError, SeriesFetch, SeriesMetadata, SeriesObservation, finite
from .registry import (
    SeriesSpec,
    UnitsMismatch,
    frequency_at_most,
    scale_to_canonical,
)


logger = logging.getLogger("optix.macro.fred")

#: Official origin. Fixed on purpose; see the module docstring.
FRED_ORIGIN = "https://api.stlouisfed.org"
_SERIES_PATH = "/fred/series"
_OBSERVATIONS_PATH = "/fred/series/observations"

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ATTEMPTS = 3
CONNECT_TIMEOUT_SECONDS = 5.0
READ_TIMEOUT_SECONDS = 20.0
TOTAL_TIMEOUT_SECONDS = 30.0
_BASE_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 8.0
#: FRED caps ``limit`` at 100000; 8 years of daily data is far below that.
_OBSERVATION_LIMIT = 100_000


def _redacted(message: str, series_id: str, code: str) -> None:
    logger.warning("%s series=%s code=%s", message, series_id, code)


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        seconds = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, _MAX_BACKOFF_SECONDS)


class FredClient:
    """One-series-at-a-time reader for the official FRED API."""

    def __init__(
        self,
        api_key: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        key = str(api_key or "").strip()
        if not key:
            raise MacroError("fred_api_key_missing", "FRED API key is not configured")
        self._api_key = key
        self._sleep = sleep or time.sleep
        self._client = httpx.Client(
            base_url=FRED_ORIGIN,
            # No environment proxies and no redirects: the origin must stay the
            # official API even if the host has proxy variables exported.
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(
                TOTAL_TIMEOUT_SECONDS,
                connect=CONNECT_TIMEOUT_SECONDS,
                read=READ_TIMEOUT_SECONDS,
            ),
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "FredClient":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- transport ---------------------------------------------------------

    def _request(self, path: str, params: Mapping[str, str], *, series_id: str) -> dict:
        query = {**params, "api_key": self._api_key, "file_type": "json"}
        last_error: MacroError | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self._client.get(path, params=query)
            except httpx.HTTPError:
                last_error = MacroError("fred_unavailable", "FRED request failed")
                _redacted("fred request failed", series_id, "fred_unavailable")
            else:
                outcome = self._interpret(response, series_id=series_id)
                if isinstance(outcome, dict):
                    return outcome
                last_error, retry_after = outcome
                if retry_after is None:
                    raise last_error
                if attempt < MAX_ATTEMPTS:
                    self._sleep(retry_after)
                continue
            if attempt < MAX_ATTEMPTS:
                self._sleep(min(_BASE_BACKOFF_SECONDS * 2 ** (attempt - 1), _MAX_BACKOFF_SECONDS))
        raise last_error or MacroError("fred_unavailable", "FRED request failed")

    def _interpret(
        self,
        response: httpx.Response,
        *,
        series_id: str,
    ) -> dict | tuple[MacroError, Optional[float]]:
        """Return the payload, or ``(error, retry_delay_or_None)``."""

        status = int(response.status_code)
        if 300 <= status < 400:
            # follow_redirects is off; a redirect would move us off the official
            # origin, so treat it as unavailable and do not retry.
            _redacted("fred redirect rejected", series_id, "fred_unavailable")
            return MacroError("fred_unavailable", "FRED redirected"), None
        if status == 429:
            delay = _parse_retry_after(response.headers.get("Retry-After"))
            _redacted("fred rate limited", series_id, "fred_rate_limited")
            return (
                MacroError("fred_rate_limited", "FRED rate limited the request"),
                delay if delay is not None else _BASE_BACKOFF_SECONDS,
            )
        if 400 <= status < 500:
            _redacted("fred rejected request", series_id, "fred_unavailable")
            return MacroError("fred_unavailable", "FRED rejected the request"), None
        if status >= 500:
            _redacted("fred server error", series_id, "fred_unavailable")
            return (
                MacroError("fred_unavailable", "FRED is unavailable"),
                _BASE_BACKOFF_SECONDS,
            )

        content_type = str(response.headers.get("Content-Type", "")).split(";")[0].strip().lower()
        if content_type != "application/json":
            _redacted("fred non-json response", series_id, "fred_schema_mismatch")
            return MacroError("fred_schema_mismatch", "FRED returned non-JSON"), None

        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAX_RESPONSE_BYTES:
                    _redacted("fred oversize response", series_id, "fred_response_too_large")
                    return (
                        MacroError("fred_response_too_large", "FRED response too large"),
                        None,
                    )
            except (TypeError, ValueError):
                pass
        body = response.content
        if len(body) > MAX_RESPONSE_BYTES:
            _redacted("fred oversize response", series_id, "fred_response_too_large")
            return MacroError("fred_response_too_large", "FRED response too large"), None
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _redacted("fred malformed json", series_id, "fred_schema_mismatch")
            return MacroError("fred_schema_mismatch", "FRED returned invalid JSON"), None
        if not isinstance(payload, dict):
            _redacted("fred unexpected json shape", series_id, "fred_schema_mismatch")
            return MacroError("fred_schema_mismatch", "FRED returned an unexpected shape"), None
        return payload

    # -- metadata ----------------------------------------------------------

    def metadata(self, spec: SeriesSpec) -> SeriesMetadata:
        payload = self._request(
            _SERIES_PATH,
            {"series_id": spec.series_id},
            series_id=spec.series_id,
        )
        entries = payload.get("seriess")
        if not isinstance(entries, list) or len(entries) != 1:
            _redacted("fred metadata shape", spec.series_id, "fred_schema_mismatch")
            raise MacroError("fred_schema_mismatch", "FRED metadata shape unexpected")
        entry = entries[0]
        if not isinstance(entry, dict):
            raise MacroError("fred_schema_mismatch", "FRED metadata shape unexpected")
        returned_id = str(entry.get("id") or "")
        if returned_id != spec.series_id:
            _redacted("fred metadata id mismatch", spec.series_id, "fred_schema_mismatch")
            raise MacroError("fred_schema_mismatch", "FRED returned another series")
        units = str(entry.get("units") or "")
        frequency_short = str(entry.get("frequency_short") or "")
        if not frequency_at_most(frequency_short, spec.expected_frequency):
            _redacted("fred frequency drift", spec.series_id, "fred_schema_mismatch")
            raise MacroError("fred_schema_mismatch", "FRED frequency is unexpected")
        try:
            scale = scale_to_canonical(units, spec.expected_units_family)
        except UnitsMismatch as exc:
            _redacted("fred units drift", spec.series_id, "fred_units_mismatch")
            raise MacroError("fred_units_mismatch", "FRED units are unexpected") from exc
        if spec.scale is not None and scale != spec.scale:
            _redacted("fred units drift", spec.series_id, "fred_units_mismatch")
            raise MacroError("fred_units_mismatch", "FRED units are unexpected")
        return SeriesMetadata(
            series_id=spec.series_id,
            units=units,
            frequency_short=frequency_short,
            canonical_unit=spec.canonical_unit,
            scale_to_canonical=scale,
            source_last_updated=_optional_text(entry.get("last_updated")),
            realtime_start=_optional_text(entry.get("realtime_start")),
            realtime_end=_optional_text(entry.get("realtime_end")),
        )

    # -- observations ------------------------------------------------------

    def observations(
        self,
        spec: SeriesSpec,
        metadata: SeriesMetadata,
        *,
        start: date,
        end: date,
    ) -> tuple[SeriesObservation, ...]:
        payload = self._request(
            _OBSERVATIONS_PATH,
            {
                "series_id": spec.series_id,
                "observation_start": start.isoformat(),
                "observation_end": end.isoformat(),
                "sort_order": "asc",
                "limit": str(_OBSERVATION_LIMIT),
            },
            series_id=spec.series_id,
        )
        rows = payload.get("observations")
        if not isinstance(rows, list):
            _redacted("fred observation shape", spec.series_id, "fred_schema_mismatch")
            raise MacroError("fred_schema_mismatch", "FRED observations shape unexpected")
        # A duplicate observation_date is resolved deterministically: the last
        # row in ascending order wins, and a real value always beats a missing
        # one for the same date.
        collected: dict[date, Optional[float]] = {}
        for row in rows:
            if not isinstance(row, dict):
                raise MacroError("fred_schema_mismatch", "FRED observation row unexpected")
            observation_date = _parse_date(row.get("date"))
            if observation_date is None:
                raise MacroError("fred_schema_mismatch", "FRED observation date unexpected")
            raw = row.get("value")
            value = _parse_observation_value(raw, metadata.scale_to_canonical)
            if observation_date in collected and value is None:
                continue
            collected[observation_date] = value
        return tuple(
            SeriesObservation(spec.series_id, key, collected[key])
            for key in sorted(collected)
        )

    def fetch(
        self,
        spec: SeriesSpec,
        *,
        start: date,
        end: date,
        metadata: SeriesMetadata | None = None,
    ) -> SeriesFetch:
        resolved = metadata or self.metadata(spec)
        return SeriesFetch(
            metadata=resolved,
            observations=self.observations(spec, resolved, start=start, end=end),
        )

    def fetch_many(
        self,
        specs: Sequence[SeriesSpec],
        *,
        start: date,
        end: date,
        metadata_cache: Mapping[str, SeriesMetadata] | None = None,
    ) -> tuple[dict[str, SeriesFetch], dict[str, str]]:
        """Fetch sequentially. Returns ``(fetched, failures)``.

        One failing series never aborts the others: the caller keeps the rows it
        got and recomputes whatever remains computable.
        """

        fetched: dict[str, SeriesFetch] = {}
        failures: dict[str, str] = {}
        for spec in specs:
            if not spec.enabled:
                continue
            try:
                fetched[spec.series_id] = self.fetch(
                    spec,
                    start=start,
                    end=end,
                    metadata=(metadata_cache or {}).get(spec.series_id),
                )
            except MacroError as exc:
                failures[spec.series_id] = exc.code
        return fetched, failures


def _optional_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text[:64] if text else None


def _parse_date(value: Any) -> Optional[date]:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_observation_value(raw: Any, scale: float) -> Optional[float]:
    """FRED encodes a missing observation as ``"."``.

    Anything non-finite is treated as missing rather than stored, so NaN and
    infinity can never enter a percentile window.
    """

    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text or text == ".":
        return None
    value = finite(text)
    if value is None:
        return None
    scaled = finite(value * scale)
    return scaled


def build_client(
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] | None = None,
) -> FredClient:
    return FredClient(api_key, transport=transport, sleep=sleep)


__all__ = [
    "CONNECT_TIMEOUT_SECONDS",
    "FRED_ORIGIN",
    "MAX_ATTEMPTS",
    "MAX_RESPONSE_BYTES",
    "READ_TIMEOUT_SECONDS",
    "TOTAL_TIMEOUT_SECONDS",
    "FredClient",
    "build_client",
]
