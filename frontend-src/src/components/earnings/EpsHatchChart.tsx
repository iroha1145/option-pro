/**
 * EPS 预期 vs 实际斜纹柱状图（design.md §6-3 / earnings.md）
 * 预估 = brand 同色系 45° 斜纹 decal（4px 间距 · 1.2px 线 · 透明度 .55）
 * 实际 = brand-600 实心（未公布留空）；图注 HatchLegend「▨ 预估 ▮ 实际」
 * 首绘 900–1200ms cubicOut + 逐柱错峰
 */
import { useMemo } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import HatchLegend from '@/components/shared/HatchLegend';
import { CH, baseAnimation, baseGrid, categoryAxis, glassTooltip, hatchDecal, valueAxis, type ChartOption } from '@/lib/chart';
import type { EarningsRow } from './types';

interface EpsHatchChartProps {
  items: EarningsRow[];
  title?: string;
}

const MAX_BARS = 10;

export default function EpsHatchChart({ items, title = 'EPS 预期 vs 实际' }: EpsHatchChartProps) {
  const rows = useMemo(
    () => items.filter((r) => r.epsEstimate != null || r.epsActual != null).slice(0, MAX_BARS),
    [items],
  );

  const option = useMemo<ChartOption>(() => {
    const ests = rows.map((r) => r.epsEstimate ?? null);
    const acts = rows.map((r) => r.epsActual ?? null);
    return {
      ...baseAnimation,
      grid: baseGrid({ top: 20, bottom: 4 }),
      tooltip: glassTooltip({
        valueFormatter: (v: unknown) => (typeof v === 'number' ? `$${v.toFixed(2)}` : '未公布'),
      }),
      xAxis: categoryAxis(rows.map((r) => r.ticker)),
      yAxis: valueAxis(),
      series: [
        {
          name: '预估 EPS',
          type: 'bar',
          data: ests,
          barWidth: 13,
          barGap: '30%',
          itemStyle: {
            color: 'rgba(46,70,224,.16)',
            borderRadius: [2, 2, 0, 0],
            decal: hatchDecal(CH.brand600),
          } as never,
          animationDelay: (idx: number) => idx * 70,
        },
        {
          name: '实际 EPS',
          type: 'bar',
          data: acts,
          barWidth: 13,
          itemStyle: { color: CH.brand600, borderRadius: [2, 2, 0, 0] },
          animationDelay: (idx: number) => idx * 70 + 90,
        },
      ],
    };
  }, [rows]);

  if (rows.length === 0) return null;

  const hasActual = rows.some((r) => r.epsActual != null);

  return (
    <section className="card-surface p-5" aria-label="EPS 预期与实际对照图">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="eyebrow">{title}</p>
        <HatchLegend estimate="预估" actual="实际" />
      </div>
      <div className="mt-3 h-[240px]">
        <ReactECharts
          option={option}
          ariaLabel={`EPS 预期与实际对照柱状图，共 ${rows.length} 只标的，斜纹为预估值，实心为实际值`}
        />
      </div>
      <p className="mt-2 border-t border-line pt-2.5 text-micro text-ink-400">
        {hasActual ? '斜纹为共识预估，实心为已公布实际值；未公布标的实际列留空。' : '本组标的均待公布，实际值披露后以实心柱呈现。'}
      </p>
    </section>
  );
}
