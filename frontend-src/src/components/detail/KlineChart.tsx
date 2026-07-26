/**
 * K线主图（stock-detail.md S1 · design.md §6-1 / §6-2）
 * 蜡烛：阳实心 --up-600 / 阴实心 --down-600 / 影线 1.2px / MA20 --brand-500 虚线(4/4)
 * 成交量副图（18% 高，随阴阳 40% 透明）· 十字光标 dash 3/3
 * 面积模式：brand-500 主线 + 点阵填充 + 虚线趋势线；5 分钟图叠昨收基准虚线 + 末端价格旗标
 * quote_only bar 半透明标注 · _stale 横幅 · 503 → empty-chart.svg「快照不可用」
 */
import { useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import ReactECharts from '@/components/charts/ReactECharts';
import Segmented from '@/components/shared/Segmented';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Icon from '@/components/icons';
import { usePolling } from '@/hooks/usePolling';
import { baseAnimation, CH, glassTooltip, stippleAreaStyle, type ChartOption } from '@/lib/chart';
import { fmtCompact, fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import { CHART_RANGES, DEFAULT_CHART_RANGE, getDetailChart, type ChartRange } from './api';
import type { ChartBarEx } from '@/mocks/fixtures';
import { t } from '@/i18n/core';

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

function buildOption(
  bars: ChartBarEx[],
  ma20: (number | null)[],
  range: ChartRange,
  mode: ChartMode,
  prevClose?: number,
): ChartOption {
  const labels = bars.map((b) => fmtAxisLabel(b.t, range));
  const upFill = CH.up600;
  const downFill = CH.down600;

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
          markLine:
            range === '5m' && prevClose
              ? {
                  symbol: 'none',
                  silent: true,
                  data: [{ yAxis: prevClose }],
                  lineStyle: { color: CH.ink400, width: 1, type: [6, 4] as number[] },
                  label: {
                    formatter: `昨收 ${fmtPrice(prevClose)}`,
                    color: CH.ink400,
                    fontSize: 10,
                    fontFamily: '"IBM Plex Mono", monospace',
                    position: 'insideStartTop' as const,
                  },
                }
              : undefined,
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

  const option = useMemo(
    () => (data ? buildOption(data.bars, data.ma20, range, mode, prevClose) : null),
    [data, range, mode, prevClose],
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
        <Segmented
          options={[
            { value: 'candle' as ChartMode, label: 'K 线' },
            { value: 'area' as ChartMode, label: '面积' },
          ]}
          value={mode}
          onChange={setMode}
        />
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
              <ReactECharts option={option} ariaLabel={`${ticker} ${range} ${mode === 'candle' ? 'K 线' : '面积'}图`} />
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      <p className={cn('mt-2 flex items-center justify-between text-micro text-ink-400')}>
        <span className="font-mono tnum">
          {data
            ? t('共 {n} 根 · 末根{status}', {
                n: data.bars.length,
                status: data.bars[data.bars.length - 1]?.quote_only ? t('为仅报价 bar') : t('已收齐'),
              })
            : ' '}
        </span>
        <span className="font-mono tnum">{data ? `as of ${new Date(data.as_of).toLocaleString('zh-CN', { hour12: false })}` : ''}</span>
      </p>
    </section>
  );
}
