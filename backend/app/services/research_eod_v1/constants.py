from __future__ import annotations

FACTORS = ("T", "M", "S", "B", "P", "V", "R", "G")
ALGORITHMS = (
    "A_trend_quality",
    "B_confirmed_base_breakout",
    "C_trend_pullback",
    "D_residual_momentum",
)
PROFILES = ("conservative", "balanced", "aggressive")
HORIZONS = ("short", "mid", "long")

SWING_SPAN = 3
ATR_PERIOD = 14
INDUSTRY_SHRINK_K = 30
PARENT_MIN_FOR_Q = 20
INDUSTRY_MIN_FOR_LAMBDA = 3
G_MIN_PEERS = 5
RESIDUAL_FIT_WINDOW = 252
RESIDUAL_HISTORY_MIN = 330
RESIDUAL_SUM_START = 67
RESIDUAL_SUM_END = 5
BASE_WINDOWS = (10, 15, 20, 30, 40, 60, 80)
TOUCH_MIN_GAP = 3
BREAKOUT_TRACK_MAX_SESSIONS = 5
PLATFORM_FAIL_CONFIRM_SESSIONS = 3
PLATFORM_REPAIR_WINDOW = 10
PLATFORM_EXPIRE_MULTIPLE = 3

SCORE_FLOORS = {"conservative": 78.0, "balanced": 74.0, "aggressive": 70.0}
STRUCTURE_FLOORS = {"conservative": 65.0, "balanced": 55.0, "aggressive": 50.0}
COMPOSITE_FLOORS = {"conservative": 82.0, "balanced": 78.0, "aggressive": 74.0}
M2_LAMBDA_RISK = {"conservative": 0.75, "balanced": 0.50, "aggressive": 0.25}

US_MAJOR_MICS = frozenset({"XNYS", "XNAS", "XASE", "ARCX", "BATS", "BATS", "IEXG", "XCBO"})
US_MAJOR_EXCHANGES = frozenset({
    "NYSE",
    "NASDAQ",
    "NYSE ARCA",
    "NYSE AMERICAN",
    "AMEX",
    "BATS",
    "CBOE BZX",
    "IEX",
})
OTC_MICS = frozenset({"OTCM", "OTCB", "PINX", "PSGM", "OOTC"})
OTC_EXCHANGES = frozenset({"OTC", "OTCQX", "OTCQB", "PINK", "GREY"})
