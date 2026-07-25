/**
 * 选股扫描页 · 共享类型与常量（screener.md / api-contract.md strength 字段对照）
 * - 分档：S≥90 / A 80–89 / B 70–79 / C 60–69（D<60 仅计入「全部」）
 * - 周期 short/mid/long/all · 偏好 conservative/balanced/aggressive（契约枚举）
 */
import type { ScreenerRow, ScreenerSubscoreDim } from '@/api/types';

export type Tier = 'S' | 'A' | 'B' | 'C' | 'D';
export type TierFilter = 'all' | 'S' | 'A' | 'B' | 'C';
export type Timeframe = 'short' | 'mid' | 'long' | 'all';
export type ProfilePref = 'conservative' | 'balanced' | 'aggressive';
export type SortMode = 'deterministic' | 'latest' | 'impact';

export const TIMEFRAME_CN: Record<Timeframe, string> = {
  short: '短期',
  mid: '中期',
  long: '长期',
  all: '全部周期',
};

export const PROFILE_CN: Record<ProfilePref, string> = {
  conservative: '稳健',
  balanced: '均衡',
  aggressive: '进取',
};

export const SORT_CN: Record<SortMode, string> = {
  deterministic: '确定性',
  latest: '最新催化',
  impact: '影响力',
};

export function tierOf(score: number): Tier {
  if (score >= 90) return 'S';
  if (score >= 80) return 'A';
  if (score >= 70) return 'B';
  if (score >= 60) return 'C';
  return 'D';
}

/** 分档计数（D 档仅计入 all） */
export function countByTier(scores: number[]): Record<TierFilter, number> {
  const counts: Record<TierFilter, number> = { all: scores.length, S: 0, A: 0, B: 0, C: 0 };
  scores.forEach((s) => {
    const t = tierOf(s);
    if (t !== 'D') counts[t] += 1;
  });
  return counts;
}

export const TIER_RANGE: Record<Tier, string> = {
  S: '≥90',
  A: '80–89',
  B: '70–79',
  C: '60–69',
  D: '<60',
};

/**
 * 选股结果强度条的视觉分档。
 * A 档在 85 分处细分颜色与文案，避免默认 Top 20 高分聚集时整页只有一种颜色；
 * 对外仍属于 A 档，且以固定分数区间着色，不随当前结果集或涨跌方向漂移。
 */
export type ScreenerStrengthTone = 's' | 'a-high' | 'a' | 'b' | 'c' | 'd';

export interface ScreenerStrengthPresentation {
  band: Tier;
  tone: ScreenerStrengthTone;
  label: string;
  barClass: string;
  textClass: string;
}

export function screenerStrengthPresentation(score: number): ScreenerStrengthPresentation {
  if (score >= 90) {
    return { band: 'S', tone: 's', label: '顶尖', barClass: 'bg-up-700', textClass: 'text-up-700' };
  }
  if (score >= 85) {
    return { band: 'A', tone: 'a-high', label: '极强', barClass: 'bg-up-600', textClass: 'text-up-700' };
  }
  if (score >= 80) {
    return { band: 'A', tone: 'a', label: '强势', barClass: 'bg-brand-600', textClass: 'text-brand-700' };
  }
  if (score >= 70) {
    return { band: 'B', tone: 'b', label: '较强', barClass: 'bg-brand-400', textClass: 'text-brand-700' };
  }
  if (score >= 60) {
    return { band: 'C', tone: 'c', label: '观察', barClass: 'bg-warn-600', textClass: 'text-warn-600' };
  }
  return { band: 'D', tone: 'd', label: '偏弱', barClass: 'bg-ink-300', textClass: 'text-ink-600' };
}

/** 筛选条件（draft = 工作台编辑中；applied = 上次扫描快照） */
export interface ScanFilters {
  tier: TierFilter;
  timeframe: Timeframe;
  profile: ProfilePref;
  /** 返回条数上限；0 = 全部 */
  topN: number;
  sectors: string[];
  priceMin: number | null;
  priceMax: number | null;
  /** 成交额下限（美元）；0 = 不限 */
  minDollarVol: number;
  /** 强度分下限（由预设策略注入；null = 不限） */
  minScore: number | null;
  presetId: string | null;
}

