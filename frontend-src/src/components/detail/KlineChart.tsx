import { useLiveQuote, useQuoteStatus } from '@/hooks/useLiveQuote';
import { displayedQuoteLabel, preferLiveQuote } from '@/lib/liveQuotes';
/**
 * K线主图（stock-detail.md S1 · design.md §6-1 / §6-2）
 * 蜡烛：阳实心 --up-600 / 阴实心 --down-600 / 影线 1.2px / MA20 --brand-500 虚线(4/4)
 * 成交量副图（18% 高，随阴阳 40% 透明）· 十字光标 dash 3/3
 * 面积模式：brand-500 主线 + 点阵填充 + 虚线趋势线；5 分钟图叠昨收基准虚线 + 末端价格旗标
 * quote_only bar 半透明标注 · _stale 横幅 · 503 → empty-chart.svg「快照不可用」
 * 回撤尺：手动两点区间测量（K线默认高—低口径可切收盘，面积固定收盘口径），
 * 锚点按 bar 时间戳存储、静默刷新后重新解析，解析不到判失效
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'framer-motion';
import ReactECharts from '@/components/charts/ReactECharts';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import InfoHint from '@/components/shared/InfoHint';
import ManualStockPull from '@/components/detail/ManualStockPull';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { STRUCTURE_HINTS } from '@/lib/structureHints';
import { usePolling } from '@/hooks/usePolling';
import { useAccess } from '@/hooks/useAccess';
import { baseAnimation, CH, CHART_MONO_FONT, escapeTooltipText, glassTooltip, stippleAreaStyle, withAlpha, type ChartOption, type EChartsInstance } from '@/lib/chart';
import { directionColors, getColorMode, type ColorMode } from '@/lib/colorPreference.ts';
import { useColorMode } from '@/hooks/useColorMode.ts';
import { useDrawingController } from './chart-drawings/useDrawingController.ts';
import { snapCandidatesFromOverlays } from './chart-drawings/snap.ts';
import { railCandidatesFromOverlays } from './chart-drawings/railSnap.ts';
import { barKeyOf } from './chart-drawings/projection.ts';
import { detectSmartLines, selectSmartOverlays, withChartIndices } from './chart-drawings/analysis/smartLines.ts';
import { isSupportLevel } from './chart-drawings/linePresentation.ts';
import AnalysisLegend from './chart-drawings/AnalysisLegend';
import { clippedLineSeries, isClippedLine } from './chart-drawings/clippedLines';
import DrawingToolbar from './chart-drawings/DrawingToolbar.tsx';
import DrawingWorkspace from './chart-drawings/DrawingWorkspace.tsx';
import LayerMenu from './chart-drawings/LayerMenu.tsx';
import {
  mapChartAnalysis,
  analysisGate,
  filterOverlays,
  filterPanes,
  labelBudget,
  fingerprintForBundle,
  fingerprintDiagnosis,
  closedBarsForFingerprint,
} from './chart-drawings/analysis/mapBundle.ts';
import { overlaysToMarks, overlaysToSeries, analysisLayout, panesToOption, type PanePlot } from './chart-drawings/analysis/overlaysToMarks.ts';
import { loadLayerSettings, saveLayerSettings } from './chart-drawings/analysis/settings.ts';
import type { LayerSettings } from './chart-drawings/analysis/settings.ts';
import {
  barTimeMs,
  measureRange,
  resolveAnchor,
  snapToMeasurableBar,
  type MeasureBasis,
  type RangeMeasure,
} from '@/lib/drawdown';
import { fmtCompact, fmtLocaleDateTime, fmtPct, fmtPrice, fmtSigned } from '@/lib/format';
import { escapeHandledByOverlay } from './chart-drawings/tools.ts';
import { cn } from '@/lib/utils';
import { t } from '../../i18n/core.ts';
import { CHART_RANGES, DEFAULT_CHART_RANGE, getDetailChart, type ChartRange } from './api';
import { insideZoom, zoomFromOption, type ZoomWindow } from './chart-drawings/zoom.ts';
import { deconflictEndLabels } from './chart-drawings/renderer.ts';
import type { ChartBarEx } from '@/mocks/fixtures';
import type { TechnicalStructure } from '@/api/types';

type ChartMode = 'candle' | 'area';
export type TechOverlays = TechnicalStructure['chart_overlays'];

/**
 * 结构负载与当前图表 bars 是否同一份数据。
 *
 * 两者各有缓存（chart 10 分钟 / technical 10 分钟 + 各自的拉取快照通路），
 * 拉取或静默刷新后可能短暂错版本——把旧序列的阻力带画到新 K 线上，比暂时
 * 不画危险得多。锚点：结构声明的末根（last_bar，旧负载退 data_through）
 * 必须能在当前日线序列里找到，且落后不超过 2 根（未收盘末根 + 一个刷新周期）。
 */
function overlaysConsistentWithBars(
  technical: Pick<TechnicalStructure, 'last_bar' | 'data_through'> | null | undefined,
  bars: { t: string; ext?: boolean }[],
): boolean {
  if (!technical) return false;
  const anchor = technical.last_bar?.trade_date ?? technical.data_through;
  if (!anchor) return true; // 更旧的负载没有锚点可校验，维持原行为
  const days = bars.filter((b) => b.ext !== true).map((b) => b.t.slice(0, 10));
  const position = days.lastIndexOf(anchor);
  return position >= 0 && days.length - 1 - position <= 2;
}

