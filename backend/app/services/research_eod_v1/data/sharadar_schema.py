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
# `table` tells which dataset a master row belongs to (stocks / funds / ...). The
# official docs list it, but a row without it is still a usable master row.
TICKERS_OPTIONAL_FIELDS = ("table",)
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
# v2: date-bound tables are requested with an explicit sort so page boundaries
# do not depend on the vendor default (date descending, ties unspecified).
REQUEST_PLAN_VERSION = "sharadar-request-plan-v2"
DATE_SORT = "date.asc"
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

# Download modes. The first full backfill should come from the vendor's bulk
# archive (one zip per table); paging is the fallback and the incremental path.
DOWNLOAD_MODES = ("bulk_first", "paged")
BULK_YEARS_PROBE_ORDER = ("full", "10", "5")

# Acceptance thresholds. Pre-registered here; the pipeline does not tune them.
TRANSFORM_SKIP_TOLERANCE = 0.001
RECONCILE_MIN_RETURN_COVERAGE = 250
RECONCILE_MIN_SECURITIES = 10
IDENTITY_MIN_CONCRETE_TERMINALS = 12
ADV20_MAX_GAP_DAYS = 45
EVIDENCE_ROW_CAP = 1_000
COMPLETENESS_SAMPLE_DATES = 8

# Corporate-action vocabulary. These are the strings the terminal classifier
# recognises; the real vendor vocabulary is recorded on every run so that this
# list can be corrected against evidence instead of guessed.
ACTION_BANKRUPTCY = frozenset({
    "bankruptcyliquidation",
    "bankruptcy",
    "liquidation",
    "chapter11",
    "chapter7",
})
ACTION_DELISTED = frozenset({
    "delisted",
    "regulatorydelisting",
    "voluntarydelisting",
    "deleted",
})
ACTION_ACQUISITION_STOCK = frozenset({
    "acquisitionstock",
    "acquisitionbystock",
    "acquisitionelectstock",
    "mergerstock",
})
ACTION_ACQUISITION_ELECT_CASH = frozenset({"acquisitionelectcash"})

# Value-unit semantics. Sharadar documents `actions.value` as a bare number; the
# unit it carries depends on the action. Only codes that name a cash
# consideration are read as dollars per share of the settled security. An
# acquisition or merger code that does not name one keeps its number with the
# unit reported as unverified -- it is not assumed to be dollars and it is not
# assumed to be an exchange ratio either.
ACTION_VALUE_SEMANTICS_VERSION = "sharadar-action-value-units-v1"
ACTION_CASH_CONSIDERATION = frozenset({
    "acquisitioncash",
    "acquisitionbycash",
})
# Terminates a listing, says nothing about what `value` measures.
ACTION_ACQUISITION_UNIT_UNVERIFIED = frozenset({
    "acquisitionby",
    "acquired",
    "merger",
    "mergerfrom",
    "takeprivate",
})
# One leg of an elected or contingent consideration. The absence of the other
# leg in the store is not evidence that this leg settled the whole position.
ACTION_PARTIAL_CONSIDERATION = frozenset({
    "acquisitionelectcash",
    "acquisitionelectstock",
    "cvr",
    "contingentvaluerights",
    "contingentconsideration",
})
ACTION_ACQUISITION_TERMINAL = (
    ACTION_CASH_CONSIDERATION
    | ACTION_ACQUISITION_UNIT_UNVERIFIED
    | ACTION_ACQUISITION_STOCK
    | ACTION_PARTIAL_CONSIDERATION
)
ACTION_VALUE_UNITS: dict[str, str] = {
    **{code: "usd_per_share" for code in ACTION_CASH_CONSIDERATION},
    **{code: "unverified" for code in ACTION_ACQUISITION_UNIT_UNVERIFIED},
    **{code: "unverified" for code in ACTION_ACQUISITION_STOCK},
    **{code: "unverified" for code in ACTION_PARTIAL_CONSIDERATION},
}
UNIT_UNVERIFIED = "unverified"
UNIT_USD_PER_SHARE = "usd_per_share"


def download_request_plan(
    *,
    start: date = FULL_HISTORY_START,
    end: date = ALLOWED_END,
    limit: int = DEFAULT_PAGE_LIMIT,
    sort: bool = True,
) -> dict[str, dict[str, object]]:
    """Pinned request range per table. A page cursor alone cannot drift to the vendor default year."""

    window: dict[str, object] = {"from": start.isoformat(), "to": end.isoformat()}
    if sort:
        window["sort"] = DATE_SORT
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
            "explicit_sort": DATE_SORT if (sort and table in DATE_BOUND_TABLES) else None,
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
    "REDIRECT_UNEXPECTED",
    "EMPTY_BODY",
    "PAGING_UNSUPPORTED",
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