/** 与后端每日真实强度快照的默认成交额门槛保持一致。 */
export const DEFAULT_MIN_DOLLAR_VOLUME = 10_000_000;
export const DEFAULT_MIN_PRICE = 5;
export const DEFAULT_TOP_N = 20;

export const DEFAULT_FILTERS: ScanFilters = {
  tier: 'all',
  timeframe: 'all',
  profile: 'balanced',
  topN: DEFAULT_TOP_N,
  sectors: [],
  priceMin: DEFAULT_MIN_PRICE,
  priceMax: null,
  minDollarVol: DEFAULT_MIN_DOLLAR_VOLUME,
  minScore: null,
  presetId: null,
};

/** 72h 窗口催化剂汇总（catalystsApi.batchSummaries72h 单次批量） */
export interface CatalystSummary {
  loaded: boolean;
  count: number;
  pos: number;
  neg: number;
  neu: number;
  latestAt: string | null;
  latestTitle: string | null;
  /** 契约 has_more（limit=5 截断）：true 时徽标显「5+」 */
  hasMore?: boolean;
  /** 契约 summary.pending（待分析计数）；mock 无此概念 → 不填 */
  pending?: number | null;
  /** 批量接口失败：如实显「—」（区别于真实 0） */
  failed?: boolean;
  /**
   * 本条摘要的取得时刻（审计 P2-12）。
   *
   * 旧实现只看键是否存在，成功、真实为 0、失败三种结果都会永久占住该键：当天
   * 新出现的新闻不会进入已经看过的股票，除非整页刷新。有了时间戳就能按 TTL 过期。
   */
  fetchedAt?: number;
}

/** 催化摘要有效期；超过即视为需要重取。 */
export const CATALYST_SUMMARY_TTL_MS = 5 * 60_000;

export function catalystSummaryUsable(
  summary: CatalystSummary | undefined,
  now: number,
): boolean {
  if (!summary?.loaded) return false;
  // 失败的条目不该占住键位阻止重试。
  if (summary.failed) return false;
  return (
    summary.fetchedAt !== undefined
    && now - summary.fetchedAt < CATALYST_SUMMARY_TTL_MS
  );
}

export const EMPTY_CATALYST: CatalystSummary = {
  loaded: false,
  count: 0,
  pos: 0,
  neg: 0,
  neu: 0,
  latestAt: null,
  latestTitle: null,
};

/** 成交额缓存（后端 avg_dollar_volume_20d）；dollarVolume null = 契约缺失 */
export type DetailCache = Record<string, { dollarVolume: number | null } | undefined>;

/** 扫描历史条目（页头 popover 最近 5 次） */
export interface ScanHistoryEntry {
  at: number;
  count: number;
  durationMs: number;
  summary: string;
}

/** 分项元数据（mock ScreenerRow.subscores 键 → 中文名；live 行自带 subscoreDims 真实标签） */
export const SUBSCORE_META: { key: keyof ScreenerRow['subscores']; label: string }[] = [
  { key: 'trend', label: '趋势' },
  { key: 'momentum', label: '动量' },
  { key: 'volume', label: '量能' },
  { key: 'volatility', label: '波动' },
];

/**
 * 行分项统一读取：live 优先契约 subscoreDims（短期/中期/长期/突破质量，值可为 null），
 * mock 回退 subscores 四维（趋势/动量/量能/波动）。行内与展开区共用，保证两处同源。
 */
export function subscoreDimsOf(row: ScreenerRow): ScreenerSubscoreDim[] {
  if (row.subscoreDims && row.subscoreDims.length > 0) return row.subscoreDims;
  return SUBSCORE_META.map(({ key, label }) => ({ key, label, value: row.subscores[key] }));
}

export const TOPN_OPTIONS: { value: number; label: string }[] = [
  { value: 10, label: 'Top 10' },
  { value: 20, label: 'Top 20' },
  { value: 40, label: 'Top 40' },
  { value: 0, label: '最多 120' },
];

export const DOLLAR_VOL_OPTIONS: { value: number; label: string }[] = [
  { value: 0, label: '不限成交额' },
  { value: DEFAULT_MIN_DOLLAR_VOLUME, label: '≥ 10M' },
  { value: 5e8, label: '≥ 500M' },
  { value: 1e9, label: '≥ 1B' },
  { value: 3e9, label: '≥ 3B' },
  { value: 8e9, label: '≥ 8B' },
];
