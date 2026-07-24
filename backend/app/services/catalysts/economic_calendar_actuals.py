"""Best-effort actual-value enrichment for recent economic-calendar events.

MacroLens remains the calendar-of-record.  TradingView is queried only for
already-released events whose upstream ``actual`` value is still empty.
Matches are conservative: currency and release time must agree, then the
translated title or published forecast/previous values must identify one
unambiguous source row.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
import time as monotonic_time
from typing import Any, Mapping, Sequence

import httpx

from .local_intelligence import _public_calendar_title


_TRADINGVIEW_CALENDAR_URL = "https://economic-calendar.tradingview.com/events"
_PROVIDER = "TradingView Economic Calendar"
_CACHE_TTL_SECONDS = 300.0
_FAILURE_CACHE_TTL_SECONDS = 60.0
_FETCH_TIMEOUT_SECONDS = 5.0
_MAX_SOURCE_ROWS = 8_000
_MAX_ENRICH_DAYS = 4
_MIN_CANDIDATE_SCORE = 6
_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}
_failure_cache: dict[tuple[str, str], float] = {}
_cache_locks: dict[tuple[str, str], asyncio.Lock] = {}
_VALUE_RE = re.compile(r"^([-+]?\d+(?:\.\d+)?)([KMBT%]?)$", re.IGNORECASE)


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _plain_decimal(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    if not number.is_finite():
        return None
    text = format(number.normalize(), "f")
    return "0" if text in {"-0", ""} else text


def _has_value(value: Any) -> bool:
    return value is not None and bool(str(value).strip())


def _format_source_value(row: Mapping[str, Any], field: str) -> str | None:
    raw = str(row.get(field) if row.get(field) is not None else "").strip()
    raw = (
        raw.replace(",", "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("£", "")
        .replace("€", "")
        .replace("$", "")
        .replace("¥", "")
        .upper()
    )
    match = _VALUE_RE.fullmatch(raw)
    if match is None:
        return None
    text = _plain_decimal(match.group(1))
    if text is None:
        return None
    suffix = match.group(2).upper()
    if suffix:
        text = f"{text}{suffix}"
    scale = str(row.get("scale") or "").strip().upper()
    if (
        scale in {"K", "M", "B", "T"}
        and suffix not in {"K", "M", "B", "T"}
    ):
        text = f"{text}{scale}"
    unit = str(row.get("unit") or "").strip()
    if unit == "%" and suffix != "%":
        text = f"{text}%"
    return text


def _value_signature(value: Any) -> tuple[Decimal, str] | None:
    text = str(value or "").strip().upper()
    if not text:
        return None
    text = (
        text.replace(",", "")
        .replace("−", "-")
        .replace("–", "-")
        .replace("£", "")
        .replace("€", "")
        .replace("$", "")
        .replace("¥", "")
    )
    match = _VALUE_RE.fullmatch(text)
    if match is None:
        return None
    try:
        number = Decimal(match.group(1)).normalize()
    except InvalidOperation:
        return None
    return number, match.group(2).upper()


def _title_signature(value: Any) -> str:
    public = _public_calendar_title(value)
    return re.sub(r"[\W_]+", "", public.casefold(), flags=re.UNICODE)


def _candidate_score(
    event: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    as_of: datetime,
) -> int | None:
    event_time = _parse_time(event.get("scheduled_at_utc") or event.get("scheduled_at"))
    source_time = _parse_time(candidate.get("date"))
    if event_time is None or source_time is None or source_time > as_of:
        return None
    if abs((event_time - source_time).total_seconds()) > 120:
        return None
    event_currency = str(event.get("currency") or event.get("country_code") or "").upper()
    source_currency = str(candidate.get("currency") or "").upper()
    if not event_currency or source_currency != event_currency:
        return None
    if _format_source_value(candidate, "actual") is None:
        return None

    score = 0
    event_title = _title_signature(event.get("title"))
    source_titles = {
        _title_signature(candidate.get("title")),
        _title_signature(candidate.get("indicator")),
    }
    source_titles.discard("")
    if event_title and event_title in source_titles:
        score += 8

    for field, weight in (("forecast", 4), ("previous", 2)):
        expected = _value_signature(event.get(field))
        observed = _value_signature(_format_source_value(candidate, field))
        if expected is not None and observed is not None and expected == observed:
            score += weight
    return score


def merge_recent_actuals(
    payload: Mapping[str, Any],
    source_rows: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
) -> tuple[dict[str, Any], int, int]:
    """Return a copied payload with unambiguous recent actual values filled."""

    output = deepcopy(dict(payload))
    raw_items = output.get("items")
    if not isinstance(raw_items, list):
        return output, 0, 0
    rows = [row for row in source_rows if isinstance(row, Mapping)]
    attempted = 0
    filled = 0
    for value in raw_items:
        if not isinstance(value, dict):
            continue
        if _has_value(value.get("actual")):
            continue
        scheduled = _parse_time(value.get("scheduled_at_utc") or value.get("scheduled_at"))
        recent_start = as_of.date() - timedelta(days=_MAX_ENRICH_DAYS - 1)
        if (
            scheduled is None
            or scheduled > as_of
            or scheduled.date() < recent_start
        ):
            continue
        attempted += 1
        ranked: list[tuple[int, Mapping[str, Any]]] = []
        for candidate in rows:
            score = _candidate_score(value, candidate, as_of=as_of)
            if score is not None:
                ranked.append((score, candidate))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked or ranked[0][0] < _MIN_CANDIDATE_SCORE:
            continue
        if len(ranked) > 1 and ranked[0][0] == ranked[1][0]:
            continue
        actual = _format_source_value(ranked[0][1], "actual")
        if actual is None:
            continue
        value["actual"] = actual
        value["release_status"] = "released"
        value["actual_source"] = _PROVIDER
        filled += 1
    return output, filled, attempted


async def _fetch_source_rows(date_from: date, date_to: date) -> list[dict[str, Any]]:
    key = (date_from.isoformat(), date_to.isoformat())
    now = monotonic_time.monotonic()
    cached = _cache.get(key)
    if cached is not None and cached[0] > now:
        return deepcopy(cached[1])
    if _failure_cache.get(key, 0.0) > now:
        raise ValueError("economic calendar fallback is cooling down")
    lock = _cache_locks.setdefault(key, asyncio.Lock())
    async with lock:
        cached = _cache.get(key)
        now = monotonic_time.monotonic()
        if cached is not None and cached[0] > now:
            return deepcopy(cached[1])
        if _failure_cache.get(key, 0.0) > now:
            raise ValueError("economic calendar fallback is cooling down")
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(_FETCH_TIMEOUT_SECONDS),
                headers={
                    "Origin": "https://www.tradingview.com",
                    "Referer": "https://www.tradingview.com/",
                    "User-Agent": "Optix-Pro/1.0 economic-calendar",
                    "Accept": "application/json",
                },
            ) as client:
                response = await client.get(
                    _TRADINGVIEW_CALENDAR_URL,
                    params={
                        "from": datetime.combine(
                            date_from,
                            time.min,
                            tzinfo=timezone.utc,
                        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                        "to": datetime.combine(
                            date_to,
                            time.max,
                            tzinfo=timezone.utc,
                        ).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    },
                )
                response.raise_for_status()
                body = response.json()
            raw_rows = body.get("result") if isinstance(body, dict) else None
            if not isinstance(raw_rows, list):
                raise ValueError(
                    "economic calendar fallback returned an invalid payload"
                )
        except (httpx.HTTPError, TypeError, ValueError):
            _failure_cache[key] = (
                monotonic_time.monotonic() + _FAILURE_CACHE_TTL_SECONDS
            )
            raise
        rows = [
            dict(row)
            for row in raw_rows[:_MAX_SOURCE_ROWS]
            if isinstance(row, Mapping)
        ]
        _failure_cache.pop(key, None)
        _cache[key] = (monotonic_time.monotonic() + _CACHE_TTL_SECONDS, rows)
        return deepcopy(rows)


async def enrich_recent_actuals(
    payload: Mapping[str, Any],
    *,
    date_from: date,
    date_to: date,
    as_of: datetime,
) -> dict[str, Any]:
    """Fill recent missing actuals without making the calendar route fragile."""

    observed = as_of.astimezone(timezone.utc)
    recent_start = observed.date() - timedelta(days=_MAX_ENRICH_DAYS - 1)
    start_date = max(date_from, recent_start)
    end_date = min(date_to, observed.date())
    output = deepcopy(dict(payload))
    if end_date < start_date:
        return output
    missing_past = [
        item
        for item in output.get("items", [])
        if isinstance(item, Mapping)
        and not _has_value(item.get("actual"))
        and (
            scheduled := _parse_time(
                item.get("scheduled_at_utc") or item.get("scheduled_at")
            )
        )
        is not None
        and start_date <= scheduled.date() <= end_date
        and scheduled <= observed
    ]
    if not missing_past:
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "not_needed",
            "attempted": 0,
            "filled": 0,
        }
        return output
    try:
        source_rows = await _fetch_source_rows(start_date, end_date)
        output, filled, attempted = merge_recent_actuals(
            output,
            source_rows,
            as_of=observed,
        )
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "active" if filled else "no_match",
            "attempted": attempted,
            "filled": filled,
        }
    except (httpx.HTTPError, TypeError, ValueError):
        output["actual_fallback"] = {
            "provider": _PROVIDER,
            "status": "unavailable",
            "attempted": len(missing_past),
            "filled": 0,
        }
    return output
