import { closedStructuralBars, medianRange, type StructuralBar, type StructuralOverlay } from './structuralOverlays.ts';
export interface GapInterval { low: number; high: number }
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);

/** Subtract the range that actually traded. A jump across a gap is NOT a fill.
 * This also preserves both untraded pieces when a later candle trades only in the
 * middle. Never inflate a shrunken interval when another bar is appended.
 */
export function subtractTradedRange(intervals: readonly GapInterval[], low: number, high: number): GapInterval[] {
  if (!finite(low) || !finite(high) || low > high) return intervals.map(i => ({ ...i }));
  const result: GapInterval[] = [];
  for (const part of intervals) {
    if (high <= part.low || low >= part.high) { result.push({ ...part }); continue; }
    if (low > part.low) result.push({ low: part.low, high: Math.min(low, part.high) });
    if (high < part.high) result.push({ low: Math.max(high, part.low), high: part.high });
  }
  return result.filter(p => p.high - p.low > Math.max(1e-8, p.high * 1e-10));
}
function consecutiveSession(a: StructuralBar, b: StructuralBar): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(a.key) || !/^\d{4}-\d{2}-\d{2}$/.test(b.key)) return false;
  if (a.chartIndex !== undefined && b.chartIndex !== undefined && b.chartIndex - a.chartIndex !== 1) return false;
  const start = Date.parse(`${a.key}T00:00:00Z`), end = Date.parse(`${b.key}T00:00:00Z`);
  if (!finite(start) || !finite(end) || end <= start || end - start > 4 * 86400000) return false;
  // No exchange calendar is supplied. Conservatively skip holiday/missing-session
  // ambiguity rather than manufacturing a gap over an absent daily candle.
  let weekdays = 0;
  for (let t = start + 86400000; t <= end; t += 86400000) {
    const day = new Date(t).getUTCDay();
    if (day !== 0 && day !== 6) weekdays++;
  }
  return weekdays === 1;
}
/** Full high/low daily gaps only. Intraday/week gaps need an exchange calendar and
 * an explicit session policy, so this first version deliberately emits none there.
 * Huge discontinuities are quarantined; this is not corporate-action detection.
 */
export function detectPriceGaps(input: readonly StructuralBar[], range: string): StructuralOverlay[] {
  if (range !== '1d') return [];
  const bars = closedStructuralBars(input).slice(-360);
  if (bars.length < 21) return [];
  const proposals: StructuralOverlay[] = [];
  let safeStart = 0;
  for (let i = 1; i < bars.length; i++) {
    // Raw-price discontinuities can be a split, adjustment or real extraordinary
    // news. Without authoritative action data do not draw across them.
    if (Math.abs(Math.log(bars[i].c / bars[i - 1].c)) > Math.log(1.35)) safeStart = i;
  }
  for (let i = Math.max(20, safeStart + 1); i < bars.length; i++) {
    const a = bars[i - 1], b = bars[i];
    if (!consecutiveSession(a, b)) continue;
    const up = b.l > a.h, down = b.h < a.l;
    if (!up && !down) continue;
    const low = up ? a.h : b.h, high = up ? b.l : a.l;
    const history = bars.slice(Math.max(safeStart, i - 20), i);
    if (history.length < 10) continue;
    const ranges = history.map((v, n) => {
      const prev = n ? history[n - 1].c : v.o;
      return Math.max(v.h - v.l, Math.abs(v.h - prev), Math.abs(v.l - prev));
    });
    if (high - low < Math.max(medianRange(ranges) * 0.5, a.c * 0.002)) continue;
    let remaining: GapInterval[] = [{ low, high }];
    let filledAt = -1, tooFragmented = false;
    for (let j = i + 1; j < bars.length; j++) {
      remaining = subtractTradedRange(remaining, bars[j].l, bars[j].h);
      // Bound pathological repeated island gaps without pretending a fill.
      if (remaining.length > 8) { tooFragmented = true; break; }
      if (!remaining.length) { filledAt = j; break; }
    }
    if (tooFragmented) continue;
    const unfilled = remaining.reduce((sum, p) => sum + p.high - p.low, 0);
    const filledFraction = Math.max(0, Math.min(1, 1 - unfilled / (high - low)));
    const last = bars[bars.length - 1], distance = Math.min(...(remaining.length ? remaining : [{ low, high }])
      .map(p => last.c < p.low ? p.low - last.c : last.c > p.high ? last.c - p.high : 0));
    const end = filledAt >= 0 ? bars[filledAt] : last;
    proposals.push({
      id: `gap:${b.key}:${up ? 'up' : 'down'}:${low.toPrecision(8)}:${high.toPrecision(8)}`,
      sourceId: 'local-closed-bars', algorithmVersion: 'daily-high-low-gaps-v1', group: 'price', kind: 'gap',
      geometry: { low, high, remainingIntervals: remaining, subtype: up ? 'up' : 'down',
        startBarKey: a.key, endBarKey: end.key, filledBarKey: filledAt >= 0 ? end.key : null,
        // Rendering uses bar identity, never guessed timestamps.
        anchors: [{ time: b.t, barKey: b.key, price: low }, { time: end.t, barKey: end.key, price: high }] },
      status: filledAt >= 0 ? 'expired' : filledFraction > 0 ? 'testing' : 'forming',
      direction: 'neutral', shapeQuality: 1,
      displayPriority: 45 / (1 + distance / Math.max(medianRange(ranges) * 4, last.c * 0.01))
        + 20 * Math.exp(-(bars.length - 1 - i) / 60) - (filledAt >= 0 ? 50 : 0),
      evidence: { closedBarsOnly: true, visualizationOnly: true, filledFraction,
        definition: 'adjacent-daily-high-low', fillBasis: 'traded-high-low-intersection',
        calendarPolicy: 'conservative-weekday-adjacency', corporateActionsVerified: false },
      formationStart: b.key, formationEnd: end.key, dataThrough: last.key,
      label: '', detail: 'Untraded daily price intervals, not support or resistance and not a forecast.',
    });
  }
  return proposals;
}
