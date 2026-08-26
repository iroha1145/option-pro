/** CSS-pixel hit testing. Thresholds stay in CSS px under page zoom / high DPR. */
import { distancePointToSegment } from './geometry.ts';
import type { ChartDrawing, Point, Segment } from './types.ts';

export const DESKTOP_LINE_TOLERANCE_PX = 8;
export const TOUCH_LINE_TOLERANCE_PX = 20;
export const DESKTOP_ANCHOR_TOLERANCE_PX = 8;
export const TOUCH_ANCHOR_TOLERANCE_PX = 18;

export type PointerKind = 'mouse' | 'touch' | 'pen';

/** 文字标签的渲染度量（renderer 的 markPoint label：11px，position:'right'）。 */
export const TEXT_LABEL_FONT_PX = 11;
export const TEXT_LABEL_CHAR_PX = 6.2;
export const TEXT_LABEL_OFFSET_PX = 8;
export const TEXT_LABEL_PAD_PX = 3;

export interface ProjectedDrawing {
  id: string;
  zOrder: number;
  locked: boolean;
  hidden: boolean;
  anchors: Point[];
  segments: Segment[];
  fills: Point[][];
  /** 文字图形的可见标签；命中区靠它撑开，不然只剩 6px 的锚点圆。 */
  label?: { text: string } | null;
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

/**
 * 文字标签在像素空间的包围盒（左上/右下）。渲染把空白折叠成单空格并截到 240
 * 字，命中区必须按同一份文本算，否则点在字上会落进取消选中分支。
 */
export function textLabelBox(
  anchor: Point,
  text: string,
): { x0: number; y0: number; x1: number; y1: number } {
  const clean = text.replace(/\s+/g, ' ').slice(0, 240);
  const width = Math.max(TEXT_LABEL_CHAR_PX, clean.length * TEXT_LABEL_CHAR_PX);
  const half = TEXT_LABEL_FONT_PX / 2 + TEXT_LABEL_PAD_PX;
  const x0 = anchor.x + TEXT_LABEL_OFFSET_PX - TEXT_LABEL_PAD_PX;
  return { x0, y0: anchor.y - half, x1: x0 + width + TEXT_LABEL_PAD_PX * 2, y1: anchor.y + half };
}

function boxContains(
  box: { x0: number; y0: number; x1: number; y1: number },
  point: Point,
  tolerance: number,
): boolean {
  return point.x >= box.x0 - tolerance
    && point.x <= box.x1 + tolerance
    && point.y >= box.y0 - tolerance
    && point.y <= box.y1 + tolerance;
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
  if (drawing.label?.text && drawing.anchors[0]) {
    const box = textLabelBox(drawing.anchors[0], drawing.label.text);
    if (boxContains(box, pointer, pointerKind === 'touch' ? TEXT_LABEL_PAD_PX : 0)) {
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
