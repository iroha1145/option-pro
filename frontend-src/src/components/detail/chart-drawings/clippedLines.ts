import type { CustomSeriesOption } from 'echarts/charts';

type Rect = { x: number; y: number; width: number; height: number };
type Point = [number, number];
interface LineHead {
  coord: Point;
  clipToPlot?: boolean;
  lineStyle?: { color?: string; width?: number; type?: string | number[]; opacity?: number; shadowColor?: string; shadowBlur?: number };
  label?: { show?: boolean; formatter?: unknown; position?: string; distance?: number; fontSize?: number; lineHeight?: number;
    color?: string; backgroundColor?: string; borderColor?: string; borderWidth?: number; borderRadius?: number; padding?: number[] };
}
type RailMark = [LineHead, { coord: Point }];

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

/** markLine drops a whole segment when either endpoint leaves an axis. A custom
 * series clips intersecting automatic rails on every zoom without expanding axes. */
export function clippedLineSeries(marks: readonly object[]): CustomSeriesOption | null {
  const rows = marks.filter(isClippedLine);
  if (!rows.length) return null;
  return {
    type: 'custom', name: 'drawing-rails', coordinateSystem: 'cartesian2d',
    xAxisIndex: 0, yAxisIndex: 0, clip: true, silent: true, animation: false, z: 6,
    tooltip: { show: false },
    // Geometry is projected by renderItem, and must not participate in axis extents
    // or be removed by dataZoom before the intersecting segment can be clipped.
    encode: { x: -1, y: -1 }, data: rows.map((_, index) => index),
    renderItem(params, api) {
      const row = rows[params.dataIndex];
      const rect = params.coordSys as unknown as Rect;
      // ECharts rounds numeric category values. Interpolate adjacent category
      // centers explicitly so a projected endpoint at 62.5 stays between bars.
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
      const [head, tail] = visible, ink = row[0].lineStyle ?? {}, label = row[0].label;
      const originalTailVisible = b[0] >= rect.x && b[0] <= rect.x + rect.width && b[1] >= rect.y && b[1] <= rect.y + rect.height;
      const below = label?.position === 'insideEndBottom';
      const labelShown = Boolean(label?.show && originalTailVisible && typeof label.formatter === 'string');
      const dash = Array.isArray(ink.type) ? ink.type : ink.type === 'dashed' ? [6, 4] : ink.type === 'dotted' ? [2, 3] : undefined;
      return {
        type: 'group', children: [
          { type: 'line', shape: { x1: head[0], y1: head[1], x2: tail[0], y2: tail[1] },
            style: { stroke: ink.color, lineWidth: ink.width ?? 2.5, lineDash: dash, opacity: ink.opacity ?? 1,
              lineCap: 'round', shadowColor: ink.shadowColor, shadowBlur: ink.shadowBlur } },
          { type: 'text', invisible: !labelShown,
            style: { text: labelShown ? String(label!.formatter) : '', x: b[0] - 4,
              y: b[1] + (below ? 1 : -1) * (label?.distance ?? 4), align: 'right', verticalAlign: below ? 'top' : 'bottom',
              fontSize: label?.fontSize ?? 11, lineHeight: label?.lineHeight ?? 12,
              fill: label?.color ?? ink.color, backgroundColor: label?.backgroundColor,
              borderColor: label?.borderColor, borderWidth: label?.borderWidth, borderRadius: label?.borderRadius,
              padding: label?.padding } },
        ],
      };
    },
  };
}
