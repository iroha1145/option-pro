/**
 * 情景曲线（/cta 主区）：若明日收于横轴价格，估算目标仓位为纵轴值。
 * 实线=完整敞口（含波动率调整），虚线=仅趋势（波动率冻结）；阴影带为主要
 * 触发区间。曲线是分段线性的模型输出，不是预测路径。
 */
import { useMemo } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import {
  baseAnimation,
  CH,
  CHART_MONO_FONT,
  INSIGHT_FRAME,
  insightDotRow,
  insightLine,
  insightTooltip,
  insightTooltipBody,
  type ChartOption,
} from '@/lib/chart';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import type { CtaInstrumentEstimate } from '@/api/types';
import { useColorMode } from '@/hooks/useColorMode.ts';
import { t } from '../../i18n/core.ts';

function scenarioOption(row: CtaInstrumentEstimate): ChartOption | null {
  const curve = row.scenario_curve;
  if (!curve || curve.prices.length === 0 || row.reference_price === null) return null;
  const refIndex = curve.prices.reduce(
    (best, p, i) => (Math.abs(p - row.reference_price!) < Math.abs(curve.prices[best] - row.reference_price!) ? i : best),
    0,
  );
  const ref = row.reference_price;
  const zones = [...(row.trigger_levels?.above ?? []), ...(row.trigger_levels?.below ?? [])];
  const idxOf = (price: number) =>
    curve.prices.reduce((best, p, i) => (Math.abs(p - price) < Math.abs(curve.prices[best] - price) ? i : best), 0);
  /* 横轴=距现价百分比（基准 reference_price）：-12% … 0 … +12%，避免长价格标签被裁 */
  const pctOf = (i: number) => ((curve.prices[i] - ref) / ref) * 100;
  const pctLabel = (i: number) => {
    const p = Math.round(pctOf(i));
    return p === 0 ? '0' : `${p > 0 ? '+' : ''}${p}%`;
  };
  return {
    ...baseAnimation,
    grid: { left: 8, right: 14, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: curve.prices.map((p) => fmtPrice(p)),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        color: CH.ink400,
        fontSize: 10,
        fontFamily: CHART_MONO_FONT,
        /* interval 传数值时 ECharts 按「每 N+1 个取一」采样，中心标签会偏离
           0 基准（实测落在 +1%）。改函数式：固定取 0/24/48/72/96 五点，
           refIndex=48 正好落在 0 标签上。 */
        interval: (index: number) => index % 24 === 0,
        formatter: (_value: string, index: number) => pctLabel(index),
      },
    },
    yAxis: {
      type: 'value' as const,
      min: -100,
      max: 100,
      position: 'right' as const,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: CHART_MONO_FONT },
      splitLine: { lineStyle: { color: CH.lineChart, width: 1 } },
    },
    tooltip: insightTooltip({
      trigger: 'axis',
      formatter: (params: unknown) => {
        const arr = params as { dataIndex: number; seriesName: string; data: number; color?: string }[];
        if (!arr.length) return '';
        const idx = arr[0].dataIndex;
        const rows = arr.map((s) =>
          insightDotRow(
            typeof s.color === 'string' ? s.color : CH.brand500,
            s.seriesName,
            `${s.data > 0 ? '+' : ''}${s.data}`,
          ),
        );
        const pct = `${pctOf(idx) > 0 ? '+' : ''}${pctOf(idx).toFixed(1)}%`;
        return insightTooltipBody(
          t('收于 {p}（{pct}）', { p: fmtPrice(curve.prices[idx]), pct }),
          rows,
        );
      },
    }),
    series: [
      /* Insight Cards 折线工艺：2.25px 圆头主线 + 细虚线对照；
         曲线是分段线性的模型输出，不平滑（平滑会在档位之间编出假读数） */
      insightLine(CH.brand500, {
        name: t('完整敞口'),
        data: curve.full,
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
                fontFamily: CHART_MONO_FONT,
                position: 'insideEndTop' as const,
              },
            },
          ],
        },
        /* 当前仓位 markPoint：brand-600 实心小点钉在 (现价, position_score) */
        markPoint: {
          symbol: 'circle',
          symbolSize: 7,
          silent: true,
          itemStyle: { color: CH.brand600 },
          label: {
            show: true,
            /* top 会与现价 markLine 的竖排标签撞在同一 x 上，改放右侧 */
            position: 'right' as const,
            distance: 6,
            fontSize: 10,
            fontFamily: CHART_MONO_FONT,
            color: CH.brand600,
            formatter: t('现值 {v}', {
              v: row.position_score === null
                ? '—'
                : `${row.position_score > 0 ? '+' : ''}${row.position_score.toFixed(1)}`,
            }),
          },
          data: row.position_score === null ? [] : [{ coord: [refIndex, row.position_score] }],
        },
        markArea: zones.length
          ? {
              silent: true,
              data: zones.map((zone) => [
                {
                  xAxis: idxOf(zone.price_low),
                  itemStyle: {
                    color: zone.est_position_change >= 0 ? CH.up600 : CH.down600,
                    opacity: 0.1,
                  },
                },
                { xAxis: idxOf(zone.price_high) },
              ]),
            }
          : undefined,
        z: 3,
      }),
      insightLine(CH.ink400, {
        name: t('仅趋势（波动率冻结）'),
        data: curve.trend_only,
        lineStyle: { width: 1.5, type: [5, 4] as number[] },
        z: 2,
      }),
    ],
  } as ChartOption;
}

export default function ScenarioChart({ row }: { row: CtaInstrumentEstimate }) {
  const colorMode = useColorMode();
  const option = useMemo(() => {
    // 图表构造器读取 CSS 涨跌色，配色模式变化时需重新取值。
    void colorMode;
    return scenarioOption(row);
  }, [row, colorMode]);
  if (!option) return <p className="mt-2 text-caption text-ink-400">{t('暂无数据')}</p>;
  return (
    <>
      <div className={cn(INSIGHT_FRAME, 'mt-1.5')}>
        <div className="h-56">
          <ReactECharts option={option} ariaLabel={t('CTA 情景曲线')} />
        </div>
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
