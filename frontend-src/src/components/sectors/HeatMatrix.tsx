/**
 * 板块热力：按所选周期的平均收益从高到低排成一列发散条。
 *
 * 此前是 6 列热力砖：11 个板块排完剩一行 5 块的残行，而且砖按接口顺序摆，
 * 要靠扫颜色自己排名次。现在名次、条长和颜色同时表达同一个数：条从零轴向右
 * 是涨、向左是跌，两栏共用一个零轴和比例尺；颜色仍走 heatColor 的周期色阶
 * （±6% / ±12% / ±18%）。
 * 1280 以上分两栏、按列读名次，更窄时单栏。数量不论多少都不会留下残行。
 */
import { useMemo, type CSSProperties, type ReactNode } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_UI, EASE_PAPER, GROW_X } from '@/lib/motion';
import { fmtPct } from '@/lib/format';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import StrengthBar from '@/components/shared/StrengthBar';
import { useColorMode } from '@/hooks/useColorMode.ts';
import { useAppearance } from '@/hooks/useAppearance.ts';
import type { SectorVm } from './model';
import { heatSpan, heatTone, periodLabel } from './model';
import { t } from '../../i18n/core.ts';

/* 名次 · 板块 · 发散条 · 收益 · 平均强度（≥md）。320 宽也给条留出约 44px。 */
const ROW_GRID =
  'grid grid-cols-[1rem_minmax(0,5.5rem)_minmax(0,1fr)_4.25rem] items-center gap-x-2 sm:grid-cols-[1.25rem_minmax(0,6.5rem)_minmax(0,1fr)_4.25rem] sm:gap-x-2.5 md:grid-cols-[1.5rem_minmax(0,8rem)_minmax(0,1fr)_4.5rem_6.5rem] md:gap-x-3';

/* 宽屏（≥1280）分两栏、按列读名次：前一半在左栏，后一半在右栏，每栏顶上一行
   列名。更窄时两栏会把条挤到几十像素，保持单栏。 */
const LIST_GRID =
  'grid grid-cols-1 gap-y-0.5 xl:grid-flow-col xl:grid-cols-2 xl:gap-x-10 xl:[grid-template-rows:repeat(var(--heat-rows),auto)]';

/** 收益从高到低；没有收益的板块排在最后，按名称稳定排序。 */
function rankByReturn(sectors: SectorVm[]): SectorVm[] {
  return [...sectors].sort((left, right) => {
    if (left.avgReturn === null || right.avgReturn === null) {
      if (left.avgReturn === right.avgReturn) return left.name.localeCompare(right.name);
      return left.avgReturn === null ? 1 : -1;
    }
    return right.avgReturn - left.avgReturn || (right.avgStrength ?? 0) - (left.avgStrength ?? 0);
  });
}

/**
 * 两栏共用的一把尺：零轴按本期最跌与最涨之间的比例放，正负同一比例尺（1% 等长）。
 * 全距不足一个周期色阶时按色阶补齐，避免月内微小涨跌被画成满格长条。
 */
interface ReturnScale {
  /** 零轴在条区里的位置（0–100） */
  axis: number;
  /** 条区全宽代表的收益（%） */
  extent: number;
}

function returnScale(sectors: SectorVm[], span: number): ReturnScale {
  const values = sectors.flatMap((sector) => (sector.avgReturn === null ? [] : [sector.avgReturn]));
  let low = Math.min(0, ...values);
  let high = Math.max(0, ...values);
  if (high - low < span) {
    if (high === 0 && low === 0) {
      low = -span / 2;
      high = span / 2;
    } else {
      const grow = span / (high - low);
      low *= grow;
      high *= grow;
    }
  }
  return { axis: (-low / (high - low)) * 100, extent: high - low };
}

function ColumnHeader({ period, className }: { period: SectorVm['period']; className?: string }) {
  return (
    <div className={cn(ROW_GRID, 'border-b border-line px-2 pb-2 text-micro text-ink-500', className)} aria-hidden="true">
      <span />
      <span>{t('行业')}</span>
      <span className="col-span-2 text-right md:col-span-2">{t('{period}平均收益', { period: periodLabel(period) })}</span>
      <span className="hidden md:block">{t('平均评分')}</span>
    </div>
  );
}

