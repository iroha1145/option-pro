/** Map ChartAnalysisBundle overlays to ECharts marks/series. No algorithm logic. */

import { autoPatternsToMarks, type DrawingMarks, type RenderContext } from '../renderer.ts';
import { resolveAnchor } from '../projection.ts';
import { barStampForRange, isPatternKind, type AnalysisOverlay, type AnalysisPane } from './mapBundle.ts';

export interface OverlaySeriesLine {
  id: string;
  name: string;
  data: Array<number | null>;
}

export interface OverlayRender {
  marks: DrawingMarks;
  series: OverlaySeriesLine[];
}

export interface LayoutGrid {
  left: number;
  right: number;
  top: string;
  height: string;
  containLabel: boolean;
}

export function alignSeriesToBars(
  values: Array<number | null | undefined>,
  dates: string[] | undefined,
  bars: { t: string }[],
  range: string,
): Array<number | null> {
  const finite = (value: unknown) => (typeof value === 'number' && Number.isFinite(value) ? value : null);
  if (!dates || dates.length !== values.length) {
    const out = values.map(finite);
    if (out.length === bars.length) return out;
    if (out.length === bars.length - 1) return [...out, null];
    if (out.length > bars.length) return out.slice(out.length - bars.length);
    return [...Array(Math.max(0, bars.length - out.length)).fill(null), ...out];
  }
  const byDate = new Map<string, number | null>();
  dates.forEach((day, index) => {
    byDate.set(day, finite(values[index]));
  });
  return bars.map((bar) => byDate.get(barStampForRange(bar.t, range)) ?? null);
}

function paleLine(color = '#8A94B0', width = 1) {
  return { color, width, type: [4, 4] as number[] };
}

function asNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function overlayAnchors(overlay: AnalysisOverlay): { time: string; barKey: string; price: number }[] {
  const raw = overlay.geometry.anchors;
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (!item || typeof item !== 'object') return [];
    const row = item as Record<string, unknown>;
    const time = typeof row.time === 'string' ? row.time : '';
    const barKey = typeof row.barKey === 'string' ? row.barKey : '';
    const price = asNumber(row.price);
    if (!time || !barKey || price === null) return [];
    return [{ time, barKey, price }];
  });
}

export function overlaysToSeries(
  overlays: AnalysisOverlay[],
  bars: { t: string }[] = [],
  range = '1d',
): OverlaySeriesLine[] {
  return overlays
    .filter((overlay) => overlay.kind === 'ma' || overlay.kind === 'vwap')
    .map((overlay) => {
      const values = Array.isArray(overlay.geometry.values)
        ? (overlay.geometry.values as Array<number | null | undefined>)
        : [];
      const dates = Array.isArray(overlay.geometry.dates)
        ? (overlay.geometry.dates as string[])
        : undefined;
      return {
        id: overlay.id,
        name: overlay.label || overlay.id,
        data: alignSeriesToBars(values, dates, bars, range),
      };
    });
}

/** 形态线按 kind 分色（与 lib/chart 的 CH 调色同值；此处写字面量是因为
 *  本模块要进单测的 vm bundle，不能拖 echarts 进来）。box 维持中性灰：
 *  它是「区域」不是「方向线」，抢色只会稀释支撑/阻力的红绿语义。 */
/* 一律取 700 档：600 档细虚线浮在白纸上不够显（用户实测「颜色不够明显」）。
   字面量而非 import——本模块进 vm 测试 bundle，进不了 CSS token。 */
const PATTERN_LINE_COLORS: Record<string, string> = {
  support_trend: '#0B7A55',    // up700：支撑在下，向上托
  resistance_trend: '#C4302B', // down700：阻力在上，向下压
  channel: '#2338C8',          // brand700
  triangle: '#C27706',         // warn600 按 600→700 同比例加深（体系无 warn-700）
  wedge: '#085E6E',            // ai600 青瓷同比例加深（体系无 ai-700）
};

