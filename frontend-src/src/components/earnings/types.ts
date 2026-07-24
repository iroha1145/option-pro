/**
 * 财报页共享类型与日期工具（美东 ET 基准）
 * 扩展字段对齐后端契约（eps_high/eps_low/market_cap/sector/days_until），
 * api/types 为精简子集 —— mock 经 fixtures2 扩展下发；live 缺失时一律留空「—」。
 */
import type { EarningsImpact, EarningsItem } from '@/api/types';

/** upcoming 行（契约扩展字段可选，兼容 snake_case 下发） */
export interface EarningsRow extends EarningsItem {
  epsHigh?: number | null;
  epsLow?: number | null;
  marketCap?: number | null;
  sector?: string;
  expectedMovePct?: number | null;
  impactReady?: boolean;
}

/** AI 影响结果（扩展：生成时间 / 历史均值 / 置信度） */
export interface EarningsImpactResult extends EarningsImpact {
  generatedAt?: string;
  histAvgMovePct?: number | null;
  confidence?: number | null;
}

/** 读取扩展字段：camelCase 优先，兼容 live 的 snake_case；缺失返回 null（留空纪律） */
export function exNum(
  row: EarningsRow | EarningsImpactResult,
  camel: 'epsHigh' | 'epsLow' | 'marketCap' | 'expectedMovePct' | 'histAvgMovePct' | 'confidence' | 'year' | 'quarter',
): number | null {
  const r = row as unknown as Record<string, unknown>;
  const snake = camel.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
  const v = r[camel] ?? r[snake];
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}
export function exStr(
  row: EarningsRow,
  camel: 'sector' | 'releaseStatus' | 'analysisStage' | 'reportId',
): string | null {
  const record = row as unknown as Record<string, unknown>;
  const snake = camel.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
  const v = record[camel] ?? record[snake] ?? record[`_${snake}`];
  return typeof v === 'string' && v ? v : null;
}
export function exBool(
  row: EarningsRow,
  camel: 'impactReady' | 'locked' | 'final' | 'finalizationInProgress',
): boolean | null {
  const record = row as unknown as Record<string, unknown>;
  const snake = camel.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);
  const v = record[camel] ?? record[snake] ?? record[`_${snake}`];
  return typeof v === 'boolean' ? v : null;
}

/**
 * 在有限的首屏额度里同时保留两类关键信息：
 * - 最近三天已经公布、带实际 EPS 的财报（大市值优先）；
 * - 今天及未来待公布的财报（日近、市值高者优先）。
 *
 * 最终仍按接口原顺序展示，避免日期分组被打散。额度增加时旧结果只会保留、
 * 不会被新结果替换；点选单日时调用方应直接展示该日完整列表。
 */
export function prioritizeEarningsRows(
  items: EarningsRow[],
  limit: number,
): EarningsRow[] {
  const boundedLimit = Math.max(0, Math.floor(limit));
  if (boundedLimit === 0) return [];
  if (items.length <= boundedLimit) return items;

  const released = items
    .filter((row) => daysUntil(row.date) <= 0 && Number.isFinite(row.epsActual))
    .sort((a, b) => {
      const marketCapDelta = (exNum(b, 'marketCap') ?? -1) - (exNum(a, 'marketCap') ?? -1);
      if (marketCapDelta !== 0) return marketCapDelta;
      const dateDelta = b.date.localeCompare(a.date);
      return dateDelta !== 0 ? dateDelta : a.ticker.localeCompare(b.ticker);
    });
  const releasedSet = new Set(released);
  const scheduled = items
    .filter((row) => !releasedSet.has(row) && daysUntil(row.date) >= 0)
    .sort((a, b) => {
      const aDistance = daysUntil(a.date);
      const bDistance = daysUntil(b.date);
      if (aDistance !== bDistance) return aDistance - bDistance;
      const marketCapDelta = (exNum(b, 'marketCap') ?? -1) - (exNum(a, 'marketCap') ?? -1);
      return marketCapDelta !== 0 ? marketCapDelta : a.ticker.localeCompare(b.ticker);
    });
  const scheduledSet = new Set(scheduled);
  const historicalMissingActual = items
    .filter((row) => !releasedSet.has(row) && !scheduledSet.has(row))
    .sort((a, b) => {
      const dateDelta = b.date.localeCompare(a.date);
      if (dateDelta !== 0) return dateDelta;
      const marketCapDelta = (exNum(b, 'marketCap') ?? -1) - (exNum(a, 'marketCap') ?? -1);
      return marketCapDelta !== 0 ? marketCapDelta : a.ticker.localeCompare(b.ticker);
    });

  const selected = new Set<EarningsRow>();
  let releasedIndex = 0;
  let scheduledIndex = 0;
  let historicalIndex = 0;
  while (selected.size < boundedLimit) {
    const releasedRow = released[releasedIndex++];
    const scheduledRow = scheduled[scheduledIndex++];
    if (releasedRow) selected.add(releasedRow);
    if (selected.size >= boundedLimit) break;
    if (scheduledRow) selected.add(scheduledRow);
    if (!releasedRow && !scheduledRow) break;
  }
  while (selected.size < boundedLimit) {
    const historicalRow = historicalMissingActual[historicalIndex++];
    if (!historicalRow) break;
    selected.add(historicalRow);
  }
  return items.filter((row) => selected.has(row));
}

/* ---------------- 美东日期 ---------------- */
const etFmt = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

/** 今日（ET）YYYY-MM-DD */
export function etToday(): string {
  return etFmt.format(new Date());
}

/** date('YYYY-MM-DD') → 距 ET 今日的天数（0=今天） */
export function daysUntil(date: string): number {
  const a = Date.parse(`${date}T00:00:00Z`);
  const b = Date.parse(`${etToday()}T00:00:00Z`);
  return Math.round((a - b) / 86_400_000);
}

/** 在 YYYY-MM-DD 上加减天数（UTC 正午锚定，规避 DST） */
export function addDays(date: string, n: number): string {
  const d = new Date(Date.parse(`${date}T12:00:00Z`) + n * 86_400_000);
  return d.toISOString().slice(0, 10);
}

/** 该日期所在周的周一（美东周历：周一→周日） */
export function weekStartMonday(date: string): string {
  const d = new Date(`${date}T12:00:00Z`);
  const dow = d.getUTCDay(); // 0=周日
  const back = dow === 0 ? 6 : dow - 1;
  return addDays(date, -back);
}

/** 本周 7 天（周一→周日） */
export function weekDays(monday: string): string[] {
  return Array.from({ length: 7 }, (_, i) => addDays(monday, i));
}

export const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'] as const;

/** '2024-11-13' → '11/13'（Mono 用） */
export function fmtMMDD(date: string): string {
  return `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
}

/** '2024-11-13' → '11月13日' */
export function fmtMDCN(date: string): string {
  return `${Number(date.slice(5, 7))}月${Number(date.slice(8, 10))}日`;
}

/** 周X（按 UTC 正午锚定） */
export function weekdayCN(date: string): string {
  return WEEK_CN[new Date(`${date}T12:00:00Z`).getUTCDay()];
}

/** 相对日标签：今天/明天/后天/周X */
export function relativeDayCN(date: string): string {
  const d = daysUntil(date);
  if (d === 0) return '今天';
  if (d === 1) return '明天';
  if (d === 2) return '后天';
  if (d < 0) return `${-d} 天前`;
  return weekdayCN(date);
}
