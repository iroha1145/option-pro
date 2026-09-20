import type { ScanFilters } from '@/components/screener/types';
import { t } from '../i18n/core.ts';

export const EOD_LIMITED_RANKING = 'eod_limited_v1';

export function isEodLimitedRanking(algorithm?: string | null): boolean {
  return algorithm === EOD_LIMITED_RANKING;
}

/** follow_default can resolve to the EOD product default; final resolution remains server-owned. */
export function followsEodScreenerView(algorithm?: string | null): boolean {
  return isEodLimitedRanking(algorithm) || algorithm === 'follow_default';
}

/**
 * Keep an explicit algorithm choice on a view that the backend accepts.
 *
 * The product default moved to EOD/mid, but an existing personal A0 choice
 * must still reopen on A0's fixed all/balanced view. Otherwise restoring the
 * preference creates an invalid request before the user touches any filter.
 */
export function applyEodLimitedView(filters: ScanFilters): ScanFilters {
  if (filters.rankingAlgorithm === 'a0_mid_long') {
    if (filters.timeframe === 'all' && filters.profile === 'balanced') return filters;
    return {
      ...filters,
      timeframe: 'all',
      profile: 'balanced',
      presetId: null,
    };
  }
  if (!isEodLimitedRanking(filters.rankingAlgorithm) || filters.timeframe !== 'all') return filters;
  return { ...filters, timeframe: 'mid' };
}

export function supportsDollarVolumeFilter(input: {
  rankingAlgorithm: ScanFilters['rankingAlgorithm'];
  effectiveAlgorithm?: string | null;
  serverSupport?: boolean | null;
}): boolean {
  if (input.serverSupport != null) return input.serverSupport;
  if (isEodLimitedRanking(input.rankingAlgorithm)) return false;
  if (input.rankingAlgorithm !== 'follow_default') return true;
  if (input.effectiveAlgorithm) return !isEodLimitedRanking(input.effectiveAlgorithm);
  // Until follow_default has resolved, do not present an unverified gate as active.
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