function fmtAxisLabel(iso: string, range: ChartRange): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  if (range === '5m' || range === '15m' || range === '1h') {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function barTooltipTitle(iso: string, range: ChartRange): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, '0');
  const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return range === '5m' || range === '15m' || range === '1h'
    ? `${ymd} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : ymd;
}

/** 读回 ECharts 实例当前的 inside 缩放窗口（索引口径）。 */
function readZoomWindow(chart: EChartsInstance, barCount: number): ZoomWindow | null {
  return zoomFromOption(
    chart.getOption() as { dataZoom?: { startValue?: unknown; endValue?: unknown }[] } | null,
    barCount,
  );
}

/** 回撤尺覆盖层：pending = 已选起点待终点；done = 测量完成 */
type MeasureOverlay =
  | { kind: 'pending'; aIdx: number }
  | { kind: 'done'; m: RangeMeasure };

const MEASURE_LABEL_FONT = { fontSize: 10, fontFamily: CHART_MONO_FONT };

/** 覆盖层 → markLine / markPoint / markArea 数据（K线与面积模式共用） */
function measureMarks(overlay: MeasureOverlay | null | undefined) {
  const empty = { lines: [] as object[], points: [] as object[], areas: [] as object[] };
  if (!overlay) return empty;
  if (overlay.kind === 'pending') {
    return {
      ...empty,
      lines: [
        {
          xAxis: overlay.aIdx,
          lineStyle: { color: CH.brand500, width: 1, type: [4, 4] as number[] },
          label: { ...MEASURE_LABEL_FONT, formatter: t('起点'), color: CH.brand600, position: 'insideEndTop' as const },
        },
      ],
    };
  }
  const { m } = overlay;
  const dir = m.isDrawdown ? CH.down600 : CH.up600;
  const dirFill = withAlpha(dir, 0.07);
  return {
    lines: [
      {
        yAxis: m.startPrice,
        lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
        label: {
          ...MEASURE_LABEL_FONT,
          formatter: `${m.isDrawdown ? t('高') : t('低')} ${fmtPrice(m.startPrice)}`,
          color: CH.ink400,
          position: 'insideStartTop' as const,
        },
      },
      {
        yAxis: m.endPrice,
        lineStyle: { color: dir, width: 1, type: [6, 4] as number[] },
        label: {
          ...MEASURE_LABEL_FONT,
          formatter: `${m.isDrawdown ? t('低') : t('高')} ${fmtPrice(m.endPrice)} · ${fmtPct(m.changePct)}`,
          color: dir,
          position: m.isDrawdown ? ('insideStartBottom' as const) : ('insideStartTop' as const),
        },
      },
    ],
    points: [
      { coord: [m.startIdx, m.startPrice], itemStyle: { color: CH.ink400 } },
      { coord: [m.endIdx, m.endPrice], itemStyle: { color: dir } },
    ],
    areas: [
      [
        { xAxis: m.startIdx, yAxis: Math.min(m.startPrice, m.endPrice), itemStyle: { color: dirFill } },
        { xAxis: m.endIdx, yAxis: Math.max(m.startPrice, m.endPrice) },
      ],
    ],
  };
}

function buildOption(
  bars: ChartBarEx[],
  ma20: (number | null)[],
  range: ChartRange,
  mode: ChartMode,
  prevClose?: number,
  overlay?: MeasureOverlay | null,
  extra?: { lines: object[]; points: object[]; areas: object[]; polygons?: { vertices: { x: number; y: number }[]; color: string; opacity: number }[] } | null,
  analysis?: { showMa20?: boolean; extraMa?: { name: string; data: (number | null)[] }[]; panes?: PanePlot[] } | null,
  zoom?: ZoomWindow | null,
  colorMode: ColorMode = getColorMode(),
): ChartOption {
  const labels = bars.map((b) => fmtAxisLabel(b.t, range));
  const { up600: upFill, down600: downFill } = directionColors(colorMode);
  const measure = measureMarks(overlay);
  const railSeries = clippedLineSeries(extra?.lines ?? []);
  const marks = {
    lines: [...(extra?.lines ?? []).filter(line => !isClippedLine(line)), ...measure.lines],
    points: [...(extra?.points ?? []), ...measure.points],
    areas: [...(extra?.areas ?? []), ...measure.areas],
  };

  const polygons = extra?.polygons ?? [];
  const fillSeries = polygons.length
    ? {
        type: 'custom' as const,
        name: 'drawing-fills',
        clip: true,
        silent: true,
        xAxisIndex: 0,
        yAxisIndex: 0,
        z: 5,
        data: polygons,
        renderItem: (params: { dataIndex: number }, api: { coord: (value: number[]) => number[] }) => {
          const poly = polygons[params.dataIndex];
          if (!poly) return;
          return {
            type: 'polygon',
            shape: { points: poly.vertices.map((vertex) => api.coord([vertex.x, vertex.y])) },
            style: { fill: poly.color, opacity: poly.opacity },
            silent: true,
          };
        },
      }
    : null;

  const common = {
    ...baseAnimation,
    axisPointer: { link: [{ xAxisIndex: 'all' as const }] },
  };

  if (mode === 'area') {
    const closes = bars.map((b) => b.c);
    // （原全史最小二乘趋势线已删：拟合域是全部历史、显示域是默认视窗，
    //  两者不一致时那条线只会误导；且对研究没有独立价值。）
    const n = closes.length;
    const last = closes[n - 1];
    // 昨收基准线与回撤尺测量线共用一个 markLine（样式随 data 项各自指定）
    const areaMarkLines: object[] = [];
    if (range === '5m' && prevClose) {
      areaMarkLines.push({
        yAxis: prevClose,
        lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
        label: {
          formatter: t('昨收 {p}', { p: fmtPrice(prevClose) }),
          color: CH.ink400,
          fontSize: 10,
          fontFamily: CHART_MONO_FONT,
          position: 'insideStartTop' as const,
        },
      });
    }
    areaMarkLines.push(...marks.lines);
    return {
      ...common,
      grid: baseGridArea(),
      dataZoom: insideZoom(range, bars.length, [0], zoom),
      xAxis: {
        type: 'category' as const,
        data: labels,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: CHART_MONO_FONT },
      },
      yAxis: {
        type: 'value' as const,
        scale: true,
        position: 'right' as const,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: CHART_MONO_FONT },
        splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
      },
      tooltip: glassTooltip({
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = params as { dataIndex: number }[];
          const b = bars[arr[0]?.dataIndex ?? 0];
          if (!b) return '';
          const chg = b.c - b.o;
          const color = chg >= 0 ? upFill : downFill;
          return (
            `<div style="font-family:${CHART_MONO_FONT};font-size:12px;line-height:19px">` +
            `<div style="color:#6F7B9E">${barTooltipTitle(b.t, range)}${b.quote_only ? t(' · 仅报价') : ''}</div>` +
            `<div>${t('收 {c}', { c: `<b style="color:${color}">${fmtPrice(b.c)}</b>` })}</div>` +
            `<div>${t('量 {v}', { v: fmtCompact(b.v) })}</div></div>`
          );
        },
      }),
      series: [
        {
          type: 'line' as const,
          data: closes,
          showSymbol: false,
          lineStyle: { color: CH.brand500, width: 1.5 },
          areaStyle: stippleAreaStyle(),
          endLabel: {
            show: true,
            formatter: () => fmtPrice(last),
            color: CH.brand600,
            fontSize: 11,
            fontFamily: CHART_MONO_FONT,
            distance: 6,
          },
          markLine: areaMarkLines.length
            ? { symbol: 'none', silent: true, data: areaMarkLines }
            : undefined,
          markPoint: marks.points.length
            ? { symbol: 'circle', symbolSize: 7, silent: true, data: marks.points }
            : undefined,
          markArea: marks.areas.length ? { silent: true, data: marks.areas } : undefined,
          z: 3,
        },
        ...(fillSeries ? [{ ...fillSeries, xAxisIndex: 0, yAxisIndex: 0 }] : []),
        ...(railSeries ? [railSeries] : []),
      ],
    } as ChartOption;
  }

  /* ---- K线模式 ---- */
  const candleData = bars.map((b) => ({
    value: [b.o, b.c, b.l, b.h],
    ...(b.quote_only ? { itemStyle: { opacity: 0.45 } } : {}),
  }));
  const volData = bars.map((b) => ({
    value: b.v,
    itemStyle: { color: withAlpha(b.c >= b.o ? upFill : downFill, 0.4) },
  }));

  const panes = analysis?.panes ?? [];
  const grids = analysisLayout(panes.length);
  const axisIndexes = grids.map((_, index) => index);
  const paneSeries = panes.flatMap((pane, paneIndex) => {
    const axis = paneIndex + 2;
    return pane.series.map((row, seriesIndex) => ({
      type: row.type === 'bar' ? ('bar' as const) : ('line' as const),
      name: row.name,
      xAxisIndex: axis,
      yAxisIndex: axis,
      data: row.data,
      showSymbol: false,
      connectNulls: true,
      barMaxWidth: 8,
      lineStyle: {
        color: seriesIndex === 0 ? CH.brand500 : seriesIndex === 1 ? CH.ai600 : CH.ink400,
        width: 1,
      },
      itemStyle: row.type === 'bar' ? { color: CH.ink400 } : undefined,
      tooltip: { show: true },
      z: 2,
    }));
  });
  const paneMarkLines = panes.map((pane, paneIndex) => ({
    paneIndex,
    marks: pane.markLines ?? [],
    yMin: pane.yMin,
    yMax: pane.yMax,
  }));
  return {
    ...common,
    grid: grids,
    dataZoom: insideZoom(range, bars.length, axisIndexes, zoom),
    xAxis: grids.map((_, index) => ({
      type: 'category' as const,
      gridIndex: index,
      data: labels,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: index === grids.length - 1
        ? { color: CH.ink400, fontSize: 11, fontFamily: CHART_MONO_FONT }
        : { show: false },
    })),
    yAxis: grids.map((_, index) => {
      const pane = index >= 2 ? panes[index - 2] : null;
      return {
        type: 'value' as const,
        gridIndex: index,
        scale: pane?.yMin == null && pane?.yMax == null,
        min: pane?.yMin,
        max: pane?.yMax,
        position: 'right' as const,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: index === 0 || pane
          ? { color: CH.ink400, fontSize: 11, fontFamily: CHART_MONO_FONT }
          : { show: false },
        splitLine: index === 0
          ? { lineStyle: { color: CH.lineChart, width: 1 } }
          : { show: false },
      };
    }),
    tooltip: glassTooltip({
      trigger: 'axis',
      axisPointer: {
        type: 'cross' as const,
        lineStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
        crossStyle: { color: CH.ink300, width: 1, type: [3, 3] as number[] },
        label: {
          backgroundColor: 'rgba(253,252,249,.92)',
          borderColor: '#E9ECF1', // v8.1 tooltip 边框随 line 降温（原 #E9E7E0 暖灰漏网）
          borderWidth: 1,
          color: '#5A6788',
          fontFamily: CHART_MONO_FONT,
          fontSize: 10,
        },
      },
      formatter: (params: unknown) => {
        const rows = params as { seriesType?: string; seriesName?: string; dataIndex: number; value?: unknown; data?: unknown }[];
        const idx = rows.find((p) => p.seriesType === 'candlestick' || p.seriesType === 'bar')?.dataIndex
          ?? rows[0]?.dataIndex
          ?? 0;
        const b = bars[idx];
        if (!b) return '';
        const signedCell = (chg: number, pct: number | null) => {
          const color = chg >= 0 ? upFill : downFill;
          const sign = chg >= 0 ? '+' : '−';
          const pctText = pct === null ? '' : ` (${sign}${Math.abs(pct).toFixed(2)}%)`;
          return `<span style="color:${color}">${sign}${Math.abs(chg).toFixed(2)}${pctText}</span>`;
        };
        const chg = b.c - b.o;
        const color = chg >= 0 ? upFill : downFill;
        // 「开→收」量的是 bar 实体；跳空行情里它看不见隔夜缺口，所以再给
        // 「较前收」一行（上一根收盘为基准）。首根没有前收，只显示开→收。
        const prev = idx > 0 ? bars[idx - 1] : null;
        const gapChg = prev && prev.c > 0 ? b.c - prev.c : null;
        const row = (k: string, v: string) =>
          `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:#6F7B9E">${escapeTooltipText(k)}</span><span>${v}</span></div>`;
        return (
          `<div style="font-family:${CHART_MONO_FONT};font-size:12px;line-height:19px;min-width:150px">` +
          `<div style="color:#6F7B9E;margin-bottom:2px">${barTooltipTitle(b.t, range)}${b.quote_only ? t(' · <span style="color:#E8930C">仅报价</span>') : ''}</div>` +
          row(t('开'), fmtPrice(b.o)) +
          row(t('高'), fmtPrice(b.h)) +
          row(t('低'), fmtPrice(b.l)) +
          row(t('收'), `<b style="color:${color}">${fmtPrice(b.c)}</b>`) +
          row(t('开→收'), signedCell(chg, b.o ? (chg / b.o) * 100 : null)) +
          (gapChg !== null ? row(t('较前收'), signedCell(gapChg, (gapChg / prev!.c) * 100)) : '') +
          row(t('量'), fmtCompact(b.v)) +
          panes.flatMap((pane) => pane.series.map((series) => {
            const value = series.data[idx];
            if (value == null) return '';
            return row(series.name, Number(value).toFixed(series.name === 'OBV' ? 0 : 2));
          })).join('') +
          `</div>`
        );
      },
    }),
    series: [
      {
        type: 'candlestick' as const,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: candleData,
        itemStyle: {
          color: upFill,
          color0: downFill,
          borderColor: upFill,
          borderColor0: downFill,
          borderWidth: 1,
        },
        barMaxWidth: 14,
        markLine: marks.lines.length ? { symbol: 'none', silent: true, data: marks.lines } : undefined,
        markPoint: marks.points.length
          ? { symbol: 'circle', symbolSize: 7, silent: true, data: marks.points }
          : undefined,
        markArea: marks.areas.length ? { silent: true, data: marks.areas } : undefined,
        z: 3,
      },
      ...((analysis?.showMa20 !== false) ? [{
        type: 'line' as const,
        name: 'MA20',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ma20,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { color: CH.brand500, width: 1.5, type: [4, 4] as number[] },
        tooltip: { show: false },
        z: 4,
      }] : []),
      ...((analysis?.extraMa ?? []).map((line) => ({
        type: 'line' as const,
        name: line.name,
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: line.data,
        showSymbol: false,
        connectNulls: true,
        lineStyle: { color: CH.ink400, width: 1, type: [4, 4] as number[] },
        tooltip: { show: false },
        z: 4,
      }))),
      {
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volData,
        barMaxWidth: 12,
        tooltip: { show: false },
        z: 2,
      },
      ...paneSeries.map((series, index) => {
        const paneMeta = paneMarkLines.find((row) => {
          const start = panes.slice(0, row.paneIndex).reduce((sum, pane) => sum + pane.series.length, 0);
          return index >= start && index < start + panes[row.paneIndex].series.length;
        });
        const isFirstOfPane = paneMeta
          ? index === panes.slice(0, paneMeta.paneIndex).reduce((sum, pane) => sum + pane.series.length, 0)
          : false;
        return {
          ...series,
          markLine: isFirstOfPane && paneMeta && paneMeta.marks.length
            ? {
                symbol: 'none',
                silent: true,
                data: paneMeta.marks.map((value) => ({
                  yAxis: value,
                  lineStyle: { color: CH.ink300, width: 1, type: [4, 4] as number[] },
                  /* 关掉 echarts 默认线尾数值：它把 0/30/70 打进轴槽，正好骑在
                     副图自己的轴刻度上（用户截图里加粗的 0 就是两字叠印）。 */
                  label: { show: false },
                })),
              }
            : undefined,
        };
      }),
      ...(fillSeries ? [fillSeries] : []),
      ...(railSeries ? [railSeries] : []),
    ],
  } as ChartOption;
}

