/** Data-coordinate marks for completed drawings. Unresolved anchors emit nothing. */
import {
  channelEdges,
  clipRayToRect,
  fibonacciPrices,
  normalizeRectangle,
} from './geometry.ts';
import { barKeyOf, resolveAnchor } from './projection.ts';
import { resolvePaintColor } from './schema.ts';
import type { ProjectedDrawing } from './hitTest.ts';
import type { ChartDrawing, ChartRange, DrawingKind, Point, Segment } from './types.ts';

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

export interface FillPolygon {
  vertices: Point[];
  color: string;
  opacity: number;
}

export interface DrawingMarks {
  lines: object[];
  areas: object[];
  points: object[];
  polygons: FillPolygon[];
  unresolvedIds: string[];
}

export interface OverlayGeometry {
  anchors: Point[];
  segments: Segment[];
  fills: Point[][];
}

export interface MarksOptions {
  selectedId?: string | null;
  inProgress?: { kind: DrawingKind; points: { barIndex: number; price: number }[] } | null;
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
    color: resolvePaintColor(drawing.style.color),
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

export function geometryFromPoints(
  kind: DrawingKind,
  points: Point[],
  ctx: RenderContext,
): {
  segments: Segment[];
  fill: Point[] | null;
  horizontals: { price: number; label: string }[];
} {
  const empty = { segments: [] as Segment[], fill: null as Point[] | null, horizontals: [] as { price: number; label: string }[] };
  if (!points.length) return empty;
  const rect = { xMin: ctx.xMin, xMax: ctx.xMax, yMin: ctx.yMin, yMax: ctx.yMax };
  if (kind === 'horizontal') {
    return {
      ...empty,
      horizontals: [{ price: points[0].y, label: String(points[0].y) }],
    };
  }
  if (kind === 'text') return empty;
  if (points.length < 2) return empty;
  if (kind === 'segment') {
    return { ...empty, segments: [{ a: points[0], b: points[1] }] };
  }
  if (kind === 'ray') {
    const clipped = clipRayToRect(points[0], points[1], rect);
    return { ...empty, segments: clipped ? [clipped] : [] };
  }
  if (kind === 'channel') {
    if (points.length < 3) return { ...empty, segments: [{ a: points[0], b: points[1] }] };
    const edges = channelEdges(points[0], points[1], points[2]);
    return { ...empty, segments: [edges.main, edges.parallel], fill: edges.fill };
  }
  if (kind === 'rectangle') {
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
  if (kind === 'fibonacci') {
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
  // 新增 DrawingKind 必须在上面补一个分支：漏掉就在这里编译不过，而不是
  // 静默什么都不画、顺带连命中测试也没有几何可用。
  const unsupported: never = kind;
  void unsupported;
  return empty;
}

export function drawingSegments(drawing: ChartDrawing, ctx: RenderContext): {
  segments: Segment[];
  fill: Point[] | null;
  horizontals: { price: number; label: string }[];
  unresolved: boolean;
} {
  const { points, unresolved } = projectAnchors(drawing, ctx);
  if (unresolved) {
    return { segments: [], fill: null, horizontals: [], unresolved: true };
  }
  return { ...geometryFromPoints(drawing.kind, points, ctx), unresolved: false };
}

export function toProjectedDrawing(
  drawing: ChartDrawing,
  ctx: RenderContext,
): ProjectedDrawing | null {
  const { points, unresolved } = projectAnchors(drawing, ctx);
  if (unresolved) return null;
  const geom = geometryFromPoints(drawing.kind, points, ctx);
  const segments = [
    ...geom.segments,
    ...geom.horizontals.map((level) => ({
      a: { x: ctx.xMin, y: level.price },
      b: { x: ctx.xMax, y: level.price },
    })),
  ];
  return {
    id: drawing.id,
    zOrder: drawing.zOrder,
    locked: drawing.locked,
    hidden: drawing.hidden,
    anchors: points,
    segments,
    fills: geom.fill ? [geom.fill] : [],
    // 文字没有线段也没有填充：把可见标签交给命中测试撑出一个矩形命中区。
    label: drawing.kind === 'text' && drawing.text ? { text: drawing.text } : null,
  };
}

export function projectToPixels(
  projected: ProjectedDrawing,
  toPixel: (point: Point) => Point | null,
): ProjectedDrawing {
  const map = (point: Point): Point => toPixel(point) ?? { x: Number.NaN, y: Number.NaN };
  return {
    ...projected,
    anchors: projected.anchors.map(map),
    segments: projected.segments.map((segment) => ({ a: map(segment.a), b: map(segment.b) })),
    fills: projected.fills.map((fill) => fill.map(map)),
  };
}

export function fillIsAxisAligned(vertices: Point[]): boolean {
  if (vertices.length < 4) return false;
  const xs = new Set(vertices.map((point) => point.x));
  const ys = new Set(vertices.map((point) => point.y));
  return xs.size === 2 && ys.size === 2;
}

export function selectionOverlay(drawing: ChartDrawing, ctx: RenderContext): OverlayGeometry | null {
  const projected = toProjectedDrawing(drawing, ctx);
  if (!projected) return null;
  return { anchors: projected.anchors, segments: projected.segments, fills: projected.fills };
}

export function draftOverlay(
  draft: { kind: DrawingKind; points: { barIndex: number; price: number }[] },
  ctx: RenderContext,
): OverlayGeometry {
  const points = draft.points.map((point) => ({ x: point.barIndex, y: point.price }));
  const geom = geometryFromPoints(draft.kind, points, ctx);
  const segments = [
    ...geom.segments,
    ...geom.horizontals.map((level) => ({
      a: { x: ctx.xMin, y: level.price },
      b: { x: ctx.xMax, y: level.price },
    })),
  ];
  return { anchors: points, segments, fills: geom.fill ? [geom.fill] : [] };
}

export function overlayMarks(
  overlay: OverlayGeometry,
  style: { color: string; width?: number; dash?: string | number[] },
): { lines: object[]; points: object[] } {
  const lineStyle = {
    color: style.color,
    width: style.width ?? 2,
    type: style.dash ?? [4, 3],
  };
  const lines = overlay.segments.map((segment) => [
    { ...coord(segment.a.x, segment.a.y), lineStyle },
    coord(segment.b.x, segment.b.y),
  ]);
  const points = overlay.anchors.map((anchor) => ({
    coord: [anchor.x, anchor.y],
    symbol: 'circle',
    symbolSize: 10,
    itemStyle: { color: '#FDFCF9', borderColor: style.color, borderWidth: 2 },
  }));
  return { lines, points };
}

export function graphicFromOverlay(
  overlay: OverlayGeometry,
  toPixel: (point: Point) => Point | null,
  color: string,
  options: { solid?: boolean; width?: number } = {},
): object[] {
  const elements: object[] = [];
  const stroke = resolvePaintColor(color);
  const lineDash = options.solid ? undefined : [4, 3];
  const lineWidth = options.width ?? (options.solid ? 2 : 1.5);
  overlay.fills.forEach((fill, index) => {
    const points = fill.map((point) => toPixel(point)).filter((item): item is Point => item !== null);
    if (points.length < 3) return;
    elements.push({
      type: 'polygon',
      id: `overlay-fill-${index}`,
      shape: { points: points.map((point) => [point.x, point.y]) },
      style: { fill: stroke, opacity: 0.12, stroke: 'none' },
      silent: true,
    });
  });
  overlay.segments.forEach((segment, index) => {
    const a = toPixel(segment.a);
    const b = toPixel(segment.b);
    if (!a || !b) return;
    elements.push({
      type: 'line',
      id: `overlay-seg-${index}`,
      shape: { x1: a.x, y1: a.y, x2: b.x, y2: b.y },
      style: { stroke, lineWidth, lineDash },
      silent: true,
    });
  });
  overlay.anchors.forEach((anchor, index) => {
    const pixel = toPixel(anchor);
    if (!pixel) return;
    elements.push({
      type: 'circle',
      id: `overlay-anchor-${index}`,
      shape: { cx: pixel.x, cy: pixel.y, r: 5 },
      style: { fill: '#FDFCF9', stroke, lineWidth: 2 },
      silent: true,
    });
  });
  return elements;
}

export function drawingsToMarks(
  drawings: ChartDrawing[],
  ctx: RenderContext,
  options: MarksOptions = {},
): DrawingMarks {
  const lines: object[] = [];
  const areas: object[] = [];
  const points: object[] = [];
  const polygons: FillPolygon[] = [];
  const unresolvedIds: string[] = [];
  const sorted = [...drawings].sort((a, b) => a.zOrder - b.zOrder);
  for (const drawing of sorted) {
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
      if (fillIsAxisAligned(geom.fill)) {
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
      } else {
        polygons.push({
          vertices: geom.fill,
          color: lineStyle.color,
          opacity: drawing.style.fillOpacity,
        });
      }
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
            color: lineStyle.color,
          },
        });
      }
    }
  }
  const selected = options.selectedId
    ? drawings.find((item) => item.id === options.selectedId && !item.hidden)
    : null;
  if (selected) {
    const overlay = selectionOverlay(selected, ctx);
    if (overlay) {
      const extra = overlayMarks(overlay, { color: styleOf(selected).color, width: 2, dash: 'solid' });
      lines.push(...extra.lines);
      points.push(...extra.points);
    }
  }
  if (options.inProgress && options.inProgress.points.length) {
    const overlay = draftOverlay(options.inProgress, ctx);
    const extra = overlayMarks(overlay, { color: '#2E46E0', width: 1.5, dash: [4, 3] });
    lines.push(...extra.lines);
    points.push(...extra.points);
  }
  return { lines, areas, points, polygons, unresolvedIds };
}

