/** 校验后端 signal_analysis 结构；缺字段时不拼接任何替代结论。 */
import { t } from '../../i18n/core.ts';

export const FINAL_BIAS_VALUES = [
  'bullish_continuation',
  'healthy_rotation',
  'trend_pullback',
  'range_consolidation',
  'tactical_top_risk',
  'dip_buy_setup',
  'capitulation_bottom_setup',
  'bearish_breakdown',
  'insufficient_data',
] as const;

export type SignalFinalBias = (typeof FINAL_BIAS_VALUES)[number];

export const FLOW_DIRECTION_VALUES = [
  'bullish',
  'bearish',
  'mixed',
  'unknown',
] as const;

export type SignalFlowDirection = (typeof FLOW_DIRECTION_VALUES)[number];

export interface OptionsFlowRead {
  net_direction: SignalFlowDirection;
  confidence: number;
  bullish_flow_evidence: string[];
  bearish_flow_evidence: string[];
  unknown_or_neutral_flow: string[];
  warnings: string[];
}

export interface SignalKeyLevels {
  support: string[];
  resistance: string[];
  vwap_levels: string[];
  options_levels: string[];
}

export interface SignalAnalysisResult {
  output_language: 'zh-CN';
  asset: string;
  horizon: string;
  dominant_regime: string;
  trend_bias_confidence: number;
  top_risk_confidence: number;
  bottom_opportunity_confidence: number;
  dip_buy_quality: number;
  breakdown_risk: number;
  data_quality: number;
  final_bias: SignalFinalBias;
  top_evidence: string[];
  bottom_evidence: string[];
  dip_buy_evidence: string[];
  bearish_evidence: string[];
  contradictions: string[];
  options_flow_read: OptionsFlowRead;
  key_levels: SignalKeyLevels;
  confirmation_signals: string[];
  invalidation_signals: string[];
  event_risks: string[];
  data_quality_notes: string[];
  summary: string;
}

export const FINAL_BIAS_META: Record<
  SignalFinalBias,
  { label: string; tone: 'up' | 'down' | 'warn' | 'neutral' | 'ai' }
> = {
  bullish_continuation: { label: t('多头延续'), tone: 'up' },
  healthy_rotation: { label: t('健康轮动'), tone: 'ai' },
  trend_pullback: { label: t('趋势回撤'), tone: 'warn' },
  range_consolidation: { label: t('区间整理'), tone: 'neutral' },
  tactical_top_risk: { label: t('战术见顶风险'), tone: 'warn' },
  dip_buy_setup: { label: t('逢低买入结构'), tone: 'up' },
  capitulation_bottom_setup: { label: t('恐慌见底结构'), tone: 'up' },
  bearish_breakdown: { label: t('空头破位'), tone: 'down' },
  insufficient_data: { label: t('数据不足'), tone: 'neutral' },
};

export const FLOW_DIRECTION_META: Record<
  SignalFlowDirection,
  { label: string; tone: 'up' | 'down' | 'warn' | 'neutral' }
> = {
  bullish: { label: t('偏多'), tone: 'up' },
  bearish: { label: t('偏空'), tone: 'down' },
  mixed: { label: t('多空混合'), tone: 'warn' },
  unknown: { label: t('方向未知'), tone: 'neutral' },
};

