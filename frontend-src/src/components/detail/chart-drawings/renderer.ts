/** Data-coordinate marks for completed drawings. Unresolved anchors emit nothing. */
import {
  channelEdges,
  clipRayToRect,
  fibonacciPrices,
  horizontalProjection,
  normalizeRectangle,
} from './geometry.ts';
import { barKeyOf, resolveAnchor } from './projection.ts';
import type { ChartDrawing, ChartRange, Point, Segment } from './types.ts';

export interface BarLike {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface RenderContext {
  bars: BarLike[];
  range: ChartRange;
  xMin: number;
  xMax: number;
  yMin: number;
  yMax: number;
}

export interface DrawingMarks {
  lines: object[];
  areas: object[];
  points: object[];
  unresolvedIds: string[];
}

const DASH: Record<string, string | number[]> = {
  solid: 'solid',
  dashed: [6, 4],
  dotted: [2, 3],
};

function coord(index: number, price: number) {
  return { coord: [index, price] };
}

function styleOf(drawing: ChartDrawing) {
  return {
    color: drawing.style.color.startsWith('#') ? drawing.style.color : '#2E46E0',
    width: drawing.style.width,
    type: DASH[drawing.style.dash] ?? 'solid',
  };
}

export function projectAnchors(
  drawing: ChartDrawing,
  ctx: RenderContext,
): { points: Point[]; unresolved: boolean } {
  const points: Point[] = [];
  for (const anchor of drawing.anchors) {
    const index = resolveAnchor(ctx.bars, anchor, ctx.range);
    if (index < 0) return { points: [], unresolved: true };
    points.push({ x: index, y: anchor.price });
  }
  return { points, unresolved: false };
}

export function drawingSegments(drawing: ChartDrawing, ctx: RenderContext): {
  segments: Segment[];
  fill: Point[] | null;
  horizontals: { price: number; label: string }[];
  unresolved: boolean;
} {
  const empty = { segments: [] as Segment[], fill: null as Point[] | null, horizontals: [] as { price: number; label: string }[], unresolved: false };
  const { points, unresolved } = projectAnchors(drawing, ctx);
  if (unresolved) return { ...empty, unresolved: true };
  const rect = { xMin: ctx.xMin, xMax: ctx.xMax, yMin: ctx.yMin, yMax: ctx.yMax };
  if (drawing.kind === 'horizontal') {
    const line = horizontalProjection(points[0].y, ctx.xMin, ctx.xMax);
    return { ...empty, segments: [line], horizontals: [{ price: points[0].y, label: String(points[0].y) }] };
  }
  if (drawing.kind === 'segment') {
    return { ...empty, segments: [{ a: points[0], b: points[1] }] };
  }
  if (drawing.kind === 'ray') {
    const clipped = clipRayToRect(points[0], points[1], rect);
    return { ...empty, segments: clipped ? [clipped] : [] };
  }
  if (drawing.kind === 'channel') {
    const edges = channelEdges(points[0], points[1], points[2]);
    return { ...empty, segments: [edges.main, edges.parallel], fill: edges.fill };
  }
  if (drawing.kind === 'rectangle') {
    const box = normalizeRectangle(points[0], points[1]);
    return {
      ...empty,
      segments: [
        { a: { x: box.x0, y: box.y0 }, b: { x: box.x1, y: box.y0 } },
        { a: { x: box.x1, y: box.y0 }, b: { x: box.x1, y: box.y1 } },
        { a: { x: box.x1, y: box.y1 }, b: { x: box.x0, y: box.y1 } },
        { a: { x: box.x0, y: box.y1 }, b: { x: box.x0, y: box.y0 } },
      ],
      fill: [
        { x: box.x0, y: box.y0 },
        { x: box.x1, y: box.y0 },
        { x: box.x1, y: box.y1 },
        { x: box.x0, y: box.y1 },
      ],
    };
  }
  if (drawing.kind === 'fibonacci') {
    const levels = fibonacciPrices(points[0].y, points[1].y);
    return {
      ...empty,
      segments: [{ a: points[0], b: points[1] }],
      horizontals: levels.map((level) => ({
        price: level.price,
        label: `${level.ratio} · ${level.price}`,
      })),
    };
  }
  return empty;
}

export function drawingsToMarks(
  drawings: ChartDrawing[],
  ctx: RenderContext,
): DrawingMarks {
  const lines: object[] = [];
  const areas: object[] = [];
  const points: object[] = [];
  const unresolvedIds: string[] = [];
  for (const drawing of drawings) {
    if (drawing.hidden) continue;
    const geom = drawingSegments(drawing, ctx);
    if (geom.unresolved) {
      unresolvedIds.push(drawing.id);
      continue;
    }
    const lineStyle = styleOf(drawing);
    for (const segment of geom.segments) {
      lines.push([
        { ...coord(segment.a.x, segment.a.y), lineStyle },
        coord(segment.b.x, segment.b.y),
      ]);
    }
    for (const level of geom.horizontals) {
      lines.push([
        {
          ...coord(ctx.xMin, level.price),
          lineStyle,
          label: {
            show: true,
            formatter: level.label,
            position: 'insideEndTop',
            fontSize: 10,
            fontFamily: '"IBM Plex Mono", monospace',
            color: lineStyle.color,
          },
        },
        coord(ctx.xMax, level.price),
      ]);
    }
    if (geom.fill && drawing.style.fillOpacity != null && drawing.style.fillOpacity > 0) {
      const xs = geom.fill.map((p) => p.x);
      const ys = geom.fill.map((p) => p.y);
      areas.push([
        {
          xAxis: Math.min(...xs),
          yAxis: Math.min(...ys),
          itemStyle: { color: lineStyle.color, opacity: drawing.style.fillOpacity },
        },
        { xAxis: Math.max(...xs), yAxis: Math.max(...ys) },
      ]);
    }
    if (drawing.kind === 'text' && drawing.text) {
      const { points: textAt, unresolved } = projectAnchors(drawing, ctx);
      if (!unresolved && textAt[0]) {
        points.push({
          coord: [textAt[0].x, textAt[0].y],
          symbol: 'circle',
          symbolSize: 6,
          itemStyle: { color: lineStyle.color },
          label: {
            show: true,
            formatter: drawing.text.replace(/\s+/g, ' ').slice(0, 240),
            position: 'right',
            fontSize: 11,
            color: '#3D4A68',
          },
        });
      }
    }
  }
  return { lines, areas, points, unresolvedIds };
}

export interface AutoPatternLike {
  id: string;
  kind: string;
  subtype?: string;
  confidence: number;
  status: string;
  anchors: { time: string; barKey: string; price: number }[];
  hidden?: boolean;
}

export function autoPatternsToMarks(
  patterns: AutoPatternLike[],
  ctx: RenderContext,
  minConfidence = 70,
): DrawingMarks {
  const visible = patterns.filter((item) => item.confidence >= minConfidence);
  const asDrawings: ChartDrawing[] = visible.map((item) => ({
    schemaVersion: 1,
    id: item.id,
    ticker: 'AUTO',
    range: ctx.range,
    adjustment: 'raw',
    kind: item.kind === 'box' || item.kind === 'channel' || item.kind === 'triangle' || item.kind === 'wedge'
      ? item.anchors.length >= 3 ? 'channel' : 'segment'
      : item.kind === 'resistance_trend' || item.kind === 'support_trend'
        ? 'segment'
        : 'segment',
    anchors: item.anchors.slice(0, item.anchors.length >= 3 ? 3 : 2).map((anchor) => ({
      time: anchor.time,
      barKey: anchor.barKey,
      price: anchor.price,
    })),
    style: { color: '#8A94B0', width: 1, dash: 'dashed', fillOpacity: 0.06 },
    locked: true,
    hidden: false,
    zOrder: -10,
    revision: 1,
    createdAt: '',
    updatedAt: '',
  }));
  return drawingsToMarks(asDrawings, ctx);
}

export function lastBarKey(bars: BarLike[], range: ChartRange): string | null {
  if (!bars.length) return null;
  return barKeyOf(bars[bars.length - 1], range);
}
