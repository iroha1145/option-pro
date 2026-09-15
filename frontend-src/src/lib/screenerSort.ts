import type { ScreenerRow } from '@/api/types';

export const A0_RANKING = 'a0_mid_long';

export function isA0Ranking(algorithm?: string | null): boolean {
  return algorithm === A0_RANKING;
}

/** The number the current ranking actually used. Missing A0 stays missing. */
export function rowPrimarySortScore(
  row: ScreenerRow,
  effectiveAlgorithm?: string | null,
): number | null {
  if (isA0Ranking(row.sortAlgorithm ?? effectiveAlgorithm)) {
    return typeof row.sortScore === 'number' && Number.isFinite(row.sortScore) ? row.sortScore : null;
  }
  return typeof row.strengthScore === 'number' && Number.isFinite(row.strengthScore)
    ? row.strengthScore
    : null;
}

export function keepServerRankingOrder(effectiveAlgorithm?: string | null): boolean {
  return isA0Ranking(effectiveAlgorithm);
}