function baseGridArea() {
  return { left: 8, right: 56, top: 16, bottom: 8, containLabel: true };
}

/** 回撤尺状态机：idle → 选起点 → 选终点 → 完成（Esc / 再点按钮回 idle） */
type MeasureState =
  | { phase: 'idle' }
  | { phase: 'selectStart' }
  | { phase: 'selectEnd'; aMs: number }
  | { phase: 'done'; aMs: number; bMs: number };

function measureDurationText(range: ChartRange, m: RangeMeasure): string {
  if (range === '1w') return t('{n} 周', { n: m.barCount });
  if (range === '1d') return t('{n} 个交易日', { n: m.barCount });
  return t('{n} 根 · 跨 {d} 个交易日', { n: m.barCount, d: m.sessionDays });
}

/** 与 MacroHistoryChart 叠加线按钮一致的开关样式 */
function toggleButtonCls(active: boolean): string {
  return cn(
    'rounded-xs border px-2 py-0.5 text-micro outline-none transition-colors duration-fast',
    active
      ? 'border-brand-400 bg-brand-50 text-brand-700 shadow-chip'
      : 'border-line text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
  );
}

export default function KlineChart({
  ticker,
  prevClose,
  currentPrice,
  quoteUpdatedAt,
  height = 320,
  className,
  refreshVersion = 0,
  technical = null,
}: {
  ticker: string;
  prevClose?: number;
  currentPrice?: number | null;
  quoteUpdatedAt?: string | null;
  height?: number;
  className?: string;
  refreshVersion?: number;
  /** 技术结构负载（/stocks/{t}/technical）；叠加只在日 K 绘制，且要过与
      当前 bars 的同源校验——不一致宁可暂隐，也不把旧带画到新 K 线上 */
  technical?: TechnicalStructure | null;
}) {
  const liveQuote = useLiveQuote(ticker);
  const quoteStatus = useQuoteStatus();
  const overlays = technical?.chart_overlays ?? null;
  const colorMode = useColorMode();
  // Daily bars are the reliable default covered by Massive Stocks Starter;
  // intraday intervals remain available on demand. The default lives in ./api so
  // the prefetch and this component request the same URL.
  const [range, setRange] = useState<ChartRange>(DEFAULT_CHART_RANGE);
  const [mode, setMode] = useState<ChartMode>('candle');
  // Per-chart view preference; never rewrites saved hand drawings or backend scores.
  const [smartDrawingEnabled, setSmartDrawingEnabled] = useState(true);
  const seenRefreshVersion = useRef(refreshVersion);
  const { data, error, loading, refresh } = usePolling(
    () => {
      const force = seenRefreshVersion.current !== refreshVersion;
      seenRefreshVersion.current = refreshVersion;
      return getDetailChart(ticker, range, force);
    },
    null,
    [ticker, range, refreshVersion],
  );

  const [measure, setMeasure] = useState<MeasureState>({ phase: 'idle' });
  const [basis, setBasis] = useState<MeasureBasis>('wick');
  const [chartInst, setChartInst] = useState<EChartsInstance | null>(null);
  const measureActive = measure.phase !== 'idle';
  const { username, isOwner, isCustomer, canManageWatchlist } = useAccess();
  const reducedMotion = Boolean(useReducedMotion());
  const identityKey = isCustomer && username ? `account:${username}` : isOwner ? 'owner' : 'anonymous';
  const bars = data?.bars;
  // 结构负载与图表 bars 各有缓存，可能短暂错版本；不同源就暂隐叠加。
  const overlaysConsistent = useMemo(
    () => (range === '1d' && bars ? overlaysConsistentWithBars(technical, bars) : false),
    [range, bars, technical],
  );
  const levelsInconsistent = overlays !== null && range === '1d' && mode === 'candle' && !overlaysConsistent;
  // 面积图没有影线可吸附，强制收盘口径；K线默认高—低，可切收盘
  const effectiveBasis: MeasureBasis = mode === 'area' ? 'close' : basis;

  // 锚点属于旧价格序列：切标的 / 周期 / 显示模式一律清除，不跨序列迁移
  const measureScope = `${ticker}|${range}|${mode}`;
  const zoomScope = `${ticker}|${range}`;
  const [armedScope, setArmedScope] = useState(measureScope);
  const zoomRef = useRef<{ scope: string; window: ZoomWindow } | null>(null);
  useLayoutEffect(() => { zoomRef.current = null; }, [zoomScope]);
  if (armedScope !== measureScope) {
    setArmedScope(measureScope);
    setMeasure({ phase: 'idle' });
  }

  // 用户滚过的视窗记在 ref 里：option 每次重建（落笔、点选、拖拽提交、切图层）
  // 都从这里读回，缩放才不会被 notMerge 弹回默认的最后 126 根。
  useEffect(() => {
    const chart = chartInst;
    if (!chart || chart.isDisposed()) return;
    const handler = () => {
      if (chart.isDisposed()) return;
      const next = readZoomWindow(chart, bars?.length ?? 0);
      if (next) zoomRef.current = { scope: zoomScope, window: next };
    };
    chart.on('datazoom', handler);
    return () => {
      if (!chart.isDisposed()) chart.off('datazoom', handler);
    };
  }, [chartInst, bars, zoomScope]);

  // 选点走 zrender 全域点击：点击任意位置吸附最近可测 bar（series 命中区太窄，移动端点不中）
  useEffect(() => {
    const chart = chartInst;
    const phase = measure.phase;
    if (!chart || chart.isDisposed() || !bars?.length) return;
    if (phase !== 'selectStart' && phase !== 'selectEnd') return;
    const zr = chart.getZr();
    const handler = (event: { offsetX: number; offsetY: number }) => {
      const converted = chart.convertFromPixel({ gridIndex: 0 }, [event.offsetX, event.offsetY]) as
        | number[]
        | null;
      if (!converted || !Number.isFinite(converted[0])) return;
      const idx = snapToMeasurableBar(bars, converted[0]);
      if (idx < 0) return;
      const clickedMs = barTimeMs(bars[idx]);
      setMeasure((prev) => {
        if (prev.phase === 'selectStart') return { phase: 'selectEnd', aMs: clickedMs };
        if (prev.phase === 'selectEnd') return { phase: 'done', aMs: prev.aMs, bMs: clickedMs };
        return prev;
      });
    };
    zr.on('click', handler);
    return () => {
      if (!chart.isDisposed()) zr.off('click', handler);
    };
  }, [chartInst, measure.phase, bars]);

  const measurement = useMemo(() => {
    if (measure.phase !== 'done' || !bars) return null;
    return measureRange(bars, measure.aMs, measure.bMs, effectiveBasis);
  }, [measure, bars, effectiveBasis]);

  // 数据静默刷新后锚点时间戳解析不到 → 失效态，如实提示而不是挪到别的 bar
  const measureInvalid = measure.phase === 'done' && !!bars && !measurement;

  const overlay = useMemo<MeasureOverlay | null>(() => {
    if (measure.phase === 'selectEnd' && bars) {
      const aIdx = resolveAnchor(bars, measure.aMs);
      return aIdx >= 0 ? { kind: 'pending', aIdx } : null;
    }
    if (measure.phase === 'done' && measurement) return { kind: 'done', m: measurement };
    return null;
  }, [measure, bars, measurement]);

  // 图层设置按身份分桶。useAccess 是异步落定的（AccessProvider 先渲染子树再等
  // /access/status），登录/登出也换 key 而不重挂载——只在初始化时读一次，就会拿
  // 匿名桶的默认值，之后第一次勾选又以「默认 + 单个改动」写进账号桶，把用户存的
  // 预设悄悄抹掉。这里跟 useDrawingController 一样按 identity 重新装载，
  // 并且只往「装载时那把 key」上落盘。
  const [layersIdentity, setLayersIdentity] = useState(identityKey);
  const [layerSettings, setLayerSettings] = useState<LayerSettings>(() => loadLayerSettings(identityKey));
  if (layersIdentity !== identityKey) {
    setLayersIdentity(identityKey);
    setLayerSettings(loadLayerSettings(identityKey));
  }
  const [layersOpen, setLayersOpen] = useState(false);
  const persistLayers = (next: LayerSettings) => {
    setLayerSettings(next);
    saveLayerSettings(layersIdentity, next);
  };
  const analysisBundle = useMemo(
    () => mapChartAnalysis(
      range === '1d'
        ? (technical?.chart_analysis ?? technical ?? null)
        : (data?.chart_analysis ?? null),
    ),
    [technical, data, range],
  );
  // 指纹用 bundle 自带的元数据（首/末 bar 日期）来切窗口，而不是拿
  // series_break_at 猜后端到底哈希了哪一段；闸门是 fail-closed 的，猜错就是
  // 整块分析静默消失。
  const fingerprintOpts = useMemo(() => ({
    dropLast: range === '1d' && technical?.last_bar?.closed === false,
    fromDate: range === '1d' ? (technical?.series_break_at ?? null) : null,
  }), [range, technical]);
  const visibleFingerprint = useMemo(
    () => (data?.bars?.length ? fingerprintForBundle(analysisBundle, data.bars, range, fingerprintOpts) : null),
    [data, range, analysisBundle, fingerprintOpts],
  );
  const gatedBars = useMemo(
    () => (data?.bars?.length ? closedBarsForFingerprint(data.bars, range, {
      ...fingerprintOpts,
      throughDate: analysisBundle?.lastBarDate ?? null,
    }) : []),
    [analysisBundle?.lastBarDate, data, fingerprintOpts, range],
  );
  const gateReason = analysisGate(analysisBundle, {
    range,
    adjustment: 'raw',
    ticker,
    dataThrough: range === '1d' ? technical?.data_through : analysisBundle?.dataThrough,
    barCount: gatedBars.length || null,
    lastClose: gatedBars.length ? gatedBars[gatedBars.length - 1]?.c ?? null : null,
    fingerprint: visibleFingerprint,
  });
  const analysisOk = gateReason === 'ok' && (range !== '1d' || overlaysConsistent);
  // 指纹对不上时说出来：整套图层不画，但用户至少知道是版本错位而不是「没形态」。
  const analysisDrift = useMemo(() => {
    if (!analysisBundle || !data?.bars?.length) return null;
    if (gateReason !== 'fingerprint' && gateReason !== 'bar_count') return null;
    if (levelsInconsistent) return null; // 图例那条「版本不一致」已经说过同一件事
    return fingerprintDiagnosis(analysisBundle, data.bars, range, fingerprintOpts);
  }, [analysisBundle, data, gateReason, levelsInconsistent, range, fingerprintOpts]);
  const chartKeys = useMemo(() => (data?.bars ?? []).map(bar => barKeyOf(bar, range)), [data?.bars, range]);
  const smartBars = useMemo(() => withChartIndices(gatedBars.map(bar => ({
    ...bar, key: barKeyOf(bar, range),
  })), chartKeys), [gatedBars, range, chartKeys]);
  const smartProposals = useMemo(() => {
    if (!analysisOk || !smartDrawingEnabled
      || !layerSettings.enabled.some(id => id === 'auto_patterns' || id === 'support_resistance')) return [];
    return detectSmartLines(smartBars);
  }, [analysisOk, smartDrawingEnabled, smartBars, layerSettings.enabled]);
  const visibleOverlays = useMemo(() => {
    if (!analysisOk || !analysisBundle) return [];
    if (!smartDrawingEnabled) return filterOverlays(analysisBundle.overlays, layerSettings);
    // Filter enabled layers/status/quality BEFORE geometric deduplication. An invisible
    // channel must never suppress a visible trendline; apply the count cap afterwards.
    const candidates = filterOverlays([...analysisBundle.overlays, ...smartProposals], {
      ...layerSettings, maxPatterns: 64,
    });
    return selectSmartOverlays(candidates, smartBars, layerSettings.maxPatterns);
  }, [analysisOk, analysisBundle, layerSettings, smartDrawingEnabled, smartProposals, smartBars]);
  const drawingSnapCandidates = useMemo(() => [
    ...snapCandidatesFromOverlays(visibleOverlays),
    ...railCandidatesFromOverlays(visibleOverlays, chartKeys),
  ], [visibleOverlays, chartKeys]);
  const visiblePanes = useMemo(() => {
    if (!analysisOk || !analysisBundle) return [];
    return filterPanes(analysisBundle.indicatorPanes, layerSettings);
  }, [analysisOk, analysisBundle, layerSettings]);
  const visibleLabels = useMemo(
    () => labelBudget(visibleOverlays, layerSettings),
    [visibleOverlays, layerSettings],
  );

  const drawing = useDrawingController({
    ticker,
    range,
    bars: data?.bars,
    ma20: data?.ma20,
    snapCandidates: drawingSnapCandidates,
    chart: chartInst,
    identity: { signedIn: canManageWatchlist, key: identityKey },
    measureActive,
    onCancelMeasure: () => setMeasure({ phase: 'idle' }),
    reducedMotion,
  });

  useEffect(() => {
    if (!measureActive) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      const openModals = typeof document === 'undefined'
        ? 0
        : document.querySelectorAll('[role="dialog"],[role="alertdialog"],[aria-modal="true"]').length;
      if (escapeHandledByOverlay({
        defaultPrevented: event.defaultPrevented,
        openModals,
        workspaceExpanded: drawing.expanded,
      })) return;
      event.preventDefault();
      event.stopPropagation();
      setMeasure({ phase: 'idle' });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [drawing.expanded, measureActive]);

  const extraMarks = useMemo(() => {
    const hand = drawing.marks;
    if (!analysisOk || !data) return hand;
    const prices = data.bars.flatMap((bar) => [bar.h, bar.l]);
    const yMin = Math.min(...prices);
    const yMax = Math.max(...prices);
    const auto = overlaysToMarks(visibleOverlays, {
      bars: data.bars,
      range,
      xMin: 0,
      xMax: data.bars.length - 1,
      yMin,
      yMax,
    }, autoPatternName, new Set(visibleLabels.map(item => item.id)));
    // 自动形态与手绘线的线端标签在这里汇合，防叠必须在汇合后做（见 deconflictEndLabels）。
    return {
      lines: deconflictEndLabels([...auto.lines, ...hand.lines], yMin, yMax),
      points: [...auto.points, ...hand.points],
      areas: [...auto.areas, ...hand.areas],
      polygons: [...(auto.polygons ?? []), ...(hand.polygons ?? [])],
    };
  }, [analysisOk, data, drawing.marks, range, visibleOverlays, visibleLabels]);

  const analysisOption = useMemo(() => {
    const showMa20 = layerSettings.enabled.includes('ma20');
    const extraMa = (analysisOk && data
      ? overlaysToSeries(visibleOverlays, data.bars, range)
      : []
    )
      .filter((line) => line.id !== 'ma20')
      .map((line) => ({ name: line.name, data: line.data }));
    // 副图必须和 MA 走同一条按日期对齐的路：分析序列可能只覆盖 series_break_at
    // 之后的一段（长度 M < 图上 N 根），直接当成从索引 0 开始的裸数组，
    // 会把最近的动量值画到 N−M 根之前的老蜡烛底下。
    const compact = !drawing.expanded;
    const limited = compact ? visiblePanes.slice(0, 1) : visiblePanes;
    const panes = analysisOk && data && mode === 'candle'
      ? panesToOption(limited, data.bars, range)
      : [];
    return { showMa20: mode === 'candle' && showMa20, extraMa: mode === 'candle' ? extraMa : [], panes };
  }, [analysisOk, data, drawing.expanded, mode, range, visibleOverlays, visiblePanes, layerSettings]);

  const option = useMemo(
    () => {
      if (!data) return null;
      const base = buildOption(data.bars, data.ma20, range, mode, prevClose, overlay, extraMarks, analysisOption, null, colorMode);
      const series = Array.isArray(base.series) ? base.series : base.series ? [base.series] : [];
      return { ...base, series: [...series, { id: 'realtime-price-reference', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: [], showSymbol: false, lineStyle: { opacity: 0 }, silent: true, animation: false, tooltip: { show: false }, markLine: { symbol: 'none', data: [] } }] } as ChartOption;
    },
    [data, range, mode, prevClose, overlay, extraMarks, analysisOption, colorMode],
  );
  // Depend on the visible reference values, not the entire quote: unchanged
  // prices with newer trade timestamps must not call ECharts.setOption again.
  const usesLiveReference = preferLiveQuote(liveQuote, typeof currentPrice === 'number' && currentPrice > 0, quoteUpdatedAt);
  const referenceCandidate = liveQuote ? (usesLiveReference ? liveQuote.price : currentPrice) : null;
  const referencePrice = typeof referenceCandidate === 'number' && Number.isFinite(referenceCandidate) && referenceCandidate > 0 ? referenceCandidate : null;
  const referenceLabel = referencePrice !== null && liveQuote
    ? `${displayedQuoteLabel(liveQuote, quoteStatus, usesLiveReference)} $${referencePrice.toFixed(2)}` : '';
  const referenceBarCount = bars?.length ?? 0;
  useEffect(() => {
    if (!chartInst || chartInst.isDisposed() || !option) return;
    chartInst.setOption({ series: [{ id: 'realtime-price-reference', data: referencePrice !== null && referenceBarCount ? [[referenceBarCount - 1, referencePrice]] : [], markLine: {
      symbol: 'none', silent: true, animation: false,
      lineStyle: { type: 'dashed', width: 1, color: CH.brand500 },
      label: { show: true, position: 'insideEndTop', formatter: referenceLabel, color: CH.brand500, fontSize: 10 },
      data: referencePrice !== null ? [{ yAxis: referencePrice }] : [],
    } }] }, { notMerge: false, lazyUpdate: true, silent: true });
  }, [chartInst, option, referencePrice, referenceLabel, referenceBarCount]);

  // The chart calls this from its commit effect. A scroll never rebuilds the
  // option, and an abandoned render cannot reset the live chart's viewport.
  const prepareOption = useCallback((next: ChartOption): ChartOption => {
    const saved = zoomRef.current;
    if (!saved || saved.scope !== zoomScope || !Array.isArray(next.dataZoom)) return next;
    const restored = insideZoom(range, bars?.length ?? 0, [], saved.window)?.[0];
    if (!restored) return next;
    return {
      ...next,
      dataZoom: next.dataZoom.map((row) => ({
        ...row, startValue: restored.startValue, endValue: restored.endValue,
      })),
    };
  }, [bars?.length, range, zoomScope]);

  const chartBody = (
    <section
      className={cn(className, drawing.expanded && 'flex h-full min-h-0 flex-col')}
      aria-label={t('{ticker} K 线图', { ticker })}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <Segmented
          options={CHART_RANGES}
          value={range}
          onChange={setRange}
          className="[&_button]:font-mono [&_button]:text-micro"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            options={[
              { value: 'candle' as ChartMode, label: t('K 线') },
              { value: 'area' as ChartMode, label: t('面积') },
            ]}
            value={mode}
            onChange={setMode}
          />
          <button
            type="button"
            aria-pressed={smartDrawingEnabled}
            disabled={!analysisOk || !layerSettings.enabled.some(id => id === 'auto_patterns' || id === 'support_resistance')}
            title={t('基于已收盘 K 线补充识别、合并重复线；不改变后端信号评分')}
            onClick={() => setSmartDrawingEnabled(value => !value)}
            className={cn(toggleButtonCls(smartDrawingEnabled), 'min-h-8 disabled:cursor-not-allowed disabled:opacity-50')}
          >
            {t('智能画线')}
          </button>
          {mode === 'area' && (
            <span className="text-micro text-ink-400">{t('面积图不支持副图与均线叠加')}</span>
          )}
          {range === '1h' && (
            <span className="text-micro text-ink-400">{t('1小时图无法在没有 5 分钟数据时绘制开盘区间')}</span>
          )}
          {measureActive && mode === 'candle' && (
            <button
              type="button"
              aria-pressed={basis === 'close'}
              aria-label={t('按收盘价口径测量')}
              onClick={() => setBasis((prev) => (prev === 'close' ? 'wick' : 'close'))}
              className={toggleButtonCls(basis === 'close')}
            >
              {t('收盘口径')}
            </button>
          )}
          <button
            type="button"
            aria-pressed={measureActive}
            aria-label={t('回撤测量尺')}
            onClick={() => {
              drawing.setTool('select');
              setMeasure((prev) => (prev.phase === 'idle' ? { phase: 'selectStart' } : { phase: 'idle' }));
            }}
            className={toggleButtonCls(measureActive)}
          >
            {t('回撤')}
          </button>
        </div>
      </div>
      <div className={cn('mt-2', drawing.expanded && 'hidden')}>
        <DrawingToolbar
          tool={drawing.tool}
          onTool={(next) => {
            if (next !== 'select') setMeasure({ phase: 'idle' });
            drawing.setTool(next);
          }}
          canUndo={drawing.canUndo}
          canRedo={drawing.canRedo}
          onUndo={drawing.undo}
          onRedo={drawing.redo}
          autoPatternsEnabled={layerSettings.enabled.includes('auto_patterns')}
          onToggleAuto={() => persistLayers({
            ...layerSettings,
            preset: 'custom',
            enabled: layerSettings.enabled.includes('auto_patterns')
              ? layerSettings.enabled.filter((id) => id !== 'auto_patterns')
              : [...layerSettings.enabled, 'auto_patterns'],
          })}
          layersOpen={layersOpen}
          onOpenLayers={() => setLayersOpen(true)}
          expanded={drawing.expanded}
          onToggleExpanded={() => drawing.setExpanded((prev) => !prev)}
          syncStatus={drawing.syncStatus}
          syncHint={drawing.syncHint}
          onRetry={drawing.retry}
          onKeepLocal={() => void drawing.keepLocalConflict()}
          onTakeServer={() => void drawing.takeServerConflict()}
          compact
        />
      </div>
      {drawing.tool !== 'select' && (
        <p className="mt-1 text-micro text-ink-400" aria-live="polite">
          {t('当前工具：{name}', {
            name:
              drawing.tool === 'horizontal' ? t('水平线')
                : drawing.tool === 'segment' ? t('趋势线')
                  : drawing.tool === 'ray' ? t('射线')
                    : drawing.tool === 'channel' ? t('平行通道')
                      : drawing.tool === 'rectangle' ? t('矩形')
                        : drawing.tool === 'fibonacci' ? t('斐波那契')
                          : t('文字'),
          })}
          {' · '}
          {t('按 Esc 取消绘制')}
        </p>
      )}
      {drawing.inProgress?.kind === 'text' && (
        <form
          className="mt-2 flex items-center gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            drawing.commitText(drawing.draftText);
          }}
        >
          <label className="flex-1 text-micro">
            <span className="sr-only">{t('文字注释')}</span>
            <input
              autoFocus
              maxLength={240}
              value={drawing.draftText}
              onChange={(event) => drawing.setDraftText(event.target.value)}
              className="w-full rounded-xs border border-line bg-card px-2 py-1 text-caption outline-none focus-visible:ring-2 focus-visible:ring-brand-500/30"
              placeholder={t('点击放置文字，然后输入内容')}
            />
          </label>
          <button type="submit" className="rounded-xs border border-brand-400 bg-brand-50 px-2 py-1 text-micro text-brand-700">{t('保存文字')}</button>
        </form>
      )}

      {data?._stale && (
        <p className="mt-3 flex items-center gap-1.5 rounded-xs border border-warn-600/30 bg-warn-50 px-2.5 py-1.5 text-caption text-warn-600">
          <Icon name="bell" size={13} />
          {t('数据暂未刷新 · 显示最近一次结果（延迟行情）')}
        </p>
      )}

      <div className="relative mt-3 min-h-0" style={{ height: drawing.expanded ? '100%' : height }}>
        <AnimatePresence mode="wait">
          {loading ? (
            <motion.div
              key="skeleton"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, transition: { duration: 0.16 } }}
              className="absolute inset-0 flex flex-col gap-2"
              aria-hidden="true"
            >
              <SkeletonBlock className="h-[62%] w-full rounded-md border border-line-chart" />
              <SkeletonBlock className="h-[18%] w-full rounded-md border border-line-chart" />
            </motion.div>
          ) : error || !option ? (
            <motion.div key="error" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 overflow-auto">
              <EmptyState
                variant="empty"
                image="/empty-chart.svg"
                title={
                  error?.bizCode === 'public_snapshot_unavailable'
                    ? t('该标的暂无完整数据')
                    : t('K 线暂不可用')
                }
                description={
                  error?.bizCode === 'public_snapshot_unavailable'
                    ? t('该股票暂无数据，可手动获取最新行情、日线与技术指标')
                    : t('{ticker} · {range}数据暂不可用，其他周期仍可切换', { ticker, range: CHART_RANGES.find((item) => item.value === range)?.label ?? range })
                }
                action={
                  error?.bizCode === 'public_snapshot_unavailable' ? (
                    <ManualStockPull ticker={ticker} compact onPulled={() => refresh({ force: true })} />
                  ) : (
                    <button type="button" onClick={() => refresh()} className="btn-primary">
                      {t('重试')}
                    </button>
                  )
                }
                className="py-6"
              />
            </motion.div>
          ) : (
            <motion.div
              key={`${range}-${mode}`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1, transition: { duration: 0.26, ease: [0.16, 1, 0.3, 1] } }}
              exit={{ opacity: 0, transition: { duration: 0.16 } }}
              className="absolute inset-0"
            >
              <ReactECharts
                option={option}
                prepareOption={prepareOption}
                onInit={setChartInst}
                className={measureActive || drawing.tool !== 'select' ? 'cursor-crosshair' : undefined}
                ariaLabel={t('{ticker} {range} {mode}图', { ticker, range, mode: mode === 'candle' ? t('K 线') : t('面积') })}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {measureActive && (
        <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-micro">
          {measure.phase === 'selectStart' && (
            <span className="text-ink-400">{t('回撤尺：点击图表选择起点（Esc 退出）')}</span>
          )}
          {measure.phase === 'selectEnd' && (
            <span className="text-ink-400">{t('回撤尺：再次点击选择终点（Esc 退出）')}</span>
          )}
          {measure.phase === 'done' && measurement && (
            <>
              <span className="text-ink-400">
                {effectiveBasis === 'wick' ? t('高—低') : t('收盘—收盘')}
                {measurement.isDrawdown ? t('回撤') : t('涨幅')}
              </span>
              <span
                className={cn(
                  'font-mono tnum font-medium',
                  measurement.isDrawdown ? 'text-down-600' : 'text-up-600',
                )}
              >
                {t('{pct}（{abs}）', { pct: fmtPct(measurement.changePct), abs: fmtSigned(measurement.changeAbs) })}
              </span>
              <span className="font-mono tnum text-ink-400">
                {fmtPrice(measurement.startPrice)} → {fmtPrice(measurement.endPrice)}
              </span>
              <span className="text-ink-400">{measureDurationText(range, measurement)}</span>
              {measurement.recoveryPct !== null && (
                <span className="text-ink-400">
                  {t('修复需')} <span className="font-mono tnum">{fmtPct(measurement.recoveryPct)}</span>
                </span>
              )}
              <button
                type="button"
                onClick={() => setMeasure({ phase: 'selectStart' })}
                className="text-brand-600 underline-offset-2 hover:underline"
              >
                {t('重测')}
              </button>
            </>
          )}
          {measureInvalid && (
            <>
              <span className="text-warn-600">{t('测量已失效（数据已更新）')}</span>
              <button
                type="button"
                onClick={() => setMeasure({ phase: 'selectStart' })}
                className="text-brand-600 underline-offset-2 hover:underline"
              >
                {t('重新测量')}
              </button>
            </>
          )}
        </p>
      )}

      {data && (
        <OverlayLegend
          mode={mode}
          shown={analysisOk && visibleOverlays.length > 0}
          inconsistent={levelsInconsistent}
          hasBase={overlays?.resistance_high != null}
          baseStatus={overlays?.base_status ?? null}
          showMa20={layerSettings.enabled.includes('ma20')}
        />
      )}
      {analysisDrift && (
        <p className="mt-1 text-micro text-warn-600" role="status">
          {t('分析图层与当前 K 线不同版本（图上 {n} 根 / 分析 {m} 根），已暂隐，刷新后恢复', {
            n: analysisDrift.bars,
            m: analysisDrift.expected ?? analysisDrift.bars,
          })}
        </p>
      )}
      {analysisOk && <AnalysisLegend overlays={visibleOverlays} smartEnabled={smartDrawingEnabled} />}
      {analysisOk && visibleLabels.length > 0 && (
        <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-micro text-ink-400">
          {visibleLabels.map((overlayRow) => {
            const name = autoPatternName(
              overlayRow.kind === 'level' ? (isSupportLevel(overlayRow.geometry, data?.bars[data.bars.length - 1]?.c) ? 'support_trend' : 'resistance_trend') : overlayRow.kind,
              overlayRow.kind === 'level' ? 'horizontal' : typeof overlayRow.geometry.subtype === 'string' ? overlayRow.geometry.subtype : null,
            );
            if (!name) return null;
            const touches = overlayRow.evidence.touches;
            return (
              <span key={overlayRow.id} title={t('几何质量不是胜率')}>
                {t('形态 · {name} · 几何质量 {n}', { name, n: Math.round(overlayRow.shapeQuality * 100) })}
                {typeof touches === 'number' && touches > 0 ? ` · ${t('触碰 {n} 次', { n: touches })}` : ''}
              </span>
            );
          })}
        </p>
      )}
      <LayerMenu
        open={layersOpen}
        onClose={() => setLayersOpen(false)}
        settings={layerSettings}
        onChange={persistLayers}
        strengthContext={analysisBundle?.strengthContext ?? null}
        mode={mode}
      />

      <p className={cn('mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-micro text-ink-400')}>
        <span className="font-mono tnum">
          {data
            ? t('共 {n} 根 · 末根 {at}{status}', {
                n: data.bars.length,
                at: lastBarText(data, range),
                status: data.bars[data.bars.length - 1]?.quote_only ? t('（仅报价）') : '',
              })
            : ' '}
        </span>
        <span className="font-mono tnum">
          {data ? t('读取于 {at}', { at: fmtLocaleDateTime(data.as_of, { hour12: false }) }) : ''}
        </span>
      </p>
    </section>
  );

  return (
    <DrawingWorkspace
      open={drawing.expanded}
      controller={drawing}
      reducedMotion={reducedMotion}
      layersOpen={layersOpen}
      onOpenLayers={() => setLayersOpen(true)}
      autoPatternsEnabled={layerSettings.enabled.includes('auto_patterns')}
      onToggleAuto={() => persistLayers({
        ...layerSettings,
        preset: 'custom',
        enabled: layerSettings.enabled.includes('auto_patterns')
          ? layerSettings.enabled.filter((id) => id !== 'auto_patterns')
          : [...layerSettings.enabled, 'auto_patterns'],
      })}
    >
      {chartBody}
    </DrawingWorkspace>
  );
}

