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
REQUEST_PLAN_VERSION = "sharadar-request-plan-v1"
DERIVED_FLAG = "DERIVED_FROM_VENDOR_ADJUSTMENT"
PRICE_HISTORY_RECONSTRUCTED = "PRICE_HISTORY_RECONSTRUCTED"
CLASSIFICATION_CURRENT = "CLASSIFICATION_CURRENT"
VENUE_HISTORY_UNVERIFIED = "VENUE_HISTORY_UNVERIFIED"
ETF_SUBASSET_MAPPING_MANUAL = "ETF_SUBASSET_MAPPING_MANUAL"

ALLOWED_END = date(2024, 6, 28)
HOLDOUT_START = date(2024, 7, 1)
FULL_HISTORY_START = date(2010, 1, 1)
HISTORY_10Y_START = date(2016, 9, 1)

LABEL_HORIZONS = (5, 20, 63)

# Tables whose official docs expose `from`/`to`. The master table has no date
# parameter, so a bounded download shards only the date-bearing tables.
DATE_BOUND_TABLES = ("stocks", "funds", "actions")
MASTER_TABLES = ("tickers",)


def download_request_plan(
    *,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
    limit: int = DEFAULT_PAGE_LIMIT,
) -> dict[str, dict[str, object]]:
    """Pinned request range per table. A page cursor alone cannot drift to the vendor default year."""

    window = {"from": start.isoformat(), "to": end.isoformat()}
    plan: dict[str, dict[str, object]] = {}
    for table in TABLES:
        extra = dict(window) if table in DATE_BOUND_TABLES else {}
        plan[table] = {
            "extra": extra,
            "limit": int(limit),
            "date_bound": table in DATE_BOUND_TABLES,
            "plan_version": REQUEST_PLAN_VERSION,
            "source_version": SOURCE_VERSION,
            "shard": "pinned_from_to" if table in DATE_BOUND_TABLES else "full_master_no_date_param",
            "order_not_assumed_dedupe_by_primary_key": True,
        }
    return plan

VENUE_OK = frozenset({"NYSE", "NASDAQ", "NYSEMKT", "NYSEARCA", "BATS"})
CLOSEUNADJ_MIN = 5.0
UNADJ_ADV20_MIN = 20_000_000.0

STATUSES = (
    "READ_OK",
    "PARTIAL",
    "AUTH_REQUIRED",
    "AUTH_FAILED",
    "ENTITLEMENT_MISSING",
    "ENTITLEMENT_SHORT_5Y",
    "HISTORY_10Y",
    "NETWORK_UNAVAILABLE",
    "SCHEMA_MISMATCH",
    "VENDOR_ERROR",
    "CHECKPOINT_QUERY_MISMATCH",
    "INSUFFICIENT",
    "UNSUPPORTED",
    "NOT_COMPUTED",
    "RECONCILIATION_MISSING",
    "EMPTY_NO_SAMPLE",
    "ACCESS_VERIFIED_RANGE_UNKNOWN",
)

# Every layer reports its own status and evidence. Download completion is not acceptance.
GATE_STAGES = (
    "AUTH",
    "TABLE_ACCESS",
    "DOWNLOAD",
    "TRANSFORM",
    "IDENTITY",
    "RECONCILE",
    "HISTORY",
    "VOLUME_SCOPE",
    "EXECUTION",
)

# Pre-registered for this stage. VOLUME_SCOPE and EXECUTION stay blocked on their
# own evidence without holding back the raw price history already obtained.
REQUIRED_GATE_STAGES = (
    "AUTH",
    "TABLE_ACCESS",
    "DOWNLOAD",
    "TRANSFORM",
    "IDENTITY",
    "RECONCILE",
    "HISTORY",
)

TERMINAL_ACCEPTED = "DATA_GATE_ACCEPTED"
TERMINAL_PARTIAL = "DATA_GATE_PARTIAL_REVIEW_REQUIRED"
TERMINAL_INSUFFICIENT = "DATA_GATE_INSUFFICIENT"
RAW_DOWNLOAD_COMPLETE = "RAW_DOWNLOAD_COMPLETE"

OFFICIAL_HTTPS_HOST = "api.sharadar.com"
OFFICIAL_BULK_META_FIELDS = ("table", "name", "size", "sizeLabel", "modified")
# The actions bulk status document lists archives under `files`; stocks/funds are flat.
OFFICIAL_BULK_FILE_LIST_FIELD = "files"
OFFICIAL_BULK_FILE_FIELDS = ("name", "size", "sizeLabel", "modified")
SOURCE_VERSION = "api.sharadar.com/v1.0"

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
