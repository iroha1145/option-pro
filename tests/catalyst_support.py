from __future__ import annotations

from datetime import datetime, timezone

from app.services.catalysts.models import (
    AffectedStockImpact,
    AnalysisStatus,
    CatalystItem,
    Classification,
    PublicTickerValidation,
    RemoteAnalysis,
)


def utc(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 7, 11, hour, minute, tzinfo=timezone.utc)


def catalyst_item(
    *,
    sequence: int,
    updated_at: datetime,
    analysis: bool,
    news_id: int = 101,
    ticker: str = "NVDA",
) -> CatalystItem:
    fetched_at = utc(10, 4)
    analyzed_at = utc(10, 6) if analysis else None
    available_at = max(fetched_at, analyzed_at) if analyzed_at else None
    result = None
    if analysis:
        result = RemoteAnalysis(
            title_zh="芯片公司发布新产品",
            headline_summary="新产品面向数据中心市场。",
            overall_sentiment=42,
            classification=Classification.BULLISH,
            confidence=78,
            market_relevance=81,
            affected_stocks=[
                AffectedStockImpact(
                    ticker=ticker,
                    company="NVIDIA",
                    impact_score=55,
                    confidence=76,
                    horizon="days",
                    mechanism="direct_company",
                    reason="产品更新直接影响公司数据中心业务。",
                )
            ],
            affected_sectors=["Semiconductors"],
            affected_commodities=[],
            causal_summary="产品更新可能提高市场对数据中心业务的关注。",
            key_factors=["产品更新"],
            uncertainty_notes=["商业化速度仍不确定"],
            insufficient_context=False,
            analysis_id=9001,
            revision=1,
            model="gpt-5.6-terra",
            reasoning="max",
            prompt_version="news-impact-v2",
            schema_version="news-impact-schema-v2",
            analyzed_at=analyzed_at,
            available_at=available_at,
            stock_validations=[
                PublicTickerValidation(
                    ticker=ticker,
                    validation_status="canonical",
                    validated_at=available_at,
                    focus_revision=1,
                    universe_version="fixture-v1",
                )
            ],
        )
    return CatalystItem(
        news_id=news_id,
        content_hash=f"content-hash-{news_id}",
        source="Fixture Wire",
        title="NVIDIA announces a new data-center product",
        summary="A bounded fixture summary.",
        url="https://example.com/news/101",
        published_at=utc(10, 0),
        fetched_at=fetched_at,
        updated_at=updated_at,
        change_sequence=sequence,
        source_tickers=[ticker],
        analysis_status=(AnalysisStatus.COMPLETED if analysis else AnalysisStatus.NOT_REQUESTED),
        analysis=result,
        analyzed_at=analyzed_at,
        available_at=available_at,
        is_stale=False,
    )
