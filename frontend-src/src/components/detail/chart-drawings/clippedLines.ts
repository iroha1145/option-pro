import type { CustomSeriesOption } from 'echarts/charts';
import { measureLabel, packEndLabels, type LabelRect } from './labelLayout.ts';

type Rect = { x: number; y: number; width: number; height: number };
type Point = [number, number];
interface LineHead {
  coord: Point;
  clipToPlot?: boolean;
  lineStyle?: { color?: string; width?: number; type?: string | number[]; opacity?: number; shadowColor?: string; shadowBlur?: number };
  label?: { show?: boolean; formatter?: unknown; position?: string; distance?: number; fontSize?: number; lineHeight?: number;
    color?: string; backgroundColor?: string; borderColor?: string; borderWidth?: number; borderRadius?: number; padding?: number[]; priority?: number };
}
type RailMark = [LineHead, { coord: Point }];
export interface ReferenceLabel { price: number | null; text: string }

export function isClippedLine(mark: object): mark is RailMark {
  return Array.isArray(mark) && mark.length === 2 && mark[0]?.clipToPlot === true;
}

/** Clip only projected pixels; the saved and fitted data coordinates never change. */
export function clipLineToRect(a: Point, b: Point, rect: Rect): [Point, Point] | null {
  if (![...a, ...b, rect.x, rect.y, rect.width, rect.height].every(Number.isFinite)
    || rect.width <= 0 || rect.height <= 0) return null;
  const dx = b[0] - a[0], dy = b[1] - a[1];
  let from = 0, to = 1;
  for (const [p, q] of [[-dx, a[0] - rect.x], [dx, rect.x + rect.width - a[0]],
    [-dy, a[1] - rect.y], [dy, rect.y + rect.height - a[1]]]) {
    if (p === 0) { if (q < 0) return null; continue; }
    const ratio = q / p;
    if (p < 0) from = Math.max(from, ratio); else to = Math.min(to, ratio);
    if (from > to) return null;
  }
  return [[a[0] + from * dx, a[1] + from * dy], [a[0] + to * dx, a[1] + to * dy]];
}

/** Existing hand-drawing labels are obstacles, never restyled or repositioned. */
function manualObstacles(marks: readonly object[], project: (p: Point) => Point, rect: Rect): LabelRect[] {
  return marks.flatMap(mark => {
    if (isClippedLine(mark) || !Array.isArray(mark) || mark.length !== 2) return [];
    const row = mark as RailMark, label = row[0]?.label, tail = row[1]?.coord;
    if (!label?.show || typeof label.formatter !== 'string' || !Array.isArray(tail) || tail.length !== 2) return [];
    const [x, y] = project(tail);
    if (!Number.isFinite(x) || !Number.isFinite(y) || x < rect.x || x > rect.x + rect.width || y < rect.y || y > rect.y + rect.height) return [];
    const size = measureLabel(label.formatter, label.fontSize ?? 11, label.lineHeight ?? 14);
    const below = label.position === 'insideEndBottom';
    return [{ x: x - size.width - 3, y: below ? y + (label.distance ?? 4) : y - size.height - (label.distance ?? 4), ...size }];
  });
}

/** markLine drops a whole segment when either endpoint leaves an axis. The custom
 * series clips rails and lays out their labels once per ECharts render pass.
 * A live-reference-only update rebuilds this small series, not the detector or Kline.
 */
