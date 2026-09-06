/** Chart ink is independent of the candle up/down preference. Values are CSS pixels. */
export const LINE_INK = Object.freeze({
  support: '#0E647F', resistance: '#8D299B', channel: '#4F46E5',
  neutral: '#52617A', manual: '#2E46E0', surface: '#FFFFFF', gap: '#B87821',
});
interface Point { x: number; y: number }
interface Segment { a: Point; b: Point }
interface Context { xMin: number; xMax: number; yMin: number; yMax: number }
export interface PatternInkInput {
  id: string;
  kind: string;
  status: string;
  confidence: number;
  hidden?: boolean;
  label?: string;
  color?: string;
  tier?: 'primary' | 'secondary' | 'context' | 'historical';
  observedEnds?: Array<number | null>;
  labelPriority?: number;
}
export interface PatternInkMarks {
  lines: object[];
  areas: object[];
  points: object[];
  polygons: { vertices: Point[]; color: string; opacity: number }[];
  unresolvedIds: string[];
}
const BROKEN = new Set(['invalidated', 'broken_up', 'broken_down', 'failed', 'expired']);
const PAIRED = new Set(['channel', 'triangle', 'wedge']);
const valid = (p: Point) => Number.isFinite(p.x) && Number.isFinite(p.y) && p.y > 0;
const value = (s: Segment, x: number) => s.a.y + (s.b.y - s.a.y) * (x - s.a.x) / (s.b.x - s.a.x);

/** Shared by drawing, candidate selection and snapping: invisible geometry is not a target. */
export function normalizePatternSegments(input: Segment[], kind: string): Segment[] {
  let segments = input.map(s => s.a.x <= s.b.x ? s : { a: s.b, b: s.a });
  if (!segments.length || segments.some(s => !valid(s.a) || !valid(s.b))) return [];
  if (kind === 'box') return segments;
  if (segments.some(s => s.b.x - s.a.x < 1)) return [];
  if (!PAIRED.has(kind)) return segments.length === 1 ? segments : [];
  if (segments.length !== 2) return [];
  const start = Math.max(...segments.map(s => s.a.x));
  const end = Math.min(...segments.map(s => s.b.x));
  if (end <= start) return [];
  const gapStart = value(segments[1], start) - value(segments[0], start);
  const gapEnd = value(segments[1], end) - value(segments[0], end);
  if (!Number.isFinite(gapStart * gapEnd) || gapStart * gapEnd <= 0) return [];
  segments = segments.map(s => ({ a: { x: start, y: value(s, start) }, b: { x: end, y: value(s, end) } }));
  return segments;
}

/** Preserve explicitly saved manual widths. A small white keyline separates intersecting ink. */
export function manualLineInk(color: string, width: number, dash: string | number[] = 'solid') {
  return { color, width: Number.isFinite(width) ? Math.max(1, Math.min(4, width)) : 3,
    type: dash, opacity: 1, cap: 'round' as const, join: 'round' as const,
    shadowColor: 'rgba(255,255,255,0.95)', shadowBlur: 3 };
}

