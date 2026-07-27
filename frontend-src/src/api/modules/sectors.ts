/** 板块域：真实目录、强度聚合与板块内 IV 横截面。 */
import { get, mockOr, toQuery } from '../client';
import { registryGet } from '../queryRegistry';
import { asRec, pickB, pickN, pickS, pickLabel, unwrap, type Rec } from '../live';
import { mapMacroFitDrivers } from '../macroFields';
import type { MacroFitDriver } from '@/lib/macroFit';
import * as fx2 from '@/mocks/fixtures2';
import type { IvRankRow, Sector } from '../types';

export type SectorPeriod = '1mo' | '3mo' | '6mo';
export type SectorSourceStatus = 'active' | 'degraded' | 'stale' | 'insufficient_data';

export interface SectorCatalogItem extends Sector {
  tickers: string[];
}

export interface SectorStrengthLeader {
  ticker: string;
  score: number | null;
}

export interface SectorStrengthRow {
  sectorId: string;
  name: string;
  count: number | null;
  period: SectorPeriod;
  periodDays: number | null;
  avgReturn: number | null;
  avgReturn1mo: number | null;
  avgReturn3mo: number | null;
  avgReturn6mo: number | null;
  avgStrength: number | null;
  leaders: SectorStrengthLeader[];
  /**
   * 宏观适配（影子字段）：与 avgStrength 并列，绝不混入它。
   * 两者不一致的板块正是值得看的，混成一个数就把它们藏掉了。null = 没读到。
   */
  macroFit: number | null;
  macroTailwind: string | null;
  macroFitConfidence: number | null;
  macroSupporting: MacroFitDriver[];
  macroOpposing: MacroFitDriver[];
}

export interface SectorStrengthEnvelope {
  asOf: string | null;
  period: SectorPeriod;
  periodDays: number | null;
  sectors: SectorStrengthRow[];
  count: number;
  cached: boolean;
  snapshotSource: string | null;
}

export interface SectorIvRankingEnvelope {
  sectorId: string;
  sectorName: string;
  rows: IvRankRow[];
  sourceStatus: SectorSourceStatus;
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

function normalizeStatus(value: unknown): SectorSourceStatus {
  return value === 'degraded' || value === 'stale' || value === 'insufficient_data'
    ? value
    : 'active';
}

function stringArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === 'string' ? item.trim().toUpperCase() : ''))
    .filter(Boolean);
}

function sourceArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === 'string' ? item.trim() : ''))
    .filter(Boolean);
}

/** GET /api/sectors → 只保留后端真正提供的目录字段。 */
export function mapSectorCatalog(payload: unknown): SectorCatalogItem[] {
  return unwrap(payload, 'sectors')
    .map((raw) => {
      const tickers = stringArray(raw.tickers);
      const fixtureTickers =
        tickers.length > 0 || !Array.isArray(raw.constituents)
          ? []
          : (raw.constituents as unknown[])
              .map((item) => pickS(asRec(item), 'ticker')?.toUpperCase() ?? '')
              .filter(Boolean);
      return {
        id: pickS(raw, 'id', 'sector_id') ?? '',
        name: pickLabel(raw, 'name', 'sector_name') ?? '',
        tickers: tickers.length > 0 ? tickers : fixtureTickers,
      };
    })
    .filter((row) => row.id !== '' && row.name !== '');
}

function mapLeader(value: unknown): SectorStrengthLeader | null {
  const row = asRec(value);
  const ticker = pickS(row, 'ticker')?.toUpperCase();
  if (!ticker) return null;
  return { ticker, score: pickN(row, 'score', 'final_score') };
}

function mapStrengthRow(raw: Rec, requestedPeriod: SectorPeriod): SectorStrengthRow | null {
  const sectorId = pickS(raw, 'sector_id', 'id');
  if (!sectorId) return null;
  const rawPeriod = pickS(raw, 'period');
  const period: SectorPeriod =
    rawPeriod === '1mo' || rawPeriod === '3mo' || rawPeriod === '6mo'
      ? rawPeriod
      : requestedPeriod;
  const leaders = Array.isArray(raw.leaders)
    ? raw.leaders.map(mapLeader).filter((leader): leader is SectorStrengthLeader => leader !== null)
    : [];
  return {
    sectorId,
    name: pickLabel(raw, 'name', 'sector_name') ?? sectorId,
    count: pickN(raw, 'count'),
    period,
    periodDays: pickN(raw, 'period_days'),
    avgReturn: pickN(raw, 'avg_return', 'avg_return_period'),
    avgReturn1mo: pickN(raw, 'avg_return_1mo'),
    avgReturn3mo: pickN(raw, 'avg_return_3mo', 'avg_return_3m'),
    avgReturn6mo: pickN(raw, 'avg_return_6mo'),
    avgStrength: pickN(raw, 'avg_strength'),
    leaders,
    macroFit: pickN(raw, 'macro_sector_fit'),
    macroTailwind: pickS(raw, 'macro_sector_tailwind'),
    macroFitConfidence: pickN(raw, 'macro_sector_fit_confidence'),
    macroSupporting: mapMacroFitDrivers(raw.macro_sector_supporting_factors),
    macroOpposing: mapMacroFitDrivers(raw.macro_sector_opposing_factors),
  };
}

