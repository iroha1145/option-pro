"""Economic calendar from the Forex Factory weekly file, ported from News-feed.

``event_id`` keeps the News-feed formula, so the calendar event-group ids that
focus cycles store stay the same across the switch. A fetch writes a new
snapshot only when the event set changed; otherwise it confirms the latest
snapshot. Snapshots older than seven days are deleted in bounded batches,
and the snapshot readers currently use is always kept.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Mapping

import httpx

from .etl_repository import CatalystEtlRepository
from .ingest_models import CALENDAR_PAGE_LIMIT, CalendarPage
from .news_sources import (
    SourceError,
    SourceRequest,
    SourceSpec,
    conditional_headers,
    fetch_url,
    parse_json,
    parse_utc,
    utc_micros,
    utc_seconds,
    validator_cursor,
)


CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
CALENDAR_MAX_BYTES = 1_000_000
CALENDAR_SOURCE = SourceSpec("forexfactory", "Forex Factory", "nfs.faireconomy.media", "calendar")
SNAPSHOT_RETENTION = timedelta(days=7)
PRUNE_LIMIT_PER_REFRESH = 500
COUNTRY_MAP = {
    "USD": "🇺🇸 美国", "EUR": "🇪🇺 欧元区", "GBP": "🇬🇧 英国",
    "JPY": "🇯🇵 日本", "CNY": "🇨🇳 中国", "AUD": "🇦🇺 澳大利亚",
    "CAD": "🇨🇦 加拿大", "CHF": "🇨🇭 瑞士", "NZD": "🇳🇿 新西兰",
}
IMPACT_MAP = {"high": "高", "medium": "中", "low": "低", "holiday": "假日"}
MAJOR_CURRENCIES = {"USD", "EUR", "GBP", "JPY", "CNY", "AUD", "CAD", "CHF"}
_TOKEN_PREFIX = "ff-"


@dataclass(frozen=True)
class CalendarBatch:
    events: tuple[dict[str, Any], ...] = ()
    cursor: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class StoredCalendar:
    written: bool
    events: int
    sequence: int
    token: str


def calendar_event_id(date_text: str, country_code: str, title: str) -> str:
    identity = "\n".join((date_text, country_code.upper(), title.strip()))
    return hashlib.sha256(identity.encode()).hexdigest()


def normalize_events(raw_events: object) -> tuple[dict[str, Any], ...]:
    """Major-currency events that are high or medium impact or already have an actual."""

    if not isinstance(raw_events, list) or not raw_events:
        raise SourceError("invalid_response")
    events: dict[str, dict[str, Any]] = {}
    for raw in raw_events:
        if not isinstance(raw, Mapping):
            continue
        country_code = str(raw.get("country") or "").upper()
        impact = str(raw.get("impact", "") or "").lower()
        date_text = str(raw.get("date") or "").strip()
        title = str(raw.get("title") or "").strip()
        actual = str(raw.get("actual") or "")
        if not date_text or not title:
            continue
        if country_code not in MAJOR_CURRENCIES or not (impact in {"high", "medium"} or actual):
            continue
        if impact not in IMPACT_MAP:
            continue
        scheduled = parse_utc(date_text)
        if scheduled is None:
            raise SourceError("invalid_response")
        event_id = calendar_event_id(date_text, country_code, title)
        scheduled_text = utc_seconds(scheduled)
        events.setdefault(
            event_id,
            {
                "event_id": event_id,
                "country_code": country_code,
                "currency": country_code,
                "country": COUNTRY_MAP.get(country_code, country_code),
                "title": title[:4_000],
                "impact": impact,
                "impact_zh": IMPACT_MAP[impact],
                "scheduled_at": scheduled_text,
                "scheduled_at_utc": scheduled_text,
                "forecast": str(raw.get("forecast") or "") or None,
                "previous": str(raw.get("previous") or "") or None,
                "actual": actual or None,
            },
        )
    return tuple(
        sorted(events.values(), key=lambda event: (event["scheduled_at_utc"], event["event_id"]))
    )


def content_token(events: tuple[dict[str, Any], ...]) -> str:
    """Snapshot token of an event set; fetch and availability times are not part of it."""

    canonical = json.dumps(list(events), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return _TOKEN_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def fetch_forexfactory(client: httpx.AsyncClient, request: SourceRequest) -> CalendarBatch:
    response = await fetch_url(
        client,
        CALENDAR_URL,
        headers={"Accept": "application/json", **conditional_headers(request.cursor)},
        max_bytes=CALENDAR_MAX_BYTES,
    )
    if response.status_code == 304:
        return CalendarBatch(cursor=request.cursor, not_modified=True)
    return CalendarBatch(
        normalize_events(parse_json(response.body)),
        cursor=validator_cursor(response),
    )


def build_calendar_fetchers(client: httpx.AsyncClient) -> dict[str, Any]:
    return {CALENDAR_SOURCE.key: partial(fetch_forexfactory, client)}


def store_calendar(
    repository: CatalystEtlRepository,
    connection: sqlite3.Connection,
    batch: CalendarBatch,
    *,
    fetched_at: datetime,
    now: datetime,
) -> StoredCalendar:
    """Write a changed event set as a new snapshot, or confirm the latest one."""

    state = repository.state("calendar", connection=connection)
    checkpoint = utc_micros(max(now, parse_utc(state.updated_after) or now))
    fetched_text = utc_micros(fetched_at)
    latest = repository.latest_calendar_snapshot(connection)
    token = None if batch.not_modified else content_token(batch.events)
    if latest is not None and (batch.not_modified or latest[1] == token):
        repository.touch_calendar_snapshot(
            connection,
            sequence=latest[0],
            data_through=fetched_text,
            checked_at=checkpoint,
        )
        return StoredCalendar(False, len(batch.events), latest[0], latest[1])
    if token is None:
        raise SourceError("calendar_snapshot_missing")
    sequence = repository.next_calendar_sequence(connection)
    items = [
        {
            **event,
            "is_stale": False,
            "source_fetched_at": fetched_text,
            "available_at": checkpoint,
            "ordinal": ordinal,
        }
        for ordinal, event in enumerate(batch.events, 1)
    ]
    chunks = [
        items[offset : offset + CALENDAR_PAGE_LIMIT]
        for offset in range(0, len(items), CALENDAR_PAGE_LIMIT)
    ] or [[]]
    cursor: str | None = None
    generation = state.generation
    for index, chunk in enumerate(chunks):
        last = index == len(chunks) - 1
        page = CalendarPage.model_validate(
            {
                "items": chunk,
                "has_more": not last,
                "next_cursor": None if last else f"local-calendar:{sequence}:{index + 1}",
                "watermark": {"sequence": sequence, "as_of": checkpoint, "snapshot_token": token},
                "data_through": fetched_text,
                "is_stale": False,
                "next_updated_after": checkpoint if last else None,
                "next_after_sequence": sequence if last else None,
            }
        )
        repository.apply_calendar_page(
            page,
            expected_cursor=cursor,
            expected_generation=generation,
            connection=connection,
        )
        cursor = page.next_cursor
        generation += 1
    return StoredCalendar(True, len(items), sequence, token)


def prune_snapshots(repository: CatalystEtlRepository, *, now: datetime) -> int:
    return repository.prune_calendar_snapshots(
        before=utc_micros(now - SNAPSHOT_RETENTION),
        limit=PRUNE_LIMIT_PER_REFRESH,
    )


def calendar_stale(state_last_success_at: str | None, *, now: datetime) -> bool:
    """A failed fetch degrades the task only once the calendar is a day old."""

    succeeded = parse_utc(state_last_success_at)
    return succeeded is None or now - succeeded > timedelta(hours=24)
