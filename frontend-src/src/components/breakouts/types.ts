/**
 * 突破雷达页级类型与字典（api-contract.md BreakoutEvent 全字段）
 * API 层类型为精简形状；fixture/真实接口在运行时携带本文件定义的完整字段，
 * 页面经 breakoutsApi 获取后按此结构读取。
 */
import type { BreakoutEvent, BreakoutEventDetail, BreakoutSignal, BreakoutStatus } from '@/api/types';
import type { MacroFitDriver } from '@/lib/macroFit';
import { t } from '../../i18n/core.ts';

/* ---------------- 枚举（契约） ---------------- */
export type LifecycleState =
  | 'DISCOVERED' | 'WATCHING' | 'TRIGGERED' | 'CONFIRMED' | 'HOLDING'
  | 'RETESTING' | 'RETEST_HELD' | 'REACCELERATING' | 'EXTENDED' | 'FAILED' | 'EXPIRED';

export type SetupType =
  | 'DAILY_BASE_BREAKOUT' | 'OPENING_RANGE_BREAKOUT' | 'PREMARKET_GAP' | 'GAP_AND_GO' | 'GAP_HOLD'
  | 'GAP_FADE' | 'RETEST_BREAKOUT' | 'MOMENTUM_SPIKE' | 'RECOVERY_BREAKOUT';

export type BreakoutSession = 'premarket' | 'regular' | 'postmarket' | 'closed';

/* ---------------- 中文映射（契约 §2 枚举→中文） ---------------- */
export const LIFECYCLE_CN: Record<LifecycleState, string> = {
  DISCOVERED: t('已发现'),
  WATCHING: t('观察中'),
  TRIGGERED: t('已触发'),
  CONFIRMED: t('已确认'),
  HOLDING: t('持有中'),
  RETESTING: t('回踩中'),
  RETEST_HELD: t('回踩企稳'),
  REACCELERATING: t('再加速'),
  EXTENDED: t('过热延伸'),
  FAILED: t('已失效'),
  EXPIRED: t('已过期'),
};

export const SETUP_CN: Record<SetupType, string> = {
  DAILY_BASE_BREAKOUT: t('日线基底突破'),
  OPENING_RANGE_BREAKOUT: t('开盘区间突破'),
  PREMARKET_GAP: t('盘前跳空'),
  GAP_AND_GO: t('跳空续攻'),
  GAP_HOLD: t('跳空企稳'),
  GAP_FADE: t('跳空回落'),
  RETEST_BREAKOUT: t('回踩突破'),
  MOMENTUM_SPIKE: t('动量急拉'),
  RECOVERY_BREAKOUT: t('修复突破'),
};

export const SESSION_CN: Record<BreakoutSession, string> = {
  premarket: t('盘前'),
  regular: t('盘中'),
  postmarket: t('盘后'),
  closed: t('休市'),
};

export const LIFECYCLE_LIST = Object.keys(LIFECYCLE_CN) as LifecycleState[];
export const SETUP_LIST = Object.keys(SETUP_CN) as SetupType[];

/** 生命周期语义分组：brand=进行中 / up=走强 / down=失败归档 / ink=早期观察 */
export type LifecycleTone = 'brand' | 'up' | 'down' | 'ink';
export const LIFECYCLE_TONE: Record<LifecycleState, LifecycleTone> = {
  DISCOVERED: 'ink',
  WATCHING: 'ink',
  TRIGGERED: 'brand',
  CONFIRMED: 'brand',
  HOLDING: 'brand',
  RETESTING: 'brand',
  RETEST_HELD: 'up',
  REACCELERATING: 'up',
  EXTENDED: 'up',
  FAILED: 'down',
  EXPIRED: 'down',
};

export const LIFECYCLE_CHIP_CLASS: Record<LifecycleTone, string> = {
  brand: 'border-brand-400/60 bg-brand-50 text-brand-600',
  up: 'border-up-600/40 bg-up-50 text-up-700',
  down: 'border-down-600/40 bg-down-50 text-down-700',
  ink: 'border-line-strong bg-card-warm text-ink-500',
};

/* ---------------- 评分套组 ---------------- */
export const SCORE_DEFS = [
  { key: 'intrinsic_strength_score', label: t('内在强度') },
  { key: 'base_quality_score', label: t('基底质量') },
  { key: 'breakout_confirmation_score', label: t('突破确认') },
  { key: 'liquidity_quality_score', label: t('流动性') },
  { key: 'chase_risk_score', label: t('追高风险') },
  { key: 'sector_fit_score', label: t('板块契合') },
  { key: 'market_fit_score', label: t('市场契合') },
  { key: 'alert_priority_score', label: t('警示优先级') },
  { key: 'data_confidence_score', label: t('数据置信') },
] as const;
export type ScoreKey = (typeof SCORE_DEFS)[number]['key'];

/* ---------------- range_persistence 五维 ---------------- */
export interface RangePersistence {
  trend: number;
  hold: number;
  volatility: number;
  volume: number;
  participation: number;
}
/** 生产契约的真实区间持续指标；与早期演示五维不是同一口径。 */
export interface RangePersistenceLive {
  kind: 'live';
  value: number | null;
  slope5d: number | null;
  ratio10d: number | null;
  selfPercentile: number | null;
  globalPercentile: number | null;
  sectorPercentile: number | null;
  status: string;
}
export const RANGE_PERSISTENCE_DEFS = [
  { key: 'trend', label: t('趋势持续') },
  { key: 'hold', label: t('区间保持') },
  { key: 'volatility', label: t('波动稳定') },
  { key: 'volume', label: t('量能延续') },
  { key: 'participation', label: t('参与广度') },
] as const;

