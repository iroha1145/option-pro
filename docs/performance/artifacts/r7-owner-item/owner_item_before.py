def _item(
    self,
    connection: sqlite3.Connection,
    row: dict[str, Any],
    *,
    as_of: datetime,
    jobs: Mapping[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result, available = self._analysis_for_revision(connection, row, as_of=as_of)
    if current_request_is_owner():
        job_public, _detail_job = self._linked_news_job_at(
            connection,
            row,
            as_of=as_of,
            jobs=jobs,
        )
        status = str(
            job_public.get("status") if job_public else "not_requested"
        )
    else:
        # Published local analysis is enough for the visitor view. Do not
        # read the mutable AI job store merely to expose queue state.
        status = "not_requested"
    if result is not None:
        status = "completed"
    elif status == "completed":
        status = "pending"
    item = {
        "news_id": int(row["news_id"]),
        "change_sequence": int(row["change_sequence"]),
        "content_hash": str(row["content_hash"]),
        "source": str(row["source"]),
        # PersonalCatalystService performs a second, fail-closed validation
        # at the public boundary. Preserve the exact source context used
        # by the paid request so legitimate source-bound company names are
        # not mistaken for untranslated English, then strip these private
        # fields before returning an API payload.
        "_validation_source": str(row["source"]),
        "_validation_title": str(row.get("raw_title") or ""),
        "_validation_summary": row.get("raw_summary"),
        "_validation_sources": list(row.get("source_names") or []),
        "_validation_allowed_tickers": list(
            row.get("canonical_tickers") or []
        ),
        "title": str(
            result.get("title_zh")
            if result
            else row.get("raw_title") or ""
        ),
        "title_zh": str(
            result.get("title_zh")
            if result
            else row.get("raw_title") or ""
        ),
        "summary": str(
            result.get("headline_summary")
            if result
            else row.get("raw_summary") or ""
        ),
        "summary_zh": str(
            result.get("summary_zh")
            if result
            else row.get("raw_summary") or ""
        ),
        "url": str(row["url"]),
        "image_url": row.get("image_url"),
        "published_at": row.get("published_at"),
        "fetched_at": row.get("fetched_at"),
        "updated_at": row.get("source_available_at"),
        "source_tickers": list(row.get("canonical_tickers") or []),
        "source_count": int(row.get("source_count") or 1),
        "analysis_status": status,
        "analysis": result,
        "analyzed_at": available,
        "available_at": available,
        "is_stale": False,
    }
    if result:
        item.update(
            {
                "classification": result.get("classification"),
                "confidence": result.get("confidence"),
                "market_relevance": result.get("market_relevance"),
                "overall_sentiment": result.get("overall_sentiment"),
                "trusted_stock_impacts": result.get("affected_stocks") or [],
            }
        )
    return item