export function clippedLineSeries(marks: readonly object[], reference?: ReferenceLabel): CustomSeriesOption | null {
  const rows = marks.filter(isClippedLine);
  if (!rows.length) return null;
  return {
    id: 'drawing-rails', type: 'custom', name: 'drawing-rails', coordinateSystem: 'cartesian2d',
    xAxisIndex: 0, yAxisIndex: 0, clip: true, silent: true, animation: false, z: 6,
    tooltip: { show: false },
    encode: { x: -1, y: -1 }, data: rows.map((_, index) => index),
    renderItem(params, api) {
      const row = rows[params.dataIndex];
      if (!row) return;
      const rect = params.coordSys as unknown as Rect;
      const project = ([x, y]: Point): Point => {
        const left = Math.floor(x), fraction = x - left;
        const from = api.coord([left, y]) as Point;
        if (fraction === 0) return from;
        const to = api.coord([left + 1, y]) as Point;
        return [from[0] + fraction * (to[0] - from[0]), from[1]];
      };
      const a = project(row[0].coord), b = project(row[1].coord);
      const visible = clipLineToRect(a, b, rect);
      if (!visible) return;
      // ECharts provides a fresh shared context for each render cycle. Compute all
      // labels together so item order, resize and dataZoom cannot change priority.
      const context = params.context as { endLabels?: ReturnType<typeof packEndLabels> };
      if (!context.endLabels) {
        const obstacles = manualObstacles(marks, project, rect);
        if (reference?.price != null && Number.isFinite(reference.price) && reference.text) {
          const y = project([0, reference.price])[1];
          if (y >= rect.y && y <= rect.y + rect.height) {
            const size = measureLabel(reference.text, 10, 14);
            obstacles.push({ x: rect.x + rect.width - size.width - 4, y: y - size.height - 4, ...size });
          }
        }
        const requests = rows.flatMap((r, index) => {
          const label = r[0].label;
          if (!label?.show || typeof label.formatter !== 'string' || !label.formatter) return [];
          const [x, y] = project(r[1].coord);
          if (x < rect.x || x > rect.x + rect.width || y < rect.y || y > rect.y + rect.height) return [];
          const size = measureLabel(label.formatter, label.fontSize ?? 11, label.lineHeight ?? 14);
          return [{ id: String(index), anchorX: x, anchorY: y, ...size,
            priority: label.priority ?? (r[0].lineStyle?.opacity ?? 1) * 100 }];
        });
        context.endLabels = packEndLabels(requests, { x: rect.x + 4, y: rect.y + 4,
          width: Math.max(0, rect.width - 8), height: Math.max(0, rect.height - 8) }, obstacles);
      }
      const placement = context.endLabels.find(p => p.id === String(params.dataIndex));
      const [head, tail] = visible, ink = row[0].lineStyle ?? {}, label = row[0].label;
      const dash = Array.isArray(ink.type) ? ink.type : ink.type === 'dashed' ? [6, 4] : ink.type === 'dotted' ? [2, 3] : undefined;
      const moved = placement && Math.abs(placement.y + placement.height / 2 - b[1]) > placement.height;
      return {
        type: 'group', children: [
          { type: 'line', shape: { x1: head[0], y1: head[1], x2: tail[0], y2: tail[1] },
            style: { stroke: ink.color, lineWidth: ink.width ?? 2.5, lineDash: dash, opacity: ink.opacity ?? 1,
              lineCap: 'round', shadowColor: ink.shadowColor, shadowBlur: ink.shadowBlur } },
          { type: 'line', invisible: !moved,
            shape: { x1: b[0], y1: b[1], x2: placement ? placement.x + placement.width : b[0],
              y2: placement ? placement.y + placement.height / 2 : b[1] },
            style: { stroke: label?.color ?? ink.color, lineWidth: 0.7, opacity: 0.55 } },
          { type: 'text', invisible: !placement,
            style: { text: placement ? String(label!.formatter) : '',
              // Text uses the same font and padding as its measured outer box.
              x: placement ? placement.x + placement.width / 2 : b[0],
              y: placement ? placement.y + placement.height / 2 : b[1], align: 'center', verticalAlign: 'middle',
              fontFamily: 'sans-serif', fontSize: label?.fontSize ?? 11, lineHeight: label?.lineHeight ?? 14,
              fill: label?.color ?? ink.color, backgroundColor: label?.backgroundColor,
              borderColor: label?.borderColor, borderWidth: label?.borderWidth, borderRadius: label?.borderRadius,
              padding: [2, 5] } },
        ],
      };
    },
  };
}
