/** Pure click-machine for in-progress drawings. One active tool at a time. */
import { ANCHOR_COUNTS, type DrawingKind } from './types.ts';
import { constrainByShift } from './geometry.ts';
import type { PointerKind } from './hitTest.ts';
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

/**
 * zrender 5.x 的事件包裹里没有 pointerType，只有 zrByTouch；而 env 只在 Edge/IE
 * 上开 pointer events，iOS Safari / Android Chrome 一律走 touch 回退。所以要先看
 * zrByTouch 和原生事件，读 packet.pointerType 等于永远判成鼠标。
 */
export function pointerKindFromEvent(event: {
  zrByTouch?: boolean;
  pointerType?: string;
  event?: { pointerType?: string; type?: string } | null;
}): PointerKind {
  if (event.zrByTouch) return 'touch';
  const raw = event.event ?? null;
  const pointerType = raw?.pointerType ?? event.pointerType;
  if (pointerType === 'touch') return 'touch';
  if (pointerType === 'pen') return 'pen';
  if (typeof raw?.type === 'string' && raw.type.startsWith('touch')) return 'touch';
  return 'mouse';
}

/**
 * Escape 归最上层：Drawer / ConfirmDialog 用 document 级监听且不 stopPropagation，
 * 不让路的话一次 Escape 会既关弹层又重置工具、收起全屏工作区。工作区自己那一层
 * 不算覆盖层，否则全屏永远收不起来。
 */
export function escapeHandledByOverlay(args: {
  defaultPrevented: boolean;
  openModals: number;
  workspaceExpanded: boolean;
}): boolean {
  if (args.defaultPrevented) return true;
  return args.openModals > (args.workspaceExpanded ? 1 : 0);
}

export function moveWholeAnchors(points: Point[], dx: number, dy: number): Point[] {
  return points.map((point) => ({ x: point.x + dx, y: point.y + dy }));
}