export function overlaysToMarks(
  overlays: AnalysisOverlay[],
  ctx: RenderContext,
  /** 形态的展示名（KlineChart 注入 autoPatternName）：给了才画线端标签。 */
  patternLabel?: (kind: string, subtype?: string | null) => string | null,
): DrawingMarks {
  const lines: object[] = [];
  const points: object[] = [];
  const areas: object[] = [];
  const polygons: DrawingMarks['polygons'] = [];
  const patterns = overlays.filter((overlay) => isPatternKind(overlay.kind)).map((overlay) => {
    const supportRail = Array.isArray(overlay.geometry.supportRail) ? overlay.geometry.supportRail : null;
    const resistanceRail = Array.isArray(overlay.geometry.resistanceRail) ? overlay.geometry.resistanceRail : null;
    const fitAnchors = Array.isArray(overlay.geometry.fitAnchors) ? overlay.geometry.fitAnchors : null;
    let anchors = overlayAnchors({
      ...overlay,
      geometry: {
        ...overlay.geometry,
        anchors: (
          supportRail && resistanceRail && supportRail.length >= 2 && resistanceRail.length >= 2
            ? [...supportRail, ...resistanceRail]
            : fitAnchors && fitAnchors.length >= 2
              ? fitAnchors
              : overlay.geometry.anchors
        ),
      },
    });
    if (overlay.kind === 'box' && anchors.length < 2) {
      const lo = asNumber(overlay.geometry.supportLow);
      const hi = asNumber(overlay.geometry.resistanceHigh);
      if (lo !== null && hi !== null) {
        anchors = [
          { time: `${overlay.formationStart}T00:00:00+00:00`, barKey: overlay.formationStart, price: lo },
          { time: `${overlay.formationEnd}T00:00:00+00:00`, barKey: overlay.formationEnd, price: lo },
          { time: `${overlay.formationStart}T00:00:00+00:00`, barKey: overlay.formationStart, price: hi },
          { time: `${overlay.formationEnd}T00:00:00+00:00`, barKey: overlay.formationEnd, price: hi },
        ];
      }
    }
    const subtype = typeof overlay.geometry.subtype === 'string' ? overlay.geometry.subtype : undefined;
    return {
      id: overlay.id,
      kind: overlay.kind,
      subtype,
      confidence: overlay.shapeQuality * 100,
      status: overlay.status,
      anchors,
      color: PATTERN_LINE_COLORS[overlay.kind],
      label: patternLabel ? patternLabel(overlay.kind, subtype) ?? undefined : undefined,
    };
  });
  const patternMarks = autoPatternsToMarks(patterns, ctx, 0);
  lines.push(...patternMarks.lines);
  points.push(...patternMarks.points);
  areas.push(...patternMarks.areas);
  polygons.push(...(patternMarks.polygons ?? []));

  const pushLevel = (price: number, color = '#8A94B0') => {
    lines.push([
      { coord: [ctx.xMin, price], lineStyle: paleLine(color) },
      { coord: [ctx.xMax, price] },
    ]);
  };

  for (const overlay of overlays) {
    if (isPatternKind(overlay.kind) || overlay.kind === 'ma' || overlay.kind === 'vwap') continue;
    if (overlay.kind === 'swing' || overlay.kind === 'candle' || overlay.kind === 'trap') {
      const anchors = overlayAnchors(overlay);
      for (const anchor of anchors) {
        const index = resolveAnchor(ctx.bars, anchor, ctx.range);
        if (index < 0) continue;
        const high = overlay.geometry.role === 'high' || (overlay.kind === 'trap' && overlay.direction === 'bearish');
        points.push({
          coord: [index, anchor.price],
          symbol: overlay.kind === 'swing' ? 'triangle' : 'diamond',
          symbolRotate: overlay.kind === 'swing' && high ? 180 : 0,
          symbolSize: overlay.kind === 'swing' ? 8 : 7,
          itemStyle: { color: overlay.kind === 'candle' ? '#5A6788' : (high ? '#BA7517' : '#0B6E99') },
          label: { show: false },
        });
      }
      continue;
    }
    if (overlay.kind === 'level') {
      const price = asNumber(overlay.geometry.price);
      if (price !== null) pushLevel(price);
      continue;
    }
    if (overlay.kind === 'pivot' || overlay.kind === 'breakout') {
      const pivot = asNumber(overlay.geometry.pivot);
      const invalidation = asNumber(overlay.geometry.invalidation);
      if (pivot !== null) pushLevel(pivot, '#2E46E0');
      if (invalidation !== null) pushLevel(invalidation, '#E5484D');
      continue;
    }
    if (overlay.kind === 'opening_range') {
      const hi = asNumber(overlay.geometry.high);
      const lo = asNumber(overlay.geometry.low);
      if (hi !== null && lo !== null) {
        const startKey = typeof overlay.geometry.sessionStartBarKey === 'string'
          ? overlay.geometry.sessionStartBarKey
          : null;
        const endKey = typeof overlay.geometry.sessionEndBarKey === 'string'
          ? overlay.geometry.sessionEndBarKey
          : null;
        const start = startKey
          ? Math.max(ctx.xMin, ctx.bars.findIndex((bar) => barStampForRange(bar.t, ctx.range) === startKey))
          : ctx.xMin;
        const end = endKey
          ? Math.max(start, ctx.bars.findIndex((bar) => barStampForRange(bar.t, ctx.range) === endKey))
          : ctx.xMax;
        areas.push([
          { xAxis: start < 0 ? ctx.xMin : start, yAxis: lo, itemStyle: { color: '#8A94B0', opacity: 0.08 } },
          { xAxis: end < 0 ? ctx.xMax : end, yAxis: hi },
        ]);
      }
      continue;
    }
    if (overlay.kind === 'volume_setup') {
      const window = Math.max(1, Math.round(asNumber(overlay.geometry.window) ?? 10));
      const start = Math.max(ctx.xMin, ctx.xMax - window + 1);
      areas.push([
        { xAxis: start, yAxis: ctx.yMin, itemStyle: { color: '#8A94B0', opacity: 0.05 } },
        { xAxis: ctx.xMax, yAxis: ctx.yMax },
      ]);
    }
  }

  return { lines, points, areas, polygons, unresolvedIds: [] };
}

