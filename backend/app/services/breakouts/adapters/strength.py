"""Adapter for explicit ticker-set intrinsic strength scoring."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from app.services.breakouts.config import get_breakout_settings
from app.services.breakouts.models import StrengthScoreSnapshot, normalize_ticker
from app.services.strength import scanner


class ExistingStrengthAdapter:
    version = scanner.INTRINSIC_STRENGTH_VERSION

    async def score_ticker_set(
        self,
        tickers: Sequence[str],
        *,
        as_of: datetime,
        include_options: bool = False,
    ) -> dict[str, StrengthScoreSnapshot]:
        if include_options:
            raise ValueError("breakout intrinsic scoring excludes options")
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        settings = get_breakout_settings()
        payload = await scanner.score_ticker_set(
            symbols,
            as_of=as_of,
            include_options=False,
            range_mode=settings.range_persistence_mode,
            range_version=settings.range_persistence_version,
            range_length=settings.range_persistence_length,
            range_fast_length=settings.range_persistence_fast_length,
            range_slope_days=settings.range_persistence_slope_days,
            range_ratio_window=settings.range_persistence_ratio_window,
            range_ratio_threshold=settings.range_persistence_ratio_threshold,
            range_min_history_multiplier=(
                settings.range_persistence_min_history_multiplier
            ),
            range_trend_weight=settings.range_persistence_trend_family_weight,
            range_final_cap=settings.range_persistence_final_weight_cap,
        )
        results: dict[str, StrengthScoreSnapshot] = {}
        for row in payload.get("rows", []):
            snapshot = StrengthScoreSnapshot(
                ticker=row["ticker"],
                score=row.get("score"),
                score_scope=str(row.get("score_scope") or "unknown"),
                confidence=float(row.get("confidence") or 0.0),
                score_version=str(row.get("score_version") or self.version),
                included_features=list(row.get("included_features") or []),
                factor_breakdown=dict(row.get("factor_breakdown") or {}),
                coverage=dict(row.get("coverage") or {}),
                as_of=as_of,
            )
            results[snapshot.ticker] = snapshot
        return results

    async def score_from_daily_snapshots(
        self,
        tickers: Sequence[str],
        *,
        snapshots: Mapping[str, Any],
        as_of: datetime,
        include_options: bool = False,
        range_mode: str = "shadow",
        range_trend_weight: float = 0.15,
        range_final_cap: float = 0.04,
    ) -> dict[str, StrengthScoreSnapshot]:
        if include_options:
            raise ValueError("breakout intrinsic scoring excludes options")
        symbols = list(dict.fromkeys(normalize_ticker(value) for value in tickers))
        settings = get_breakout_settings()
        frames = {
            symbol: snapshot.frame
            for symbol, snapshot in snapshots.items()
            if hasattr(snapshot, "frame")
        }
        sources = sorted(
            {
                str(snapshot.source)
                for snapshot in snapshots.values()
                if getattr(snapshot, "source", None)
            }
        )
        payload = await scanner.score_ticker_frames(
            symbols,
            frames=frames,
            as_of=as_of,
            range_mode=range_mode,
            range_version=settings.range_persistence_version,
            range_length=settings.range_persistence_length,
            range_fast_length=settings.range_persistence_fast_length,
            range_slope_days=settings.range_persistence_slope_days,
            range_ratio_window=settings.range_persistence_ratio_window,
            range_ratio_threshold=settings.range_persistence_ratio_threshold,
            range_min_history_multiplier=(
                settings.range_persistence_min_history_multiplier
            ),
            range_trend_weight=range_trend_weight,
            range_final_cap=range_final_cap,
            price_source={
                "provider": " + ".join(sources) or "unavailable",
                "status": "active" if frames else "unavailable",
                "message": "reused Breakout Radar daily snapshots",
            },
        )
        results: dict[str, StrengthScoreSnapshot] = {}
        for row in payload.get("rows", []):
            snapshot = StrengthScoreSnapshot(
                ticker=row["ticker"],
                score=row.get("score"),
                score_scope=str(row.get("score_scope") or "unknown"),
                confidence=float(row.get("confidence") or 0.0),
                score_version=str(row.get("score_version") or self.version),
                included_features=list(row.get("included_features") or []),
                factor_breakdown=dict(row.get("factor_breakdown") or {}),
                coverage=dict(row.get("coverage") or {}),
                as_of=as_of,
            )
            results[snapshot.ticker] = snapshot
        return results
