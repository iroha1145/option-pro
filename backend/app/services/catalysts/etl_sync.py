from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .etl_client import (
    CALENDAR_PAGE_LIMIT,
    NEWS_PAGE_LIMIT,
    EtlClientError,
    EtlCursorResetRequired,
    EtlResponseTooLarge,
    HealthProbe,
    MacroLensEtlClient,
)
from .etl_repository import CatalystEtlRepository, StreamName


MIN_NEWS_PAGE_LIMIT = 50


@dataclass(frozen=True)
class SyncResult:
    stream: Literal["news", "calendar"]
    pages: int
    records: int
    deletes: int
    replayed: int
    complete: bool
    cursor_resets: int
    updated_after: str
    watermark_sequence: int
    watermark_as_of: str | None


class MacroLensIncrementalSync:
    """Pull complete frozen pages and advance checkpoints atomically."""

    def __init__(
        self,
        client: MacroLensEtlClient,
        repository: CatalystEtlRepository,
        *,
        news_page_limit: int = NEWS_PAGE_LIMIT,
        calendar_page_limit: int = CALENDAR_PAGE_LIMIT,
    ) -> None:
        if (
            isinstance(news_page_limit, bool)
            or not 1 <= news_page_limit <= NEWS_PAGE_LIMIT
        ):
            raise ValueError(
                f"news_page_limit must be between 1 and {NEWS_PAGE_LIMIT}"
            )
        if (
            isinstance(calendar_page_limit, bool)
            or not 1 <= calendar_page_limit <= CALENDAR_PAGE_LIMIT
        ):
            raise ValueError(
                f"calendar_page_limit must be between 1 and {CALENDAR_PAGE_LIMIT}"
            )
        self.client = client
        self.repository = repository
        self.news_page_limit = news_page_limit
        self.calendar_page_limit = calendar_page_limit

    async def probe_health(self) -> HealthProbe:
        return await self.client.probe_health()

    async def sync_news(self, *, max_pages: int = 100) -> SyncResult:
        return await self._sync("news", max_pages=max_pages)

    async def sync_calendar(self, *, max_pages: int = 100) -> SyncResult:
        return await self._sync("calendar", max_pages=max_pages)

    async def _sync(self, stream: StreamName, *, max_pages: int) -> SyncResult:
        if isinstance(max_pages, bool) or not 1 <= max_pages <= 10_000:
            raise ValueError("max_pages is invalid")
        self.repository.initialize()
        pages = 0
        records = 0
        deletes = 0
        replayed = 0
        resets = 0
        complete = False
        request_limit = (
            self.news_page_limit if stream == "news" else self.calendar_page_limit
        )
        minimum_news_limit = min(MIN_NEWS_PAGE_LIMIT, self.news_page_limit)

        while pages < max_pages:
            state = self.repository.state(stream)
            try:
                if stream == "news":
                    if state.cursor:
                        page = await self.client.news_changes(
                            cursor=state.cursor,
                            limit=request_limit,
                        )
                    else:
                        page = await self.client.news_changes(
                            updated_after=state.updated_after,
                            after_sequence=state.completed_watermark_sequence,
                            limit=request_limit,
                        )
                    applied = self.repository.apply_news_page(
                        page,
                        expected_cursor=state.cursor,
                        expected_generation=state.generation,
                    )
                    records += int(applied["upserts"])
                    deletes += int(applied["deletes"])
                    replayed += int(applied["replayed"])
                    complete = bool(applied["complete"])
                else:
                    if state.cursor:
                        calendar_page = await self.client.calendar(
                            cursor=state.cursor,
                            limit=request_limit,
                        )
                    else:
                        calendar_page = await self.client.calendar(
                            updated_after=state.updated_after,
                            after_sequence=state.completed_watermark_sequence,
                            limit=request_limit,
                        )
                    applied = self.repository.apply_calendar_page(
                        calendar_page,
                        expected_cursor=state.cursor,
                        expected_generation=state.generation,
                    )
                    records += int(applied["items"])
                    replayed += int(applied["replayed"])
                    complete = bool(applied["complete"])
            except EtlResponseTooLarge as exc:
                if stream != "news" or request_limit <= minimum_news_limit:
                    self.repository.record_error(stream, exc.code)
                    raise
                request_limit = max(minimum_news_limit, request_limit // 2)
                continue
            except EtlCursorResetRequired as exc:
                if state.cursor is None or resets >= 1:
                    self.repository.record_error(stream, exc.code)
                    raise
                self.repository.reset_cursor(stream, error_code=exc.code)
                resets += 1
                continue
            except EtlClientError as exc:
                self.repository.record_error(stream, exc.code)
                raise

            pages += 1
            if complete:
                break

        final = self.repository.state(stream)
        watermark_sequence = (
            final.completed_watermark_sequence
            if complete
            else int(final.pending_watermark_sequence or 0)
        )
        watermark_as_of = (
            final.completed_as_of if complete else final.pending_watermark_as_of
        )
        return SyncResult(
            stream=stream,
            pages=pages,
            records=records,
            deletes=deletes,
            replayed=replayed,
            complete=complete,
            cursor_resets=resets,
            updated_after=final.updated_after,
            watermark_sequence=watermark_sequence,
            watermark_as_of=watermark_as_of,
        )
