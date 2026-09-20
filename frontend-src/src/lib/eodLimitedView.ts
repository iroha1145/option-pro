import type { ScanFilters } from '@/components/screener/types';
import { t } from '../i18n/core.ts';

export const EOD_LIMITED_RANKING = 'eod_limited_v1';

export function isEodLimitedRanking(algorithm?: string | null): boolean {
  return algorithm === EOD_LIMITED_RANKING;
}

/** Normalize every legacy choice onto the single current engine and view. */
export function applyEodLimitedView(filters: ScanFilters): ScanFilters {
  const timeframe = filters.timeframe === 'all' ? 'mid' : filters.timeframe;
  if (filters.rankingAlgorithm === EOD_LIMITED_RANKING && timeframe === filters.timeframe) return filters;
  return { ...filters, rankingAlgorithm: EOD_LIMITED_RANKING, timeframe };
}

export function supportsDollarVolumeFilter(input: {
  serverSupport?: boolean | null;
}): boolean {
  if (input.serverSupport != null) return input.serverSupport;
  // Until the server reports support, do not present an unverified gate as active.
  return false;
}

export function eodEmptyEligibleLabel(reason?: string | null): string {
  if (reason === 'consensus_insufficient') return t('合格综合为空：家族共识不足');
  if (reason === 'data_qualification_unverified') return t('合格综合为空：数据资格未核实');
  if (reason === 'technical_threshold') return t('合格综合为空：技术门槛未达到');
  return t('合格综合为空：没有完整候选');
}

export function isEodLimitedSnapshotProblem(error: { code?: number; bizCode?: string } | null | undefined): boolean {
  return (
    error?.code === 503
    && (error.bizCode === 'eod_limited_snapshot_unavailable' || error.bizCode === 'eod_limited_snapshot_preparing')
  );
}

export function isEodLimitedPreparing(error: { code?: number; bizCode?: string } | null | undefined): boolean {
  return error?.code === 503 && error.bizCode === 'eod_limited_snapshot_preparing';
}
