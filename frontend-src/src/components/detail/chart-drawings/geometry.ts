/**
 * Side-effect-free drawing geometry in a generic 2-D plane.
 * Callers map time/price ↔ x/y; this module never touches the DOM or ECharts.
 */
import { FIB_RATIOS, type Point, type Segment, type VisibleRect } from './types.ts';

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Distance from point P to segment AB in the same plane units (CSS px in hit-tests). */
export function distancePointToSegment(p: Point, a: Point, b: Point): number {
  const vx = b.x - a.x;
  const vy = b.y - a.y;
  const wx = p.x - a.x;
  const wy = p.y - a.y;
  const v2 = vx * vx + vy * vy;
  if (v2 === 0) return Math.hypot(wx, wy);
  let t = (wx * vx + wy * vy) / v2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(p.x - (a.x + t * vx), p.y - (a.y + t * vy));
}

export function horizontalProjection(price: number, xMin: number, xMax: number): Segment {
  return {
    a: { x: xMin, y: price },
    b: { x: xMax, y: price },
  };
}

export function segmentEndpoints(a: Point, b: Point): Segment {
  return { a: { ...a }, b: { ...b } };
}

/**
 * Clip a ray that starts at `origin` and passes through `through` to the visible
 * rectangle. Uses Liang–Barsky so the far end is a real intersection with the
 * price-grid boundary — never a huge fake coordinate.
 */
export function clipRayToRect(origin: Point, through: Point, rect: VisibleRect): Segment | null {
  const dx = through.x - origin.x;
  const dy = through.y - origin.y;
  if (dx === 0 && dy === 0) return null;
  let t0 = 0;
  let t1 = Number.POSITIVE_INFINITY;
  const p = [-dx, dx, -dy, dy];
  const q = [
    origin.x - rect.xMin,
    rect.xMax - origin.x,
    origin.y - rect.yMin,
    rect.yMax - origin.y,
  ];
  for (let i = 0; i < 4; i++) {
    const pi = p[i];
    const qi = q[i];
    if (pi === 0) {
      if (qi < 0) return null;
      continue;
    }
    const t = qi / pi;
    if (pi < 0) t0 = Math.max(t0, t);
    else t1 = Math.min(t1, t);
    if (t0 > t1) return null;
  }
  if (!Number.isFinite(t1)) {
    // Ray is parallel to an infinite slab; still clip to the far rectangle edge
    // by intersecting with the four sides for t >= 0 inside the rect.
    t1 = t0;
    const candidates: number[] = [];
    if (dx !== 0) {
      candidates.push((rect.xMin - origin.x) / dx, (rect.xMax - origin.x) / dx);
    }
    if (dy !== 0) {
      candidates.push((rect.yMin - origin.y) / dy, (rect.yMax - origin.y) / dy);
    }
    const inside = candidates.filter((t) => t >= t0 && Number.isFinite(t));
    if (!inside.length) return null;
    t1 = Math.max(...inside);
  }
  const start = { x: origin.x + t0 * dx, y: origin.y + t0 * dy };
  const end = { x: origin.x + t1 * dx, y: origin.y + t1 * dy };
  return { a: start, b: end };
}

export function channelOffset(p1: Point, p2: Point, p3: Point): Point {
  const vx = p2.x - p1.x;
  const vy = p2.y - p1.y;
  const wx = p3.x - p1.x;
  const wy = p3.y - p1.y;
  const v2 = vx * vx + vy * vy;
  if (v2 === 0) return { x: wx, y: wy };
  const proj = (wx * vx + wy * vy) / v2;
  return { x: wx - proj * vx, y: wy - proj * vy };
}

export function channelEdges(p1: Point, p2: Point, p3: Point): {
  main: Segment;
  parallel: Segment;
  fill: Point[];
} {
  const o = channelOffset(p1, p2, p3);
  const q1 = { x: p1.x + o.x, y: p1.y + o.y };
  const q2 = { x: p2.x + o.x, y: p2.y + o.y };
  return {
    main: { a: { ...p1 }, b: { ...p2 } },
    parallel: { a: q1, b: q2 },
    fill: [{ ...p1 }, { ...p2 }, q2, q1],
  };
}

export function vectorsParallel(a: Point, b: Point, c: Point, d: Point, eps = 1e-6): boolean {
  const vx = b.x - a.x;
  const vy = b.y - a.y;
  const ux = d.x - c.x;
  const uy = d.y - c.y;
  const cross = vx * uy - vy * ux;
  const scale = Math.hypot(vx, vy) * Math.hypot(ux, uy) + 1e-12;
  return Math.abs(cross) <= eps * scale;
}

export function moveChannelWhole(
  p1: Point,
  p2: Point,
  p3: Point,
  dx: number,
  dy: number,
): [Point, Point, Point] {
  const shift = (p: Point): Point => ({ x: p.x + dx, y: p.y + dy });
  return [shift(p1), shift(p2), shift(p3)];
}

/**
 * Keep the two boundaries strictly parallel.
 * P1/P2 define the main line; P3 defines width. Moving P1 keeps P3's offset
 * from P1; moving P2 leaves the offset (from P1) unchanged; moving P3 only
 * changes the perpendicular width.
 */
export function moveChannelAnchor(
  p1: Point,
  p2: Point,
  p3: Point,
  index: 0 | 1 | 2,
  next: Point,
): [Point, Point, Point] {
  if (index === 2) {
    return [p1, p2, { ...next }];
  }
  if (index === 0) {
    const dx = next.x - p1.x;
    const dy = next.y - p1.y;
    return [next, p2, { x: p3.x + dx, y: p3.y + dy }];
  }
  return [p1, next, p3];
}

export function normalizeRectangle(
  a: Point,
  b: Point,
): { x0: number; x1: number; y0: number; y1: number } {
  return {
    x0: Math.min(a.x, b.x),
    x1: Math.max(a.x, b.x),
    y0: Math.min(a.y, b.y),
    y1: Math.max(a.y, b.y),
  };
}

export function fibonacciPrices(
  startPrice: number,
  endPrice: number,
): { ratio: number; price: number }[] {
  return FIB_RATIOS.map((ratio) => ({
    ratio,
    price: startPrice + (endPrice - startPrice) * ratio,
  }));
}

/** Shift: snap the free end to 0 / 45 / 90 / … degrees in the working plane. */
export function constrainByShift(from: Point, to: Point): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  if (dx === 0 && dy === 0) return { ...to };
  const angle = Math.atan2(dy, dx);
  const snapped = Math.round(angle / (Math.PI / 4)) * (Math.PI / 4);
  const len = Math.hypot(dx, dy);
  return {
    x: from.x + Math.cos(snapped) * len,
    y: from.y + Math.sin(snapped) * len,
  };
}

export function applyAltNoSnap<T extends { snapped?: boolean }>(
  alt: boolean,
  candidate: T,
): T {
  if (!alt) return candidate;
  return { ...candidate, snapped: false };
}

export function nudgePoint(point: Point, key: string, large: boolean): Point {
  const step = large ? 5 : 1;
  if (key === 'ArrowLeft') return { x: point.x - step, y: point.y };
  if (key === 'ArrowRight') return { x: point.x + step, y: point.y };
  if (key === 'ArrowUp') return { x: point.x, y: point.y + step };
  if (key === 'ArrowDown') return { x: point.x, y: point.y - step };
  return point;
}
