"""Official Sharadar.com table and field contract. Do not invent columns."""

from __future__ import annotations

from datetime import date

OFFICIAL_CHANNEL = "api.sharadar.com"
OFFICIAL_BASE_URL = "https://api.sharadar.com/v1.0/data"
OFFICIAL_SCHEMA_URL = "https://api.sharadar.com/v1.0/schema"
AUTH_QUERY_PARAM = "api_key"
ENV_KEY_NAME = "SHARADAR_API_KEY"
CHANNEL_DOCS = (
    "https://sharadar.com/docs/auth",
    "https://sharadar.com/docs/stocks",
    "https://sharadar.com/docs/funds",
    "https://sharadar.com/docs/tickers",
    "https://sharadar.com/docs/actions",
    "https://sharadar.com/docs/faqs",
    "https://sharadar.com/blog/posts/sharadar-stock-prices-fund-prices-and-adjustments",
)

TABLES = {
    "stocks": {"alias": "SEP", "logical": "equity_eod"},
    "funds": {"alias": "SFP", "logical": "fund_eod"},
    "tickers": {"alias": "TICKERS", "logical": "security_master"},
    "actions": {"alias": "ACTIONS", "logical": "corporate_actions"},
}

STOCKS_FIELDS = (
    "ticker",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "closeadj",
    "closeunadj",
    "lastupdated",
)
FUNDS_FIELDS = STOCKS_FIELDS
TICKERS_FIELDS = (
    "permaticker",
    "ticker",
    "name",
    "exchange",
    "isdelisted",
    "category",
    "currency",
    "siccode",
    "sicsector",
    "sicindustry",
    "famaindustry",
    "sector",
    "industry",
    "location",
    "firstpricedate",
    "lastpricedate",
    "relatedtickers",
    "cusips",
    "figi",
    "scalemarketcap",
    "lastupdated",
)
ACTIONS_FIELDS = (
    "date",
    "action",
    "ticker",
    "name",
    "value",
    "contraticker",
    "contraname",
)

TABLE_FIELDS = {
    "stocks": STOCKS_FIELDS,
    "funds": FUNDS_FIELDS,
    "tickers": TICKERS_FIELDS,
    "actions": ACTIONS_FIELDS,
}

DEFAULT_PAGE_LIMIT = 10_000
FORMULA_VERSION = "sharadar-raw-imputation-v1"
DERIVED_FLAG = "DERIVED_FROM_VENDOR_ADJUSTMENT"
PRICE_HISTORY_RECONSTRUCTED = "PRICE_HISTORY_RECONSTRUCTED"
CLASSIFICATION_CURRENT = "CLASSIFICATION_CURRENT"
VENUE_HISTORY_UNVERIFIED = "VENUE_HISTORY_UNVERIFIED"
ETF_SUBASSET_MAPPING_MANUAL = "ETF_SUBASSET_MAPPING_MANUAL"

ALLOWED_END = date(2024, 6, 28)
HOLDOUT_START = date(2024, 7, 1)
FULL_HISTORY_START = date(2010, 1, 1)
HISTORY_10Y_START = date(2016, 9, 1)

VENUE_OK = frozenset({"NYSE", "NASDAQ", "NYSEMKT", "NYSEARCA", "BATS"})
CLOSEUNADJ_MIN = 5.0
UNADJ_ADV20_MIN = 20_000_000.0

STATUSES = (
    "READ_OK",
    "AUTH_REQUIRED",
    "AUTH_FAILED",
    "ENTITLEMENT_MISSING",
    "ENTITLEMENT_SHORT_5Y",
    "HISTORY_10Y",
    "NETWORK_UNAVAILABLE",
    "SCHEMA_MISMATCH",
)

PROBE_SAMPLES = {
    "non_free_example": "MSFT",
    "delisted_example": "BBBY",
    "history_example": "AAPL",
    "fund_example": "SPY",
}

ETF_SUBASSETS = (
    "broad_equity",
    "sector_equity",
    "thematic_equity",
    "gold",
    "long_bond",
)

FUND_CATEGORIES = frozenset({"ETF", "CEF", "ETN", "ETD"})
TERMINAL_BANKRUPTCY = "bankruptcy_last_trade"
TERMINAL_ACQUISITION_CASH = "acquisition_cash"
TERMINAL_UNKNOWN = "TERMINAL_UNKNOWN"
