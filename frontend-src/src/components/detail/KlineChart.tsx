/**
 * K线主图（stock-detail.md S1 · design.md §6-1 / §6-2）
 * 蜡烛：阳实心 --up-600 / 阴实心 --down-600 / 影线 1.2px / MA20 --brand-500 虚线(4/4)
 * 成交量副图（18% 高，随阴阳 40% 透明）· 十字光标 dash 3/3
 * 面积模式：brand-500 主线 + 点阵填充 + 虚线趋势线；5 分钟图叠昨收基准虚线 + 末端价格旗标
 * quote_only bar 半透明标注 · _stale 横幅 · 503 → empty-chart.svg「快照不可用」
 * 回撤尺：手动两点区间测量（K线默认高—低口径可切收盘，面积固定收盘口径），
 * 锚点按 bar 时间戳存储、静默刷新后重新解析，解析不到判失效
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import ReactECharts from '@/components/charts/ReactECharts';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { usePolling } from '@/hooks/usePolling';
import { baseAnimation, CH, glassTooltip, stippleAreaStyle, type ChartOption, type EChartsInstance } from '@/lib/chart';
import {
  barTimeMs,
  measureRange,
  resolveAnchor,
  snapToMeasurableBar,
  type MeasureBasis,
  type RangeMeasure,
} from '@/lib/drawdown';
import { fmtCompact, fmtPct, fmtPrice, fmtSigned } from '@/lib/format';
import { cn } from '@/lib/utils';
import { CHART_RANGES, DEFAULT_CHART_RANGE, getDetailChart, type ChartRange } from './api';
import type { ChartBarEx } from '@/mocks/fixtures';

type ChartMode = 'candle' | 'area';

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

/** 回撤尺覆盖层：pending = 已选起点待终点；done = 测量完成 */
type MeasureOverlay =
  | { kind: 'pending'; aIdx: number }
  | { kind: 'done'; m: RangeMeasure };

