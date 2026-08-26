/** CSS-pixel hit testing. Thresholds stay in CSS px under page zoom / high DPR. */
import { distancePointToSegment } from './geometry.ts';
import type { ChartDrawing, Point, Segment } from './types.ts';

export const DESKTOP_LINE_TOLERANCE_PX = 8;
export const TOUCH_LINE_TOLERANCE_PX = 20;
export const DESKTOP_ANCHOR_TOLERANCE_PX = 8;
export const TOUCH_ANCHOR_TOLERANCE_PX = 18;

export type PointerKind = 'mouse' | 'touch' | 'pen';

export interface ProjectedDrawing {
  id: string;
  zOrder: number;
  locked: boolean;
  hidden: boolean;
  anchors: Point[];
  segments: Segment[];
  fills: Point[][];
}

export interface HitResult {
  id: string;
  kind: 'anchor' | 'body';
  anchorIndex: number;
  distance: number;
  zOrder: number;
}

export function lineTolerancePx(pointer: PointerKind): number {
  return pointer === 'touch' ? TOUCH_LINE_TOLERANCE_PX : DESKTOP_LINE_TOLERANCE_PX;
}

export function anchorTolerancePx(pointer: PointerKind): number {
  return pointer === 'touch' ? TOUCH_ANCHOR_TOLERANCE_PX : DESKTOP_ANCHOR_TOLERANCE_PX;
}

export function pointInPolygon(point: Point, polygon: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const xi = polygon[i].x;
    const yi = polygon[i].y;
    const xj = polygon[j].x;
    const yj = polygon[j].y;
    const intersect = yi > point.y !== yj > point.y
      && point.x < ((xj - xi) * (point.y - yi)) / (yj - yi + 1e-12) + xi;
    if (intersect) inside = !inside;
  }
  return inside;
}

export function hitTestProjected(
  drawing: ProjectedDrawing,
  pointer: Point,
  pointerKind: PointerKind,
): HitResult | null {
  if (drawing.hidden) return null;
  const anchorTol = anchorTolerancePx(pointerKind);
  let bestAnchor = -1;
  let bestAnchorDist = Infinity;
  drawing.anchors.forEach((anchor, index) => {
    const dist = Math.hypot(pointer.x - anchor.x, pointer.y - anchor.y);
    if (dist <= anchorTol && dist < bestAnchorDist) {
      bestAnchor = index;
      bestAnchorDist = dist;
    }
  });
  if (bestAnchor >= 0) {
    return {
      id: drawing.id,
      kind: 'anchor',
      anchorIndex: bestAnchor,
      distance: bestAnchorDist,
      zOrder: drawing.zOrder,
    };
  }
  const lineTol = lineTolerancePx(pointerKind);
  let bestSeg = Infinity;
  for (const segment of drawing.segments) {
    const dist = distancePointToSegment(pointer, segment.a, segment.b);
    if (dist < bestSeg) bestSeg = dist;
  }
  if (bestSeg <= lineTol) {
    return {
      id: drawing.id,
      kind: 'body',
      anchorIndex: -1,
      distance: bestSeg,
      zOrder: drawing.zOrder,
    };
  }
  for (const fill of drawing.fills) {
    if (fill.length >= 3 && pointInPolygon(pointer, fill)) {
      return {
        id: drawing.id,
        kind: 'body',
        anchorIndex: -1,
        distance: 0,
        zOrder: drawing.zOrder,
      };
    }
  }
  return null;
}

/**
 * Overlap rule: anchors beat bodies; the currently selected drawing beats
 * others; then higher zOrder; then closer distance.
 */
export function pickTopHit(
  hits: HitResult[],
  selectedId: string | null,
): HitResult | null {
  if (!hits.length) return null;
  const ranked = [...hits].sort((a, b) => {
    if (a.kind === 'anchor' && b.kind !== 'anchor') return -1;
    if (b.kind === 'anchor' && a.kind !== 'anchor') return 1;
    if (selectedId) {
      if (a.id === selectedId && b.id !== selectedId) return -1;
      if (b.id === selectedId && a.id !== selectedId) return 1;
    }
    if (a.zOrder !== b.zOrder) return b.zOrder - a.zOrder;
    return a.distance - b.distance;
  });
  return ranked[0];
}

export function hitTestDrawings(
  drawings: ProjectedDrawing[],
  pointer: Point,
  pointerKind: PointerKind,
  selectedId: string | null,
): HitResult | null {
  const hits: HitResult[] = [];
  for (const drawing of drawings) {
    const hit = hitTestProjected(drawing, pointer, pointerKind);
    if (hit) hits.push(hit);
  }
  return pickTopHit(hits, selectedId);
}

export function isLockedDragBlocked(drawing: Pick<ChartDrawing, 'locked'>): boolean {
  return drawing.locked === true;
}
