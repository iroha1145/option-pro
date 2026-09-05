/** Price/time snap. Thresholds are CSS pixels. Alt disables every snap. */
import { applyAltNoSnap } from './geometry.ts';
import { snapBarIndex } from './projection.ts';
import { candidatesAtBar, type RailSnapCandidate } from './railSnap.ts';

export type SnapCandidate = RailSnapCandidate;

/** A price just beyond a plot edge must not attract a pointer still inside it. */
export function visiblePriceToY(chart: {
  convertToPixel: (finder: { gridIndex: number }, point: number[]) => unknown;
  containPixel: (finder: { gridIndex: number }, point: number[]) => boolean;
}, barIndex: number, price: number): number {
  const pixel = chart.convertToPixel({ gridIndex: 0 }, [barIndex, price]);
  if (!Array.isArray(pixel) || !pixel.every(Number.isFinite)
    || !chart.containPixel({ gridIndex: 0 }, pixel)) return Number.NaN;
  return pixel[1];
}

export interface SnapResult {
  barIndex: number | null;
  price: number;
  snapped: boolean;
  snapKind: SnapCandidate['kind'] | 'none';
}

export function nearestPrice(
  _pointerPrice: number,
  pointerY: number,
  candidates: SnapCandidate[],
  priceToY: (price: number) => number,
  thresholdPx: number,
): { price: number; kind: SnapCandidate['kind'] } | null {
  let best: { price: number; kind: SnapCandidate['kind']; dist: number } | null = null;
  for (const candidate of candidates) {
    if (!Number.isFinite(candidate.price) || candidate.price <= 0) continue;
    const dist = Math.abs(priceToY(candidate.price) - pointerY);
    if (dist <= thresholdPx && (!best || dist < best.dist)) {
      best = { price: candidate.price, kind: candidate.kind, dist };
    }
  }
  if (!best) {
    return null;
  }
  return { price: best.price, kind: best.kind };
}

export function snapPointer(args: {
  x: number;
  y: number;
  barCount: number;
  pointerPrice: number;
  candidates: SnapCandidate[];
  priceToY: (price: number) => number;
  thresholdPx: number;
  alt: boolean;
}): SnapResult {
  const barIndex = snapBarIndex(args.x, args.barCount);
  if (args.alt) {
    return applyAltNoSnap(true, {
      barIndex,
      price: args.pointerPrice,
      snapped: false,
      snapKind: 'none' as const,
    });
  }
  if (barIndex === null) {
    return { barIndex: null, price: args.pointerPrice, snapped: false, snapKind: 'none' };
  }
  const hit = nearestPrice(
    args.pointerPrice,
    args.y,
    candidatesAtBar(args.candidates, barIndex),
    args.priceToY,
    args.thresholdPx,
  );
  if (!hit) {
    return { barIndex, price: args.pointerPrice, snapped: false, snapKind: 'none' };
  }
  const snapped = Math.abs(args.priceToY(hit.price) - args.y) <= args.thresholdPx;
  return {
    barIndex,
    price: hit.price,
    snapped,
    snapKind: snapped ? hit.kind : 'none',
  };
}

export function ohlcCandidates(bar: { o: number; h: number; l: number; c: number }): SnapCandidate[] {
  return [
    { price: bar.o, kind: 'ohlc' },
    { price: bar.h, kind: 'ohlc' },
    { price: bar.l, kind: 'ohlc' },
    { price: bar.c, kind: 'ohlc' },
  ];
}

function finitePrice(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

/** Hand-draw snap from overlays that already passed analysisGate. */
export function snapCandidatesFromOverlays(
  overlays: Array<{ kind: string; status?: string; hidden?: boolean; geometry?: Record<string, unknown> }>,
): SnapCandidate[] {
  const out: SnapCandidate[] = [];
  for (const overlay of overlays) {
    if (overlay.hidden || ['invalidated', 'broken_up', 'broken_down', 'failed', 'expired'].includes(overlay.status ?? '')) continue;
    const geometry = overlay.geometry ?? {};
    if (overlay.kind === 'swing') {
      const anchors = Array.isArray(geometry.anchors) ? geometry.anchors : [];
      for (const anchor of anchors) {
        const price = finitePrice(anchor && typeof anchor === 'object' ? (anchor as { price?: unknown }).price : null);
        if (price != null) out.push({ price, kind: 'swing' });
      }
      const point = finitePrice(geometry.price);
      if (point != null) out.push({ price: point, kind: 'swing' });
      continue;
    }
    if (overlay.kind === 'level' || overlay.kind === 'pivot' || overlay.kind === 'box') {
      for (const key of ['price', 'resistanceHigh', 'resistanceLow', 'supportLow', 'supportHigh', 'pivot', 'invalidation']) {
        const price = finitePrice(geometry[key]);
        if (price != null) out.push({ price, kind: 'level' });
      }
    }
  }
  return out;
}
