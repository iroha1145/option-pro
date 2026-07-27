/**
 * 财报页共享类型与日期工具（美东 ET 基准）
 * 扩展字段对齐后端契约（eps_high/eps_low/market_cap/sector/days_until），
 * api/types 为精简子集 —— mock 经 fixtures2 扩展下发；live 缺失时一律留空「—」。
 */
import type { EarningsImpact, EarningsItem } from '@/api/types';
import { t, getLocale } from '../../i18n/core.ts';

/** upcoming 行（契约扩展字段可选，兼容 snake_case 下发） */
export interface EarningsRow extends EarningsItem {
  epsHigh?: number | null;
  epsLow?: number | null;
  marketCap?: number | null;
  sector?: string;
  expectedMovePct?: number | null;
  impactReady?: boolean;
  /** 后端公共重点标注（市值门槛 / 公共关注池）；账号自选在前端合并 */
  publicFeatured?: boolean;
}

/** 列表模式：重点公司（默认）/ 全部公司 */
export type EarningsListMode = 'featured' | 'all';

/**
 * 重点公司 = 后端公共标注（市值 ≥ 门槛 或 公共关注池）∪ 当前账号自选。
 * market_cap 缺失表示 unknown，绝不当小公司证据——unknown 行不因此进入
 * 也不因此被排除，一切以显式标注与账号自选为准。
 */
export function isFeaturedRow(
  row: EarningsRow,
  personalTickers: ReadonlySet<string>,
): boolean {
  if (exBool(row, 'publicFeatured') === true) return true;
  return personalTickers.has(row.ticker.toUpperCase());
}

/** 默认选中策略（原 Earnings.tsx pickDefault，抽出以便按模式复用与测试） */
export function pickDefaultEarningsRow(rows: EarningsRow[]): EarningsRow | null {
  return (
    rows.find((it) => daysUntil(it.date) === 0 && typeof it.epsActual === 'number')
    ?? rows.find((it) => daysUntil(it.date) === 0)
    ?? rows.find((it) => daysUntil(it.date) > 0)
    ?? rows[0]
    ?? null
  );
}

export interface EarningsListState {
  /** 周历/月历/密度条/全部统计恒用完整 allItems——模式只过滤列表 */
  calendarItems: EarningsRow[];
  /** 当前日期范围（选中日 或 -3..+30 滚动窗口）内的全部行 */
  baseItems: EarningsRow[];
  featuredCount: number;
  allCount: number;
  /** 模式过滤后的列表行 */
  listItems: EarningsRow[];
  /** 渐进挂载切片；选中行若在 listItems 内则强制挂载可见 */
  visibleItems: EarningsRow[];
}

/**
 * 财报列表的派生状态（纯函数：页面与行为测试共用同一实现）。
 * - 两种模式共享同一 baseItems；计数按当前日期范围计算，与列表范围一致；
 * - selectedTicker 属于 listItems 但被渐进额度挤出时，强制并入 visibleItems
 *  （保持原始顺序），保证「点击后行已挂载并可见」。
 */
export function computeEarningsListState(input: {
  items: EarningsRow[];
  selectedDay: string | null;
  mode: EarningsListMode;
  personalTickers: ReadonlySet<string>;
  visibleLimit: number;
  selectedTicker: string | null;
}): EarningsListState {
  const { items, selectedDay, mode, personalTickers, visibleLimit, selectedTicker } = input;
  const baseItems = selectedDay
    ? items.filter((it) => it.date === selectedDay)
    : items.filter((it) => {
        const distance = daysUntil(it.date);
        return distance >= -3 && distance <= 30;
      });
  const featuredItems = baseItems.filter((it) => isFeaturedRow(it, personalTickers));
  const listItems = mode === 'featured' ? featuredItems : baseItems;
  let visibleItems = prioritizeEarningsRows(listItems, visibleLimit);
  if (
    selectedTicker
    && listItems.some((it) => it.ticker === selectedTicker)
    && !visibleItems.some((it) => it.ticker === selectedTicker)
  ) {
    const visibleSet = new Set(visibleItems);
    visibleItems = listItems.filter(
      (it) => visibleSet.has(it) || it.ticker === selectedTicker,
    );
  }
  return {
    calendarItems: items,
    baseItems,
    featuredCount: featuredItems.length,
    allCount: baseItems.length,
    listItems,
    visibleItems,
  };
}

