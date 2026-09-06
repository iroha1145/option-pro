/** Deterministic display selection. Scores describe geometry, never win probability.
 * Receives the same closed, fingerprint-gated bars as the existing detector.
 * No viewport, live quote, network, storage or manual-drawing dependency.
 */
export interface StructuralBar {
  t: string; key: string; o: number; h: number; l: number; c: number;
  chartIndex?: number; closed?: boolean; ext?: boolean; quote_only?: boolean;
}
export interface StructuralOverlay {
  id: string; sourceId: string; algorithmVersion: string; group: string; kind: string;
  geometry: Record<string, unknown>; status: string; direction: string;
  shapeQuality: number; displayPriority: number; evidence: Record<string, unknown>;
  formationStart: string; formationEnd: string; dataThrough: string; label: string; detail: string;
}
type Side = 'support' | 'resistance';
type Tier = 'primary' | 'secondary' | 'context' | 'historical';
interface Rail { start: number; end: number; slope: number; intercept: number; side: Side }
interface Anchor { time: string; barKey: string; price: number }
const TREND = new Set(['support_trend', 'resistance_trend']);
const PAIRED = new Set(['channel', 'triangle', 'wedge']);
const BROKEN = new Set(['invalidated', 'broken_up', 'broken_down', 'failed', 'expired']);
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const clamp = (n: number, low: number, high: number) => Math.max(low, Math.min(high, n));
const xAt = (bars: readonly StructuralBar[], i: number) => bars[i].chartIndex ?? i;
const at = (r: Rail, x: number) => r.slope * x + r.intercept;
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
export function medianRange(values: readonly number[]): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b), mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}
/** Robust median true range, deliberately not labelled ATR. */
export function structuralTolerance(bars: readonly StructuralBar[]): number {
  if (!bars.length) return 0;
  const start = Math.max(0, bars.length - 32);
  const ranges = bars.slice(start).map((b, i) => {
    const prev = start + i > 0 ? bars[start + i - 1].c : b.o;
    return Math.max(b.h - b.l, Math.abs(b.h - prev), Math.abs(b.l - prev));
  });
  return Math.max(medianRange(ranges) * 0.28, bars[bars.length - 1].c * 0.0015, 1e-6);
}
export function closedStructuralBars(input: readonly StructuralBar[]): StructuralBar[] {
  const bars = input.filter(b => !b.ext && !b.quote_only && b.closed !== false);
  let previousTime = -Infinity, previousX = -Infinity;
  const keys = new Set<string>();
  for (let i = 0; i < bars.length; i++) {
    const b = bars[i], stamp = Date.parse(b.t), x = xAt(bars, i);
    if (!finite(stamp) || stamp <= previousTime || !b.key || keys.has(b.key)
      || !Number.isSafeInteger(x) || x <= previousX
      || ![b.o, b.h, b.l, b.c].every(v => finite(v) && v > 0 && v <= 10_000_000)
      || b.h < Math.max(b.o, b.c) || b.l > Math.min(b.o, b.c) || b.l > b.h) return [];
    previousTime = stamp; previousX = x; keys.add(b.key);
  }
  return bars;
}
function readAnchor(value: unknown, byKey: Map<string, number>): { x: number; y: number } | null {
  if (!record(value) || typeof value.barKey !== 'string' || !finite(value.price) || value.price <= 0) return null;
  const x = byKey.get(value.barKey);
  return x === undefined ? null : { x, y: value.price };
}
function railsOf(o: StructuralOverlay, bars: readonly StructuralBar[], byKey: Map<string, number>): Rail[] {
  const g = o.geometry;
  if (o.kind === 'level') {
    if (!finite(g.price) || g.price <= 0) return [];
    const start = byKey.get(o.formationStart), end = byKey.get(o.formationEnd);
    if (start === undefined || end === undefined || end < start) return [];
    const side = g.role === 'support' || g.role === 'resistance' ? g.role
      : g.price <= bars[bars.length - 1].c ? 'support' : 'resistance';
    return [{ start, end, slope: 0, intercept: g.price, side }];
  }
  const paired = PAIRED.has(o.kind);
  if (!paired && !TREND.has(o.kind)) return [];
  let pairs: unknown[];
  if (paired && Array.isArray(g.supportRail) && Array.isArray(g.resistanceRail)) pairs = [g.supportRail, g.resistanceRail];
  else {
    const anchors = Array.isArray(g.fitAnchors) ? g.fitAnchors : g.anchors;
    if (!Array.isArray(anchors)) return [];
    pairs = paired ? [anchors.slice(0, 2), anchors.slice(2, 4)] : [anchors];
  }
  const rails: Rail[] = [];
  for (const pair of pairs) {
    if (!Array.isArray(pair) || pair.length !== 2) return [];
    let a = readAnchor(pair[0], byKey), b = readAnchor(pair[1], byKey);
    if (!a || !b || a.x === b.x) return [];
    if (a.x > b.x) [a, b] = [b, a];
    const slope = (b.y - a.y) / (b.x - a.x);
    rails.push({ start: a.x, end: b.x, slope, intercept: a.y - slope * a.x,
      side: o.kind === 'resistance_trend' ? 'resistance' : 'support' });
  }
  if (paired) {
    const start = Math.max(...rails.map(r => r.start)), end = Math.min(...rails.map(r => r.end));
    if (end <= start || (at(rails[1], start) - at(rails[0], start)) * (at(rails[1], end) - at(rails[0], end)) <= 0) return [];
    const lower = at(rails[0], start) < at(rails[1], start) ? 0 : 1;
    rails[lower].side = 'support'; rails[1 - lower].side = 'resistance';
  }
  return rails;
}
function touchKeys(o: StructuralOverlay, r: Rail, byKey: Map<string, number>, tolerance: number): Set<string> {
  const values = Array.isArray(o.geometry.touchAnchors) ? o.geometry.touchAnchors : [];
  const keys = new Set<string>();
  for (const raw of values) {
    const p = readAnchor(raw, byKey);
    if (p && p.x >= r.start && p.x <= r.end && Math.abs(p.y - at(r, p.x)) <= tolerance * 2) {
      keys.add((raw as Record<string, unknown>).barKey as string);
    }
  }
  return keys;
}
const stateFamily = (o: StructuralOverlay) => BROKEN.has(o.status) ? o.status : 'active';