/** Extend only to existing chart bars, never through a wedge apex or into negative prices. */
export function projectPatternRails(segments: Segment[], kind: string, status: string, ctx: Context): Segment[] {
  if (BROKEN.has(status) || kind === 'box' || ![ctx.xMin, ctx.xMax, ctx.yMin, ctx.yMax].every(Number.isFinite)
    || ctx.xMax < ctx.xMin || ctx.yMax <= Math.max(0, ctx.yMin)) return [];
  const sorted = segments.map(s => s.a.x <= s.b.x ? s : { a: s.b, b: s.a });
  if (!sorted.length || sorted.some(s => !valid(s.a) || !valid(s.b) || s.b.x - s.a.x < 1)) return [];
  let stop = ctx.xMax;
  if (PAIRED.has(kind) && sorted.length === 2) {
    const [a, b] = sorted;
    const mA = (a.b.y - a.a.y) / (a.b.x - a.a.x);
    const mB = (b.b.y - b.a.y) / (b.b.x - b.a.x);
    const end = Math.max(a.b.x, b.b.x);
    const gap = value(b, end) - value(a, end);
    const closing = mB - mA;
    if (Math.abs(closing) > 1e-12) {
      const apex = end - gap / closing;
      if (apex >= Math.min(a.a.x, b.a.x)) stop = Math.min(stop, apex - 0.5);
    }
  }
  const extensions: Segment[] = [];
  for (const s of sorted) {
    let end = Math.min(stop, s.b.x + Math.min(48, Math.max(8, (s.b.x - s.a.x) * 0.75)));
    const slope = (s.b.y - s.a.y) / (s.b.x - s.a.x);
    const yMin = Math.max(1e-6, ctx.yMin), yMax = ctx.yMax;
    if (slope > 0 && value(s, end) > yMax) end = Math.min(end, s.b.x + (yMax - s.b.y) / slope);
    if (slope < 0 && value(s, end) < yMin) end = Math.min(end, s.b.x + (yMin - s.b.y) / slope);
    const target = { x: end, y: value(s, end) };
    if (end > s.b.x + 0.01 && valid(target)) extensions.push({ a: s.b, b: target });
  }
  return extensions;
}

export function autoLineColor(kind: string): string {
  if (kind === 'support_trend') return LINE_INK.support;
  if (kind === 'resistance_trend') return LINE_INK.resistance;
  return PAIRED.has(kind) ? LINE_INK.channel : LINE_INK.neutral;
}

/** Share the fallback role between the plotted level and its text description. */
export function isSupportLevel(geometry: Record<string, unknown>, lastClose: number | null | undefined): boolean {
  if (geometry.role === 'support') return true;
  if (geometry.role === 'resistance') return false;
  const price = typeof geometry.price === 'number' && Number.isFinite(geometry.price) ? geometry.price : 0;
  return price <= (typeof lastClose === 'number' && Number.isFinite(lastClose) ? lastClose : price);
}

/** Strong foreground boundaries, quiet background structures. Manual ink stays separate. */
export function automaticLineInk(tier: PatternInkInput['tier'], broken = false) {
  if (broken || tier === 'historical') return { width: 1.25, opacity: 0.3, extensionOpacity: 0, labelPriority: 10 };
  if (tier === 'context') return { width: 1.2, opacity: 0.35, extensionOpacity: 0.24, labelPriority: 20 };
  if (tier === 'secondary') return { width: 1.5, opacity: 0.58, extensionOpacity: 0.4, labelPriority: 50 };
  return { width: 2.4, opacity: 1, extensionOpacity: 0.72, labelPriority: 100 };
}

/** Solids stop at the last confirmed touch when supplied. A dashed continuation
 * is a geometric extension, not evidence of repeated testing. No stroke animation.
 */
