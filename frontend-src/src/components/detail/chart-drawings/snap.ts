/** Price/time snap. Thresholds are CSS pixels. Alt disables every snap. */
import { applyAltNoSnap } from './geometry.ts';
import { snapBarIndex } from './projection.ts';

export interface SnapCandidate {
  price: number;
  kind: 'ohlc' | 'swing' | 'ma20' | 'level' | 'anchor';
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
    args.candidates,
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
