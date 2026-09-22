import type { ScreenerRow } from '@/api/types';
/** The single current engine publishes its visible ranking score as strengthScore. */
export function rowPrimarySortScore(
  row: ScreenerRow,
): number | null {
  return typeof row.strengthScore === 'number' && Number.isFinite(row.strengthScore)
    ? row.strengthScore
    : null;
}