export interface AutoPatternLike {
  id: string;
  kind: string;
  subtype?: string;
  confidence: number;
  status: string;
  anchors: { time: string; barKey: string; price: number }[];
  hidden?: boolean;
  /** 线色（按 kind 分色）；缺省中性灰。 */
  color?: string;
  /** 线端标签（如「上升支撑」）；缺省不画。 */
  label?: string;
}

export function autoPatternGeometry(
  pattern: AutoPatternLike,
  ctx: RenderContext,
): { segments: Segment[]; fill: Point[] | null } | null {
  const points: Point[] = [];
  for (const anchor of pattern.anchors) {
    const index = resolveAnchor(ctx.bars, { time: anchor.time, barKey: anchor.barKey, price: anchor.price }, ctx.range);
    if (index < 0) return null;
    points.push({ x: index, y: anchor.price });
  }
  if (points.length < 2) return null;
  if (pattern.kind === 'box') {
    const box = normalizeRectangle(
      { x: Math.min(...points.map((p) => p.x)), y: Math.min(...points.map((p) => p.y)) },
      { x: Math.max(...points.map((p) => p.x)), y: Math.max(...points.map((p) => p.y)) },
    );
    return {
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
  if (
    (pattern.kind === 'channel' || pattern.kind === 'triangle' || pattern.kind === 'wedge')
    && points.length >= 4
  ) {
    return {
      segments: [
        { a: points[0], b: points[1] },
        { a: points[2], b: points[3] },
      ],
      fill: [points[0], points[1], points[3], points[2]],
    };
  }
  return { segments: [{ a: points[0], b: points[1] }], fill: null };
}

export function autoPatternsToMarks(
  patterns: AutoPatternLike[],
  ctx: RenderContext,
  minConfidence = 70,
): DrawingMarks {
  const lines: object[] = [];
  const areas: object[] = [];
  const points: object[] = [];
  const polygons: FillPolygon[] = [];
  for (const pattern of patterns) {
    if (pattern.confidence < minConfidence) continue;
    const geom = autoPatternGeometry(pattern, ctx);
    if (!geom) continue;
    /* 分色 + 线端标签：此前所有形态线都是同一根淡灰虚线且无名，图上支撑/
       阻力/通道边完全分不出来，只能去页脚标签条猜。标签只挂第一段（通道/
       三角的第二条边不重复报名），文字与线同色。 */
    const color = pattern.color ?? '#8A94B0';
    const lineStyle = { color, width: 1, type: [4, 4] as number[] };
    geom.segments.forEach((segment, index) => {
      const label = index === 0 && pattern.label
        ? {
            show: true,
            formatter: pattern.label,
            position: 'end' as const,
            color,
            fontSize: 10,
            distance: 4,
          }
        : { show: false };
      lines.push([
        { ...coord(segment.a.x, segment.a.y), lineStyle, label },
        coord(segment.b.x, segment.b.y),
      ]);
    });
    if (geom.fill) {
      if (fillIsAxisAligned(geom.fill)) {
        const xs = geom.fill.map((p) => p.x);
        const ys = geom.fill.map((p) => p.y);
        areas.push([
          {
            xAxis: Math.min(...xs),
            yAxis: Math.min(...ys),
            itemStyle: { color, opacity: 0.06 },
          },
          { xAxis: Math.max(...xs), yAxis: Math.max(...ys) },
        ]);
      } else {
        polygons.push({ vertices: geom.fill, color, opacity: 0.06 });
      }
    }
  }
  return { lines, areas, points, polygons, unresolvedIds: [] };
}

export function lastBarKey(bars: BarLike[], range: ChartRange): string | null {
  if (!bars.length) return null;
  return barKeyOf(bars[bars.length - 1], range);
}
