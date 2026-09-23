import { t } from '../i18n/core.ts';

export type DiagnosticProfile = 'conservative' | 'balanced' | 'aggressive';
export type DiagnosticTimeframe = 'short' | 'mid' | 'long';

export type DiagnosticRecord = Record<string, unknown>;

export interface SecurityDiagnosticPath extends DiagnosticRecord {
  theme_id?: string | null;
  algorithm_id?: string | null;
  sector_context?: string | null;
  name?: string | null;
  score?: number | null;
  status?: string | null;
  rejection_reasons?: string[] | null;
}

export interface SecurityDiagnostic extends DiagnosticRecord {
  ticker: string;
  requested_ticker?: string | null;
  profile: DiagnosticProfile;
  horizon: DiagnosticTimeframe;
  served_session?: string | null;
  data_status?: string | null;
  paths: SecurityDiagnosticPath[];
  display?: DiagnosticRecord | null;
  coverage?: DiagnosticRecord | null;
  weight_provenance_sources?: Record<string, DiagnosticRecord>;
  _stale?: boolean;
  historical_example?: boolean;
  synthetic?: boolean;
}

export const DIAGNOSTIC_FACTORS = ['T', 'M', 'S', 'R', 'B', 'P', 'V', 'G'] as const;

export function diagnosticRecord(value: unknown): DiagnosticRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as DiagnosticRecord : null;
}

export function diagnosticNumber(value: unknown, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : t('未提供');
}

