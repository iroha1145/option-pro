"""CTA 趋势资金估算的集中配置（唯一参数来源，带版本号）。

研究依据（模型群设计，不模仿任何单一机构）：
- Moskowitz / Ooi / Pedersen (2012) "Time Series Momentum"：趋势信号 =
  多周期时序收益方向，仓位按已实现波动率定标；
- Baz et al. (2015) "Dissecting Investment Strategies ..."：EWMA 快慢对
  (8/24, 16/48, 32/96) 的三速趋势刻画；
- Hurst / Ooi / Pedersen "A Century of Evidence on Trend-Following"：
  1/3/12 个月混合的稳健性；
- Donchian 通道（海龟 20/55）：区间突破分量；
- 波动率目标（MOP 2012；Moreira & Muir 2017）：目标风险 ÷ 已实现波动，
  设上下限——本实现只去杠杆不加杠杆（scalar ≤ 1），标准化仓位天然落在
  [-100, +100]，不需要事后截断（截断会破坏资金流的恒等拆分）。

语义红线：这是**代理估算**——不同 CTA 的真实规则各不相同，本模型不声称
知道任何机构的实际算法，也不输出美元流量；改任何参数必须 bump
METHOD_VERSION，快照 schema 随之换代，新旧估算不得混排。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# v2（2026-08-09，GPT-5.6-Pro 审计）：下方触发区 Δ 改按下行穿越方向计算、
# 区间垫衬钳位在现价一侧、距离按最近边界、权重按 (model,component) 去重、
# 标签跟随净变化方向（新增趋势-波动率冲突键）、新增 trend_strength /
# active_model_weight。版本写入快照 parameters，旧快照按参数不匹配自动失效。
# v3（2026-08-09，GPT-5.6-Pro 审计二轮）：触发区标签改按区间前后真实状态
# 迁移生成（下发 position/trend_before/after；「翻空/翻多」必须真穿越中性带，
# 撤销 rank 深度标签「加速/进一步翻空」）；聚簇保留底层事件类型，kind 细分
# trend_cross / trend_saturation / trend_cross_and_saturation（饱和上沿不再
# 冒充「翻转」）；冲突键按趋势是否过零拆 firming/fading 变体；新增最近原始
# 断点距离 nearest_event_distance_pct 与 aligned/active_models 同向计数。
METHOD_VERSION = "cta-proxy-v3"

# ── 标的：首版 ETF 代理（供应商无期货连续合约能力，见模块 docstring） ──


@dataclass(frozen=True)
class CtaInstrument:
    key: str
    label: str            # 中文显示名（前端过 t()）
    proxy_symbol: str     # 实际计算用的代理代码
    proxy_type: str       # "etf"；将来接期货连续合约时扩展 "futures"
    index_symbol: str     # 对应指数（仅展示关联，不参与计算）


INSTRUMENTS: tuple[CtaInstrument, ...] = (
    CtaInstrument("sp500", "标普 500", "SPY", "etf", "^GSPC"),
    CtaInstrument("nasdaq100", "纳斯达克 100", "QQQ", "etf", "^NDX"),
    CtaInstrument("russell2000", "罗素 2000", "IWM", "etf", "^RUT"),
    CtaInstrument("dow", "道琼斯", "DIA", "etf", "^DJI"),
)

# ── 三速模型群 ───────────────────────────────────────────────
#
# 每个分量输出 [-1, +1] 的连续信号（分段线性：过零点线性爬坡、±1σ 饱和），
# 子模型 = 分量加权和，总趋势信号 = 子模型加权和。全部对「假设收盘价 P」
# 是分段线性函数——翻转价与饱和价可解析求出，触发位不靠人手写百分比。


@dataclass(frozen=True)
class ComponentSpec:
    kind: str             # "tsmom" | "ewma" | "donchian"
    weight: float
    # tsmom: horizon 个交易日的收益方向；ewma: fast/slow 跨度；donchian: 通道窗口
    horizon: int = 0
    fast: int = 0
    slow: int = 0
    window: int = 0


@dataclass(frozen=True)
class SubmodelSpec:
    key: str              # "fast" | "medium" | "slow"
    label: str
    weight: float
    components: tuple[ComponentSpec, ...] = field(default_factory=tuple)


SUBMODELS: tuple[SubmodelSpec, ...] = (
    SubmodelSpec(
        "fast", "快速（≈1 个月）", 0.30,
        (
            ComponentSpec("tsmom", 0.40, horizon=21),
            ComponentSpec("ewma", 0.40, fast=8, slow=24),
            ComponentSpec("donchian", 0.20, window=20),
        ),
    ),
    SubmodelSpec(
        "medium", "中速（≈3 个月）", 0.40,
        (
            ComponentSpec("tsmom", 0.40, horizon=63),
            ComponentSpec("ewma", 0.40, fast=16, slow=48),
            ComponentSpec("donchian", 0.20, window=55),
        ),
    ),
    SubmodelSpec(
        "slow", "慢速（≈12 个月）", 0.30,
        (
            ComponentSpec("tsmom", 0.50, horizon=252),
            ComponentSpec("ewma", 0.50, fast=32, slow=96),
        ),
    ),
)

# 信号饱和刻度：TSMOM 在 |收益| = SATURATION_SIGMA × σ×√h 处到 ±1；
# EWMA 差在 EWMA_SATURATION_SIGMA × σ×√(slow) × price 处到 ±1。
SATURATION_SIGMA = 1.0
EWMA_SATURATION_SIGMA = 0.5

# ── 波动率目标 ───────────────────────────────────────────────

TARGET_VOL_ANNUAL = 0.15          # 目标年化波动（可配置，非模仿某机构）
VOL_EWMA_LAMBDA = 0.94            # RiskMetrics 日频衰减
VOL_WARMUP_DAYS = 63              # EWMA 方差种子窗口
VOL_SCALAR_FLOOR = 0.25           # 极端高波动时最少保留的敞口比例
VOL_SCALAR_CAP = 1.0              # 只去杠杆不加杠杆（见模块 docstring）
VOL_ANNUALIZE = 252
# 单日收益稳健截断：|r| 超过该值按该值参与方差更新（数据坏点/极端跳变防御），
# 原始收益仍如实进入趋势分量——趋势要看见真实暴跌，方差不被单点炸穿。
VOL_RETURN_CLAMP = 0.20

# ── 状态与流阈值（0-100 标准化仓位刻度） ─────────────────────

POSITION_STRONG = 60.0            # |position| ≥ 60 → 强净多/强净空
POSITION_NEUTRAL_BAND = 15.0      # |position| < 15 → 分歧/中性带
FLOW_EPS = 3.0                    # |flow| < 3 视为无边际变化
AGREEMENT_DIVERGENT = 0.55        # 加权同向占比低于此值 → 模型分歧
SUBMODEL_ACTIVE_EPS = 0.10        # 子模型 |signal| ≤ 0.1 视为不表态

# ── 情景网格与触发聚簇 ───────────────────────────────────────

SCENARIO_SPAN_PCT = 0.12          # 当前价 ±12%
SCENARIO_STEPS = 97               # 网格点数（奇数，含当前价）
TRIGGER_CLUSTER_ATR = 0.40        # 相邻触发事件按 0.4×ATR 聚簇成区间
TRIGGER_MIN_DELTA = 2.0           # 区间内预计仓位变化 < 2 分不单列
TRIGGER_MAX_ZONES_PER_SIDE = 3    # 每侧最多呈现的触发区数
ATR_WINDOW = 14

# ── 数据要求 ─────────────────────────────────────────────────

# 最慢分量 252 + EWMA 慢线 96 预热 + 波动种子 63，留余量。
MIN_BARS_REQUIRED = 380
HISTORY_POINTS = 120              # 快照附带的逐日仓位回放长度（点检稳定性）

__all__ = [
    "AGREEMENT_DIVERGENT",
    "ATR_WINDOW",
    "ComponentSpec",
    "CtaInstrument",
    "EWMA_SATURATION_SIGMA",
    "FLOW_EPS",
    "HISTORY_POINTS",
    "INSTRUMENTS",
    "METHOD_VERSION",
    "MIN_BARS_REQUIRED",
    "POSITION_NEUTRAL_BAND",
    "POSITION_STRONG",
    "SATURATION_SIGMA",
    "SCENARIO_SPAN_PCT",
    "SCENARIO_STEPS",
    "SUBMODELS",
    "SUBMODEL_ACTIVE_EPS",
    "SubmodelSpec",
    "TARGET_VOL_ANNUAL",
    "TRIGGER_CLUSTER_ATR",
    "TRIGGER_MAX_ZONES_PER_SIDE",
    "TRIGGER_MIN_DELTA",
    "VOL_ANNUALIZE",
    "VOL_EWMA_LAMBDA",
    "VOL_RETURN_CLAMP",
    "VOL_SCALAR_CAP",
    "VOL_SCALAR_FLOOR",
    "VOL_WARMUP_DAYS",
]