/** GET /api/strength/sectors 的真实聚合契约；缺失数值保留 null。 */
export function mapSectorStrengthEnvelope(
  payload: unknown,
  requestedPeriod: SectorPeriod,
): SectorStrengthEnvelope {
  const envelope = asRec(payload);
  const sectors = unwrap(payload, 'sectors')
    .map((row) => mapStrengthRow(row, requestedPeriod))
    .filter((row): row is SectorStrengthRow => row !== null);
  const rawPeriod = pickS(envelope, 'period');
  const period: SectorPeriod =
    rawPeriod === '1mo' || rawPeriod === '3mo' || rawPeriod === '6mo'
      ? rawPeriod
      : requestedPeriod;
  return {
    asOf: pickS(envelope, 'as_of'),
    period,
    periodDays: pickN(envelope, 'period_days'),
    sectors,
    count: pickN(envelope, 'count') ?? sectors.length,
    cached: pickB(envelope, '_cached') ?? false,
    snapshotSource: pickS(envelope, 'snapshot_source'),
  };
}

/** GET /api/sectors/{id}/iv-ranking，保留顶层覆盖率和数据源状态。 */
export function mapSectorIvRankingEnvelope(payload: unknown): SectorIvRankingEnvelope {
  const envelope = asRec(payload);
  const rows = unwrap(payload, 'rankings')
    .map((raw): IvRankRow | null => {
      const ticker = pickS(raw, 'ticker')?.toUpperCase();
      if (!ticker) return null;
      return {
        ticker,
        name: pickLabel(raw, 'name') ?? ticker,
        price: pickN(raw, 'price'),
        priceProvider: pickS(raw, 'price_provider', 'priceProvider'),
        sectorIvRank: pickN(raw, 'sector_iv_rank', 'sectorIvRank', 'ivPercentile'),
        atmIvPercent: pickN(raw, 'atm_iv_percent', 'atmIvPercent', 'iv'),
        stale: pickB(raw, '_stale', 'stale') ?? false,
        asOf: pickS(raw, 'as_of', 'asOf'),
      };
    })
    .filter((row): row is IvRankRow => row !== null);
  const stale = (pickB(envelope, '_stale', 'stale') ?? false) || rows.some((row) => row.stale);
  const sourceStatus = stale ? 'stale' : normalizeStatus(envelope.source_status);
  return {
    sectorId: pickS(envelope, 'sector_id') ?? '',
    sectorName: pickLabel(envelope, 'sector_name') ?? '',
    rows,
    sourceStatus,
    stale,
    asOf: pickS(envelope, 'as_of') ?? rows.find((row) => row.asOf)?.asOf ?? null,
    dataLimited: pickB(envelope, 'data_limited') ?? false,
    successCount: pickN(envelope, 'success_count'),
    requestedCount: pickN(envelope, 'requested_count'),
    successRate: pickN(envelope, 'success_rate'),
    failedSymbols: stringArray(envelope.failed_symbols),
    snapshotSource: pickS(envelope, 'snapshot_source'),
    snapshotOrigin: pickS(envelope, 'snapshot_origin'),
    providers: sourceArray(envelope.providers),
  };
}

function mockStrength(period: SectorPeriod): SectorStrengthEnvelope {
  return {
    asOf: null,
    period,
    periodDays: null,
    sectors: [],
    count: 0,
    cached: false,
    snapshotSource: null,
  };
}

export const sectorsApi = {
  list: (): Promise<SectorCatalogItem[]> =>
    mockOr(
      () => mapSectorCatalog({ sectors: fx2.getSectors() }),
      () => registryGet('/sectors').then(mapSectorCatalog),
    ),
  strength: (period: SectorPeriod = '3mo'): Promise<SectorStrengthEnvelope> =>
    mockOr(
      () => mockStrength(period),
      () => {
        const query = toQuery({ period });
        return get(`/strength/sectors?${query}`).then((payload) =>
          mapSectorStrengthEnvelope(payload, period),
        );
      },
    ),
  ivRanking: (sectorId: string): Promise<SectorIvRankingEnvelope> =>
    mockOr(
      () =>
        mapSectorIvRankingEnvelope({
          sector_id: sectorId,
          rankings: fx2.getSectorIvRanking(sectorId),
          source_status: 'active',
        }),
      () =>
        get(`/sectors/${encodeURIComponent(sectorId)}/iv-ranking`).then(
          mapSectorIvRankingEnvelope,
        ),
    ),
};