export function diagnosticPercent(value: unknown, digits = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(digits)}%` : t('未提供');
}

export function pathStatus(status: unknown): string {
  if (status === 'eligible') return t('通过技术条件');
  if (status === 'watch') return t('观察，资格尚未核实');
  if (status === 'rejected') return t('未通过技术条件');
  return t('状态未提供');
}

export function diagnosticDataStatus(status: unknown): string {
  if (status === 'scored') return t('已生成路径诊断');
  if (status === 'data_insufficient') return t('数据不足，无法生成完整路径');
  if (status === 'out_of_scope') return t('本批次范围外');
  if (status === 'unknown_symbol') return t('证券目录中未找到该代码');
  if (status === 'diagnostics_unavailable' || status === 'variant_diagnostics_unavailable') return t('该批次诊断暂不可用');
  return t('诊断状态未提供');
}

const reasonLabels: Record<string, string> = {
  DOLLAR_LIQUIDITY_UNVERIFIED: t('成交额资格未核实，不能视为已通过流动性门'),
  VOLUME_SESSION_UNVERIFIED: t('成交量所属交易时段未核实'),
  ATR_REFERENCE_UNAVAILABLE: t('缺少波动幅度参照，无法判断波动门槛'),
  ADV_TOO_LOW: t('成交额低于要求'),
  HIGH_ATR: t('波动幅度超过门槛'),
  EXTENDED: t('价格离基准位置过远'),
  EXTENDED_ENTRY_WAIT: t('价格偏离基准过远，等待入场'),
  LOW_SCORE: t('技术分数未达到要求'),
  LOW_COVERAGE: t('可用因子不足'),
  MISSING_SCORE: t('缺少技术分数'),
  DATA_INSUFFICIENT: t('数据不足'),
  SHORT_HISTORY: t('历史交易日不足'),
  NOT_TRADABLE: t('当前不可交易'),
  HALTED_SESSION: t('该交易日停牌'),
  SETUP_NOT_MET: t('形态条件未满足'),
  INCOMPLETE_COMMON_INPUTS: t('通用条件所需数据不完整'),
  MISSING_T_BAR: t('缺少截止日价格数据'),
  LATE_SOURCE: t('数据源晚于截止时间'),
  SOURCE_UNAVAILABLE: t('数据源暂不可用'),
  BELOW_SMA50: t('尚未确认价格高于五十日均线'),
  BREAKOUT_TRACK_EXPIRED: t('突破确认的观察期已经结束'),
  BREAKOUT_UNCONFIRMED: t('突破后的连续收盘确认不足，或未守住阻力位'),
  DEPTH_OUT_OF_RANGE: t('回撤深度缺失，或超出半倍至主题上限的波动范围'),
  INCOMPLETE_DAILY_DATA: t('日线数据不完整'),
  LH_LL: t('更低高点与更低低点结构尚未修复'),
  LOW_ADV: t('二十日平均成交额低于所需门槛'),
  LOW_CLV: t('收盘位置指标低于当前形态要求'),
  LOW_EVENT_RVOL: t('突破首日相对成交量低于门槛'),
  LOW_PRICE: t('原始价格低于最低价格门槛'),
  LOW_RVOL: t('相对成交量缺失或低于零点八'),
  MISSING_FIRST_DAY_CLV: t('缺少突破首日收盘位置指标'),
  MISSING_FIRST_DAY_RVOL: t('缺少突破首日相对成交量'),
  MISSING_RESIDUAL: t('缺少残差动量数据'),
  MISSING_SESSION_BAR: t('缺少本次截止交易日的完整行情'),
  NONPOSITIVE_MOMENTUM: t('所选周期动量缺失或不高于零'),
  NONPOSITIVE_RESIDUAL: t('残差动量不高于零'),
  NOT_THROUGH_RESISTANCE: t('价格尚未越过阻力位'),
  NO_FROZEN_BASE: t('缺少已确认的底部形态'),
  NO_REBOUND: t('回撤后尚未出现反弹'),
  SMA50_SLOPE: t('五十日均线未确认较二十日前上行'),
  SUPPORT_BROKEN: t('支撑结构已经失效'),
  TOO_FAR_FROM_BASE: t('价格距底部平台超过两倍波动幅度'),
  TOO_FAR_FROM_MA: t('价格距均线超过主题上限'),
  TREND_DIRECTION: t('趋势方向条件未满足'),
  UNRESOLVED_UPTHRUST: t('冲高回落信号尚未解除'),
  WEAK_STRUCTURE: t('结构分数缺失或低于门槛'),
};

export function diagnosticReason(code: unknown): string {
  if (typeof code !== 'string' || !code) return t('原因未提供');
  const label = reasonLabels[code];
  return label ?? `${t('其他条件未通过')} (${code})`;
}

export function diagnosticCoverageStatus(code: unknown): string {
  if (code === 'ok') return t('当日日线完整');
  if (code === 'no_history') return t('未取得日线历史');
  if (code === 'invalid') return t('日线数据无效');
  if (code === 'missing_target_session' || code === 'missing_session') return t('缺少当日日线');
  if (code === 'NOT_IN_DIRECTORY') return t('不在当前证券目录');
  if (code === 'NOT_EVALUATED_IN_BATCH' || code === 'FULL_THEME_STATISTICS_UNAVAILABLE') return t('本批次尚无全成员统计');
  if (code === 'SCORE_UNAVAILABLE') return t('缺少技术分数');
  if (typeof code === 'string' && code.startsWith('excluded:')) return `${t('不属于当前扫描范围')} (${code.slice(9)})`;
  return diagnosticReason(code);
}

export function diagnosticFamily(code: unknown): string {
  const labels: Record<string, string> = {
    A_trend_quality: t('趋势质量'),
    B_confirmed_base_breakout: t('底部突破确认'),
    C_trend_pullback: t('趋势回撤'),
    D_residual_momentum: t('残差动量（风险调整）'),
  };
  return typeof code === 'string' && code ? (labels[code] ?? code) : t('未知家族');
}

export function diagnosticTheme(code: unknown): string {
  const labels: Record<string, string> = {
    all_market_stocks: t('全市场股票'),
    semiconductors: t('半导体'), software: t('软件基础设施'), ai_cloud: t('AI 与云'),
    biotech: t('生物技术'), healthcare: t('医疗保健'), consumer_electronics: t('消费电子'),
    automotive: t('汽车 / EV'), ev_supply: t('电动车供应链'), finance: t('大型银行'),
    fintech: t('金融科技'), retail: t('零售消费'), luxury: t('奢侈品'),
    media_streaming: t('媒体与流媒体'), social_internet: t('社交与互联网'),
    energy: t('能源'), utilities: t('电力公用'), defense_aero: t('国防航空'),
    airlines: t('航空运输'), real_estate: t('房地产'), crypto: t('加密相关'),
    china_adr: t('中概 ADR'), telecom: t('电信'), industrials: t('工业制造'),
    etfs: t('宽基 ETF'),
  };
  return typeof code === 'string' && code ? (labels[code] ?? code) : t('未知主题');
}

export function diagnosticTrack(code: unknown): string {
  if (code === 'PRICE_ONLY_DIAGNOSTIC') return t('仅价格诊断');
  if (code === 'D_MARKET_RESIDUAL_DIAGNOSTIC') return t('市场相对动量诊断');
  if (code === 'FULL_EIGHT_FACTOR') return t('完整八因子');
  return t('轨道未提供');
}

export function diagnosticAtrReference(source: unknown): string {
  if (source === 'unavailable' || source == null) return t('参照样本不足');
  if (source === 'legacy_all_tracks_industry') return t('旧版全轨道行业参照');
  if (source === 'legacy_all_tracks_missing_industry') return t('未分类证券合并参照（含股票与基金）');
  if (typeof source === 'string') {
    const [, kind] = source.split(':');
    if (kind === 'industry') return t('同类证券的行业参照');
    if (kind === 'parent') return t('同类证券的上级行业参照');
    if (kind === 'track') return t('同类证券的整体参照');
  }
  return t('参照来源未说明');
}

export function diagnosticAtrPolicy(policy: unknown): string {
  if (policy === 'track_liquid_v1') return t('按证券类型筛选可用样本，优先行业，再回退上级行业或全类');
  if (policy === 'legacy') return t('旧版行业参照规则');
  return t('参照规则未说明');
}

export function diagnosticDisplayReason(code: unknown): string {
  if (code === 'composite') return t('进入合格综合');
  if (code === 'observation_only') return t('进入技术观察');
  if (code === 'scored_but_rejected') return t('有技术分数，但未进入结果');
  return t('数据不足，未进入结果');
}
