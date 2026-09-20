import type { ScanFilters } from '@/components/screener/types';
import { t } from '../i18n/core.ts';

export const EOD_LIMITED_RANKING = 'eod_limited_v1';

export function isEodLimitedRanking(algorithm?: string | null): boolean {
  return algorithm === EOD_LIMITED_RANKING;
}

/** follow_default now consumes the product EOD default, so All remaps to Mid. */
export function followsEodScreenerView(algorithm?: string | null): boolean {
  return isEodLimitedRanking(algorithm) || algorithm === 'follow_default';
}

/** First-select remap: EOD does not accept timeframe=all. */
export function applyEodLimitedView(filters: ScanFilters): ScanFilters {
  if (!followsEodScreenerView(filters.rankingAlgorithm)) return filters;
  if (filters.timeframe !== 'all') return filters;
  return { ...filters, timeframe: 'mid' };
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