/** Reconcile single-boundary display states using closed bars before user filters.
 * Never turn an old resistance into support automatically, never revive an explicit
 * invalidation, and never refit its anchors to conceal an earlier confirmed break.
 * Paired patterns retain the server's joint lifecycle rules.
 */
export function prepareStructuralOverlays<T extends StructuralOverlay>(input: readonly T[], rawBars: readonly StructuralBar[]): T[] {
  const bars = closedStructuralBars(rawBars);
  if (!bars.length) return [];
  const byKey = new Map(bars.map((b, i) => [b.key, xAt(bars, i)]));
  const tolerance = structuralTolerance(bars), end = bars.length - 1;
  return input.flatMap(o => {
    if (!TREND.has(o.kind) && o.kind !== 'level' && !PAIRED.has(o.kind)) return [o];
    const rails = railsOf(o, bars, byKey);
    if (!rails.length) return [];
    const observedEndKeys = rails.map(r => {
      const keys = [...touchKeys(o, r, byKey, tolerance)];
      return keys.sort((a, b) => byKey.get(b)! - byKey.get(a)!)[0] ?? null;
    });
    let next: T = { ...o, geometry: { ...o.geometry, observedEndKeys }, evidence: { ...o.evidence } };
    if (rails.length !== 1 || ['invalidated', 'failed', 'expired'].includes(o.status)) return [next];
    const rail = rails[0];
    let outsideRun = 0, breakAt = -1;
    for (let i = 0; i <= end; i++) {
      if (xAt(bars, i) < rail.start) continue;
      const price = at(rail, xAt(bars, i));
      if (!finite(price) || price <= 0) return [];
      const outside = rail.side === 'support' ? bars[i].c < price - tolerance : bars[i].c > price + tolerance;
      outsideRun = outside ? outsideRun + 1 : 0;
      if (outsideRun >= 2) { breakAt = i; break; }
    }
    if (breakAt >= 0) {
      const lastX = xAt(bars, breakAt), startIndex = bars.findIndex((_, i) => xAt(bars, i) === rail.start);
      if (startIndex < 0 || lastX <= rail.start) return [];
      const anchor = (i: number): Anchor => ({ time: bars[i].t, barKey: bars[i].key, price: at(rail, xAt(bars, i)) });
      const anchors = [anchor(startIndex), anchor(breakAt)];
      // Geometry stops at the first confirmed break; a later second anchor cannot
      // restore an active lifetime. The untouched source remains available upstream.
      const rawTouches = Array.isArray(o.geometry.touchAnchors) ? o.geometry.touchAnchors : [];
      const beforeBreak = rawTouches.filter(a => {
        const p = readAnchor(a, byKey); return p && p.x >= rail.start && p.x < lastX && Math.abs(p.y - at(rail, p.x)) <= tolerance * 2;
      });
      next = { ...next, status: rail.side === 'support' ? 'broken_down' : 'broken_up',
        formationEnd: bars[breakAt].key,
        geometry: { ...next.geometry, ...(o.kind === 'level' ? {} : { anchors, fitAnchors: anchors }),
          touchAnchors: beforeBreak, breakBarKey: bars[breakAt].key },
        evidence: { ...next.evidence, ...(rawTouches.length ? { touches: new Set(beforeBreak.map(a => (a as Record<string, unknown>).barKey)).size } : {}),
          visualState: rail.side === 'support' ? 'broken_down' : 'broken_up',
          stateBasis: 'two-closed-bars', stateTolerance: tolerance } };
    } else if (!BROKEN.has(o.status)) {
      const price = at(rail, xAt(bars, end));
      const outside = rail.side === 'support' ? bars[end].c < price - tolerance : bars[end].c > price + tolerance;
      next = { ...next, status: outside || Math.abs(bars[end].c - price) <= tolerance * 2 ? 'testing' : 'forming',
        evidence: { ...next.evidence, visualState: outside
          ? rail.side === 'support' ? 'breakdown_pending' : 'breakout_pending' : 'active',
        stateBasis: 'closed-bars-only', stateTolerance: tolerance } };
    }
    return [next];
  });
}

