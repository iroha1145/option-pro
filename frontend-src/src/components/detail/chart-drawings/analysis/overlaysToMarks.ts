/** Map ChartAnalysisBundle overlays to ECharts marks/series. No algorithm logic. */

import { autoPatternsToMarks, type DrawingMarks, type RenderContext } from '../renderer.ts';
import { resolveAnchor } from '../projection.ts';
import { nyDateFromEpoch, type AnalysisOverlay } from './mapBundle.ts';

const PATTERN_KINDS = new Set(['support_trend', 'resistance_trend', 'channel', 'triangle', 'wedge', 'box']);

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

function epochOf(value: string): number {
  if (/^\d+$/.test(value)) {
    const n = Number(value);
    return n > 100_000_000_000 ? Math.floor(n / 1000) : n;
  }
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : 0;
}

function barStamp(iso: string, range: string): string {
  if (range === '5m' || range === '15m' || range === '1h') return String(epochOf(iso));
  return nyDateFromEpoch(epochOf(iso));
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
  return bars.map((bar) => byDate.get(barStamp(bar.t, range)) ?? null);
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

export function overlaysToMarks(
  overlays: AnalysisOverlay[],
  ctx: RenderContext,
): DrawingMarks {
  const lines: object[] = [];
  const points: object[] = [];
  const areas: object[] = [];
  const polygons: DrawingMarks['polygons'] = [];
  const patterns = overlays.filter((overlay) => PATTERN_KINDS.has(overlay.kind)).map((overlay) => {
    let anchors = overlayAnchors(overlay);
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
    return {
      id: overlay.id,
      kind: overlay.kind,
      subtype: typeof overlay.geometry.subtype === 'string' ? overlay.geometry.subtype : undefined,
      confidence: overlay.shapeQuality * 100,
      status: overlay.status,
      anchors,
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
    if (PATTERN_KINDS.has(overlay.kind) || overlay.kind === 'ma' || overlay.kind === 'vwap') continue;
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
        areas.push([
          { xAxis: ctx.xMin, yAxis: lo, itemStyle: { color: '#8A94B0', opacity: 0.08 } },
          { xAxis: ctx.xMax, yAxis: hi },
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
    const paneBudget = Math.min(usable * 0.42, extra * 10);
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