function recordOf(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function nonemptyText(value: unknown): string | null {
  return typeof value === 'string' && value.trim() ? value.trim() : null;
}

function score100(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 100
    ? value
    : null;
}

function textList(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  const items = value.map(nonemptyText);
  return items.some((item) => item === null) ? null : (items as string[]);
}

function parseFlow(value: unknown): OptionsFlowRead | null {
  const record = recordOf(value);
  if (!record) return null;
  const direction = record.net_direction;
  const confidence = score100(record.confidence);
  const bullish = textList(record.bullish_flow_evidence);
  const bearish = textList(record.bearish_flow_evidence);
  const unknown = textList(record.unknown_or_neutral_flow);
  const warnings = textList(record.warnings);
  if (
    !FLOW_DIRECTION_VALUES.includes(direction as SignalFlowDirection) ||
    confidence === null ||
    !bullish ||
    !bearish ||
    !unknown ||
    !warnings
  ) {
    return null;
  }
  return {
    net_direction: direction as SignalFlowDirection,
    confidence,
    bullish_flow_evidence: bullish,
    bearish_flow_evidence: bearish,
    unknown_or_neutral_flow: unknown,
    warnings,
  };
}

function parseLevels(value: unknown): SignalKeyLevels | null {
  const record = recordOf(value);
  if (!record) return null;
  const support = textList(record.support);
  const resistance = textList(record.resistance);
  const vwap = textList(record.vwap_levels);
  const options = textList(record.options_levels);
  if (!support || !resistance || !vwap || !options) return null;
  return {
    support,
    resistance,
    vwap_levels: vwap,
    options_levels: options,
  };
}

export function parseSignalAnalysisResult(result: unknown): SignalAnalysisResult | null {
  const record = recordOf(result);
  if (!record || record.output_language !== 'zh-CN') return null;
  const asset = nonemptyText(record.asset);
  const horizon = nonemptyText(record.horizon);
  const regime = nonemptyText(record.dominant_regime);
  const summary = nonemptyText(record.summary);
  const bias = record.final_bias;
  const flow = parseFlow(record.options_flow_read);
  const levels = parseLevels(record.key_levels);
  const scores = {
    trend_bias_confidence: score100(record.trend_bias_confidence),
    top_risk_confidence: score100(record.top_risk_confidence),
    bottom_opportunity_confidence: score100(record.bottom_opportunity_confidence),
    dip_buy_quality: score100(record.dip_buy_quality),
    breakdown_risk: score100(record.breakdown_risk),
    data_quality: score100(record.data_quality),
  };
  const lists = {
    top_evidence: textList(record.top_evidence),
    bottom_evidence: textList(record.bottom_evidence),
    dip_buy_evidence: textList(record.dip_buy_evidence),
    bearish_evidence: textList(record.bearish_evidence),
    contradictions: textList(record.contradictions),
    confirmation_signals: textList(record.confirmation_signals),
    invalidation_signals: textList(record.invalidation_signals),
    event_risks: textList(record.event_risks),
    data_quality_notes: textList(record.data_quality_notes),
  };
  if (
    !asset ||
    !horizon ||
    !regime ||
    !summary ||
    !FINAL_BIAS_VALUES.includes(bias as SignalFinalBias) ||
    !flow ||
    !levels ||
    Object.values(scores).some((value) => value === null) ||
    Object.values(lists).some((value) => value === null)
  ) {
    return null;
  }
  return {
    output_language: 'zh-CN',
    asset: asset.toUpperCase(),
    horizon,
    dominant_regime: regime,
    trend_bias_confidence: scores.trend_bias_confidence as number,
    top_risk_confidence: scores.top_risk_confidence as number,
    bottom_opportunity_confidence: scores.bottom_opportunity_confidence as number,
    dip_buy_quality: scores.dip_buy_quality as number,
    breakdown_risk: scores.breakdown_risk as number,
    data_quality: scores.data_quality as number,
    final_bias: bias as SignalFinalBias,
    top_evidence: lists.top_evidence as string[],
    bottom_evidence: lists.bottom_evidence as string[],
    dip_buy_evidence: lists.dip_buy_evidence as string[],
    bearish_evidence: lists.bearish_evidence as string[],
    contradictions: lists.contradictions as string[],
    options_flow_read: flow,
    key_levels: levels,
    confirmation_signals: lists.confirmation_signals as string[],
    invalidation_signals: lists.invalidation_signals as string[],
    event_risks: lists.event_risks as string[],
    data_quality_notes: lists.data_quality_notes as string[],
    summary,
  };
}
