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
COMPOSITE_METHODS = (
    "M1_consensus_veto",
    "M2_conservative_utility",
    "M3_diversified_rank",
    "M4_regime_experts",
)
ETF_SUBASSETS = (
    "broad_equity",
    "sector_equity",
    "thematic_equity",
    "gold",
    "long_bond",
)

STATUSES = (
    "REGISTERED_NOT_RUN",
    "DATA_INSUFFICIENT",
    "INSUFFICIENT_PIT_HISTORY",
    "STATISTICALLY_THIN",
    "FAILED",
    "COMPLETED_UNPROMOTED",
    "SHADOW_ONLY",
    "ENGINEERING_ONLY",
    "CURRENT_UNIVERSE_DIAGNOSTIC",
    "SHORT_WINDOW",
    "PIT_CLASSIFICATION_MISSING",
    "CORPORATE_ACTIONS_INCOMPLETE",
    "EXECUTION_DATA_UNVERIFIED",
    "AUTH_REQUIRED",
    "INVALID_EOD_CAPTURE",
)

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
M3_CORRELATION_PENALTY = 25.0
M3_HARD_CORR = 0.85

SCORE_FLOORS = {"conservative": 78.0, "balanced": 74.0, "aggressive": 70.0}
STRUCTURE_FLOORS = {"conservative": 65.0, "balanced": 55.0, "aggressive": 50.0}
COMPOSITE_FLOORS = {"conservative": 82.0, "balanced": 78.0, "aggressive": 74.0}
M4_RANK_FLOORS = {"conservative": 65.0, "balanced": 60.0, "aggressive": 55.0}
M4_BULL_WEIGHTS = {"A_trend_quality": 0.35, "B_confirmed_base_breakout": 0.30, "C_trend_pullback": 0.20, "D_residual_momentum": 0.15}
M4_MIXED_WEIGHTS = {"A_trend_quality": 0.20, "B_confirmed_base_breakout": 0.10, "C_trend_pullback": 0.40, "D_residual_momentum": 0.30}
M4_DEFENSE_WEIGHTS = {"A_trend_quality": 0.15, "B_confirmed_base_breakout": 0.05, "C_trend_pullback": 0.25, "D_residual_momentum": 0.55}
M2_LAMBDA_RISK = {"conservative": 0.75, "balanced": 0.50, "aggressive": 0.25}

CAPITALS_USD = (25_000, 100_000, 500_000, 1_000_000)
SLIPPAGE_BPS = (
    (100_000_000.0, 5.0),
    (20_000_000.0, 10.0),
    (0.0, 25.0),
)

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