/** 末根 K 线自身的时间：日/周只到日期，分钟带时刻（as_of 只是读取时刻，两回事） */
function lastBarText(data: { bars: ChartBarEx[]; last_bar_at?: string | null }, range: ChartRange): string {
  const iso = data.bars[data.bars.length - 1]?.t ?? data.last_bar_at;
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const pad = (n: number) => String(n).padStart(2, '0');
  const ymd = `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
  return range === '5m' || range === '15m' || range === '1h'
    ? `${ymd} ${pad(d.getHours())}:${pad(d.getMinutes())}`
    : ymd;
}

/** 只认已命名的形态；认不出就返回 null，绝不把 kind 原样打成「形态 · ma」。 */
function autoPatternName(kind: string, subtype?: string | null): string | null {
  if (kind === 'support_trend') return subtype === 'horizontal' ? t('水平支撑') : subtype === 'falling' ? t('下降支撑') : t('上升支撑');
  if (kind === 'resistance_trend') return subtype === 'horizontal' ? t('水平阻力') : subtype === 'rising' ? t('上升阻力') : t('下降阻力');
  if (kind === 'channel') return subtype === 'horizontal' ? t('水平通道') : subtype === 'falling' ? t('下降通道') : t('上升通道');
  if (kind === 'triangle') {
    if (subtype === 'ascending') return t('上升三角形');
    if (subtype === 'descending') return t('下降三角形');
    return t('对称三角形');
  }
  if (kind === 'wedge') return subtype === 'falling' ? t('下降楔形') : t('上升楔形');
  if (kind === 'box') return t('水平箱体');
  return null;
}

/**
 * 可扫读的图上标记图例：色块/符号 + 名称，按当前模式与数据状态变化——
 * 检出失效基底时点明状态、结构与图表错版本时说明为何暂隐，
 * 而不是永远同一句「按日线结构自动标注」。
 */
function OverlayLegend({
  mode,
  shown,
  inconsistent,
  hasBase,
  baseStatus,
  showMa20 = true,
}: {
  mode: ChartMode;
  shown: boolean;
  inconsistent: boolean;
  hasBase: boolean;
  baseStatus: TechnicalStructure['chart_overlays']['base_status'] | null;
  showMa20?: boolean;
}) {
  if (inconsistent) {
    return (
      <p className="mt-2 text-micro text-warn-600">
        {t('结构分析与当前 K 线数据版本不一致，技术点位已暂隐，刷新后恢复')}
      </p>
    );
  }
  if (!shown) return null;
  const chip = (symbol: ReactNode, label: string) => (
    <span className="inline-flex items-center gap-1">
      {symbol}
      <span>{label}</span>
    </span>
  );
  return (
    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-micro text-ink-400">
      <span className="inline-flex items-center gap-1 text-ink-500">
        {t('图例')}
        <InfoHint hint={STRUCTURE_HINTS.chart_overlays} align="start" size={12} />
      </span>
      {hasBase
        && chip(
          <span className="inline-block h-2 w-4 rounded-xs bg-brand-400/30" aria-hidden />,
          baseStatus === 'failed' ? t('阻力带（基底已失效）') : t('阻力带（整理区上沿）'),
        )}
      {hasBase
        && chip(
          <span className="inline-block h-0 w-4 border-t border-dotted border-down-600" aria-hidden />,
          t('失效位'),
        )}
      {chip(<span aria-hidden className="text-warn-600" style={{ fontSize: 8 }}>▼</span>, t('确认摆动高点'))}
      {chip(<span aria-hidden className="text-ai-600" style={{ fontSize: 8 }}>▲</span>, t('确认摆动低点'))}
      {mode === 'candle' && showMa20
        && chip(
          <span className="inline-block h-0 w-4 border-t border-dashed border-brand-500" aria-hidden />,
          t('MA20 · 最近 20 根常规时段收盘的均线'),
        )}
    </p>
  );
}
