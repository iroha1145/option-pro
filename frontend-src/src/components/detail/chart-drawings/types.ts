/** Hand-drawn chart objects. Coordinates are time + price, never screen pixels. */

export type DrawingKind =
  | 'horizontal'
  | 'segment'
  | 'ray'
  | 'channel'
  | 'rectangle'
  | 'fibonacci'
  | 'text';

export type ChartRange = '5m' | '15m' | '1h' | '1d' | '1w';
export type ChartAdjustment = 'raw';
export type LineDash = 'solid' | 'dashed' | 'dotted';
export type LineWidth = 1 | 2 | 3 | 4;

export interface DrawingAnchor {
  time: string;
  barKey: string;
  price: number;
}

export interface DrawingStyle {
  color: string;
  width: LineWidth;
  dash: LineDash;
  fillOpacity?: number;
}

export interface ChartDrawing {
  schemaVersion: 1;
  id: string;
  ticker: string;
  range: ChartRange;
  adjustment: ChartAdjustment;
  kind: DrawingKind;
  anchors: DrawingAnchor[];
  style: DrawingStyle;
  text?: string;
  locked: boolean;
  hidden: boolean;
  zOrder: number;
  revision: number;
  createdAt: string;
  updatedAt: string;
}

export interface Point {
  x: number;
  y: number;
}

export interface DataPoint {
  timeIndex: number;
  price: number;
}

export interface VisibleRect {
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

export interface Segment {
  a: Point;
  b: Point;
}

export const DRAWING_KINDS: readonly DrawingKind[] = [
  'horizontal',
  'segment',
  'ray',
  'channel',
  'rectangle',
  'fibonacci',
  'text',
] as const;

export const ANCHOR_COUNTS: Record<DrawingKind, number> = {
  horizontal: 1,
  segment: 2,
  ray: 2,
  channel: 3,
  rectangle: 2,
  fibonacci: 2,
  text: 1,
};

export const CHART_RANGES: readonly ChartRange[] = ['5m', '15m', '1h', '1d', '1w'];
export const FIB_RATIOS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1, 1.272, 1.618] as const;
export const DRAWING_TEXT_MAX = 240;
export const DRAWINGS_PER_RANGE_MAX = 500;
export const SCHEMA_VERSION = 1 as const;
