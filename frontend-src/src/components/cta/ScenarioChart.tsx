/**
 * 情景曲线（/cta 主区）：若明日收于横轴价格，估算目标仓位为纵轴值。
 * 实线=完整敞口（含波动率调整），虚线=仅趋势（波动率冻结）；阴影带为主要
 * 触发区间。曲线是分段线性的模型输出，不是预测路径。
 */
import { useMemo } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import { baseAnimation, CH, glassTooltip, type ChartOption } from '@/lib/chart';
import { fmtPrice } from '@/lib/format';
import type { CtaInstrumentEstimate } from '@/api/types';
import { t } from '../../i18n/core.ts';

export function scenarioOption(row: CtaInstrumentEstimate): ChartOption | null {
  const curve = row.scenario_curve;
  if (!curve || curve.prices.length === 0 || row.reference_price === null) return null;
  const refIndex = curve.prices.reduce(
    (best, p, i) => (Math.abs(p - row.reference_price!) < Math.abs(curve.prices[best] - row.reference_price!) ? i : best),
    0,
  );
  const zones = [...(row.trigger_levels?.above ?? []), ...(row.trigger_levels?.below ?? [])];
  const idxOf = (price: number) =>
    curve.prices.reduce((best, p, i) => (Math.abs(p - price) < Math.abs(curve.prices[best] - price) ? i : best), 0);
  return {
    ...baseAnimation,
    grid: { left: 8, right: 14, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: curve.prices.map((p) => fmtPrice(p)),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: '"IBM Plex Mono", monospace', interval: 23 },
    },
    yAxis: {
      type: 'value' as const,
      min: -100,
      max: 100,
      position: 'right' as const,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: '"IBM Plex Mono", monospace' },
      splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    },
    tooltip: glassTooltip({
      trigger: 'axis',
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number; seriesName: string; data: number }[];
        if (!arr.length) return '';
        const idx = arr[0].dataIndex;
        const rows = arr
          .map((s) => `<div>${s.seriesName}: <b>${s.data > 0 ? '+' : ''}${s.data}</b></div>`)
          .join('');
        return (
          `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;line-height:17px">` +
          `<div style="color:#8A94B0">${t('若收于 {p}', { p: fmtPrice(curve.prices[idx]) })}</div>${rows}</div>`
        );
      },
    }),
    series: [
      {
        type: 'line' as const,
        name: t('完整敞口'),
        data: curve.full,
        showSymbol: false,
        lineStyle: { color: CH.brand500, width: 1.8 },
        markLine: {
          symbol: 'none',
          silent: true,
          data: [
            {
              xAxis: refIndex,
              lineStyle: { color: CH.ink400, width: 1, type: [4, 4] as number[] },
              label: {
                formatter: t('现价 {p}', { p: fmtPrice(row.reference_price) }),
                color: CH.ink400,
                fontSize: 10,
                fontFamily: '"IBM Plex Mono", monospace',
                position: 'insideEndTop' as const,
              },
            },
          ],
        },
        markArea: zones.length
          ? {
              silent: true,
              data: zones.map((zone) => [
                {
                  xAxis: idxOf(zone.price_low),
                  itemStyle: {
                    color: zone.est_position_change >= 0 ? CH.up600 : CH.down600,
                    opacity: 0.08,
                  },
                },
                { xAxis: idxOf(zone.price_high) },
              ]),
            }
          : undefined,
        z: 3,
      },
      {
        type: 'line' as const,
        name: t('仅趋势（波动率冻结）'),
        data: curve.trend_only,
        showSymbol: false,
        lineStyle: { color: CH.ink400, width: 1.2, type: [5, 4] as number[] },
        z: 2,
      },
    ],
  } as ChartOption;
}

export default function ScenarioChart({ row }: { row: CtaInstrumentEstimate }) {
  const option = useMemo(() => scenarioOption(row), [row]);
  if (!option) return <p className="mt-2 text-caption text-ink-400">{t('暂无数据')}</p>;
  return (
    <>
      <div className="mt-1.5 h-56">
        <ReactECharts option={option} ariaLabel={t('CTA 情景曲线')} />
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-x-3 text-micro text-ink-400">
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0 w-4 border-t-2 border-brand-500" aria-hidden />
          {t('完整敞口（含波动率调整）')}
        </span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-0 w-4 border-t border-dashed border-ink-400" aria-hidden />
          {t('仅趋势（波动率冻结）')}
        </span>
      </p>
    </>
  );
}
