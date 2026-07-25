"""Single source of truth for Optix Macro Conditions.

Series identifiers, unit families, freshness rules, the thirty factor formulas,
module membership, module floors, regime cut-offs and every rolling window live
here as versioned constants. They are deliberately *not* configurable: a
config edit must never be able to change what a published score means.

The factor coverage was chosen with reference to the structure of a public
report the operator supplied, but every formula and every scoring rule below is
defined independently by Optix. No third-party weighting is reproduced or
inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Optional


SCORING_VERSION = "optix-macro-score-v1"

# --- Rolling windows and history requirements ------------------------------

SCORE_WINDOW_YEARS = 5
#: ``minimum_history`` counts valid values *of the factor on the daily score
#: grid*, not raw source prints. A weekly-sourced factor is carried forward onto
#: that grid, so its five-year window holds each weekly print about five times.
#: Repeating every print the same number of times leaves the empirical
#: distribution essentially unchanged, and after the eight-year backfill every
#: factor has a full five-year window, so these floors only bind at the very
#: start of history.
DAILY_MINIMUM_HISTORY = 252
#: Weekly sources are carried forward onto the daily grid, so a floor expressed
#: in grid points has to be multiplied by that carry-forward to mean what its
#: name says (incremental review P2). 104 grid points is about five months of
#: weekly prints, not 104 weeks, so the early history curve began scoring long
#: before it had two years of independent observations behind it. The floor is
#: now stated as "104 weekly prints, expressed on the daily grid".
WEEKLY_PRINTS_PER_GRID_YEAR = 5
WEEKLY_MINIMUM_PRINTS = 104
WEEKLY_MINIMUM_HISTORY = WEEKLY_MINIMUM_PRINTS * WEEKLY_PRINTS_PER_GRID_YEAR
RELATIVE_RETURN_WINDOW_DAYS = 63
#: A 63-trading-day lookback needs 64 shared closes (t and t-63 inclusive).
RELATIVE_RETURN_MINIMUM_OBSERVATIONS = 64
SHORT_VOLATILITY_WINDOW = 21
FX_VOLATILITY_WINDOW = 63
LONG_MEDIAN_WINDOW = 252
TGA_MEDIAN_WEEKLY_OBSERVATIONS = 52
NET_LIQUIDITY_MOMENTUM_WEEKS = 13
TRADING_DAYS_PER_YEAR = 252
FUNDING_FRAGMENTATION_WINDOW = 21
#: The cross-sectional dispersion of funding spreads is only meaningful with
#: most of the panel present.
FUNDING_FRAGMENTATION_MINIMUM_SPREADS = 4

#: Breakeven inflation is scored by distance from this target, not by level.
BREAKEVEN_TARGET_PERCENT = 2.0
#: ON RRP balance (USD billions) at which the buffer is considered intact.
ON_RRP_FULL_BUFFER_BILLIONS = 100.0

# --- Freshness -------------------------------------------------------------

DAILY_MAX_STALE_CALENDAR_DAYS = 7
WEEKLY_MAX_STALE_CALENDAR_DAYS = 14
ETF_MAX_STALE_TRADING_DAYS = 5

# --- Regime labels ---------------------------------------------------------

#: Lower bound, inclusive → label. These describe the current environment
#: relative to its own five-year history. They are not return forecasts.
REGIME_BANDS: tuple[tuple[float, str], ...] = (
    (70.0, "明显宽松"),
    (55.0, "偏松"),
    (45.0, "中性"),
    (30.0, "偏紧"),
    (float("-inf"), "明显收紧"),
)


def regime_for(score: Optional[float]) -> Optional[str]:
    if score is None:
        return None
    for lower, label in REGIME_BANDS:
        if score >= lower:
            return label
    return None


# --- Unit families ---------------------------------------------------------

UnitsFamily = Literal[
    "usd_amount",
    "percent",
    "index",
    "usd_per_barrel",
    "usd_per_mmbtu",
]

#: All money is normalised to USD billions. The multiplier is resolved from the
#: FRED ``units`` metadata string at fetch time, never guessed from the series
#: name — WALCL and RRPONTSYD do not publish in the same scale.
#:
#: Keys use the period-less ``us dollars`` spelling because FRED is not
#: self-consistent: WALCL and WTREGEN say "Millions of U.S. Dollars" while
#: RRPONTSYD says "Billions of US Dollars". :func:`_normalize_units` folds the
#: abbreviation so both reach the same key. Only the abbreviation is folded —
#: the scale word still has to match exactly, so an unknown scale keeps raising.
_USD_UNITS_TO_BILLIONS: Mapping[str, float] = {
    "trillions of us dollars": 1_000.0,
    "billions of us dollars": 1.0,
    "millions of us dollars": 0.001,
    "thousands of us dollars": 0.000_001,
}

_PERCENT_UNITS = ("percent", "percent per annum", "percent, annual rate")
_INDEX_PREFIX = "index"
_BARREL_UNITS = ("dollars per barrel",)
_MMBTU_UNITS = ("dollars per million btu", "dollars per mil. btu")

CANONICAL_UNIT_FOR_FAMILY: Mapping[str, str] = {
    "usd_amount": "usd_billions",
    "percent": "percentage_points",
    "index": "index_points",
    "usd_per_barrel": "usd_per_barrel",
    "usd_per_mmbtu": "usd_per_mmbtu",
}


class UnitsMismatch(ValueError):
    """Raised when FRED metadata units do not belong to the expected family."""


def _normalize_units(units: str) -> str:
    """Lower-case, collapse whitespace, and fold FRED's ``U.S.``/``US`` variants.

    Only that one abbreviation is rewritten. Stripping every period would also
    hit ``dollars per mil. btu``, and widening the match any further would let a
    genuinely unknown scale slip through as if it were recognised.
    """

    normalized = " ".join(str(units or "").strip().lower().split())
    return normalized.replace("u.s.", "us")


def scale_to_canonical(units: str, family: str) -> float:
    """Resolve the multiplier from FRED units text to the canonical unit.

    Unknown units raise instead of being silently passed through at 1.0 — a
    scale mistake on a balance-sheet series would corrupt every liquidity score.
    """

    normalized = _normalize_units(units)
    if not normalized:
        raise UnitsMismatch("missing units metadata")
    if family == "usd_amount":
        if normalized not in _USD_UNITS_TO_BILLIONS:
            raise UnitsMismatch("unrecognised USD scale")
        return _USD_UNITS_TO_BILLIONS[normalized]
    if family == "percent":
        if normalized not in _PERCENT_UNITS:
            raise UnitsMismatch("unrecognised percent units")
        return 1.0
    if family == "index":
        if not normalized.startswith(_INDEX_PREFIX):
            raise UnitsMismatch("unrecognised index units")
        return 1.0
    if family == "usd_per_barrel":
        if normalized not in _BARREL_UNITS:
            raise UnitsMismatch("unrecognised per-barrel units")
        return 1.0
    if family == "usd_per_mmbtu":
        if normalized not in _MMBTU_UNITS:
            raise UnitsMismatch("unrecognised per-MMBtu units")
        return 1.0
    raise UnitsMismatch("unsupported units family")


#: Rank of FRED ``frequency_short`` values, ascending period length. A series
#: that starts publishing *more* often than registered is metadata drift.
FREQUENCY_RANK: Mapping[str, int] = {
    "D": 1,
    "W": 2,
    "BW": 3,
    "M": 4,
    "Q": 5,
    "SA": 6,
    "A": 7,
}


def frequency_at_most(actual: str, expected: str) -> bool:
    actual_rank = FREQUENCY_RANK.get(str(actual or "").strip().upper())
    expected_rank = FREQUENCY_RANK.get(str(expected or "").strip().upper())
    if actual_rank is None or expected_rank is None:
        return False
    return actual_rank >= expected_rank


# --- FRED series registry --------------------------------------------------

FRED_SOURCE_BOARD = "美联储理事会 (H.4.1 / H.15)"
FRED_SOURCE_NY_FED = "纽约联储"
FRED_SOURCE_CHICAGO_FED = "芝加哥联储"
FRED_SOURCE_CBOE = "Cboe"
FRED_SOURCE_TREASURY = "美国财政部"
FRED_SOURCE_EIA = "美国能源信息署"


@dataclass(frozen=True, slots=True)
class SeriesSpec:
    series_id: str
    display_name_zh: str
    expected_frequency: str
    expected_units_family: str
    canonical_unit: str
    #: Explicit multiplier when the family admits only one scale; ``None`` means
    #: "resolve from metadata" (money series).
    scale: Optional[float]
    max_stale_calendar_days: int
    source_name: str
    source_attribution: str
    enabled: bool
    required_for: tuple[str, ...]


def _series(
    series_id: str,
    display_name_zh: str,
    *,
    frequency: str,
    family: str,
    source: str,
    attribution: str,
    required_for: tuple[str, ...],
) -> SeriesSpec:
    return SeriesSpec(
        series_id=series_id,
        display_name_zh=display_name_zh,
        expected_frequency=frequency,
        expected_units_family=family,
        canonical_unit=CANONICAL_UNIT_FOR_FAMILY[family],
        scale=None if family == "usd_amount" else 1.0,
        max_stale_calendar_days=(
            DAILY_MAX_STALE_CALENDAR_DAYS
            if frequency == "D"
            else WEEKLY_MAX_STALE_CALENDAR_DAYS
        ),
        source_name=source,
        source_attribution=attribution,
        enabled=True,
        required_for=required_for,
    )


FRED_SERIES: tuple[SeriesSpec, ...] = (
    # --- liquidity ---
    _series(
        "WALCL",
        "联储总资产",
        frequency="W",
        family="usd_amount",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US), H.4.1",
        required_for=("fed_net_liquidity", "net_liquidity_momentum_13w"),
    ),
    _series(
        "WTREGEN",
        "财政部一般账户 (TGA)",
        frequency="W",
        family="usd_amount",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US), H.4.1",
        required_for=(
            "fed_net_liquidity",
            "net_liquidity_momentum_13w",
            "tga_deviation_52w",
        ),
    ),
    _series(
        "RRPONTSYD",
        "隔夜逆回购余额",
        frequency="D",
        family="usd_amount",
        source=FRED_SOURCE_NY_FED,
        attribution="Federal Reserve Bank of New York",
        required_for=(
            "fed_net_liquidity",
            "net_liquidity_momentum_13w",
            "on_rrp_buffer_risk",
        ),
    ),
    _series(
        "WRESBAL",
        "银行准备金余额",
        frequency="W",
        family="usd_amount",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US), H.4.1",
        required_for=("bank_reserves",),
    ),
    # --- funding ---
    _series(
        "SOFR",
        "有担保隔夜融资利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_NY_FED,
        attribution="Federal Reserve Bank of New York",
        required_for=(
            "collateral_repo_friction",
            "corridor_friction_1",
            "corridor_friction_2",
            "funding_fragmentation_21d",
        ),
    ),
    _series(
        "OBFR",
        "银行隔夜融资利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_NY_FED,
        attribution="Federal Reserve Bank of New York",
        required_for=("collateral_repo_friction", "funding_fragmentation_21d"),
    ),
    _series(
        "IORB",
        "准备金余额利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US)",
        required_for=(
            "corridor_friction_1",
            "effr_iorb_spread",
            "funding_fragmentation_21d",
        ),
    ),
    _series(
        "RRPONTSYAWARD",
        "隔夜逆回购中标利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_NY_FED,
        attribution="Federal Reserve Bank of New York",
        required_for=("corridor_friction_2", "funding_fragmentation_21d"),
    ),
    _series(
        "EFFR",
        "联邦基金有效利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_NY_FED,
        attribution="Federal Reserve Bank of New York",
        required_for=("effr_iorb_spread", "funding_fragmentation_21d"),
    ),
    _series(
        "DCPF3M",
        "3 个月金融商业票据利率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US)",
        required_for=("cp_tbill_spread", "funding_fragmentation_21d"),
    ),
    _series(
        "DTB3",
        "3 个月国库券贴现率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=("cp_tbill_spread", "funding_fragmentation_21d"),
    ),
    # --- treasury ---
    _series(
        "DGS2",
        "2 年期国债收益率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=("curve_curvature_abs",),
    ),
    _series(
        "DGS10",
        "10 年期国债收益率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=(
            "term_premium_30y_10y",
            "rate_volatility_10y_21d",
            "curve_curvature_abs",
        ),
    ),
    _series(
        "DGS30",
        "30 年期国债收益率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=("term_premium_30y_10y", "curve_curvature_abs"),
    ),
    # --- rates ---
    _series(
        "DFII5",
        "5 年期通胀保值债券实际收益率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=("real_rate_level", "real_curve_10y_5y"),
    ),
    _series(
        "DFII10",
        "10 年期通胀保值债券实际收益率",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Board of Governors of the Federal Reserve System (US), H.15",
        required_for=("real_rate_level", "real_curve_10y_5y"),
    ),
    _series(
        "T10YIE",
        "10 年期通胀预期 (Breakeven)",
        frequency="D",
        family="percent",
        source=FRED_SOURCE_TREASURY,
        attribution="Federal Reserve Bank of St. Louis",
        required_for=("breakeven_10y",),
    ),
    # --- credit ---
    _series(
        "NFCI",
        "芝加哥联储全国金融条件指数",
        frequency="W",
        family="index",
        source=FRED_SOURCE_CHICAGO_FED,
        attribution="Federal Reserve Bank of Chicago",
        required_for=("nfci",),
    ),
    # --- risk ---
    _series(
        "VIXCLS",
        "VIX 收盘",
        frequency="D",
        family="index",
        source=FRED_SOURCE_CBOE,
        attribution="Cboe Global Markets",
        required_for=("vix", "vix_term_structure"),
    ),
    _series(
        "VXVCLS",
        "3 个月 VIX (VXV) 收盘",
        frequency="D",
        family="index",
        source=FRED_SOURCE_CBOE,
        attribution="Cboe Global Markets",
        required_for=("vix_term_structure",),
    ),
    # --- external ---
    _series(
        "DTWEXBGS",
        "美元广义名义指数",
        frequency="D",
        family="index",
        source=FRED_SOURCE_BOARD,
        attribution="Board of Governors of the Federal Reserve System (US), H.10",
        required_for=("broad_dollar_index", "fx_realized_volatility_63d"),
    ),
    _series(
        "DCOILWTICO",
        "WTI 原油现货价",
        frequency="D",
        family="usd_per_barrel",
        source=FRED_SOURCE_EIA,
        attribution="U.S. Energy Information Administration",
        required_for=("wti_oil",),
    ),
    _series(
        "OVXCLS",
        "原油波动率指数 (OVX)",
        frequency="D",
        family="index",
        source=FRED_SOURCE_CBOE,
        attribution="Cboe Global Markets",
        required_for=("oil_volatility_deviation",),
    ),
    _series(
        "DHHNGSP",
        "亨利枢纽天然气现货价",
        frequency="D",
        family="usd_per_mmbtu",
        source=FRED_SOURCE_EIA,
        attribution="U.S. Energy Information Administration",
        required_for=("natural_gas",),
    ),
)

SERIES_BY_ID: Mapping[str, SeriesSpec] = {
    spec.series_id: spec for spec in FRED_SERIES
}


# --- ETF proxy registry ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class EtfSpec:
    symbol: str
    display_name_zh: str
    required_for: tuple[str, ...]


ETF_PROXIES: tuple[EtfSpec, ...] = (
    EtfSpec("HYG", "高收益公司债 ETF", ("hy_credit",)),
    EtfSpec("IEI", "3–7 年期国债 ETF", ("hy_credit",)),
    EtfSpec("LQD", "投资级公司债 ETF", ("ig_credit",)),
    EtfSpec("IEF", "7–10 年期国债 ETF", ("ig_credit",)),
    EtfSpec("KRE", "区域银行 ETF", ("regional_banks_vs_spy",)),
    EtfSpec("SPY", "标普 500 ETF", ("regional_banks_vs_spy", "risk_vs_safe", "high_beta_preference")),
    EtfSpec("TLT", "20 年期以上国债 ETF", ("risk_vs_safe",)),
    EtfSpec("IWM", "罗素 2000 ETF", ("high_beta_preference",)),
)

ETF_SYMBOLS: tuple[str, ...] = tuple(spec.symbol for spec in ETF_PROXIES)


# --- Factor registry -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactorSpec:
    factor_id: str
    module_id: str
    display_name_zh: str
    description_zh: str
    formula_version: str
    required_series: tuple[str, ...]
    required_etfs: tuple[str, ...]
    #: Short machine-readable transform label, mirrored in the docs.
    transform: str
    score_method: str
    #: ``high`` — a larger raw value scores higher. ``low`` — smaller scores
    #: higher. ``target`` — closeness to a fixed target scores higher.
    direction: str
    display_unit: str
    minimum_history: int
    stale_rule: str
    #: Present when the score is computed from a transformed quantity.
    scores_transformed_value: bool = False


def _factor(
    factor_id: str,
    module_id: str,
    name: str,
    description: str,
    *,
    series: tuple[str, ...] = (),
    etfs: tuple[str, ...] = (),
    transform: str,
    score_method: str,
    display_unit: str,
    minimum_history: int,
    stale_rule: str,
    scores_transformed_value: bool = False,
) -> FactorSpec:
    direction = {
        "supportive_high_percentile": "high",
        "supportive_low_percentile": "low",
        "target_distance": "target",
        "direct_score": "low",
    }[score_method]
    return FactorSpec(
        factor_id=factor_id,
        module_id=module_id,
        display_name_zh=name,
        description_zh=description,
        formula_version="optix-macro-factor-v1",
        required_series=series,
        required_etfs=etfs,
        transform=transform,
        score_method=score_method,
        direction=direction,
        display_unit=display_unit,
        minimum_history=minimum_history,
        stale_rule=stale_rule,
        scores_transformed_value=scores_transformed_value,
    )


_WEEKLY_STALE = "weekly_14_calendar_days"
_DAILY_STALE = "daily_7_calendar_days"
_ETF_STALE = "etf_5_trading_days"
_MIXED_STALE = "min_of_inputs"

FACTORS: tuple[FactorSpec, ...] = (
    # ---------------- liquidity ----------------
    _factor(
        "fed_net_liquidity",
        "liquidity",
        "联储净流动性",
        "联储总资产减去财政部一般账户与隔夜逆回购余额，单位十亿美元。数值越高表示可用于金融体系的储备越多。",
        series=("WALCL", "WTREGEN", "RRPONTSYD"),
        transform="walcl_minus_tga_minus_onrrp",
        score_method="supportive_high_percentile",
        display_unit="usd_billions",
        minimum_history=WEEKLY_MINIMUM_HISTORY,
        stale_rule=_MIXED_STALE,
    ),
    _factor(
        "bank_reserves",
        "liquidity",
        "银行准备金",
        "存放在联储的银行准备金余额，单位十亿美元。准备金充裕时融资市场承压概率较低。",
        series=("WRESBAL",),
        transform="level",
        score_method="supportive_high_percentile",
        display_unit="usd_billions",
        minimum_history=WEEKLY_MINIMUM_HISTORY,
        stale_rule=_WEEKLY_STALE,
    ),
    _factor(
        "net_liquidity_momentum_13w",
        "liquidity",
        "净流动性 13 周动量",
        "当前联储净流动性减去约 13 周前的净流动性（按 as-of 对齐取最近可用观察，不要求日期完全相同），单位十亿美元。",
        series=("WALCL", "WTREGEN", "RRPONTSYD"),
        transform="net_liquidity_13w_difference",
        score_method="supportive_high_percentile",
        display_unit="usd_billions",
        minimum_history=WEEKLY_MINIMUM_HISTORY,
        stale_rule=_MIXED_STALE,
    ),
    _factor(
        "tga_deviation_52w",
        "liquidity",
        "TGA 偏离一年中位数",
        "财政部一般账户余额减去最近 52 个周度观察的滚动中位数，单位十亿美元。负值表示 TGA 低于一年中位数，对应更多现金留在市场。",
        series=("WTREGEN",),
        transform="deviation_from_rolling_median_52_weekly",
        score_method="supportive_low_percentile",
        display_unit="usd_billions",
        minimum_history=WEEKLY_MINIMUM_HISTORY,
        stale_rule=_WEEKLY_STALE,
    ),
    _factor(
        "on_rrp_buffer_risk",
        "liquidity",
        "隔夜逆回购缓冲风险",
        "以 ON RRP 余额衡量的缓冲耗尽风险：risk = (1 − clip(余额/1000 亿, 0, 1))²，分数 = 100 × (1 − risk)。该因子直接给分，不再做历史分位。",
        series=("RRPONTSYD",),
        transform="on_rrp_buffer_risk_curve",
        score_method="direct_score",
        display_unit="ratio",
        minimum_history=0,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    # ---------------- funding ----------------
    _factor(
        "collateral_repo_friction",
        "funding",
        "抵押品回购摩擦",
        "SOFR 减 OBFR，单位百分点。评分用其绝对值：偏离越小，担保与无担保隔夜市场越一致。界面同时显示带符号原值。",
        series=("SOFR", "OBFR"),
        transform="absolute_spread",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    _factor(
        "corridor_friction_1",
        "funding",
        "利率走廊摩擦（SOFR−IORB）",
        "SOFR 减准备金余额利率，单位百分点。评分用绝对值：越贴近走廊中枢越健康。界面同时显示带符号原值。",
        series=("SOFR", "IORB"),
        transform="absolute_spread",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    _factor(
        "corridor_friction_2",
        "funding",
        "利率走廊摩擦（SOFR−ON RRP）",
        "SOFR 减隔夜逆回购中标利率，单位百分点。评分用绝对值，衡量对走廊下沿的偏离。界面同时显示带符号原值。",
        series=("SOFR", "RRPONTSYAWARD"),
        transform="absolute_spread",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    _factor(
        "effr_iorb_spread",
        "funding",
        "EFFR−IORB 价差",
        "联邦基金有效利率减准备金余额利率，单位百分点。评分用绝对值，衡量政策利率传导是否顺畅。界面同时显示带符号原值。",
        series=("EFFR", "IORB"),
        transform="absolute_spread",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    _factor(
        "cp_tbill_spread",
        "funding",
        "商业票据−国库券价差",
        "3 个月金融商业票据利率减 3 个月国库券贴现率，单位百分点。评分只取正值部分：正价差扩大代表短期信用融资变贵。界面同时显示带符号原值。",
        series=("DCPF3M", "DTB3"),
        transform="positive_part_of_spread",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    _factor(
        "funding_fragmentation_21d",
        "funding",
        "融资分化度（21 日）",
        "每日先算五个带符号融资价差的总体标准差（至少 4 个价差可用才计算），再取最近 21 个有效值的均值，单位百分点。数值越低表示各融资市场越同步。",
        series=("SOFR", "OBFR", "IORB", "RRPONTSYAWARD", "EFFR", "DCPF3M", "DTB3"),
        transform="rolling_mean_21_of_daily_spread_dispersion",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    # ---------------- treasury ----------------
    _factor(
        "term_premium_30y_10y",
        "treasury",
        "30 年−10 年期限斜率",
        "30 年期减 10 年期国债收益率，单位百分点。这是 Optix 对曲线长端斜率的代理，不是学术期限溢价模型。",
        series=("DGS30", "DGS10"),
        transform="spread",
        score_method="supportive_high_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "rate_volatility_10y_21d",
        "treasury",
        "10 年期利率波动（21 日）",
        "最近 21 个有效日度变化的总体标准差，单位百分点，不做年化。数值越低表示长端定价越稳定。",
        series=("DGS10",),
        transform="population_std_of_daily_change_21",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "curve_curvature_abs",
        "treasury",
        "曲线曲率绝对值",
        "abs(2×10 年 − 2 年 − 30 年)，单位百分点。这是 Optix 自定义的 2s10s30s 蝶式曲率代理，数值越小代表曲线形态越常规。",
        series=("DGS10", "DGS2", "DGS30"),
        transform="absolute_butterfly_2s10s30s",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    # ---------------- rates ----------------
    _factor(
        "real_rate_level",
        "rates",
        "实际利率水平",
        "0.6×5 年期实际收益率 + 0.4×10 年期实际收益率，单位百分点。实际利率越低，对风险资产估值的压制越小。",
        series=("DFII5", "DFII10"),
        transform="weighted_real_yield_60_40",
        score_method="supportive_low_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "real_curve_10y_5y",
        "rates",
        "实际利率曲线（10 年−5 年）",
        "10 年期减 5 年期实际收益率，单位百分点。正斜率通常对应对长期增长的定价更高。",
        series=("DFII10", "DFII5"),
        transform="spread",
        score_method="supportive_high_percentile",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "breakeven_10y",
        "rates",
        "10 年期通胀预期",
        "显示 10 年期 Breakeven 原值，评分用其与 2% 的绝对偏离：越贴近 2% 得分越高。界面同时显示原值与偏离。",
        series=("T10YIE",),
        transform="absolute_distance_from_two_percent",
        score_method="target_distance",
        display_unit="percentage_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
        scores_transformed_value=True,
    ),
    # ---------------- credit ----------------
    _factor(
        "nfci",
        "credit",
        "全国金融条件指数",
        "芝加哥联储 NFCI 原值（指数点）。零为长期均值，正值代表金融条件收紧，故数值越低得分越高。",
        series=("NFCI",),
        transform="level",
        score_method="supportive_low_percentile",
        display_unit="index_points",
        minimum_history=WEEKLY_MINIMUM_HISTORY,
        stale_rule=_WEEKLY_STALE,
    ),
    _factor(
        "hy_credit",
        "credit",
        "高收益债相对强度",
        "HYG 相对 IEI 的 63 交易日对数收益差×100，单位百分点。至少需要 64 个共同有效交易日。数值越高代表高收益信用风险偏好越强。",
        etfs=("HYG", "IEI"),
        transform="relative_return_63d",
        score_method="supportive_high_percentile",
        display_unit="percent",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_ETF_STALE,
    ),
    _factor(
        "ig_credit",
        "credit",
        "投资级债相对强度",
        "LQD 相对 IEF 的 63 交易日对数收益差×100，单位百分点。至少需要 64 个共同有效交易日。",
        etfs=("LQD", "IEF"),
        transform="relative_return_63d",
        score_method="supportive_high_percentile",
        display_unit="percent",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_ETF_STALE,
    ),
    _factor(
        "regional_banks_vs_spy",
        "credit",
        "区域银行相对大盘",
        "KRE 相对 SPY 的 63 交易日对数收益差×100，单位百分点。区域银行走弱常与信用供给收缩同步。",
        etfs=("KRE", "SPY"),
        transform="relative_return_63d",
        score_method="supportive_high_percentile",
        display_unit="percent",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_ETF_STALE,
    ),
    # ---------------- risk ----------------
    _factor(
        "vix",
        "risk",
        "VIX 波动率",
        "VIX 收盘值（指数点）。数值越低表示期权市场定价的短期波动越低。",
        series=("VIXCLS",),
        transform="level",
        score_method="supportive_low_percentile",
        display_unit="index_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "vix_term_structure",
        "risk",
        "VIX 期限结构",
        "VIX 除以 3 个月 VIX（VXV）。VXV 小于或等于零时视为缺失。比值越低（曲线越正向）表示近端压力越小。",
        series=("VIXCLS", "VXVCLS"),
        transform="ratio",
        score_method="supportive_low_percentile",
        display_unit="ratio",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "risk_vs_safe",
        "risk",
        "风险资产相对避险资产",
        "SPY 相对 TLT 的 63 交易日对数收益差×100，单位百分点。数值越高代表资金更偏好风险资产。",
        etfs=("SPY", "TLT"),
        transform="relative_return_63d",
        score_method="supportive_high_percentile",
        display_unit="percent",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_ETF_STALE,
    ),
    _factor(
        "high_beta_preference",
        "risk",
        "高贝塔偏好",
        "IWM 相对 SPY 的 63 交易日对数收益差×100，单位百分点。小盘跑赢通常对应更强的风险承担意愿。",
        etfs=("IWM", "SPY"),
        transform="relative_return_63d",
        score_method="supportive_high_percentile",
        display_unit="percent",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_ETF_STALE,
    ),
    # ---------------- external ----------------
    _factor(
        "broad_dollar_index",
        "external",
        "美元广义指数",
        "美联储广义名义美元指数（指数点）。美元走强通常收紧全球美元流动性，故数值越低得分越高。",
        series=("DTWEXBGS",),
        transform="level",
        score_method="supportive_low_percentile",
        display_unit="index_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "fx_realized_volatility_63d",
        "external",
        "美元已实现波动（63 日）",
        "美元指数最近 63 个有效日对数收益率的总体标准差×√252，无量纲比值。数值越低表示汇率环境越平稳。",
        series=("DTWEXBGS",),
        transform="annualized_population_std_of_log_returns_63",
        score_method="supportive_low_percentile",
        display_unit="ratio",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "wti_oil",
        "external",
        "WTI 原油价格",
        "WTI 现货价（美元/桶）。该分数衡量能源成本压力，不代表油价低就一定利好经济增长。",
        series=("DCOILWTICO",),
        transform="level",
        score_method="supportive_low_percentile",
        display_unit="usd_per_barrel",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "oil_volatility_deviation",
        "external",
        "原油波动率偏离",
        "max(OVX − 最近 252 个有效观察的滚动中位数, 0)，单位指数点。只在原油波动率高于自身一年中位数时计入压力。",
        series=("OVXCLS",),
        transform="positive_deviation_from_rolling_median_252",
        score_method="supportive_low_percentile",
        display_unit="index_points",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
    _factor(
        "natural_gas",
        "external",
        "天然气价格",
        "亨利枢纽天然气现货价（美元/百万英热）。该分数衡量能源成本压力，不是天然气产业景气度评分。",
        series=("DHHNGSP",),
        transform="level",
        score_method="supportive_low_percentile",
        display_unit="usd_per_mmbtu",
        minimum_history=DAILY_MINIMUM_HISTORY,
        stale_rule=_DAILY_STALE,
    ),
)

FACTORS_BY_ID: Mapping[str, FactorSpec] = {
    spec.factor_id: spec for spec in FACTORS
}


# --- Module registry -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModuleSpec:
    module_id: str
    display_name_zh: str
    display_name_en: str
    #: Minimum number of valid factors before the module publishes a score.
    minimum_valid_factors: int
    #: Optional exponential smoothing over the daily module score.
    ema_days: Optional[int]


MODULES: tuple[ModuleSpec, ...] = (
    ModuleSpec("liquidity", "流动性", "LIQUIDITY", 3, None),
    # Funding spreads are noisy day to day; a five-day EMA keeps the module
    # readable without changing what the underlying factors measure.
    ModuleSpec("funding", "融资", "FUNDING", 4, 5),
    ModuleSpec("treasury", "国债", "TREASURY", 2, None),
    ModuleSpec("rates", "利率", "RATES", 2, None),
    ModuleSpec("credit", "信用", "CREDIT", 3, None),
    ModuleSpec("risk", "风险", "RISK", 3, None),
    ModuleSpec("external", "外部冲击", "EXTERNAL", 3, None),
)

MODULES_BY_ID: Mapping[str, ModuleSpec] = {
    spec.module_id: spec for spec in MODULES
}

MODULE_IDS: tuple[str, ...] = tuple(spec.module_id for spec in MODULES)

FACTOR_IDS_BY_MODULE: Mapping[str, tuple[str, ...]] = {
    module.module_id: tuple(
        factor.factor_id for factor in FACTORS if factor.module_id == module.module_id
    )
    for module in MODULES
}

#: v1 requires five of seven modules before publishing an official composite.
COMPOSITE_MINIMUM_VALID_MODULES = 5


SOURCE_ATTRIBUTIONS: tuple[str, ...] = tuple(
    sorted({spec.source_attribution for spec in FRED_SERIES})
)


def validate_registry() -> None:
    """Fail fast on registry drift. Called from tests and at service start."""

    if len(FACTORS) != 30:
        raise ValueError(f"expected 30 macro factors, found {len(FACTORS)}")
    if len(FRED_SERIES) != 24:
        raise ValueError(f"expected 24 FRED series, found {len(FRED_SERIES)}")
    if len(ETF_PROXIES) != 8:
        raise ValueError(f"expected 8 ETF proxies, found {len(ETF_PROXIES)}")
    if len(FACTORS_BY_ID) != len(FACTORS):
        raise ValueError("macro factor ids must be unique")
    if len(SERIES_BY_ID) != len(FRED_SERIES):
        raise ValueError("macro series ids must be unique")
    if len({spec.symbol for spec in ETF_PROXIES}) != len(ETF_PROXIES):
        raise ValueError("macro ETF symbols must be unique")
    for factor in FACTORS:
        if factor.module_id not in MODULES_BY_ID:
            raise ValueError(f"{factor.factor_id} names an unknown module")
        if not factor.formula_version:
            raise ValueError(f"{factor.factor_id} is missing a formula version")
        if not factor.required_series and not factor.required_etfs:
            raise ValueError(f"{factor.factor_id} declares no inputs")
        for series_id in factor.required_series:
            if series_id not in SERIES_BY_ID:
                raise ValueError(
                    f"{factor.factor_id} requires unregistered series {series_id}"
                )
        for symbol in factor.required_etfs:
            if symbol not in ETF_SYMBOLS:
                raise ValueError(
                    f"{factor.factor_id} requires unregistered ETF {symbol}"
                )
    for module in MODULES:
        members = FACTOR_IDS_BY_MODULE[module.module_id]
        if not members:
            raise ValueError(f"module {module.module_id} has no factors")
        if not 1 <= module.minimum_valid_factors <= len(members):
            raise ValueError(
                f"module {module.module_id} floor exceeds its factor count"
            )
    # required_for on each series must match the factor registry exactly, so the
    # two directions of the mapping cannot drift apart.
    for spec in FRED_SERIES:
        derived = tuple(
            factor.factor_id
            for factor in FACTORS
            if spec.series_id in factor.required_series
        )
        if tuple(sorted(spec.required_for)) != tuple(sorted(derived)):
            raise ValueError(
                f"series {spec.series_id} required_for does not match the factors"
            )
    for etf in ETF_PROXIES:
        derived = tuple(
            factor.factor_id
            for factor in FACTORS
            if etf.symbol in factor.required_etfs
        )
        if tuple(sorted(etf.required_for)) != tuple(sorted(derived)):
            raise ValueError(
                f"ETF {etf.symbol} required_for does not match the factors"
            )
    if not 1 <= COMPOSITE_MINIMUM_VALID_MODULES <= len(MODULES):
        raise ValueError("composite module floor is out of range")


__all__ = [
    "BREAKEVEN_TARGET_PERCENT",
    "COMPOSITE_MINIMUM_VALID_MODULES",
    "DAILY_MAX_STALE_CALENDAR_DAYS",
    "DAILY_MINIMUM_HISTORY",
    "ETF_MAX_STALE_TRADING_DAYS",
    "ETF_PROXIES",
    "ETF_SYMBOLS",
    "FACTORS",
    "FACTORS_BY_ID",
    "FACTOR_IDS_BY_MODULE",
    "FRED_SERIES",
    "FREQUENCY_RANK",
    "FUNDING_FRAGMENTATION_MINIMUM_SPREADS",
    "FUNDING_FRAGMENTATION_WINDOW",
    "FX_VOLATILITY_WINDOW",
    "LONG_MEDIAN_WINDOW",
    "MODULES",
    "MODULES_BY_ID",
    "MODULE_IDS",
    "NET_LIQUIDITY_MOMENTUM_WEEKS",
    "ON_RRP_FULL_BUFFER_BILLIONS",
    "REGIME_BANDS",
    "RELATIVE_RETURN_MINIMUM_OBSERVATIONS",
    "RELATIVE_RETURN_WINDOW_DAYS",
    "SCORE_WINDOW_YEARS",
    "SCORING_VERSION",
    "SERIES_BY_ID",
    "SHORT_VOLATILITY_WINDOW",
    "SOURCE_ATTRIBUTIONS",
    "TGA_MEDIAN_WEEKLY_OBSERVATIONS",
    "TRADING_DAYS_PER_YEAR",
    "WEEKLY_MAX_STALE_CALENDAR_DAYS",
    "WEEKLY_MINIMUM_HISTORY",
    "EtfSpec",
    "FactorSpec",
    "ModuleSpec",
    "SeriesSpec",
    "UnitsMismatch",
    "frequency_at_most",
    "regime_for",
    "scale_to_canonical",
    "validate_registry",
]