/** Geometric deduplication is semantic and symmetric. A shared right endpoint is
 * insufficient; require a common formation interval and compatible boundary roles.
 * Related fits may use the wider tolerance only with two shared touch events.
 */
export function selectStructuralOverlays<T extends StructuralOverlay>(input: readonly T[], rawBars: readonly StructuralBar[], maxPatterns: number): T[] {
  const bars = closedStructuralBars(rawBars);
  if (!bars.length) return [];
  const byKey = new Map(bars.map((b, i) => [b.key, xAt(bars, i)]));
  const positions = new Map(bars.map((b, i) => [b.key, i]));
  const tolerance = structuralTolerance(bars), last = bars.length - 1, x = xAt(bars, last), close = bars[last].c;
  const cap = finite(maxPatterns) ? clamp(Math.floor(maxPatterns), 0, 64) : 0;
  const other: T[] = [];
  const ranked = input.flatMap(o => {
    if (!TREND.has(o.kind) && !PAIRED.has(o.kind) && o.kind !== 'level') { other.push(o); return []; }
    if (!finite(o.shapeQuality) || o.shapeQuality < 0 || o.shapeQuality > 1 || !finite(o.displayPriority)) return [];
    const rails = railsOf(o, bars, byKey);
    if (!rails.length) return [];
    const keys = rails.map(r => touchKeys(o, r, byKey, tolerance));
    const indices = [...new Set(keys.flatMap(k => [...k]))].map(k => positions.get(k)!);
    const lastTouch = indices.length ? Math.max(...indices) : positions.get(o.formationEnd) ?? last;
    const age = last - lastTouch, distance = Math.min(...rails.map(r => Math.abs(at(r, x) - close)));
    const touches = finite(o.evidence.touches) ? o.evidence.touches : 0;
    const score = 35 * o.shapeQuality + 20 * Math.exp(-Math.max(0, age) / 48)
      + 25 / (1 + distance / Math.max(tolerance * 4, close * 0.01))
      + 10 * Math.min(1, touches / 6) + (PAIRED.has(o.kind) ? 12 : 0) - (BROKEN.has(o.status) ? 35 : 0);
    return [{ overlay: { ...o, evidence: { ...o.evidence } }, rails, keys, score, age, distance }];
  }).sort((a, b) => b.score - a.score || a.overlay.id.localeCompare(b.overlay.id, 'en'));
  type Candidate = typeof ranked[number];
  const matches = (a: Candidate, ai: number, b: Candidate, bi: number): boolean => {
    if (stateFamily(a.overlay) !== stateFamily(b.overlay)) return false;
    const ar = a.rails[ai], br = b.rails[bi];
    if (ar.side !== br.side) return false;
    const start = Math.max(ar.start, br.start), end = Math.min(ar.end, br.end), overlap = end - start;
    const shortSpan = Math.min(ar.end - ar.start, br.end - br.start);
    if (overlap < 8 || overlap < shortSpan * 0.5) return false;
    const endpoints = Math.max(Math.abs(at(ar, start) - at(br, start)), Math.abs(at(ar, end) - at(br, end)));
    const tailDistance = Math.abs(at(ar, x) - at(br, x));
    const drift = Math.abs(ar.slope - br.slope) * overlap;
    if (endpoints <= tolerance * 1.5 && tailDistance <= tolerance * 2 && drift <= tolerance * 2) return true;
    const shared = [...a.keys[ai]].filter(key => b.keys[bi].has(key)).length;
    return shared >= 2 && shared / Math.max(1, Math.min(a.keys[ai].size, b.keys[bi].size)) >= 0.5
      && endpoints <= tolerance * 3 && tailDistance <= tolerance * 3 && drift <= tolerance * 2;
  };
  const contains = (a: Candidate, b: Candidate) => b.rails.every((_, bi) => a.rails.some((__, ai) => matches(a, ai, b, bi)));
  const fold = (winner: Candidate, duplicate: Candidate) => {
    const old = Array.isArray(winner.overlay.evidence.alternativeSources) ? winner.overlay.evidence.alternativeSources : [];
    winner.overlay.evidence = { ...winner.overlay.evidence,
      // Counts candidates, not confirmations. Never add their touches or scores.
      collapsedCandidates: (finite(winner.overlay.evidence.collapsedCandidates) ? winner.overlay.evidence.collapsedCandidates : 0) + 1,
      alternativeSources: [...old, { id: duplicate.overlay.id, source: duplicate.overlay.sourceId,
        kind: duplicate.overlay.kind }].slice(0, 8) };
  };
  const unique: Candidate[] = [];
  for (const item of ranked) {
    const duplicate = unique.find(old => contains(old, item));
    if (duplicate) { fold(duplicate, item); continue; }
    // A complete channel may replace its already-selected standalone rail, but
    // not when doing so would discard a substantially higher-quality boundary.
    const represented = unique.filter(old => old.rails.length === 1 && item.rails.length === 2 && contains(item, old));
    if (represented.some(old => old.overlay.shapeQuality > item.overlay.shapeQuality + 0.05)) continue;
    for (const old of represented) { fold(item, old); unique.splice(unique.indexOf(old), 1); }
    unique.push(item);
  }
  unique.sort((a, b) => b.score - a.score || a.overlay.id.localeCompare(b.overlay.id, 'en'));
  let patterns = 0, levels = 0;
  const primarySides = new Set<Side>();
  const selected: T[] = [];
  for (const item of unique) {
    const level = item.overlay.kind === 'level';
    if (level ? levels >= 4 : patterns >= cap) continue;
    if (level) levels++; else patterns++;
    const far = item.distance > Math.max(tolerance * 16, close * 0.1) || item.age > 32;
    const historical = BROKEN.has(item.overlay.status);
    const primary = !historical && !far && item.rails.some(r => !primarySides.has(r.side));
    const tier: Tier = historical ? 'historical' : far ? 'context' : primary ? 'primary' : 'secondary';
    if (primary) item.rails.forEach(r => primarySides.add(r.side));
    selected.push({ ...item.overlay, displayPriority: item.score,
      evidence: { ...item.overlay.evidence, displayTier: tier, lastTouchAge: item.age,
        distanceInTolerance: item.distance / tolerance, selectionVersion: 'structural-v3' } });
  }
  // Background regions have a separate bounded budget and never consume a trend
  // slot. Keep indicators/events untouched. Do not merge unrelated gap intervals.
  for (const kind of ['box', 'gap']) {
    const regions = other.filter(o => o.kind === kind).sort((a, b) => b.displayPriority - a.displayPriority || a.id.localeCompare(b.id, 'en'));
    selected.push(...regions.slice(0, kind === 'box' ? 2 : 4));
  }
  return [...selected, ...other.filter(o => o.kind !== 'box' && o.kind !== 'gap')];
}
