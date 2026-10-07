import type { MarketSignalsSnapshot } from '../api/types';
import { t } from '../i18n/core.ts';

/** value 已是百分数；计数只使用同一快照中的原始统计。 */
export function sectorBreadthReading(
  value: number,
  coverage: MarketSignalsSnapshot['breadthCoverage'],
): string {
  const pct = `${value.toFixed(2)}%`;
  const { available, expected, aboveCount } = coverage;
  const validAvailable = available !== null && Number.isInteger(available) && available > 0;
  const validExpected = validAvailable && expected !== null && Number.isInteger(expected) && expected >= available;
  let reading = t('站上 50 日均线的板块占比 {pct}', { pct });
  if (validAvailable && aboveCount !== null && Number.isInteger(aboveCount) && aboveCount >= 0 && aboveCount <= available) {
    reading = validExpected && available === expected
      ? t('{available} 个板块中，{above} 个站上 50 日均线，占比 {pct}', { available, above: aboveCount, pct })
      : t('有数据的 {available} 个板块中，{above} 个站上 50 日均线，占比 {pct}', { available, above: aboveCount, pct });
  }
  if (validExpected && available < expected) {
    reading += t('（数据覆盖 {available}/{expected} 个板块）', { available, expected });
  }
  return reading;
}