/* ---------------- 富事件形状 ---------------- */
export interface PriceZone {
  low: number;
  high: number;
}

export interface BreakoutTransition {
  state: LifecycleState;
  at: string;
  note?: string;
}

/** 契约 BreakoutEvent 全字段（历史事件 / 事件详情） */
export interface BreakoutEventFull extends BreakoutEventDetail {
  event_id: string;
  name: string;
  sector: string;
  session: BreakoutSession;
  setup_type: SetupType;
  lifecycle_state: LifecycleState;
  event_at: string;
  triggered_at: string;
  event_price: number;
  current_price: number;
  session_change_pct: number;
  gap_pct: number;
  rvol_time_of_day: number;
  pivot_price: number;
  target_price: number;
  support_zone: PriceZone;
  resistance_zone: PriceZone;
  invalidation_price: number;
  intrinsic_strength_score: number;
  base_quality_score: number;
  breakout_confirmation_score: number;
  liquidity_quality_score: number;
  chase_risk_score: number;
  sector_fit_score: number;
  market_fit_score: number;
  alert_priority_score: number;
  data_confidence_score: number;
  range_persistence: RangePersistence | RangePersistenceLive | null;
  transitions: BreakoutTransition[];
  /**
   * 宏观影子字段（macro-linkage-v1）。只标注提醒优先级，上限 ±4。
   *
   * 上面每一个质量分和事件生命周期都不受它影响 —— 宏观逆风不会删除、不会降级、
   * 也不会推迟一个真实发生的突破事件。null 表示没读到，macro_shadow_status 说明原因。
   */
  macro_fit_score: number | null;
  macro_tailwind: string | null;
  macro_priority_adjustment_shadow: number | null;
  alert_priority_macro_shadow: number | null;
  macro_shadow_status: string | null;
  macro_supporting_factors: MacroFitDriver[];
  macro_opposing_factors: MacroFitDriver[];
}

/** 当日信号（/breakouts/current 的 events[]，叠加 BreakoutSignal 展示字段） */
export type BreakoutCurrentEvent = BreakoutEventFull & BreakoutSignal;

/** /breakouts/status 契约全字段 */
export interface BreakoutStatusFull extends BreakoutStatus {
  enabled: boolean;
  worker: { healthy: boolean; heartbeat_at: string };
  latest_completed_scan: { at: string; duration_ms: number; scanned: number; triggered: number } | null;
  market_session: BreakoutSession | null;
  next_session_at: string | null;
}

/* ---------------- 读取辅助（宽松运行时字段 → 页级类型） ---------------- */
export function asCurrentEvents(data: BreakoutSignal[] | null): BreakoutCurrentEvent[] {
  return (data ?? []) as unknown as BreakoutCurrentEvent[];
}
export function asFullEvent(e: BreakoutEvent): BreakoutEventFull {
  return e as unknown as BreakoutEventFull;
}
export function asFullDetail(e: BreakoutEventDetail): BreakoutEventFull {
  return e as unknown as BreakoutEventFull;
}
export function asFullStatus(s: BreakoutStatus | null): BreakoutStatusFull | null {
  return (s ?? null) as BreakoutStatusFull | null;
}

/** §6-5 强度色阶；追高风险反向（越高越危险） */
export function scoreBarClass(score: number): string {
  if (score >= 85) return 'bg-up-600';
  if (score >= 70) return 'bg-brand-600';
  if (score >= 50) return 'bg-brand-400';
  return 'bg-ink-300';
}
export function riskBarClass(score: number): string {
  if (score >= 70) return 'bg-down-600';
  if (score >= 50) return 'bg-warn-600';
  return 'bg-ink-300';
}

/** 板块 → 雷达 12 槽位缩写（外环刻度文字） */
export const RADAR_SECTORS: { match: string[]; label: string }[] = [
  { match: [t('信息技术')], label: t('科技') },
  { match: [t('半导体')], label: t('半导体') },
  { match: [t('通信服务')], label: t('通信') },
  { match: [t('可选消费')], label: t('可选') },
  { match: [t('必需消费')], label: t('必需') },
  { match: [t('医疗保健')], label: t('医疗') },
  { match: [t('金融')], label: t('金融') },
  { match: [t('能源')], label: t('能源') },
  { match: [t('工业')], label: t('工业') },
  { match: [t('原材料')], label: t('材料') },
  { match: [t('公用事业')], label: t('公用') },
  { match: [], label: t('其他') },
];

export function sectorSlotIndex(sector: string): number {
  const i = RADAR_SECTORS.findIndex((s) => s.match.includes(sector));
  return i >= 0 ? i : RADAR_SECTORS.length - 1;
}

/** 事件评分主分（雷达半径 / 过滤 min 评分用内在强度分） */
export function primaryScore(e: Pick<BreakoutEventFull, 'intrinsic_strength_score'>): number {
  return e.intrinsic_strength_score;
}