function HeatRow({
  sector,
  rank,
  scale,
  selected,
  onToggle,
}: {
  sector: SectorVm;
  rank: number;
  scale: ReturnScale;
  selected: boolean;
  onToggle: () => void;
}) {
  const value = sector.avgReturn;
  const hasReturn = value !== null;
  const tone = hasReturn ? heatTone(value, sector.period) : undefined;
  const length = hasReturn ? (Math.abs(value) / scale.extent) * 100 : 0;
  const leader = sector.leaders[0] ?? null;

  return (
    <motion.div layout="position" transition={{ duration: DUR_UI, ease: EASE_PAPER }}>
      <button
        type="button"
        onClick={onToggle}
        aria-pressed={selected}
        aria-label={
          hasReturn
            ? t('{name}{period}平均收益 {ret}%，平均评分 {strength}', { name: sector.name, period: periodLabel(sector.period), ret: sector.avgReturn?.toFixed(2), strength: sector.avgStrength ?? t('暂无') })
            : t('{name}暂无评分汇总', { name: sector.name })
        }
        className={cn(
          'group relative w-full rounded-md px-2 py-1.5 text-left transition-colors duration-fast',
          ROW_GRID,
          selected ? 'bg-brand-50' : 'hover:bg-paper-2',
        )}
      >
        <span className="text-right text-caption text-ink-400 tnum">{hasReturn ? rank : '—'}</span>
        <span className="min-w-0">
          <span className={cn('block truncate text-body-s font-medium', hasReturn ? 'text-ink-800' : 'text-ink-500')}>
            {sector.name}
          </span>
          <span className="block truncate text-micro text-ink-400 tnum">
            {t('有评分 {scored} / {total}', { scored: sector.scoredCount ?? '—', total: sector.memberCount })}
          </span>
        </span>

        {/* 发散条：向右为涨、向左为跌，零轴位置随本期涨跌分布 */}
        <span className="relative block h-3" aria-hidden="true">
          {hasReturn ? (
            <motion.span
              className={cn(
                'absolute inset-y-0 transition-[left,right,width,background-color] duration-ui ease-paper',
                value >= 0 ? 'origin-left rounded-r-xs' : 'origin-right rounded-l-xs',
              )}
              style={{
                ...(value >= 0 ? { left: `${scale.axis}%` } : { right: `${100 - scale.axis}%` }),
                width: `${Math.max(length, 0.6)}%`,
                backgroundColor: tone,
              }}
              variants={GROW_X}
              transition={{ duration: 0.7, ease: EASE_PAPER }}
            />
          ) : (
            <span className="absolute inset-x-0 top-1/2 border-t border-dashed border-line-strong" />
          )}
          <span className="absolute -inset-y-1 w-px bg-line-strong transition-[left] duration-ui ease-paper" style={{ left: `${scale.axis}%` }} />
        </span>

        <span
          className={cn(
            'text-right text-body-s font-medium tnum',
            !hasReturn || value === 0 ? 'text-ink-500' : value > 0 ? 'text-up-700' : 'text-down-700',
          )}
        >
          {hasReturn ? fmtPct(value) : '—'}
        </span>
        <span className="hidden md:block">
          <StrengthBar score={sector.avgStrength} width={48} />
        </span>
        <span
          role="tooltip"
          className="cloud-popover pointer-events-none absolute -top-1.5 left-10 z-30 hidden w-52 -translate-y-full p-2.5 text-left md:group-hover:block md:group-focus-visible:block"
        >
          <span className="eyebrow block">{sector.name}</span>
          <span className="mt-1.5 block space-y-1 text-micro">
            <span className="flex items-center justify-between gap-2">
              <span className="text-ink-500">{t('平均评分')}</span>
              <span className="text-ink-800 tnum">
                {sector.avgStrength?.toFixed(1) ?? '—'}
              </span>
            </span>
            <span className="flex items-center justify-between gap-2">
              <span className="text-ink-500">{t('评分最高')}</span>
              <span className="font-medium text-ink-800">
                {leader
                  ? <><span className="">{leader.ticker}</span> <span className="tnum">{leader.score?.toFixed(1) ?? '—'}</span></>
                  : '—'}
              </span>
            </span>
            <span className="flex items-center justify-between gap-2 border-t border-line pt-1">
              <span className="text-ink-500">{t('收益覆盖')}</span>
              <span className="text-ink-800 tnum">
                {sector.coveredCount ?? '—'} / {sector.memberCount}
              </span>
            </span>
            <span className="flex items-center justify-between gap-2">
              <span className="text-ink-500">{t('评分覆盖')}</span>
              <span className="text-ink-800 tnum">
                {sector.scoredCount ?? '—'} / {sector.memberCount}
              </span>
            </span>
            {sector.scoreDataThrough && (
              <span className="flex items-center justify-between gap-2">
                <span className="text-ink-500">{t('评分截至')}</span>
                <span className="text-ink-800 tnum">{sector.scoreDataThrough}</span>
              </span>
            )}
          </span>
        </span>
      </button>

    </motion.div>
  );
}

