/** 板块页面视图模型：只组合后端目录、强度聚合和 IV 横截面。 */
import type { IvRankRow } from '@/api/types';
import type {
  SectorCatalogItem,
  SectorIvRankingEnvelope,
  SectorPeriod,
  SectorSourceStatus,
  SectorStrengthEnvelope,
  SectorStrengthLeader,
  SectorStrengthRow,
} from '@/api/modules/sectors';
import { heatColor } from '@/lib/chart';
import type { MacroFitDriver } from '@/lib/macroFit';
import { t } from '../../i18n/core.ts';

export interface SectorVm {
  id: string;
  name: string;
  tickers: string[];
  memberCount: number;
  coveredCount: number | null;
  period: SectorPeriod;
  periodDays: number | null;
  avgReturn: number | null;
  avgReturn1mo: number | null;
  avgReturn3mo: number | null;
  avgReturn6mo: number | null;
  avgStrength: number | null;
  leaders: SectorStrengthLeader[];
  asOf: string | null;
  strengthCovered: boolean;
  /** 宏观适配（影子字段）：与 avgStrength 并列，不参与任何排序。null = 没读到。 */
  macroFit: number | null;
  macroTailwind: string | null;
  macroFitConfidence: number | null;
  macroSupporting: MacroFitDriver[];
  macroOpposing: MacroFitDriver[];
}

export interface IvRowVm {
  ticker: string;
  name: string;
  price: number | null;
  priceProvider: string | null;
  rank: number | null;
  atmIv: number | null;
  stale: boolean;
  asOf: string | null;
}

export interface IvMetaVm {
  status: SectorSourceStatus;
  stale: boolean;
  asOf: string | null;
  dataLimited: boolean;
  successCount: number | null;
  requestedCount: number | null;
  successRate: number | null;
  failedSymbols: string[];
  snapshotSource: string | null;
  snapshotOrigin: string | null;
  providers: string[];
}

const PERIOD_LABELS: Record<SectorPeriod, string> = {
  '1mo': t('1 个月'),
  '3mo': t('3 个月'),
  '6mo': t('6 个月'),
};

export function periodLabel(period: SectorPeriod): string {
  return PERIOD_LABELS[period];
}

function makeSectorVm(
  catalog: SectorCatalogItem | undefined,
  strength: SectorStrengthRow | undefined,
  envelope: SectorStrengthEnvelope,
): SectorVm {
  const tickers = catalog?.tickers ?? [];
  return {
    id: strength?.sectorId ?? catalog?.id ?? '',
    name: strength?.name ?? catalog?.name ?? strength?.sectorId ?? '',
    tickers,
    memberCount: tickers.length,
    coveredCount: strength?.count ?? null,
    period: strength?.period ?? envelope.period,
    periodDays: strength?.periodDays ?? envelope.periodDays,
    avgReturn: strength?.avgReturn ?? null,
    avgReturn1mo: strength?.avgReturn1mo ?? null,
    avgReturn3mo: strength?.avgReturn3mo ?? null,
    avgReturn6mo: strength?.avgReturn6mo ?? null,
    avgStrength: strength?.avgStrength ?? null,
    leaders: strength?.leaders ?? [],
    asOf: envelope.asOf,
    strengthCovered: strength !== undefined,
    macroFit: strength?.macroFit ?? null,
    macroTailwind: strength?.macroTailwind ?? null,
    macroFitConfidence: strength?.macroFitConfidence ?? null,
    macroSupporting: strength?.macroSupporting ?? [],
    macroOpposing: strength?.macroOpposing ?? [],
  };
}

/** 强度接口顺序优先；只有目录覆盖、暂无强度聚合的板块随后展示。 */
export function buildSectorViewModels(
  catalog: SectorCatalogItem[],
  envelope: SectorStrengthEnvelope,
): SectorVm[] {
  const catalogById = new Map(catalog.map((row) => [row.id, row]));
  const seen = new Set<string>();
  const result: SectorVm[] = [];
  for (const strength of envelope.sectors) {
    result.push(makeSectorVm(catalogById.get(strength.sectorId), strength, envelope));
    seen.add(strength.sectorId);
  }
  for (const row of catalog) {
    if (!seen.has(row.id)) result.push(makeSectorVm(row, undefined, envelope));
  }
  return result.filter((row) => row.id !== '' && row.name !== '');
}

