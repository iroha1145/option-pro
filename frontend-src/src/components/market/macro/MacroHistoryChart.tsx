/**
 * 宏观历史图 — ReactECharts（不自绘 Canvas，不引入第二个图表库）。
 * 综合分主线 + 可选模块线 + 50 中性参考线；30D/90D/1Y/5Y 区间切换；
 * Tooltip 显示 history_basis；latest_revised_backfill 区间用虚线段区分。
 */
import { useMemo, useState } from 'react';
import ReactECharts from '@/components/charts/ReactECharts';
import { CH, baseAnimation, baseGrid, glassTooltip, valueAxis } from '@/lib/chart';
import { cn } from '@/lib/utils';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import InfoHint from '@/components/shared/InfoHint';
import { SCORE_HINTS_MACRO } from '@/lib/scoreHints';
import {
  MACRO_MODULE_ORDER,
  type MacroHistoryPoint,
  type MacroModule,
  type MacroModuleId,
} from '@/api/modules/macro';

export const HISTORY_RANGES = [
  { key: '30D', days: 30 },
  { key: '90D', days: 90 },
  { key: '1Y', days: 365 },
  { key: '5Y', days: 1825 },
] as const;

export type HistoryRangeKey = (typeof HISTORY_RANGES)[number]['key'];

const BASIS_LABEL: Record<string, string> = {
  latest_revised_backfill: '按当前修订值回算',
  local_point_in_time: '本地点时快照',
  mixed: '混合基础',
};

/** 模块线共用全站图表调色，不给模块分配固定鲜艳色。 */
const MODULE_LINE_COLORS: string[] = [
  CH.brand400,
  CH.ink400,
  CH.brand600,
  CH.ink300,
  CH.up600,
  CH.warn600,
  CH.ai600,
];

export default function MacroHistoryChart({
  points,
  modules,
  loading,
  range,
  onRangeChange,
}: {
  points: MacroHistoryPoint[];
  modules: MacroModule[];
  loading: boolean;
  range: HistoryRangeKey;
  onRangeChange: (value: HistoryRangeKey) => void;
}) {
  const [shownModules, setShownModules] = useState<MacroModuleId[]>([]);

  const option = useMemo(() => {
    const dates = points.map((point) => point.date);
    const basisByDate = new Map(points.map((point) => [point.date, point.historyBasis]));
    const regimeByDate = new Map(points.map((point) => [point.date, point.regime]));

    /* 回算区间与本地点时快照区间分成两条线：线型不同，读者一眼可辨。 */
    const revised = points.map((point) =>
      point.historyBasis === 'local_point_in_time' ? null : point.score,
    );
    const local = points.map((point) =>
      point.historyBasis === 'local_point_in_time' ? point.score : null,
    );

    const series: Record<string, unknown>[] = [
      {
        name: '综合分（回算）',
        type: 'line',
        data: revised,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: CH.brand600, width: 2, type: [5, 4] },
        itemStyle: { color: CH.brand600 },
        markLine: {
          silent: true,
          symbol: 'none',
          label: {
            formatter: '中性 50',
            color: CH.ink400,
            fontSize: 10,
            position: 'insideEndTop',
          },
          lineStyle: { color: CH.ink300, width: 1, type: [3, 3] },
          data: [{ yAxis: 50 }],
        },
      },
      {
        name: '综合分（本地点时）',
        type: 'line',
        data: local,
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: { color: CH.brand600, width: 2 },
        itemStyle: { color: CH.brand600 },
      },
    ];

    shownModules.forEach((moduleId) => {
      const colorIndex = MACRO_MODULE_ORDER.indexOf(moduleId);
      series.push({
        name: modules.find((item) => item.moduleId === moduleId)?.nameZh ?? moduleId,
        type: 'line',
        data: points.map((point) => point.moduleScores[moduleId] ?? null),
        showSymbol: false,
        smooth: false,
        connectNulls: false,
        lineStyle: {
          color: MODULE_LINE_COLORS[colorIndex] ?? CH.ink300,
          width: 1.2,
          opacity: 0.85,
        },
        itemStyle: { color: MODULE_LINE_COLORS[colorIndex] ?? CH.ink300 },
      });
    });

    return {
      ...baseAnimation,
      grid: baseGrid({ left: 4, right: 8, top: 16, bottom: 4 }),
      tooltip: glassTooltip({
        formatter: (params: unknown) => {
          const rows = Array.isArray(params) ? params : [params];
          const first = rows[0] as { axisValue?: string } | undefined;
          const date = first?.axisValue ?? '';
          const basis = basisByDate.get(date) ?? null;
          const regime = regimeByDate.get(date) ?? null;
          const lines = rows
            .filter((row) => (row as { value?: unknown }).value !== null)
            .map((row) => {
              const item = row as { seriesName?: string; value?: number };
              return `${item.seriesName ?? ''} ${
                typeof item.value === 'number' ? item.value.toFixed(1) : '—'
              }`;
            });
          return [
            date,
            ...lines,
            regime ? `环境：${regime}` : '',
            basis ? `历史基础：${BASIS_LABEL[basis] ?? basis}` : '',
          ]
            .filter(Boolean)
            .join('<br/>');
        },
      }),
      xAxis: {
        type: 'category' as const,
        data: dates,
        boundaryGap: false,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: {
          color: CH.ink400,
          fontSize: 10,
          hideOverlap: true,
        },
      },
      yAxis: valueAxis({ min: 0, max: 100, interval: 25 }),
      series,
    };
  }, [points, modules, shownModules]);

  return (
    <section className="card-surface p-5" aria-label="宏观环境历史">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="eyebrow">
          综合分历史 · COMPOSITE HISTORY
          <InfoHint
            hint={SCORE_HINTS_MACRO.macroHistoryBasis}
            side="bottom"
            align="start"
            size={11}
            className="ml-1"
          />
        </p>
        <div className="flex flex-wrap gap-1" role="group" aria-label="历史区间">
          {HISTORY_RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={range === item.key}
              onClick={() => onRangeChange(item.key)}
              className={cn(
                'rounded-xs px-2 py-1 font-mono text-micro tnum outline-none transition-colors duration-fast',
                range === item.key
                  ? 'bg-brand-50 text-brand-700'
                  : 'text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
              )}
            >
              {item.key}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4 h-[240px] w-full">
        {loading && points.length === 0 ? (
          <SkeletonBlock className="h-full w-full" />
        ) : points.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-body-s text-ink-400">
            历史正在积累：本地快照攒够之后这里会显示综合分曲线。
          </div>
        ) : (
          <ReactECharts option={option} ariaLabel="宏观环境综合分历史曲线" />
        )}
      </div>

      {modules.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="叠加模块线">
          {modules.map((module) => {
            const active = shownModules.includes(module.moduleId);
            return (
              <button
                key={module.moduleId}
                type="button"
                aria-pressed={active}
                onClick={() =>
                  setShownModules((previous) =>
                    previous.includes(module.moduleId)
                      ? previous.filter((item) => item !== module.moduleId)
                      : [...previous, module.moduleId],
                  )
                }
                className={cn(
                  'rounded-xs border px-2 py-0.5 text-micro outline-none transition-colors duration-fast',
                  active
                    ? 'border-brand-400 bg-brand-50 text-brand-700'
                    : 'border-line text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
                )}
              >
                {module.nameZh}
              </button>
            );
          })}
        </div>
      )}

      <p className="mt-3 border-t border-line pt-3 text-micro leading-relaxed text-ink-400">
        虚线段表示该区间按当前修订值回算，不是当时市场已知的分数；实线段来自本地点时快照。
      </p>
    </section>
  );
}