export interface PanePlotSeries {
  key: string;
  name: string;
  data: Array<number | null>;
  type: 'line' | 'bar';
}

export interface PanePlot {
  id: string;
  label: string;
  kind: string;
  yMin?: number;
  yMax?: number;
  markLines?: number[];
  series: PanePlotSeries[];
}

function paneDates(pane: AnalysisPane, values: Array<number | null | undefined>): string[] {
  if (pane.dates.length > values.length) return pane.dates.slice(0, values.length);
  return pane.dates;
}

export function panesToOption(
  panes: AnalysisPane[],
  bars: { t: string }[],
  range: string,
): PanePlot[] {
  return panes.map((pane) => {
    const align = (key: string) => {
      const values = pane.values[key] ?? [];
      return alignSeriesToBars(values, paneDates(pane, values), bars, range);
    };
    if (pane.kind === 'rsi' || pane.id === 'rsi') {
      return {
        id: pane.id,
        label: pane.label,
        kind: 'rsi',
        yMin: 0,
        yMax: 100,
        markLines: [30, 70],
        series: [{ key: 'rsi', name: 'RSI', data: align('rsi'), type: 'line' }],
      };
    }
    if (pane.kind === 'macd' || pane.id === 'macd') {
      return {
        id: pane.id,
        label: pane.label,
        kind: 'macd',
        markLines: [0],
        series: [
          { key: 'macd', name: 'MACD', data: align('macd'), type: 'line' },
          { key: 'signal', name: 'Signal', data: align('signal'), type: 'line' },
          { key: 'histogram', name: 'Hist', data: align('histogram'), type: 'bar' },
        ],
      };
    }
    if (pane.kind === 'obv' || pane.id === 'obv') {
      return {
        id: pane.id,
        label: pane.label,
        kind: 'obv',
        series: [{ key: 'obv', name: 'OBV', data: align('obv'), type: 'line' }],
      };
    }
    if (pane.kind === 'clv' || pane.id === 'clv') {
      return {
        id: pane.id,
        label: pane.label,
        kind: 'clv',
        yMin: -1,
        yMax: 1,
        markLines: [0],
        series: [{ key: 'clv', name: 'CLV', data: align('clv'), type: 'line' }],
      };
    }
    if (pane.kind === 'rs' || pane.id === 'spy_rs') {
      return {
        id: pane.id,
        label: pane.label,
        kind: 'rs',
        markLines: [100],
        series: [{ key: 'rs', name: pane.label, data: align('rs'), type: 'line' }],
      };
    }
    return {
      id: pane.id,
      label: pane.label,
      kind: pane.kind,
      series: [{
        key: 'position',
        name: pane.label,
        data: align('position'),
        type: 'line',
      }],
    };
  });
}

export function analysisLayout(paneCount: number): LayoutGrid[] {
  const extra = Math.max(0, paneCount);
  const topPad = 3;
  const bottomPad = 2;
  const gap = extra ? 1.2 : 2;
  const regions = 2 + extra;
  const usable = 100 - topPad - bottomPad - (regions - 1) * gap;
  let volH: number;
  let paneH: number;
  let priceH: number;
  if (extra === 0) {
    priceH = usable * 0.78;
    volH = usable * 0.22;
    paneH = 0;
  } else {
    volH = Math.min(11, usable * 0.12);
    const paneBudget = Math.min(usable * 0.42, extra * 16);
    paneH = paneBudget / extra;
    priceH = usable - volH - paneH * extra;
    if (priceH < 28) {
      const shrink = paneH * extra;
      const need = 28 - priceH;
      const scale = shrink > 0 ? Math.max(0, shrink - need) / shrink : 0;
      paneH *= scale;
      priceH = usable - volH - paneH * extra;
    }
  }
  const heights = [priceH, volH, ...Array.from({ length: extra }, () => paneH)];
  let cursor = topPad;
  return heights.map((height, index) => {
    const grid = {
      left: 8,
      right: 8,
      top: `${Number(cursor.toFixed(2))}%`,
      height: `${Number(height.toFixed(2))}%`,
      containLabel: true,
    };
    cursor += height + (index === heights.length - 1 ? 0 : gap);
    return grid;
  });
}