/**
 * 点击某只股票时的模式流转：重点模式下点到非重点公司 → 自动切「全部公司」，
 * 不认识的代码（不在 allItems 里）保持现状，交由调用方照常选中。
 */
export function planTickerSelection(input: {
  items: EarningsRow[];
  mode: EarningsListMode;
  personalTickers: ReadonlySet<string>;
  ticker: string;
}): { mode: EarningsListMode; switched: boolean } {
  const { items, mode, personalTickers, ticker } = input;
  if (mode === 'all') return { mode, switched: false };
  const row = items.find((it) => it.ticker === ticker);
  if (row && !isFeaturedRow(row, personalTickers)) {
    return { mode: 'all', switched: true };
  }
  return { mode, switched: false };
}

/**
 * 切回「重点公司」时的孤儿处理：当前选中不是重点公司 → 换成重点列表的
 * 默认候选（选中日优先，其次滚动窗口），没有候选则清空选择。
 * 绝不留下「selectedTicker 存在但列表行不存在」的状态。
 */
export function resolveFeaturedSelection(input: {
  items: EarningsRow[];
  selectedDay: string | null;
  personalTickers: ReadonlySet<string>;
  selectedTicker: string | null;
}): string | null {
  const { items, selectedDay, personalTickers, selectedTicker } = input;
  if (selectedTicker === null) return null;
  const current = items.find((it) => it.ticker === selectedTicker);
  if (current && isFeaturedRow(current, personalTickers)) return selectedTicker;
  const { listItems } = computeEarningsListState({
    items,
    selectedDay,
    mode: 'featured',
    personalTickers,
    visibleLimit: Number.MAX_SAFE_INTEGER,
    selectedTicker: null,
  });
  return pickDefaultEarningsRow(listItems)?.ticker ?? null;
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
  if (typeof v !== 'number' || !Number.isFinite(v)) return null;
  return camel === 'marketCap' && v <= 0 ? null : v;
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
  camel: 'impactReady' | 'locked' | 'final' | 'finalizationInProgress' | 'publicFeatured',
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

/** 今日（ET）YYYY-MM-DD。
 * 按秒记忆化：daysUntil 在筛选/排序里逐行调用，Intl.format 每次 ~0.03ms，
 * 4000 行的派生状态会烧掉数百毫秒——同一秒内答案不会变。 */
let etTodayCache: { at: number; value: string } | null = null;
export function etToday(): string {
  const at = Date.now();
  if (etTodayCache && at - etTodayCache.at < 1000) return etTodayCache.value;
  const value = etFmt.format(new Date(at));
  etTodayCache = { at, value };
  return value;
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

export const WEEK_CN = [t('周日'), t('周一'), t('周二'), t('周三'), t('周四'), t('周五'), t('周六')] as const;

/** '2024-11-13' → '11/13'（Mono 用） */
export function fmtMMDD(date: string): string {
  return `${Number(date.slice(5, 7))}/${Number(date.slice(8, 10))}`;
}

/**
 * '2024-11-13' → 各语言的自然月日写法（en「11/13」· ja「11月13日」· zh「11 月 13 日」）。
 * 函数名保留 CN 后缀是历史原因；三种写法互不通用，不能共享模板（同 HistoryRail.dayLabel）。
 */
export function fmtMDCN(date: string): string {
  const mo = Number(date.slice(5, 7));
  const day = Number(date.slice(8, 10));
  const locale = getLocale();
  if (locale === 'en') return `${mo}/${day}`;
  if (locale === 'ja') return `${mo}月${day}日`;
  return `${mo} 月 ${day} 日`;
}

/** 周X（按 UTC 正午锚定） */
export function weekdayCN(date: string): string {
  return WEEK_CN[new Date(`${date}T12:00:00Z`).getUTCDay()];
}

/** 相对日标签：今天/明天/后天/周X */
export function relativeDayCN(date: string): string {
  const d = daysUntil(date);
  if (d === 0) return t('今天');
  if (d === 1) return t('明天');
  if (d === 2) return t('后天');
  if (d < 0) return t('{n} 天前', { n: -d });
  return weekdayCN(date);
}
