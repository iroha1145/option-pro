/**
 * 宏观历史图 — ReactECharts（不自绘 Canvas，不引入第二个图表库）。
 * 综合分主线 + 可选模块线 + 50 中性参考线；30D/90D/1Y/5Y 区间切换；
 * Tooltip 显示 history_basis；latest_revised_backfill 区间用虚线段区分。
 */
import { useMemo, useState } from 'react';
import type { ApiError } from '@/api/client';
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
import { t } from '../../../i18n/core.ts';

export const HISTORY_RANGES = [
  { key: '30D', days: 30 },
  { key: '90D', days: 90 },
  { key: '1Y', days: 365 },
  { key: '5Y', days: 1825 },
] as const;

export type HistoryRangeKey = (typeof HISTORY_RANGES)[number]['key'];

const BASIS_LABEL: Record<string, string> = {
  latest_revised_backfill: t('按当前修订值回算'),
  local_point_in_time: t('本地点时快照'),
  mixed: t('混合基础'),
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
  error = null,
  onRetry,
  modules,
  loading,
  range,
  onRangeChange,
}: {
  points: MacroHistoryPoint[];
  /**
   * 历史查询失败（审计 P2-27）。
   * error 此前没有传进来，只有 points=[] 与 loading，于是「没有历史数据」和
   * 「历史接口坏了」渲染成同一个空图。
   */
  error?: ApiError | null;
  onRetry?: () => void;
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
        name: t('综合分（回算）'),
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
            formatter: t('中性 50'),
            color: CH.ink400,
            fontSize: 10,
            position: 'insideEndTop',
          },
          lineStyle: { color: CH.ink300, width: 1, type: [3, 3] },
          data: [{ yAxis: 50 }],
        },
      },
      {
        name: t('综合分（本地点时）'),
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
        name: t(modules.find((item) => item.moduleId === moduleId)?.nameZh ?? moduleId),
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
            // regime 是中文分档名（同卡片处已译）：tooltip 里同样要过词典
            regime ? t('环境：{regime}', { regime: t(regime) }) : '',
            basis ? t('历史基础：{basis}', { basis: BASIS_LABEL[basis] ?? basis }) : '',
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
    <section className="card-surface p-5" aria-label={t("宏观环境历史")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="eyebrow">
          {t('综合分历史 · COMPOSITE HISTORY')}
          <InfoHint
            hint={SCORE_HINTS_MACRO.macroHistoryBasis}
            side="bottom"
            align="start"
            size={11}
            className="ml-1"
          />
        </p>
        <div className="flex flex-wrap gap-1" role="group" aria-label={t("历史区间")}>
          {HISTORY_RANGES.map((item) => (
            <button
              key={item.key}
              type="button"
              aria-pressed={range === item.key}
              onClick={() => onRangeChange(item.key)}
              className={cn(
                'rounded-xs px-2 py-1 font-mono text-micro tnum outline-none transition-colors duration-fast',
                range === item.key
                  ? 'bg-brand-50 text-brand-700 shadow-chip'
                  : 'text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
              )}
            >
              {item.key}
            </button>
          ))}
        </div>
      </div>

      {error && points.length > 0 && (
        /* 新区间读取失败、图上还是上一区间的曲线：按钮已高亮新区间，不标注
           就是同屏说谎（审计 #54）。 */
        <p className="mt-3 flex items-center justify-between gap-2 rounded-xs border border-warn-600/30 bg-warn-50 px-2.5 py-1.5 text-caption text-warn-600">
          {t('该区间读取失败，仍显示上一区间的曲线')}
          {onRetry && (
            <button type="button" onClick={onRetry} className="shrink-0 font-medium underline underline-offset-2">
              {t('重试')}
            </button>
          )}
        </p>
      )}
      <div className="mt-4 h-[240px] w-full">
        {loading && points.length === 0 ? (
          <SkeletonBlock className="h-full w-full" />
        ) : error && points.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 px-4 text-center">
            <p className="text-body-s text-warn-600">{t('历史数据读取失败：')}{error.message}</p>
            {onRetry && (
              <button
                type="button"
                onClick={onRetry}
                className="rounded-md border border-line bg-card px-3 py-1.5 text-caption text-ink-600 shadow-btn transition-colors hover:border-brand-400 hover:text-brand-600"
              >
                {t('重试')}
              </button>
            )}
          </div>
        ) : points.length === 0 ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-body-s text-ink-400">
            {t('历史正在积累：本地快照攒够之后这里会显示综合分曲线。')}
          </div>
        ) : (
          <ReactECharts option={option} ariaLabel={t("宏观环境综合分历史曲线")} />
        )}
      </div>

      {modules.length > 0 && (
        <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label={t("叠加模块线")}>
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
                    ? 'border-brand-400 bg-brand-50 text-brand-700 shadow-chip'
                    : 'border-line text-ink-400 hover:text-ink-600 focus-visible:text-ink-600',
                )}
              >
                {t(module.nameZh)}
              </button>
            );
          })}
        </div>
      )}

      <p className="mt-3 border-t border-line pt-3 text-micro leading-relaxed text-ink-400">
        {t('虚线段表示该区间按当前修订值回算，不是当时市场已知的分数；实线段来自本地点时快照。')}
      </p>
    </section>
  );
}
