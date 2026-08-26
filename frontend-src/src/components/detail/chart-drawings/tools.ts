/** Pure click-machine for in-progress drawings. One active tool at a time. */
import { ANCHOR_COUNTS, type DrawingKind } from './types.ts';
import { constrainByShift } from './geometry.ts';
import type { Point } from './types.ts';

export type DrawingTool = 'select' | DrawingKind;

export interface DraftPoint {
  barIndex: number;
  price: number;
  time: string;
  barKey: string;
}

export interface InProgressDraw {
  kind: DrawingKind;
  points: DraftPoint[];
}

export function pointsNeeded(kind: DrawingKind): number {
  return ANCHOR_COUNTS[kind];
}

export function addDraftPoint(
  current: InProgressDraw | null,
  kind: DrawingKind,
  point: DraftPoint,
): { status: 'pending'; draft: InProgressDraw } | { status: 'complete'; points: DraftPoint[] } {
  const points = current && current.kind === kind ? [...current.points, point] : [point];
  if (points.length >= pointsNeeded(kind)) {
    return { status: 'complete', points };
  }
  return { status: 'pending', draft: { kind, points } };
}

export function applyShiftToDraft(kind: DrawingKind, points: DraftPoint[], next: DraftPoint, shift: boolean): DraftPoint {
  if (!shift || points.length === 0) return next;
  if (kind === 'horizontal') return next;
  const last = points[points.length - 1];
  const snapped = constrainByShift(
    { x: last.barIndex, y: last.price },
    { x: next.barIndex, y: next.price },
  );
  return { ...next, barIndex: snapped.x, price: snapped.y };
}

export function exclusiveTool(next: DrawingTool | 'measure'): { tool: DrawingTool; measure: boolean } {
  if (next === 'measure') return { tool: 'select', measure: true };
  return { tool: next, measure: false };
}

export function isTextInputTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  return target.isContentEditable;
}

export function pointerKindFromEvent(event: { pointerType?: string }): 'mouse' | 'touch' | 'pen' {
  if (event.pointerType === 'touch') return 'touch';
  if (event.pointerType === 'pen') return 'pen';
  return 'mouse';
}

export function moveWholeAnchors(points: Point[], dx: number, dy: number): Point[] {
  return points.map((point) => ({ x: point.x + dx, y: point.y + dy }));
}
