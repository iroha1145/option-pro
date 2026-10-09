"""Validated news-change and calendar pages for the local catalyst store.

``CatalystEtlRepository`` accepts only these shapes. The local collector builds
them from source items; the legacy MacroLens client parses the same shapes
from its HTTP responses.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


NEWS_PAGE_LIMIT = 500
CALENDAR_PAGE_LIMIT = 50


_FRACTION_RE = re.compile(r"\.(\d+)")


def _require_utc_text(value: str, *, field: str) -> str:
    """Validate an ISO-8601 timestamp and return it as UTC ``Z`` text.

    Downstream code compares these strings lexicographically (range filters
    and ordering on ``available_at`` in local_intelligence, watermark equality
    in etl_repository), so an upstream ``+08:00`` offset must not survive.
    A ``Z`` value is returned byte-for-byte: replay detection hashes the
    validated model, so stored rows and their hashes must not move. Offset
    values keep their sub-second width (none, 3 or 6 digits).
    """

    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must include a timezone")
    if value.endswith("Z"):
        return value
    utc = parsed.astimezone(timezone.utc)
    base = utc.strftime("%Y-%m-%dT%H:%M:%S")
    fraction_match = _FRACTION_RE.search(value)
    if fraction_match is None:
        return f"{base}Z"
    digits = len(fraction_match.group(1))
    fraction = f"{utc.microsecond:06d}"[:digits].ljust(digits, "0")
    return f"{base}.{fraction}Z"


class _WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SourceObservation(_WireModel):
    source: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=20_000)
    url: str = Field(min_length=1, max_length=20_000)
    source_tickers: list[str] = Field(default_factory=list, max_length=500)
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def validate_observed_at(cls, value: str) -> str:
        return _require_utc_text(value, field="source_observations.observed_at")


class NewsWatermark(_WireModel):
    sequence: int = Field(ge=0)
    as_of: str

    @field_validator("as_of")
    @classmethod
    def validate_as_of(cls, value: str) -> str:
        return _require_utc_text(value, field="watermark.as_of")


class CalendarWatermark(NewsWatermark):
    snapshot_token: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_token(self) -> "CalendarWatermark":
        if self.sequence == 0 and self.snapshot_token is not None:
            raise ValueError("an empty calendar watermark cannot have a snapshot token")
        if self.sequence > 0 and not self.snapshot_token:
            raise ValueError("a calendar watermark requires a snapshot token")
        return self


class RawNewsItem(_WireModel):
    # Unknown source-native fields are retained inside raw_json by the local store.
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)

    id: int = Field(ge=1)
    source: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=20_000)
    summary: str | None = Field(default=None, max_length=200_000)
    url: str = Field(min_length=1, max_length=20_000)
    image_url: str | None = Field(default=None, max_length=20_000)
    published_at: str | None = None
    fetched_at: str
    updated_at: str
    source_tickers: list[str] = Field(default_factory=list, max_length=500)
    sources: list[str] = Field(default_factory=list, max_length=500)
    source_count: int | None = Field(default=None, ge=1, le=500)
    source_observations: list[SourceObservation] = Field(default_factory=list, max_length=500)
    content_hash: str = Field(min_length=1, max_length=256)

    @field_validator("published_at", "fetched_at", "updated_at")
    @classmethod
    def validate_timestamps(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_utc_text(value, field=info.field_name)

    @field_validator("source_tickers")
    @classmethod
    def validate_tickers(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 100 for value in values):
            raise ValueError("source_tickers contains an invalid value")
        return values

    @field_validator("sources")
    @classmethod
    def validate_sources(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("sources contains an invalid value")
        if len(values) != len({value.casefold() for value in values}):
            raise ValueError("sources contains a duplicate")
        return values

    @model_validator(mode="after")
    def validate_source_count(self) -> "RawNewsItem":
        if bool(self.sources) != (self.source_count is not None):
            raise ValueError("sources and source_count must be supplied together")
        if self.source_count is not None and self.source_count != len(self.sources):
            raise ValueError("source_count does not match sources")
        source_names = {value.casefold() for value in self.sources}
        if self.source_observations and not source_names:
            raise ValueError("source observations require a source set")
        if any(
            observation.source.casefold() not in source_names
            for observation in self.source_observations
        ):
            raise ValueError("source observation is not represented in sources")
        return self


class NewsChange(_WireModel):
    sequence: int = Field(ge=1)
    operation: Literal["upsert", "delete"]
    changed_at: str
    source_updated_at: str
    available_at: str
    news: RawNewsItem | None
    news_id: int = Field(ge=1)

    @field_validator("changed_at", "source_updated_at", "available_at")
    @classmethod
    def validate_timestamps(cls, value: str, info: Any) -> str:
        return _require_utc_text(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_payload(self) -> "NewsChange":
        if self.operation == "upsert":
            if self.news is None or self.news.id != self.news_id:
                raise ValueError("an upsert must include its matching news item")
        elif self.news is not None:
            raise ValueError("a delete must not include a news item")
        return self


class NewsChangesPage(_WireModel):
    items: list[NewsChange] = Field(max_length=NEWS_PAGE_LIMIT)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=2_048)
    watermark: NewsWatermark
    next_updated_after: str | None
    next_after_sequence: int | None = Field(default=None, ge=0)

    @field_validator("next_updated_after")
    @classmethod
    def validate_next_updated_after(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _require_utc_text(value, field="next_updated_after")

    @model_validator(mode="after")
    def validate_page(self) -> "NewsChangesPage":
        sequences = [item.sequence for item in self.items]
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("news changes must have unique ascending sequences")
        if any(sequence > self.watermark.sequence for sequence in sequences):
            raise ValueError("news change exceeds the frozen watermark")
        if self.has_more:
            if (
                not self.items
                or not self.next_cursor
                or self.next_updated_after is not None
                or self.next_after_sequence is not None
            ):
                raise ValueError("an incomplete page requires items and a cursor")
        elif (
            self.next_cursor is not None
            or self.next_updated_after is None
            or self.next_after_sequence is None
        ):
            raise ValueError(
                "a complete page requires time and sequence checkpoints and no cursor"
            )
        elif (
            self.next_after_sequence != self.watermark.sequence
            or self.next_updated_after != self.watermark.as_of
        ):
            raise ValueError("a complete page checkpoint must match its watermark")
        return self


class CalendarEvent(_WireModel):
    model_config = ConfigDict(extra="allow", strict=True, allow_inf_nan=False)

    event_id: str = Field(min_length=1, max_length=256)
    country_code: str = Field(min_length=1, max_length=20)
    country: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=20_000)
    impact: str = Field(min_length=1, max_length=100)
    impact_zh: str = Field(min_length=1, max_length=100)
    scheduled_at: str
    scheduled_at_utc: str
    forecast: str | None = Field(default=None, max_length=10_000)
    previous: str | None = Field(default=None, max_length=10_000)
    actual: str | None = Field(default=None, max_length=10_000)
    is_stale: bool
    source_fetched_at: str
    available_at: str
    ordinal: int = Field(ge=1)

    @field_validator("scheduled_at", "scheduled_at_utc", "source_fetched_at", "available_at")
    @classmethod
    def validate_timestamps(cls, value: str, info: Any) -> str:
        return _require_utc_text(value, field=info.field_name)


class CalendarPage(_WireModel):
    items: list[CalendarEvent] = Field(max_length=CALENDAR_PAGE_LIMIT)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=2_048)
    watermark: CalendarWatermark
    data_through: str | None
    is_stale: bool
    next_updated_after: str | None
    next_after_sequence: int | None = Field(default=None, ge=0)

    @field_validator("data_through", "next_updated_after")
    @classmethod
    def validate_optional_timestamps(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _require_utc_text(value, field=info.field_name)

    @model_validator(mode="after")
    def validate_page(self) -> "CalendarPage":
        ordinals = [item.ordinal for item in self.items]
        if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
            raise ValueError("calendar items must have unique ascending ordinals")
        if self.watermark.sequence == 0 and self.items:
            raise ValueError("calendar items require a snapshot")
        if self.has_more:
            if (
                not self.items
                or not self.next_cursor
                or self.next_updated_after is not None
                or self.next_after_sequence is not None
            ):
                raise ValueError("an incomplete page requires items and a cursor")
        elif (
            self.next_cursor is not None
            or self.next_updated_after is None
            or self.next_after_sequence is None
        ):
            raise ValueError(
                "a complete page requires time and sequence checkpoints and no cursor"
            )
        elif (
            self.next_after_sequence != self.watermark.sequence
            or self.next_updated_after != self.watermark.as_of
        ):
            raise ValueError("a complete page checkpoint must match its watermark")
        return self
