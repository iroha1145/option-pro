"""Adapter from Option Pro's market analysis to the six-state radar contract."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from app.services.breakouts.models import MarketShapeSnapshot
from app.services.strength.market_shape import MARKET_SHAPE_VERSION


class ExistingMarketShapeAdapter:
    version = MARKET_SHAPE_VERSION

    async def snapshot(self, *, as_of: datetime) -> MarketShapeSnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        # Import lazily so the breakout domain remains a narrow consumer of the
        # existing strength service and does not introduce an import cycle.
        from app.services.strength.scanner import market_strength

        payload = await market_strength(as_of=as_of)
        regime = payload.get("market_regime") if isinstance(payload, Mapping) else {}
        shape = regime.get("market_shape") if isinstance(regime, Mapping) else {}
        if not isinstance(shape, Mapping):
            shape = {}
        return MarketShapeSnapshot(
            status=str(shape.get("status") or "unavailable"),
            state=str(shape["state"]) if shape.get("state") is not None else None,
            raw_state=(
                str(shape["raw_state"])
                if shape.get("raw_state") is not None
                else None
            ),
            previous_state=(
                str(shape["previous_state"])
                if shape.get("previous_state") is not None
                else None
            ),
            confidence=float(shape.get("confidence") or 0.0),
            transition_risk=(
                float(shape["transition_risk"])
                if shape.get("transition_risk") is not None
                else None
            ),
            state_instability_score=(
                float(shape["state_instability_score"])
                if shape.get("state_instability_score") is not None
                else None
            ),
            entered_at=shape.get("entered_at"),
            days_in_state=max(0, int(shape.get("days_in_state") or 0)),
            pending_state=(
                str(shape["pending_state"])
                if shape.get("pending_state") is not None
                else None
            ),
            pending_days=max(0, int(shape.get("pending_days") or 0)),
            as_of=shape.get("as_of") or as_of,
            input_coverage=dict(shape.get("input_coverage") or {}),
            hard_missing=[str(item) for item in list(shape.get("hard_missing") or [])],
            optional_missing=[
                str(item) for item in list(shape.get("optional_missing") or [])
            ],
            active_groups=[str(item) for item in list(shape.get("active_groups") or [])],
            degraded_reasons=[
                str(item) for item in list(shape.get("degraded_reasons") or [])
            ],
            rules=dict(shape.get("rules") or {}),
            warnings=[str(item) for item in list(shape.get("warnings") or [])],
            version=str(shape.get("version") or self.version),
        )
