from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


class TickerSearchResult(BaseModel):
    ticker: str
    name: Optional[str] = None
    market: Optional[str] = None
    type: Optional[str] = None


class StockOverview(BaseModel):
    ticker: str
    name: Optional[str] = None
    price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[float] = None
    market_cap: Optional[float] = None
    prev_close: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None
    description: Optional[str] = None
    sic_code: Optional[str] = None
    sic_description: Optional[str] = None


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


class ExpirationsResponse(BaseModel):
    ticker: Optional[str] = None
    expirations: List[str]


class OptionLeg(BaseModel):
    ticker: Optional[str] = None
    type: Optional[Literal["call", "put"]] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    midpoint: Optional[float] = None
    last_price: Optional[float] = None
    change: Optional[float] = None
    change_percent: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    implied_volatility: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    break_even: Optional[float] = None
    break_even_price: Optional[float] = None
    day_change: Optional[float] = None
    day_change_percent: Optional[float] = None
    in_the_money: Optional[bool] = None
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)


class OptionChainResponse(BaseModel):
    ticker: Optional[str] = None
    expiration: Optional[str] = None
    calls: List[OptionLeg] = Field(default_factory=list)
    puts: List[OptionLeg] = Field(default_factory=list)
    underlying_price: Optional[float] = None
    strikes: List[float] = Field(default_factory=list)
    grouped_by_strike: Dict[str, Dict[str, Optional[OptionLeg]]] = Field(default_factory=dict)
    data_limited: Optional[bool] = None
    upgrade_message: Optional[str] = None


class UnusualActivity(BaseModel):
    ticker: Optional[str] = None
    contract_ticker: Optional[str] = None
    contract_type: Optional[Literal["call", "put"]] = None
    type: Optional[Literal["call", "put"]] = None
    strike: Optional[float] = None
    expiration: Optional[str] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    oi: Optional[int] = None
    vol_oi_ratio: Optional[float] = None
    vol_oi: Optional[float] = None
    premium: Optional[float] = None
    last_price: Optional[float] = None
    implied_volatility: Optional[float] = None
    underlying_price: Optional[float] = None
    in_the_money: Optional[bool] = None


class Sector(BaseModel):
    id: str
    name: str
    tickers: List[str]


class SectorIVRank(BaseModel):
    ticker: str
    name: Optional[str] = None
    price: Optional[float] = None
    iv_rank: Optional[float] = None
    iv_percentile: Optional[float] = None
    iv_pct: Optional[float] = None
    iv_current: Optional[float] = None
    iv_change_30d: Optional[float] = None


class SectorHeatmapItem(BaseModel):
    ticker: str
    iv_percentile: Optional[float] = None


class UnusualActivityLimitedResponse(BaseModel):
    results: List[UnusualActivity] = Field(default_factory=list)
    data_limited: Optional[bool] = None
    message: Optional[str] = None


class SectorIVLimitedResponse(BaseModel):
    rankings: List[Union[SectorIVRank, SectorHeatmapItem]] = Field(default_factory=list)
    data: List[SectorHeatmapItem] = Field(default_factory=list)
    sector_id: Optional[str] = None
    sector_name: Optional[str] = None
    data_limited: Optional[bool] = None
    message: Optional[str] = None


class MarketStatus(BaseModel):
    market: str
    server_time: Optional[str] = None
    exchanges: Optional[Dict[str, Any]] = None
    currencies: Optional[Dict[str, Any]] = None
    raw: Optional[Dict[str, Any]] = Field(default=None, exclude=True)
