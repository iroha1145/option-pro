/** Pointer-down drag preview and pixel-space Shift constraint. Committed drawings stay still. */
import { constrainByShift, moveChannelAnchor, moveChannelWhole } from './geometry.ts';
import { barKeyOf, resolveAnchor, snapBarIndex } from './projection.ts';
import { PRICE_MAX, PRICE_MIN } from './schema.ts';
import type { ChartDrawing, ChartRange, DrawingAnchor, Point } from './types.ts';

export interface TimedOhlc {
  t: string;
}

export interface DragOrigin {
  id: string;
  mode: 'anchor' | 'whole';
  anchorIndex: number;
  origin: ChartDrawing;
  startPixel: Point;
  startData: { barIndex: number; price: number };
  /** 指针真的走出阈值才算拖动；纯选中点击不该提交任何东西。 */
  moved: boolean;
}

export const DRAG_THRESHOLD_MOUSE_PX = 3;
export const DRAG_THRESHOLD_TOUCH_PX = 8;

export function dragThresholdPx(pointer: 'mouse' | 'touch' | 'pen'): number {
  return pointer === 'touch' ? DRAG_THRESHOLD_TOUCH_PX : DRAG_THRESHOLD_MOUSE_PX;
}

export function dragExceedsThreshold(
  start: Point,
  current: Point,
  pointer: 'mouse' | 'touch' | 'pen',
): boolean {
  return Math.hypot(current.x - start.x, current.y - start.y) > dragThresholdPx(pointer);
}

/**
 * 把转换出来的数据点夹回网格与合法价格区间。不夹的话拖到图外会算出
 * price<=0：前端 schema 拒收，后端 invalid_price 回 400（不是冲突），
 * 已登录用户会拿到一条永远重放的坏任务，匿名用户直接把坏值写进 localStorage。
 */
export function clampDragPoint(
  point: { barIndex: number; price: number },
  barCount: number,
): { barIndex: number; price: number } {
  const maxIndex = Math.max(0, barCount - 1);
  const rawIndex = Number.isFinite(point.barIndex) ? Math.round(point.barIndex) : 0;
  const rawPrice = Number.isFinite(point.price) ? point.price : PRICE_MIN;
  return {
    barIndex: Math.max(0, Math.min(maxIndex, rawIndex)),
    price: Math.max(PRICE_MIN, Math.min(PRICE_MAX, rawPrice)),
  };
}

export function drawingFromPreview(origin: ChartDrawing, anchors: DrawingAnchor[]): ChartDrawing {
  return { ...origin, anchors };
}

export function previewDragAnchors(args: {
  origin: ChartDrawing;
  mode: 'anchor' | 'whole';
  anchorIndex: number;
  pointer: { barIndex: number; price: number };
  startData: { barIndex: number; price: number };
  bars: TimedOhlc[];
  range: ChartRange;
}): DrawingAnchor[] {
  const { origin, mode, pointer, startData, bars, range } = args;
  const dIndex = pointer.barIndex - startData.barIndex;
  const dPrice = pointer.price - startData.price;
  if (mode === 'whole') {
    if (origin.kind === 'channel' && origin.anchors.length === 3) {
      const moved = moveChannelWhole(
        { x: 0, y: origin.anchors[0].price },
        { x: 1, y: origin.anchors[1].price },
        { x: 2, y: origin.anchors[2].price },
        0,
        dPrice,
      );
      return origin.anchors.map((anchor, index) => {
        const idx = resolveAnchor(bars, anchor, range);
        const nextIdx = Math.max(0, Math.min(bars.length - 1, Math.round(idx + dIndex)));
        const bar = bars[nextIdx];
        return { time: bar.t, barKey: barKeyOf(bar, range), price: moved[index].y };
      });
    }
    return origin.anchors.map((anchor) => {
      const idx = resolveAnchor(bars, anchor, range);
      const nextIdx = Math.max(0, Math.min(bars.length - 1, Math.round(idx + dIndex)));
      const bar = bars[nextIdx];
      return { time: bar.t, barKey: barKeyOf(bar, range), price: anchor.price + dPrice };
    });
  }
  if (origin.kind === 'channel' && origin.anchors.length === 3) {
    const pts = origin.anchors.map((anchor) => {
      const i = resolveAnchor(bars, anchor, range);
      return { x: i, y: anchor.price };
    });
    const moved = moveChannelAnchor(
      pts[0],
      pts[1],
      pts[2],
      args.anchorIndex as 0 | 1 | 2,
      { x: pointer.barIndex, y: pointer.price },
    );
    return moved.map((point) => {
      const barAt = bars[Math.max(0, Math.min(bars.length - 1, Math.round(point.x)))];
      return { time: barAt.t, barKey: barKeyOf(barAt, range), price: point.y };
    });
  }
  return origin.anchors.map((anchor, index) => {
    if (index !== args.anchorIndex) return anchor;
    const bar = bars[Math.max(0, Math.min(bars.length - 1, pointer.barIndex))];
    return { time: bar.t, barKey: barKeyOf(bar, range), price: pointer.price };
  });
}

export function dragMove(args: {
  drawings: ChartDrawing[];
  drag: DragOrigin;
  pointer: { barIndex: number; price: number };
  bars: TimedOhlc[];
  range: ChartRange;
}): { drawings: ChartDrawing[]; preview: ChartDrawing } {
  const anchors = previewDragAnchors({
    origin: args.drag.origin,
    mode: args.drag.mode,
    anchorIndex: args.drag.anchorIndex,
    pointer: args.pointer,
    startData: args.drag.startData,
    bars: args.bars,
    range: args.range,
  });
  return {
    drawings: args.drawings,
    preview: drawingFromPreview(args.drag.origin, anchors),
  };
}

export function applyPixelShiftConstraint(args: {
  originPx: Point;
  pointerPx: Point;
  fromPixel: (x: number, y: number) => { barIndex: number; price: number } | null;
  bars: TimedOhlc[];
  range: ChartRange;
}): { barIndex: number; price: number; time: string; barKey: string } | null {
  const constrained = constrainByShift(args.originPx, args.pointerPx);
  const data = args.fromPixel(constrained.x, constrained.y);
  if (!data) return null;
  const idx = snapBarIndex(data.barIndex, args.bars.length) ?? Math.max(
    0,
    Math.min(args.bars.length - 1, Math.round(data.barIndex)),
  );
  const bar = args.bars[idx];
  if (!bar) return null;
  return {
    barIndex: idx,
    price: data.price,
    time: bar.t,
    barKey: barKeyOf(bar, args.range),
  };
}
