from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Optional

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from .models import TICKER_PATTERN


FOCUS_SCHEMA_VERSION = "option-pro-macrolens-focus-v2"
# Updated together with contracts/option-pro-macrolens-focus-v2.json.
FOCUS_SCHEMA_SHA256 = "fbc646433375bc5657ec1dcaf0f980c14191390dabe8468129fdf71f78d5cade"
FocusTicker = Annotated[
    str,
    Field(min_length=1, max_length=20, pattern=TICKER_PATTERN.pattern),
]


class FocusModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class FocusSymbol(FocusModel):
    ticker: FocusTicker
    validation_status: Literal["canonical", "valid_external", "unverified"]
    data_status: Literal["active", "stale"] = "active"
    universe_reasons: list[str] = Field(min_length=1, max_length=12)
    dollar_volume_rank: Optional[int] = Field(default=None, ge=1)
    dollar_volume: Optional[float] = Field(default=None, ge=0)
    dollar_volume_basis: Literal[
        "intraday_completed_bars",
        "previous_complete_session",
        "adv20_completed_sessions",
        "unavailable",
    ] = "unavailable"
    session_change_pct: Optional[float] = None
    rvol_time_of_day: Optional[float] = Field(default=None, ge=0)
    breakout_state: Optional[str] = Field(default=None, max_length=60)
    sector_id: Optional[str] = Field(default=None, max_length=120)
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    data_quality: Optional[float] = Field(default=None, ge=0, le=1)
    source_status: Literal[
        "active", "degraded", "fallback", "unavailable", "stale"
    ] = "unavailable"
    data_source: Optional[str] = Field(default=None, max_length=80)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return str(value).strip().upper()

    @field_validator("universe_reasons")
    @classmethod
    def unique_reasons(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if not normalized or len(normalized) != len(set(normalized)):
            raise ValueError("universe_reasons must contain unique non-empty values")
        return normalized


class FocusContextDraft(FocusModel):
    schema_version: Literal[FOCUS_SCHEMA_VERSION] = FOCUS_SCHEMA_VERSION
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    market_session: Literal[
        "premarket", "regular", "after_hours", "closed", "unknown"
    ]
    universe_version: str = Field(min_length=1, max_length=200)
    symbols: list[FocusSymbol] = Field(default_factory=list, max_length=200)
    major_market_symbols: list[FocusTicker] = Field(default_factory=list, max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("major_market_symbols")
    @classmethod
    def unique_market_symbols(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().upper() for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("major_market_symbols contains duplicates")
        return normalized


class FocusContextResponse(FocusContextDraft):
    revision: int = Field(ge=1)


def utc_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed
