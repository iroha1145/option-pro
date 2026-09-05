import { normalizePatternSegments } from '../linePresentation.ts';

/**
 * Bounded, deterministic chart annotations. NOT a trading signal or a probability.
 * Input must be the closed, fingerprint-validated bars of the displayed series.
 * No network, persistence, future timestamps, viewport-dependent fitting or randomness.
 */
export interface SmartBar {
  t: string;
  key: string;
  o: number;
  h: number;
  l: number;
  c: number;
  ext?: boolean;
  quote_only?: boolean;
  closed?: boolean;
  /** Position on the displayed category axis, including excluded session bars. */
  chartIndex?: number;
}

/** Structurally compatible with AnalysisOverlay; no runtime dependency on its mapper. */
export interface SmartOverlay {
  id: string;
  sourceId: string;
  algorithmVersion: string;
  group: string;
  kind: string;
  geometry: Record<string, unknown>;
  status: string;
  direction: string;
  shapeQuality: number;
  displayPriority: number;
  evidence: Record<string, unknown>;
  formationStart: string;
  formationEnd: string;
  dataThrough: string;
  label: string;
  detail: string;
}

type Side = 'support' | 'resistance';
interface Pivot { x: number; y: number; side: Side }
interface Rail {
  side: Side;
  slope: number;
  intercept: number;
  touches: Pivot[];
  start: number;
  end: number;
  quality: number;
  status: string;
  tolerance: number;
}
interface RailGeometry { start: number; end: number; slope: number; intercept: number }
export const SMART_LINES_VERSION = 'optix-smart-lines-v2';
export const SMART_MAX_BARS = 360;
// Preserve all bounded candidates until the user's layer/quality/status gates.
export const SMART_MAX_PROPOSALS = 1024;
const PATTERNS = new Set(['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge']);
const PAIRED = new Set(['channel', 'triangle', 'wedge']);
const BROKEN = new Set(['invalidated', 'broken_up', 'broken_down', 'failed', 'expired']);
const clamp = (n: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, n));
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const at = (r: Pick<Rail, 'slope' | 'intercept'>, x: number) => r.slope * x + r.intercept;
const chartX = (bars: readonly SmartBar[], index: number) => bars[index].chartIndex ?? index;
const atBar = (bars: readonly SmartBar[], r: Pick<Rail, 'slope' | 'intercept'>, index: number) => at(r, chartX(bars, index));

/** Fit in the same axis that will display the rails; never stretch only the endpoints. */
export function withChartIndices<T extends SmartBar>(bars: readonly T[], chartKeys: readonly string[]): Array<T & { chartIndex: number }> {
  const index = new Map(chartKeys.map((key, i) => [key, i]));
  if (index.size !== chartKeys.length) return [];
  const result: Array<T & { chartIndex: number }> = [];
  let previous = -1;
  for (const bar of bars) {
    const chartIndex = index.get(bar.key);
    if (chartIndex === undefined || chartIndex <= previous) return [];
    result.push({ ...bar, chartIndex });
    previous = chartIndex;
  }
  return result;
}

function median(values: number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

function validBars(input: readonly SmartBar[]): boolean {
  let time = -Infinity;
  let previousX = -1;
  const hasChartIndices = input.some(bar => bar.chartIndex !== undefined);
  const keys = new Set<string>();
  for (const bar of input) {
    const stamp = Date.parse(bar.t);
    if (!Number.isFinite(stamp) || stamp <= time || !bar.key || keys.has(bar.key)) return false;
    if (![bar.o, bar.h, bar.l, bar.c].every(n => finite(n) && n > 0 && n <= 10_000_000)) return false;
    if (bar.l > Math.min(bar.o, bar.c) || bar.h < Math.max(bar.o, bar.c) || bar.l > bar.h) return false;
    if (hasChartIndices && (!Number.isSafeInteger(bar.chartIndex) || bar.chartIndex! <= previousX)) return false;
    if (hasChartIndices) previousX = bar.chartIndex!;
    time = stamp;
    keys.add(bar.key);
  }
  return true;
}

/** Median true range is deliberately robust to one bad spike; not Wilder's ATR. */
export function smartTolerance(bars: readonly SmartBar[]): number {
  if (!bars.length) return 0;
  const ranges = bars.slice(-32).map((b, i, tail) => {
    const prev = i ? tail[i - 1].c : b.o;
    return Math.max(b.h - b.l, Math.abs(b.h - prev), Math.abs(b.l - prev));
  });
  return Math.max(median(ranges) * 0.28, bars[bars.length - 1].c * 0.0015, 1e-6);
}

/** Right-side bars must already exist. The last `wing` bars cannot be pivots. */
export function confirmedPivots(bars: readonly SmartBar[], wing: number, offset = 0): Pivot[] {
  if (!Number.isInteger(wing) || wing < 1 || wing > 12) return [];
  const out: Pivot[] = [];
  for (let i = wing; i < bars.length - wing; i++) {
    for (const side of ['support', 'resistance'] as const) {
      const price = side === 'support' ? bars[i].l : bars[i].h;
      let good = true;
      let changed = false;
      for (let j = i - wing; j <= i + wing; j++) {
        if (j === i) continue;
        const other = side === 'support' ? bars[j].l : bars[j].h;
        if ((side === 'support' ? other < price : other > price) || (other === price && j > i)) {
          good = false;
          break;
        }
        changed ||= other !== price;
      }
      if (good && changed) out.push({ x: offset + i, y: price, side });
    }
  }
  return out;
}

function separated(points: Pivot[], gap = 4): Pivot[] {
  const result: Pivot[] = [];
  for (const p of [...points].sort((a, b) => a.x - b.x)) {
    if (!result.length || p.x - result[result.length - 1].x >= gap) result.push(p);
  }
  return result;
}

/** A confirmed break cannot be erased by selecting a later second anchor. */
function firstBreak(bars: readonly SmartBar[], rail: Pick<Rail, 'side' | 'slope' | 'intercept'>, start: number, end: number, tolerance: number): number {
  let outsideRun = 0;
  for (let x = start; x <= end; x++) {
    const price = atBar(bars, rail, x);
    const outside = rail.side === 'support' ? bars[x].c < price - tolerance : bars[x].c > price + tolerance;
    outsideRun = outside ? outsideRun + 1 : 0;
    if (outsideRun >= 2) return x;
  }
  return -1;
}

function fitRails(bars: readonly SmartBar[], pivots: Pivot[], side: Side, tolerance: number): Rail[] {
  const own = pivots.filter(p => p.side === side).slice(-18);
  const end = bars.length - 1;
  const rails: Rail[] = [];
  for (let i = 0; i < own.length - 1; i++) {
    for (let j = i + 1; j < own.length; j++) {
      const a = own[i], b = own[j];
      if (b.x - a.x < 12) continue;
      const slope = (b.y - a.y) / (chartX(bars, b.x) - chartX(bars, a.x));
      const intercept = a.y - slope * chartX(bars, a.x);
      const value = (x: number) => slope * chartX(bars, x) + intercept;
      if (value(end) <= 0 || Math.abs(value(end) - bars[end].c) > Math.max(tolerance * 24, bars[end].c * 0.15)) continue;
      const breakAt = firstBreak(bars, { side, slope, intercept }, a.x, end, tolerance);
      if (breakAt >= 0 && b.x >= breakAt) continue;
      const touches = separated(own.filter(p => p.x >= a.x && (breakAt < 0 || p.x < breakAt) && Math.abs(p.y - value(p.x)) <= tolerance));
      if (touches.length < 2 || touches[touches.length - 1].x - touches[0].x < 12) continue;
      const lastTouch = touches[touches.length - 1].x;
      if (end - lastTouch > Math.min(48, Math.max(12, (end - a.x) * 0.45))) continue;
      let violations = 0;
      for (let x = a.x; x <= lastTouch; x++) {
        const outside = side === 'support' ? bars[x].c < value(x) - tolerance : bars[x].c > value(x) + tolerance;
        if (outside) violations++;
      }
      if (violations / (lastTouch - a.x + 1) > 0.06) continue;
      // Once broken, a rail is historical. Do not silently reconnect it through a later touch.
      const status = breakAt >= 0
        ? side === 'support' ? 'broken_down' : 'broken_up'
        : Math.abs(bars[end].c - value(end)) <= tolerance * 2 ? 'testing' : 'forming';
      const error = median(touches.map(p => Math.abs(p.y - value(p.x)))) / tolerance;
      const quality = clamp(0.45 + 0.18 * Math.min(1, (touches.length - 1) / 4)
        + 0.12 * Math.min(1, (lastTouch - a.x) / 90) + 0.15 * (1 - violations / (lastTouch - a.x + 1))
        + 0.1 * (1 - error), 0, 0.98);
      rails.push({ side, slope, intercept, touches, start: a.x, end: breakAt >= 0 ? breakAt : end, quality, status, tolerance });
    }
  }
  rails.sort((a, b) => Number(BROKEN.has(a.status)) - Number(BROKEN.has(b.status))
    || b.quality - a.quality || b.touches[b.touches.length - 1].x - a.touches[a.touches.length - 1].x || a.start - b.start);
  const kept: Rail[] = [];
  for (const rail of rails) {
    // Only collapse candidates with the same state. Historical and newly formed
    // rails may share prices but must retain distinct formation intervals.
    if (kept.some(old => old.status === rail.status && Math.abs(atBar(bars, old, end) - atBar(bars, rail, end)) < tolerance
      && Math.abs(old.slope - rail.slope) * (chartX(bars, end) - chartX(bars, Math.max(old.start, rail.start))) < tolerance * 2)) continue;
    kept.push(rail);
    if (kept.length >= 5) break;
  }
  return kept;
}

function anchor(bars: readonly SmartBar[], x: number, price: number) {
  return { time: bars[x].t, barKey: bars[x].key, price };
}

function annotation(bars: readonly SmartBar[], kind: string, rails: Rail[], suffix: string): SmartOverlay {
  const start = Math.max(...rails.map(r => r.start));
  const end = Math.min(...rails.map(r => r.end));
  const slope = rails[0].slope;
  const touches = separated(rails.flatMap(r => r.touches), 1);
  const geometry: Record<string, unknown> = {
    subtype: slope < 0 ? 'falling' : 'rising',
    anchors: rails.flatMap(r => [anchor(bars, start, atBar(bars, r, start)), anchor(bars, end, atBar(bars, r, end))]),
    touchAnchors: touches.map(p => anchor(bars, p.x, p.y)),
    observedThrough: bars[end].key,
  };
  if (rails.length === 2) {
    geometry.supportRail = [anchor(bars, start, atBar(bars, rails[0], start)), anchor(bars, end, atBar(bars, rails[0], end))];
    geometry.resistanceRail = [anchor(bars, start, atBar(bars, rails[1], start)), anchor(bars, end, atBar(bars, rails[1], end))];
  } else {
    geometry.fitAnchors = geometry.anchors;
  }
  const quality = Math.min(...rails.map(r => r.quality));
  return {
    id: `smart:${kind}:${bars[start].key}:${suffix}`,
    sourceId: 'local-closed-bars', algorithmVersion: SMART_LINES_VERSION, group: 'price', kind, geometry,
    status: rails.find(r => BROKEN.has(r.status))?.status ?? 'forming',
    direction: slope > 0 ? 'bullish' : slope < 0 ? 'bearish' : 'neutral',
    shapeQuality: quality, displayPriority: quality * 100,
    evidence: { touches: touches.length, tolerance: rails[0].tolerance, closedBarsOnly: true,
      confirmation: 'right-window-pivots', visualizationOnly: true },
    formationStart: bars[start].key, formationEnd: bars[end].key, dataThrough: bars[bars.length - 1].key,
    label: '', detail: 'Local geometric annotation; not a forecast or a trade signal.',
  };
}

function channel(bars: readonly SmartBar[], lower: Rail, upper: Rail, tolerance: number): Rail[] | null {
  if (BROKEN.has(lower.status) || BROKEN.has(upper.status)) return null;
  const start = Math.max(lower.start, upper.start), end = bars.length - 1;
  if (end - start < 24 || Math.abs(lower.slope - upper.slope) * (chartX(bars, end) - chartX(bars, start)) > tolerance * 2) return null;
  // A channel is actually parallel, not merely two lines given a channel label.
  const slope = (lower.slope + upper.slope) / 2;
  const lowTouches = lower.touches.filter(p => p.x >= start);
  const highTouches = upper.touches.filter(p => p.x >= start);
  if (lowTouches.length < 2 || highTouches.length < 2 || lowTouches.length + highTouches.length < 5) return null;
  const li = median(lowTouches.map(p => p.y - slope * chartX(bars, p.x)));
  const ui = median(highTouches.map(p => p.y - slope * chartX(bars, p.x)));
  if (ui - li < tolerance * 5 || ui - li > Math.max(tolerance * 50, bars[end].c * 0.25)) return null;
  const lows = separated(lowTouches.filter(p => Math.abs(p.y - (slope * chartX(bars, p.x) + li)) <= tolerance));
  const highs = separated(highTouches.filter(p => Math.abs(p.y - (slope * chartX(bars, p.x) + ui)) <= tolerance));
  if (lows.length < 2 || highs.length < 2 || lows.length + highs.length < 5) return null;
  // Refit changed the actual boundaries. Recheck the two-close lifecycle against
  // those final prices; aggregate containment percentages cannot confirm a break.
  if (firstBreak(bars, { side: 'support', slope, intercept: li }, start, end, tolerance) >= 0
    || firstBreak(bars, { side: 'resistance', slope, intercept: ui }, start, end, tolerance) >= 0) return null;
  let inside = 0, wickInside = 0;
  for (let x = start; x <= end; x++) {
    const chartIndex = chartX(bars, x);
    if (bars[x].c >= slope * chartIndex + li - tolerance && bars[x].c <= slope * chartIndex + ui + tolerance) inside++;
    if (bars[x].l >= slope * chartIndex + li - tolerance && bars[x].h <= slope * chartIndex + ui + tolerance) wickInside++;
  }
  if (inside / (end - start + 1) < 0.98 || wickInside / (end - start + 1) < 0.9) return null;
  return [
    { ...lower, slope, intercept: li, start, end, touches: lows },
    { ...upper, slope, intercept: ui, start, end, touches: highs },
  ];
}

/** Bounded candidates across three swing scales; user gates precede deduplication. */
export function detectSmartLines(input: readonly SmartBar[]): SmartOverlay[] {
  const bars = input.filter(b => !b.ext && !b.quote_only && b.closed !== false).slice(-SMART_MAX_BARS);
  if (bars.length < 32 || !validBars(bars)) return [];
  const tolerance = smartTolerance(bars);
  const last = bars[bars.length - 1];
  if (Math.max(...bars.map(b => b.h)) - Math.min(...bars.map(b => b.l)) < tolerance * 5) return [];
  const proposals: SmartOverlay[] = [];
  const allPivots: Pivot[] = [];
  for (const [window, wing] of [[60, 2], [120, 3], [240, 5]]) {
    const offset = Math.max(0, bars.length - window);
    const pivots = confirmedPivots(bars.slice(offset), wing, offset);
    allPivots.push(...pivots);
    const supports = fitRails(bars, pivots, 'support', tolerance);
    const resistances = fitRails(bars, pivots, 'resistance', tolerance);
    for (const r of [...supports, ...resistances]) {
      if (r.touches.length < 3 || Math.abs(r.slope) * (chartX(bars, r.end) - chartX(bars, r.start)) < tolerance * 2) continue;
      const overlay = annotation(bars, `${r.side}_trend`, [r], `${window}:${r.touches[1].x}`);
      overlay.status = r.status;
      overlay.evidence.pivotConfirmationBars = wing;
      proposals.push(overlay);
    }
    for (const low of supports.slice(0, 3)) for (const high of resistances.slice(0, 3)) {
      const paired = channel(bars, low, high, tolerance);
      if (!paired) continue;
      const overlay = annotation(bars, 'channel', paired, `${window}:${low.touches[1].x}:${high.touches[1].x}`);
      overlay.evidence.pivotConfirmationBars = wing;
      // Near-flat channels are boxes; do not call a sideways range an uptrend.
      if (Math.abs(paired[0].slope) * (chartX(bars, paired[0].end) - chartX(bars, paired[0].start)) < tolerance * 2) overlay.geometry.subtype = 'horizontal';
      proposals.push(overlay);
    }
  }
  // Horizontal levels remain behind the existing support_resistance layer switch.
  const unique = [...new Map(allPivots.map(p => [`${p.side}:${p.x}`, p])).values()];
  for (const side of ['support', 'resistance'] as const) {
    for (const p of unique.filter(p => p.side === side)) {
      // A later cluster can form a new level after a break; do not borrow its
      // pre-break touches to manufacture a lifetime spanning that break.
      const touches = separated(unique.filter(q => q.side === side && q.x >= p.x && Math.abs(q.y - p.y) <= tolerance));
      if (touches.length < 3 || touches[touches.length - 1].x - touches[0].x < 16) continue;
      const price = median(touches.map(p => p.y));
      if ((side === 'support' && price > last.c + tolerance) || (side === 'resistance' && price < last.c - tolerance)) continue;
      if (Math.abs(price - last.c) > Math.max(tolerance * 24, last.c * 0.12)) continue;
      if (bars.length - 1 - touches[touches.length - 1].x > 60) continue;
      // A cluster alone is not a support/resistance boundary: most closes during
      // its observed lifetime must have remained on the defended side.
      const defended = bars.slice(touches[0].x, touches[touches.length - 1].x + 1);
      const wrongSide = defended.filter(b => side === 'support' ? b.c < price - tolerance : b.c > price + tolerance).length;
      if (wrongSide / defended.length > 0.06) continue;
      if (firstBreak(bars, { side, slope: 0, intercept: price }, touches[0].x, bars.length - 1, tolerance) >= 0) continue;
      const rail: Rail = { side, slope: 0, intercept: price, touches, start: touches[0].x,
        end: bars.length - 1, quality: clamp(0.6 + touches.length * 0.035, 0, 0.9), status: 'testing', tolerance };
      const overlay = annotation(bars, 'level', [rail], `${side}:${price.toPrecision(6)}`);
      overlay.geometry = { ...overlay.geometry, price, role: side };
      overlay.direction = 'neutral';
      overlay.status = 'testing';
      proposals.push(overlay);
    }
  }
  // Three windows, at most ten rails + nine pairs each, and at most two pivots
  // per bar: this remains below the fixed budget without erasing higher-quality
  // alternatives before the user's quality or layer settings are known.
  return proposals.slice(0, SMART_MAX_PROPOSALS);
}

function railsOf(overlay: SmartOverlay, byKey: Map<string, number>): RailGeometry[] {
  const g = overlay.geometry;
  if (overlay.kind === 'level' && finite(g.price) && g.price > 0) {
    const xs = [...byKey.values()];
    return [{ start: Math.min(...xs), end: Math.max(...xs), slope: 0, intercept: g.price }];
  }
  const pairs = Array.isArray(g.supportRail) && Array.isArray(g.resistanceRail)
    ? [g.supportRail, g.resistanceRail]
    : [Array.isArray(g.fitAnchors) ? g.fitAnchors : g.anchors];
  const segments: Array<{ a: { x: number; y: number }; b: { x: number; y: number } }> = [];
  for (const raw of pairs) {
    if (!Array.isArray(raw)) return [];
    const groups = raw.length >= 4 ? [raw.slice(0, 2), raw.slice(2, 4)] : [raw];
    for (const pair of groups) {
      const a = pair[0], b = pair[1];
      if (!a || !b || !finite(a.price) || !finite(b.price) || a.price <= 0 || b.price <= 0) return [];
      const ax = byKey.get(a.barKey), bx = byKey.get(b.barKey);
      if (ax === undefined || bx === undefined) return [];
      segments.push({ a: { x: ax, y: a.price }, b: { x: bx, y: b.price } });
    }
  }
  return normalizePatternSegments(segments, overlay.kind).map(({ a, b }) => {
    const slope = (b.y - a.y) / (b.x - a.x);
    return { start: a.x, end: b.x, slope, intercept: a.y - slope * a.x };
  });
}

/** Must run AFTER the layer/quality/status gates, BEFORE the pattern count cap. */
export function selectSmartOverlays(overlays: readonly SmartOverlay[], bars: readonly SmartBar[], maxPatterns: number): SmartOverlay[] {
  if (!bars.length) return [...overlays];
  const cap = finite(maxPatterns) ? clamp(Math.floor(maxPatterns), 0, 64) : 0;
  const byKey = new Map(bars.map((b, i) => [b.key, chartX(bars, i)]));
  const positions = new Map(bars.map((b, i) => [b.key, i]));
  const lastPosition = bars.length - 1, lastIndex = chartX(bars, lastPosition), close = bars[lastPosition].c;
  const tolerance = smartTolerance(bars);
  const other: SmartOverlay[] = [];
  const ranked = overlays.flatMap((o) => {
    if (!PATTERNS.has(o.kind) && o.kind !== 'level') { other.push(o); return []; }
    if (!finite(o.shapeQuality) || o.shapeQuality < 0 || o.shapeQuality > 1 || !finite(o.displayPriority)) return [];
    const rails = railsOf(o, byKey);
    if (!rails.length) return [];
    const distance = Math.min(...rails.map(r => Math.abs(at(r, lastIndex) - close)));
    const end = positions.get(o.formationEnd) ?? lastPosition;
    const touchIndices = (Array.isArray(o.geometry.touchAnchors) ? o.geometry.touchAnchors : [])
      .map(a => a && typeof a === 'object' ? positions.get(a.barKey) : undefined)
      .filter((x): x is number => x !== undefined);
    const lastTouch = touchIndices.length ? Math.max(...touchIndices) : end;
    const touchCount = finite(o.evidence.touches) ? o.evidence.touches : 0;
    const score = 35 * clamp(o.shapeQuality, 0, 1)
      + 20 * Math.exp(-Math.max(0, lastPosition - lastTouch) / 48)
      + 25 / (1 + distance / Math.max(tolerance * 4, close * 0.01))
      + 10 * Math.min(1, touchCount / 6)
      + (PAIRED.has(o.kind) ? 12 : 0) - (BROKEN.has(o.status) ? 35 : 0);
    return [{ overlay: o, rails, score }];
  }).sort((a, b) => b.score - a.score || a.overlay.id.localeCompare(b.overlay.id, 'en'));
  const kept: typeof ranked = [];
  let patterns = 0, levels = 0;
  const matches = (a: RailGeometry, b: RailGeometry) => {
    const start = Math.max(a.start, b.start), end = Math.min(a.end, b.end);
    const overlap = end - start;
    if (overlap < 8) return false;
    return Math.abs(at(a, end) - at(b, end)) <= tolerance * 1.5
      && Math.abs(at(a, start) - at(b, start)) <= tolerance * 2;
  };
  for (const item of ranked) {
    const level = item.overlay.kind === 'level';
    if (level ? levels >= 4 : patterns >= cap) continue;
    // A selected channel already represents its two rails. Do not draw each again.
    if (kept.some(old => item.rails.every(r => old.rails.some(s => matches(r, s))))) continue;
    kept.push(item);
    if (level) levels++; else patterns++;
  }
  return [...kept.map(({ overlay, score }) => ({ ...overlay, displayPriority: score })), ...other];
}
