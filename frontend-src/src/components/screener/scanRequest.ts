import type { ScanParams } from '@/api/modules/strength';
import type { StrengthRefreshParameters } from '@/api/modules/runtime';
import type { ScanFilters } from './types';

export interface StrengthScanRequest {
  apiParams: ScanParams;
  refreshParameters: StrengthRefreshParameters;
}

/**
 * 把界面筛选统一转换为查询和刷新参数。
 * Owner 刷新与随后读取快照必须使用同一组参数，避免哈希错位。
 */
export function buildStrengthScanRequest(filters: ScanFilters): StrengthScanRequest {
  // 后端必须先返回足够大的真实候选集，再应用只存在于客户端的条件；
  // 否则先截 TopN 会漏掉价格上限、多板块、分档或最低分过滤后的合资格股票。
  const hasClientOnlyNarrowing =
    filters.priceMax != null ||
    filters.sectors.length > 1 ||
    filters.tier !== 'all' ||
    filters.minScore != null;
  const top = filters.topN <= 0 || hasClientOnlyNarrowing ? 120 : filters.topN;
  const sectorId = filters.sectors.length === 1 ? filters.sectors[0] : null;
  const minPrice = filters.priceMin ?? 0;

  return {
    apiParams: {
      band: 'all',
      sort: 'score',
      order: 'desc',
      universe: 'themes',
      timeframe: filters.timeframe,
      profile: filters.profile,
      top,
      sector_id: sectorId ?? undefined,
      min_price: minPrice,
      min_avg_dollar_volume: filters.minDollarVol,
      include_options: true,
      ...(filters.minScore != null ? { minScore: filters.minScore } : {}),
    },
    refreshParameters: {
      universe: 'themes',
      timeframe: filters.timeframe,
      profile: filters.profile,
      top,
      sector_id: sectorId,
      min_price: minPrice,
      min_avg_dollar_volume: filters.minDollarVol,
      include_options: true,
    },
  };
}
