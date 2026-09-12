from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class Bar(BaseModel):
    t: int
    o: float
    h: float
    l: float
    c: float
    v: int = 0
    session: Optional[Literal["regular", "pre", "post"]] = None
    ext: bool = False
    quote_only: bool = False
    # Intraday snapshots mark completion at fetch time; legacy daily rows omit it.
    closed: Optional[bool] = None


class MovingAveragePoint(BaseModel):
    time: int
    value: float


class BarsResponse(BaseModel):
    ticker: Optional[str] = None
    range: Optional[Literal["5m", "15m", "1h", "1d", "1w"]] = None
    period: Optional[str] = None
    interval: Optional[str] = None
    exchange_timezone: Optional[str] = None
    price_adjustment: Literal["raw", "adjusted"] = "raw"
    include_extended_hours: bool = False
    moving_average_scope: Literal["regular_session_only"] = "regular_session_only"
    as_of: Optional[str] = None
    last_bar_at: Optional[str] = None
    source_status: Optional[str] = None
    visible: Optional[int] = None
    bars: List[Bar]
    ema20: List[MovingAveragePoint] = Field(default_factory=list)
    sma50: List[MovingAveragePoint] = Field(default_factory=list)
