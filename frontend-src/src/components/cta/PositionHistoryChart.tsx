/**
 * 估算仓位历史图（/cta 主区）：history 快照的 120 日走势。
 *
 * 读法纪律：这是同一代理口径逐日收盘后的机械仓位历史，不是任何机构的
 * 真实持仓曲线；0 轴上下代表模型群整体多/空，±60 浅带为「强净多/强净空」
 * 阈值（与 position_label 的 strong_long/strong_short 判定一致）。
 */
import { useMemo } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import { baseAnimation, CH, glassTooltip, stippleAreaStyle, type ChartOption } from '@/lib/chart';
import { t } from '../../i18n/core.ts';
import { signed } from './ctaMeta';

export function historyOption(history: { date: string; position: number }[]): ChartOption | null {
  if (!history.length) return null;
  return {
    ...baseAnimation,
    grid: { left: 8, right: 14, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: history.map((h) => h.date.slice(5)),
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
        const arr = params as { dataIndex: number }[];
        if (!arr.length) return '';
        const point = history[arr[0].dataIndex];
        if (!point) return '';
        return (
          `<div style="font-family:'IBM Plex Mono',monospace;font-size:11px;line-height:17px">` +
          `<div style="color:#8A94B0">${point.date}</div>` +
          `<div>${t('估算目标仓位')}: <b>${signed(point.position)}</b></div></div>`
        );
      },
    }),
    series: [
      {
        type: 'line' as const,
        name: t('估算目标仓位'),
        data: history.map((h) => h.position),
        showSymbol: false,
        lineStyle: { color: CH.brand500, width: 1.8 },
        areaStyle: stippleAreaStyle(),
        markLine: {
          symbol: 'none',
          silent: true,
          data: [
            {
              yAxis: 0,
              lineStyle: { color: CH.ink400, width: 1, type: [4, 4] as number[] },
              label: { show: false },
            },
          ],
        },
        markArea: {
          silent: true,
          data: [
            [
              { yAxis: 60, itemStyle: { color: CH.up600, opacity: 0.06 } },
              { yAxis: 100 },
            ],
            [
              { yAxis: -100, itemStyle: { color: CH.down600, opacity: 0.06 } },
              { yAxis: -60 },
            ],
          ],
        },
        z: 3,
      },
    ],
  } as ChartOption;
}

export default function PositionHistoryChart({ history }: { history: { date: string; position: number }[] }) {
  const option = useMemo(() => historyOption(history), [history]);
  if (!option) return <p className="mt-2 text-caption text-ink-400">{t('暂无数据')}</p>;
  return (
    <div className="mt-1.5 h-56">
      <ReactECharts option={option} ariaLabel={t('估算仓位历史曲线')} />
    </div>
  );
}