interface HeatMatrixProps {
  sectors: SectorVm[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function HeatMatrix({
  sectors,
  selectedId,
  onSelect,
}: HeatMatrixProps) {
  /* heatTone → heatColor 在渲染期直接读全局涨跌习惯与外观（模块级快照，不是 props）。
     不订阅就只有换盘那一刻不重绘：整列条留在旧口径上，与页面其余部分（徽章、
     涨跌幅、K 线）红绿相反，直到别的原因触发一次重渲染才追上。 */
  useColorMode();
  useAppearance();
  const ranked = useMemo(() => rankByReturn(sectors), [sectors]);
  const period = ranked[0]?.period ?? '3mo';
  const scale = useMemo(() => returnScale(ranked, heatSpan(period)), [ranked, period]);
  const perColumn = Math.ceil(ranked.length / 2);

  return (
    <motion.div
      className={LIST_GRID}
      style={{ '--heat-rows': perColumn + 1 } as CSSProperties}
      role="group"
      aria-label={t("行业平均收益排名")}
      initial="hidden"
      whileInView="shown"
      viewport={{ once: true, amount: 0.2 }}
    >
      <ColumnHeader period={period} />
      {ranked.map((sector, index) => (
        <HeatRowSlot key={sector.id} showHeader={index === perColumn} period={period}>
          <HeatRow
            sector={sector}
            rank={index + 1}
            scale={scale}
            selected={selectedId === sector.id}
            onToggle={() => onSelect(sector.id)}
          />
        </HeatRowSlot>
      ))}
    </motion.div>
  );
}

/** 右栏第一行之前补一行列名（只在两栏时出现）。 */
function HeatRowSlot({
  showHeader,
  period,
  children,
}: {
  showHeader: boolean;
  period: SectorVm['period'];
  children: ReactNode;
}) {
  return (
    <>
      {showHeader && <ColumnHeader period={period} className="hidden xl:grid" />}
      {children}
    </>
  );
}

export function HeatMatrixSkeleton() {
  return (
    <div className={LIST_GRID} style={{ '--heat-rows': 12 } as CSSProperties} aria-hidden="true">
      {/* 骨架行数与真实目录（24 个主题）对齐，避免加载完成时布局跳变（审计 2.1.16） */}
      {Array.from({ length: 24 }, (_, index) => (
        <div key={index} className={cn(ROW_GRID, 'h-11 px-2')}>
          <SkeletonBlock className="h-3 w-3 justify-self-end" />
          <SkeletonBlock className="h-3.5 w-20" />
          <SkeletonBlock className="h-3 w-full" />
          <SkeletonBlock className="h-3.5 w-12 justify-self-end" />
          <SkeletonBlock className="hidden h-1 w-full md:block" />
        </div>
      ))}
    </div>
  );
}
