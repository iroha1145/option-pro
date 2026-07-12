"""Adapter for explicit ticker-set intrinsic strength scoring."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

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
        payload = await scanner.score_ticker_set(
            symbols,
            as_of=as_of,
            include_options=False,
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
