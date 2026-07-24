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
  '1mo': '1 个月',
  '3mo': '3 个月',
  '6mo': '6 个月',
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

export function toneOnColor(bg: string): 'light' | 'dark' {
  const rgb = parseRgb(bg);
  return rgb && luminance(rgb) < 0.55 ? 'light' : 'dark';
}

export const SOURCE_STATUS_CN: Record<Exclude<SectorSourceStatus, 'active'>, string> = {
  degraded: '数据源降级',
  stale: '数据过期',
  insufficient_data: '数据不足',
};