export function renderPatternInk(
  pattern: PatternInkInput,
  geometry: { segments: Segment[]; fill: Point[] | null },
  ctx: Context,
): PatternInkMarks {
  const out: PatternInkMarks = { lines: [], areas: [], points: [], polygons: [], unresolvedIds: [] };
  if (pattern.hidden || !Number.isFinite(pattern.confidence) || pattern.confidence < 0 || pattern.confidence > 100) return out;
  const segments = normalizePatternSegments(geometry.segments, pattern.kind);
  if (!segments.length) return out;
  const broken = BROKEN.has(pattern.status), baseColor = autoLineColor(pattern.kind);
  if (pattern.kind === 'box') {
    const fill = geometry.fill;
    if (!fill?.length || !fill.every(valid)) return out;
    const xs = fill.map(p => p.x), ys = fill.map(p => p.y);
    out.areas.push([
      { xAxis: Math.min(...xs), yAxis: Math.min(...ys),
        itemStyle: { color: LINE_INK.neutral, opacity: broken ? 0.025 : 0.075 },
        label: { show: Boolean(pattern.label), formatter: pattern.label ?? '', position: 'insideTopLeft',
          color: LINE_INK.neutral, fontSize: 10 } },
      { xAxis: Math.max(...xs), yAxis: Math.max(...ys) },
    ]);
    for (const segment of segments) out.lines.push([
      { coord: [segment.a.x, segment.a.y], clipToPlot: true,
        lineStyle: { color: LINE_INK.neutral, width: 0.9, opacity: broken ? 0.18 : 0.3,
          type: broken ? [2, 4] : [4, 4] }, label: { show: false } },
      { coord: [segment.b.x, segment.b.y] },
    ]);
    return out;
  }
  const paired = PAIRED.has(pattern.kind) && segments.length === 2;
  if (PAIRED.has(pattern.kind) && !paired) return out;
  const ink = automaticLineInk(pattern.tier, broken);
  const observed = segments.map((s, i) => {
    const raw = pattern.observedEnds?.[i];
    const end = !broken && typeof raw === 'number' && Number.isFinite(raw) && raw > s.a.x
      ? Math.max(s.a.x + 1, Math.min(s.b.x, raw)) : s.b.x;
    return { a: s.a, b: { x: end, y: value(s, end) } };
  });
  const projections = projectPatternRails(segments, pattern.kind, pattern.status, ctx);
  const meanY = (s: Segment) => (s.a.y + s.b.y) / 2;
  const lower = paired && meanY(segments[0]) > meanY(segments[1]) ? 1 : 0;
  const priceText = (price: number) => price.toLocaleString('en-US', { maximumFractionDigits: price < 1 ? 4 : 2 });
  observed.forEach((segment, i) => {
    const color = paired ? i === lower ? LINE_INK.support : LINE_INK.resistance : baseColor;
    const original = segments[i];
    const beyond = projections.find(p => p.a.x === original.b.x && p.a.y === original.b.y);
    const target = beyond?.b ?? original.b;
    // Split ink, not geometry: snapping continues to target exactly the same rail.
    const extension = target.x > segment.b.x + 0.01 ? { a: segment.b, b: target } : undefined;
    const label = (tail: Point) => i === 0 && pattern.label ? {
      show: true, formatter: `${pattern.label} · ${priceText(tail.y)}`,
      position: 'insideEndTop', distance: 4, fontSize: 11, lineHeight: 14,
      priority: (pattern.labelPriority ?? 0) + ink.labelPriority,
      color, backgroundColor: 'rgba(255,255,255,0.97)', borderColor: color,
      borderWidth: 0.5, borderRadius: 4, padding: [2, 5],
    } : { show: false };
    out.lines.push([
      { coord: [segment.a.x, segment.a.y], clipToPlot: true,
        lineStyle: { ...manualLineInk(color, ink.width, broken ? [2, 4] : 'solid'), opacity: ink.opacity },
        label: extension ? { show: false } : label(segment.b) },
      { coord: [segment.b.x, segment.b.y] },
    ]);
    if (extension) out.lines.push([
      { coord: [extension.a.x, extension.a.y], clipToPlot: true,
        lineStyle: { ...manualLineInk(color, Math.max(1, ink.width - 0.6), [7, 4]), opacity: ink.extensionOpacity },
        label: label(extension.b) },
      { coord: [extension.b.x, extension.b.y] },
    ]);
  });
  const fill = paired ? [segments[0].a, segments[0].b, segments[1].b, segments[1].a] : geometry.fill;
  if (!broken && fill?.length && fill.every(valid)) {
    const xs = fill.map(p => p.x), ys = fill.map(p => p.y);
    if (new Set(xs).size === 2 && new Set(ys).size === 2) out.areas.push([
      { xAxis: Math.min(...xs), yAxis: Math.min(...ys), itemStyle: { color: baseColor, opacity: 0.04 } },
      { xAxis: Math.max(...xs), yAxis: Math.max(...ys) },
    ]);
    else out.polygons.push({ vertices: fill, color: baseColor, opacity: 0.04 });
  }
  return out;
}
