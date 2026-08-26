/** Bar-key projection: persist time + price, re-resolve by identity, never migrate. */
import type { ChartAdjustment, ChartRange, DrawingAnchor } from './types.ts';

export interface TimedBar {
  t: string;
}

const NY_DAY = new Intl.DateTimeFormat('en-CA', {
  timeZone: 'America/New_York',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

export function nySessionDate(iso: string): string {
  const ms = Date.parse(iso);
  if (!Number.isFinite(ms)) return iso.slice(0, 10);
  return NY_DAY.format(new Date(ms));
}

export function barKeyOf(bar: TimedBar, range: ChartRange): string {
  if (range === '1d' || range === '1w') return nySessionDate(bar.t);
  return bar.t;
}

export function drawingScopeKey(
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): string {
  return `${ticker.toUpperCase()}|${range}|${adjustment}`;
}

export function sameScope(
  drawing: { ticker: string; range: ChartRange; adjustment: ChartAdjustment },
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): boolean {
  return drawingScopeKey(drawing.ticker, drawing.range, drawing.adjustment)
    === drawingScopeKey(ticker, range, adjustment);
}

export function resolveBarKey(
  bars: TimedBar[],
  barKey: string,
  range: ChartRange,
): number {
  for (let i = 0; i < bars.length; i++) {
    if (barKeyOf(bars[i], range) === barKey) return i;
  }
  return -1;
}

export function resolveAnchor(
  bars: TimedBar[],
  anchor: DrawingAnchor,
  range: ChartRange,
): number {
  return resolveBarKey(bars, anchor.barKey, range);
}

export function isAnchorResolved(
  bars: TimedBar[],
  anchor: DrawingAnchor,
  range: ChartRange,
): boolean {
  return resolveAnchor(bars, anchor, range) >= 0;
}

/**
 * Future blank area (x beyond the last bar) must not snap onto the last
 * historical candle. Returns null when the pointer is outside the series.
 */
export function snapBarIndex(x: number, barCount: number): number | null {
  if (!Number.isFinite(x) || barCount <= 0) return null;
  if (x < -0.45) return null;
  if (x >= barCount - 0.5) return null;
  const idx = Math.round(x);
  if (idx < 0 || idx >= barCount) return null;
  return idx;
}

export function drawingsInScope<T extends { ticker: string; range: ChartRange; adjustment: ChartAdjustment }>(
  drawings: T[],
  ticker: string,
  range: ChartRange,
  adjustment: ChartAdjustment = 'raw',
): T[] {
  return drawings.filter((item) => sameScope(item, ticker, range, adjustment));
}