const MEASURE_LABEL_FONT = { fontSize: 10, fontFamily: '"IBM Plex Mono", monospace' };

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
          label: { ...MEASURE_LABEL_FONT, formatter: '起点', color: CH.brand600, position: 'insideEndTop' as const },
        },
      ],
    };
  }
  const { m } = overlay;
  const dir = m.isDrawdown ? CH.down600 : CH.up600;
  const dirFill = m.isDrawdown ? 'rgba(229,72,77,.07)' : 'rgba(14,159,110,.07)';
  return {
    lines: [
      {
        yAxis: m.startPrice,
        lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
        label: {
          ...MEASURE_LABEL_FONT,
          formatter: `${m.isDrawdown ? '高' : '低'} ${fmtPrice(m.startPrice)}`,
          color: CH.ink400,
          position: 'insideStartTop' as const,
        },
      },
      {
        yAxis: m.endPrice,
        lineStyle: { color: dir, width: 1, type: [6, 4] as number[] },
        label: {
          ...MEASURE_LABEL_FONT,
          formatter: `${m.isDrawdown ? '低' : '高'} ${fmtPrice(m.endPrice)} · ${fmtPct(m.changePct)}`,
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
): ChartOption {
  const labels = bars.map((b) => fmtAxisLabel(b.t, range));
  const upFill = CH.up600;
  const downFill = CH.down600;
  const marks = measureMarks(overlay);

  const common = {
    ...baseAnimation,
    axisPointer: { link: [{ xAxisIndex: 'all' as const }] },
  };

  if (mode === 'area') {
    const closes = bars.map((b) => b.c);
    // 虚线趋势线（最小二乘）
    const n = closes.length;
    let slope = 0;
    let intercept = closes[0] ?? 0;
    if (n > 1) {
      let sx = 0, sy = 0, sxy = 0, sxx = 0;
      closes.forEach((c, i) => {
        sx += i; sy += c; sxy += i * c; sxx += i * i;
      });
      slope = (n * sxy - sx * sy) / Math.max(1e-9, n * sxx - sx * sx);
      intercept = (sy - slope * sx) / n;
    }
    const last = closes[n - 1];
    // 昨收基准线与回撤尺测量线共用一个 markLine（样式随 data 项各自指定）
    const areaMarkLines: object[] = [];
    if (range === '5m' && prevClose) {
      areaMarkLines.push({
        yAxis: prevClose,
        lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
        label: {
          formatter: `昨收 ${fmtPrice(prevClose)}`,
          color: CH.ink400,
          fontSize: 10,
          fontFamily: '"IBM Plex Mono", monospace',
          position: 'insideStartTop' as const,
        },
      });
    }
    areaMarkLines.push(...marks.lines);
    return {
      ...common,
      grid: baseGridArea(),
      xAxis: {
        type: 'category' as const,
        data: labels,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
      },
      yAxis: {
        type: 'value' as const,
        scale: true,
        position: 'right' as const,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
        splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
      },
      tooltip: glassTooltip({
        trigger: 'axis',
        formatter: (params: unknown) => {
          const arr = params as { dataIndex: number }[];
          const b = bars[arr[0]?.dataIndex ?? 0];
          if (!b) return '';
          const chg = b.c - b.o;
          const color = chg >= 0 ? CH.up600 : CH.down600;
          return (
            `<div style="font-family:'IBM Plex Mono',monospace;font-size:12px;line-height:19px">` +
            `<div style="color:#8A94B0">${barTooltipTitle(b.t, range)}${b.quote_only ? ' · 仅报价' : ''}</div>` +
            `<div>收 <b style="color:${color}">${fmtPrice(b.c)}</b></div>` +
            `<div>量 ${fmtCompact(b.v)}</div></div>`
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
            fontFamily: '"IBM Plex Mono", monospace',
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
        {
          type: 'line' as const,
          data: closes.map((_, i) => Number((intercept + slope * i).toFixed(2))),
          showSymbol: false,
          silent: true,
          lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
          tooltip: { show: false },
          z: 2,
        },
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
    itemStyle: { color: b.c >= b.o ? 'rgba(14,159,110,.4)' : 'rgba(229,72,77,.4)' },
  }));

  return {
    ...common,
    grid: [
      { left: 8, right: 8, top: 12, height: '60%', containLabel: true },
      { left: 8, right: 8, top: '76%', height: '17%', containLabel: true },
    ],
    xAxis: [
      {
        type: 'category' as const,
        gridIndex: 0,
        data: labels,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
      },
      {
        type: 'category' as const,
        gridIndex: 1,
        data: labels,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
      },
    ],
    yAxis: [
      {
        type: 'value' as const,
        gridIndex: 0,
        scale: true,
        position: 'right' as const,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: CH.ink400, fontSize: 11, fontFamily: '"IBM Plex Mono", monospace' },
        splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
      },
      {
        type: 'value' as const,
        gridIndex: 1,
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
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
          fontFamily: '"IBM Plex Mono", monospace',
          fontSize: 10,
        },
      },
      formatter: (params: unknown) => {
        const arr = (params as { seriesType?: string; dataIndex: number }[]).filter((p) => p.seriesType !== 'line');
        const idx = arr[0]?.dataIndex ?? 0;
        const b = bars[idx];
        if (!b) return '';
        const chg = b.c - b.o;
        const pct = b.o ? (chg / b.o) * 100 : 0;
        const color = chg >= 0 ? CH.up600 : CH.down600;
        const sign = chg >= 0 ? '+' : '−';
        const row = (k: string, v: string) =>
          `<div style="display:flex;justify-content:space-between;gap:16px"><span style="color:#8A94B0">${k}</span><span>${v}</span></div>`;
        return (
          `<div style="font-family:'IBM Plex Mono',monospace;font-size:12px;line-height:19px;min-width:150px">` +
          `<div style="color:#8A94B0;margin-bottom:2px">${barTooltipTitle(b.t, range)}${b.quote_only ? ' · <span style="color:#E8930C">仅报价</span>' : ''}</div>` +
          row('开', fmtPrice(b.o)) +
          row('高', fmtPrice(b.h)) +
          row('低', fmtPrice(b.l)) +
          row('收', `<b style="color:${color}">${fmtPrice(b.c)}</b>`) +
          row('涨跌', `<span style="color:${color}">${sign}${Math.abs(chg).toFixed(2)} (${sign}${Math.abs(pct).toFixed(2)}%)</span>`) +
          row('量', fmtCompact(b.v)) +
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
      {
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
      },
      {
        type: 'bar' as const,
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volData,
        barMaxWidth: 12,
        tooltip: { show: false },
        z: 2,
      },
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
  if (range === '1w') return `${m.barCount} 周`;
  if (range === '1d') return `${m.barCount} 个交易日`;
  return `${m.barCount} 根 · 跨 ${m.sessionDays} 个交易日`;
}

/** 与 MacroHistoryChart 叠加线按钮一致的开关样式 */
function toggleButtonCls(active: boolean): string {
  return cn(
    'rounded-xs border px-2 py-0.5 text-micro outline-none transition-colors duration-fast',
    active
      ? 'border-brand-400 bg-brand-50 text-brand-700'
      : 'border-line text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
  );
}

export default function KlineChart({
  ticker,
  prevClose,
  height = 320,
  className,
  refreshVersion = 0,
}: {
  ticker: string;
  prevClose?: number;
  height?: number;
  className?: string;
  refreshVersion?: number;
}) {
  // Daily bars are the reliable default covered by Massive Stocks Starter;
  // intraday intervals remain available on demand. The default lives in ./api so
  // the prefetch and this component request the same URL.
  const [range, setRange] = useState<ChartRange>(DEFAULT_CHART_RANGE);
  const [mode, setMode] = useState<ChartMode>('candle');
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
  // 面积图没有影线可吸附，强制收盘口径；K线默认高—低，可切收盘
  const effectiveBasis: MeasureBasis = mode === 'area' ? 'close' : basis;
  const bars = data?.bars;

  // 锚点属于旧价格序列：切标的 / 周期 / 显示模式一律清除，不跨序列迁移
  useEffect(() => {
    setMeasure({ phase: 'idle' });
  }, [ticker, range, mode]);

  useEffect(() => {
    if (!measureActive) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMeasure({ phase: 'idle' });
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [measureActive]);

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

  const option = useMemo(
    () => (data ? buildOption(data.bars, data.ma20, range, mode, prevClose, overlay) : null),
    [data, range, mode, prevClose, overlay],
  );

  return (
    <section className={className} aria-label={`${ticker} K 线图`}>
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
              { value: 'candle' as ChartMode, label: 'K 线' },
              { value: 'area' as ChartMode, label: '面积' },
            ]}
            value={mode}
            onChange={setMode}
          />
          {measureActive && mode === 'candle' && (
            <button
              type="button"
              aria-pressed={basis === 'close'}
              aria-label="按收盘价口径测量"
              onClick={() => setBasis((prev) => (prev === 'close' ? 'wick' : 'close'))}
              className={toggleButtonCls(basis === 'close')}
            >
              收盘口径
            </button>
          )}
          <button
            type="button"
            aria-pressed={measureActive}
            aria-label="回撤测量尺"
            onClick={() =>
              setMeasure((prev) => (prev.phase === 'idle' ? { phase: 'selectStart' } : { phase: 'idle' }))
            }
            className={toggleButtonCls(measureActive)}
          >
            回撤
          </button>
        </div>
      </div>

      {data?._stale && (
        <p className="mt-3 flex items-center gap-1.5 rounded-xs border border-warn-600/30 bg-warn-50 px-2.5 py-1.5 text-caption text-warn-600">
          <Icon name="bell" size={13} />
          数据暂未刷新 · 显示最近一次结果（延迟行情）
        </p>
      )}

      <div className="relative mt-3" style={{ height }}>
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
                title="K 线暂不可用"
                description={`${ticker} · ${CHART_RANGES.find((item) => item.value === range)?.label ?? range}数据暂不可用，其他周期仍可切换`}
                action={
                  <button
                    onClick={refresh}
                    className="rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] duration-fast hover:brightness-105"
                  >
                    重试
                  </button>
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
                onInit={setChartInst}
                className={measureActive ? 'cursor-crosshair' : undefined}
                ariaLabel={`${ticker} ${range} ${mode === 'candle' ? 'K 线' : '面积'}图`}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {measureActive && (
        <p className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-micro">
          {measure.phase === 'selectStart' && (
            <span className="text-ink-400">回撤尺：点击图表选择起点（Esc 退出）</span>
          )}
          {measure.phase === 'selectEnd' && (
            <span className="text-ink-400">回撤尺：再次点击选择终点（Esc 退出）</span>
          )}
          {measure.phase === 'done' && measurement && (
            <>
              <span className="text-ink-400">
                {effectiveBasis === 'wick' ? '高—低' : '收盘—收盘'}
                {measurement.isDrawdown ? '回撤' : '涨幅'}
              </span>
              <span
                className={cn(
                  'font-mono tnum font-medium',
                  measurement.isDrawdown ? 'text-down-600' : 'text-up-600',
                )}
              >
                {fmtPct(measurement.changePct)}（{fmtSigned(measurement.changeAbs)}）
              </span>
              <span className="font-mono tnum text-ink-400">
                {fmtPrice(measurement.startPrice)} → {fmtPrice(measurement.endPrice)}
              </span>
              <span className="text-ink-400">{measureDurationText(range, measurement)}</span>
              {measurement.recoveryPct !== null && (
                <span className="text-ink-400">
                  修复需 <span className="font-mono tnum">{fmtPct(measurement.recoveryPct)}</span>
                </span>
              )}
              <button
                type="button"
                onClick={() => setMeasure({ phase: 'selectStart' })}
                className="text-brand-600 underline-offset-2 hover:underline"
              >
                重测
              </button>
            </>
          )}
          {measureInvalid && (
            <>
              <span className="text-warn-600">测量已失效（数据已更新）</span>
              <button
                type="button"
                onClick={() => setMeasure({ phase: 'selectStart' })}
                className="text-brand-600 underline-offset-2 hover:underline"
              >
                重新测量
              </button>
            </>
          )}
        </p>
      )}

      <p className={cn('mt-2 flex items-center justify-between text-micro text-ink-400')}>
        <span className="font-mono tnum">
          {data ? `共 ${data.bars.length} 根 · 末根${data.bars[data.bars.length - 1]?.quote_only ? '为仅报价 bar' : '已收齐'}` : ' '}
        </span>
        <span className="font-mono tnum">{data ? `as of ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}` : ''}</span>
      </p>
    </section>
  );
}
