/**
 * 估算仓位历史图（/cta 主区）：history 快照的 120 日走势。
 *
 * 读法纪律：这是同一代理口径逐日收盘后的机械仓位历史，不是任何机构的
 * 真实持仓曲线；0 轴上下代表模型群整体多/空，±60 浅带为「强净多/强净空」
 * 阈值（与 position_label 的 strong_long/strong_short 判定一致）。
 */
import { useMemo } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import {
  baseAnimation,
  CH,
  CHART_MONO_FONT,
  INSIGHT_SMOOTH,
  insightAreaStyle,
  insightDotRow,
  insightEndpointMark,
  insightLine,
  insightLineFade,
  insightReferenceMark,
  insightTooltip,
  insightTooltipBody,
  type ChartOption,
} from '@/lib/chart';
import { InsightFrame } from '@/components/shared/InsightCard';
import { t } from '../../i18n/core.ts';
import { signed } from './ctaMeta';

function historyOption(history: { date: string; position: number }[]): ChartOption | null {
  if (!history.length) return null;
  const positions = history.map((h) => h.position);
  return {
    ...baseAnimation,
    grid: { left: 8, right: 14, top: 12, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category' as const,
      data: history.map((h) => h.date.slice(5)),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: CH.ink400, fontSize: 10, fontFamily: CHART_MONO_FONT, interval: 23 },
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
        const arr = params as { dataIndex: number }[];
        if (!arr.length) return '';
        const point = history[arr[0].dataIndex];
        if (!point) return '';
        return insightTooltipBody(point.date, [
          insightDotRow(CH.brand500, t('估算目标仓位'), signed(point.position)),
        ]);
      },
    }),
    series: [
      /* Insight Cards 折线工艺：平滑 2.25px + 同色渐隐面积（替代点阵） */
      insightLine(CH.brand500, {
        name: t('估算目标仓位'),
        data: positions,
        smooth: INSIGHT_SMOOTH,
        /* 左缘淡出：120 日窗口的起点是任意截断，不是数据的开端 */
        lineStyle: { color: insightLineFade(CH.brand500) },
        /* 面积渐变要认这条曲线的正负：净空区间的面积长在 0 轴下方 */
        areaStyle: insightAreaStyle(CH.brand500, positions),
        /* 末读数落点：实心圆 + 白环 */
        markPoint: insightEndpointMark(CH.brand500, positions.length - 1, positions[positions.length - 1]),
        markLine: {
          symbol: 'none',
          silent: true,
          data: [
            {
              yAxis: 0,
              lineStyle: { color: CH.ink400, width: 1, type: [4, 4] as number[] },
              label: { show: false },
            },
            /* 末值参考位：把「现在这条线停在哪」横着拉通，读数不用回头找 y 轴 */
            insightReferenceMark(CH.brand500, positions[positions.length - 1]),
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
      }),
    ],
  } as ChartOption;
}

export default function PositionHistoryChart({ history }: { history: { date: string; position: number }[] }) {
  const option = useMemo(() => historyOption(history), [history]);
  if (!option) return <p className="mt-2 text-caption text-ink-400">{t('暂无数据')}</p>;
  const last = history[history.length - 1];
  return (
    /* 图台表头写清参照口径：0 轴是多空分界，末值是这条线现在停的位置 */
    <InsightFrame
      label={t('0 为多空分界 · 最新 {v}', { v: signed(last.position) })}
      action={<span className="font-mono text-micro text-ink-400 tnum">{last.date}</span>}
      className="mt-1.5"
    >
      <div className="h-56">
        <ReactECharts option={option} ariaLabel={t('估算仓位历史曲线')} />
      </div>
    </InsightFrame>
  );
}
