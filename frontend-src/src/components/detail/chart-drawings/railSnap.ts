/** Resolve a sloping snap target at the pointer's bar, not at an old endpoint price. */
import { normalizePatternSegments } from './linePresentation.ts';
export interface RailSnapCandidate {
  price: number;
  kind: 'ohlc' | 'swing' | 'ma20' | 'level' | 'anchor';
  rail?: { from: number; to: number; startPrice: number; endPrice: number };
  atIndex?: number;
}
export function candidatesAtBar(candidates: readonly RailSnapCandidate[], barIndex: number): RailSnapCandidate[] {
  if (!Number.isFinite(barIndex)) return [];
  return candidates.flatMap(candidate => {
    if (candidate.atIndex !== undefined && Math.abs(candidate.atIndex - barIndex) > 1) return [];
    const r = candidate.rail;
    if (!r) return Number.isFinite(candidate.price) && candidate.price > 0 ? [candidate] : [];
    if (![r.from, r.to, r.startPrice, r.endPrice].every(Number.isFinite)
      || r.to <= r.from || barIndex < r.from || barIndex > r.to) return [];
    const price = r.startPrice + (r.endPrice - r.startPrice) * (barIndex - r.from) / (r.to - r.from);
    return Number.isFinite(price) && price > 0 ? [{ ...candidate, price }] : [];
  });
}
export function railCandidatesFromOverlays(
  overlays: readonly { kind: string; status?: string; shapeQuality?: number; hidden?: boolean; geometry?: Record<string, unknown> }[],
  keys: readonly string[],
): RailSnapCandidate[] {
  const index = new Map(keys.map((key, i) => [key, i]));
  if (index.size !== keys.length) return [];
  const result: RailSnapCandidate[] = [];
  for (const overlay of overlays) {
    if (!['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge'].includes(overlay.kind)) continue;
    if (overlay.hidden || (overlay.shapeQuality !== undefined
      && (!Number.isFinite(overlay.shapeQuality) || overlay.shapeQuality < 0 || overlay.shapeQuality > 1))) continue;
    if (['invalidated', 'broken_up', 'broken_down', 'failed', 'expired'].includes(overlay.status ?? '')) continue;
    const g = overlay.geometry ?? {};
    const pairs = Array.isArray(g.supportRail) && Array.isArray(g.resistanceRail)
      ? [g.supportRail, g.resistanceRail]
      : [Array.isArray(g.fitAnchors) ? g.fitAnchors : g.anchors];
    const segments: Array<{ a: { x: number; y: number }; b: { x: number; y: number } }> = [];
    let invalid = false;
    for (const raw of pairs) {
      if (!Array.isArray(raw)) { invalid = true; break; }
      const groups = raw.length >= 4 ? [raw.slice(0, 2), raw.slice(2, 4)] : [raw];
      for (const pair of groups) {
        const a = pair[0], b = pair[1];
        if (!a || !b || typeof a.price !== 'number' || typeof b.price !== 'number'
          || typeof a.time !== 'string' || !a.time || typeof b.time !== 'string' || !b.time) { invalid = true; break; }
        const from = index.get(a.barKey), to = index.get(b.barKey);
        if (from === undefined || to === undefined || !Number.isFinite(a.price) || !Number.isFinite(b.price)
          || a.price <= 0 || b.price <= 0) { invalid = true; break; }
        segments.push({ a: { x: from, y: a.price }, b: { x: to, y: b.price } });
      }
    }
    if (invalid) continue;
    for (const { a, b } of normalizePatternSegments(segments, overlay.kind)) {
      result.push({ price: a.y, kind: 'level', rail: { from: a.x, to: b.x, startPrice: a.y, endPrice: b.y } });
    }
  }
  return result;
}