export function normalizeIvRow(raw: IvRankRow): IvRowVm {
  return {
    ticker: raw.ticker.toUpperCase(),
    name: raw.name || raw.ticker.toUpperCase(),
    price:
      typeof raw.price === 'number' && Number.isFinite(raw.price)
        ? raw.price
        : null,
    priceProvider:
      typeof raw.priceProvider === 'string' && raw.priceProvider
        ? raw.priceProvider
        : null,
    rank:
      typeof raw.sectorIvRank === 'number' && Number.isFinite(raw.sectorIvRank)
        ? raw.sectorIvRank
        : null,
    atmIv:
      typeof raw.atmIvPercent === 'number' && Number.isFinite(raw.atmIvPercent)
        ? raw.atmIvPercent
        : null,
    stale: raw.stale === true,
    asOf: typeof raw.asOf === 'string' && raw.asOf ? raw.asOf : null,
  };
}

export function normalizeIvMeta(envelope: SectorIvRankingEnvelope | null): IvMetaVm {
  return {
    status: envelope?.sourceStatus ?? 'insufficient_data',
    stale: envelope?.stale ?? false,
    asOf: envelope?.asOf ?? null,
    dataLimited: envelope?.dataLimited ?? false,
    successCount: envelope?.successCount ?? null,
    requestedCount: envelope?.requestedCount ?? null,
    successRate: envelope?.successRate ?? null,
    failedSymbols: envelope?.failedSymbols ?? [],
    snapshotSource: envelope?.snapshotSource ?? null,
    snapshotOrigin: envelope?.snapshotOrigin ?? null,
    providers: envelope?.providers ?? [],
  };
}

type Rgb = [number, number, number];

function parseRgb(css: string): Rgb | null {
  const match = css.match(/rgb\((\d+),(\d+),(\d+)\)/);
  return match
    ? [Number(match[1]), Number(match[2]), Number(match[3])]
    : null;
}

function luminance([red, green, blue]: Rgb): number {
  return (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
}

export function heatTone(avgReturn: number): { bg: string; dark: boolean } {
  const bg = heatColor(avgReturn);
  const rgb = parseRgb(bg);
  return { bg, dark: rgb ? luminance(rgb) < 0.55 : false };
}

const IV_STOPS: { value: number; rgb: Rgb }[] = [
  { value: 0, rgb: [14, 159, 110] },
  { value: 50, rgb: [228, 233, 255] },
  { value: 100, rgb: [229, 72, 77] },
];

export function ivRankColor(rank: number): string {
  const clamped = Math.max(0, Math.min(100, rank));
  for (let index = 0; index < IV_STOPS.length - 1; index += 1) {
    const start = IV_STOPS[index];
    const end = IV_STOPS[index + 1];
    if (clamped >= start.value && clamped <= end.value) {
      const position = (clamped - start.value) / (end.value - start.value);
      const mixed = start.rgb.map((value, channel) =>
        Math.round(value + (end.rgb[channel] - value) * position),
      ) as Rgb;
      return `rgb(${mixed[0]},${mixed[1]},${mixed[2]})`;
    }
  }
  return 'rgb(228,233,255)';
}

/* v8.3 IV 高位砖去糖果色：满底热色是「AI 生成热力图」签名。砖改为白底 card +
   左缘 3px 热色竖条 + 极淡 tint 底，热色只出现在「数据墨水」上（竖条/数字）。
   toneOnColor/light 分支随满底背景一并退役（luminance 仍服务 heatTone）。 */

/** 砖底极淡 tint：ivRankColor 的 rgb 加 0.12 alpha（几乎不可见，近看有温度）。 */
export function ivRankTint(rank: number): string {
  const rgb = parseRgb(ivRankColor(rank));
  return rgb ? `rgba(${rgb[0]},${rgb[1]},${rgb[2]},0.12)` : 'rgba(228,233,255,0.12)';
}

/** rank 数字墨色：热色与 ink-800(#182338) 按 55% 混色，保证数字在 tint 底上可读。 */
export function ivRankInk(rank: number): string {
  const rgb = parseRgb(ivRankColor(rank));
  if (!rgb) return 'rgb(24,35,56)';
  const ink: Rgb = [24, 35, 56];
  const mixed = rgb.map((value, channel) =>
    Math.round(value * 0.55 + ink[channel] * 0.45),
  ) as Rgb;
  return `rgb(${mixed[0]},${mixed[1]},${mixed[2]})`;
}

export const SOURCE_STATUS_CN: Record<Exclude<SectorSourceStatus, 'active'>, string> = {
  degraded: t('数据不完整'),
  stale: t('数据过期'),
  insufficient_data: t('数据不足'),
};
