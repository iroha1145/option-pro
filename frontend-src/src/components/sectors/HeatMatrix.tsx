import { useState, type CSSProperties } from 'react';
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { DUR_UI, EASE_PAPER } from '@/lib/motion';
import { fmtPct } from '@/lib/format';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import { useColorMode } from '@/hooks/useColorMode.ts';
import { useAppearance } from '@/hooks/useAppearance.ts';
import type { SectorVm } from './model';
import { heatTone, periodLabel } from './model';
import { t } from '../../i18n/core.ts';

const GRID_CLASS =
  'grid grid-cols-2 gap-2.5 md:grid-cols-4 md:gap-3 xl:grid-cols-6';

/** 与 GRID_CLASS 的断点同步（Tailwind 默认 md 768 / xl 1280）。 */
function gridColumns(): number {
  if (typeof window === 'undefined') return 4;
  return window.innerWidth >= 1280 ? 6 : window.innerWidth >= 768 ? 4 : 2;
}

/**
 * 对角波入场（beUI heat-calendar 的 (行+列)×步长；rareui github-activity 同一思路）：
 * 从左上角向右下铺开，而不是逐块排队。步长 35ms、封顶 300ms——此前按序号
 * index×40ms，24 块砖最后一块要等近 1 秒才出现。
 */
function waveDelayMs(index: number, cols: number): number {
  return Math.min((Math.floor(index / cols) + (index % cols)) * 35, 300);
}

function HeatTile({
  sector,
  delayMs,
  selected,
  onToggle,
}: {
  sector: SectorVm;
  delayMs: number;
  selected: boolean;
  onToggle: () => void;
}) {
  /* heatTone → heatColor 在渲染期直接读全局涨跌习惯（模块级快照，不是 props）。
     不订阅就只有换盘那一刻不重绘：整块热力矩阵留在旧口径上，与页面其余部分
     （徽章、涨跌幅、K 线）红绿相反，直到别的原因触发一次重渲染才追上。 */
  useColorMode();
  useAppearance();
  const value = sector.avgReturn ?? 0;
  /* count-up 减量：热力砖涨跌直接呈现终值 */
  const animated = value;
  const hasReturn = sector.avgReturn !== null;
  /* 缺数不能与「真实持平」同色（审计 2.1.17）：热力图的主要读法就是扫颜色，
     avgReturn 为 null 的砖底色换成中性纸面+虚线边，一眼可辨「没数据」。 */
  const tone = hasReturn ? heatTone(value, sector.period) : { bg: 'var(--card-warm, #FBFCFD)', dark: false };
  const leader = sector.leaders[0] ?? null;
  const textMain = !hasReturn ? 'text-ink-800' : tone.dark ? 'text-white' : 'text-black';
  const textSub = !hasReturn ? 'text-ink-500' : tone.dark ? 'text-white' : 'text-black';
  const barFill = !hasReturn ? 'bg-ink-900/25' : tone.dark ? 'bg-white/40' : 'bg-black/25';

  return (
    <button
      type="button"
      onClick={onToggle}
      aria-pressed={selected}
      aria-label={
        hasReturn
          ? t('{name}{period}平均收益 {ret}%，平均强度 {strength}', { name: sector.name, period: periodLabel(sector.period), ret: sector.avgReturn?.toFixed(2), strength: sector.avgStrength ?? t('暂无') })
          : t('{name}暂无强度聚合', { name: sector.name })
      }
      className={cn(
        'heat-tile group relative h-[92px] overflow-visible rounded-md text-left shadow-sh-1 hover:shadow-sh-2 md:h-[108px]',
        !hasReturn && 'border border-dashed border-line-strong',
        selected && 'shadow-sh-2',
      )}
      style={{ backgroundColor: tone.bg, '--heat-delay': `${delayMs}ms` } as CSSProperties}
    >
      {selected && (
        <motion.span
          layoutId="sector-selected-bar"
          className="absolute left-0 top-0 z-10 h-full w-[3px] rounded-l-md bg-brand-600"
          initial={{ scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{ duration: DUR_UI, ease: EASE_PAPER }}
          style={{ originY: 0.5 }}
          aria-hidden="true"
        />
      )}

      <span className="flex h-full flex-col justify-between p-3">
        <span className="flex min-w-0 items-start justify-between gap-1.5">
          <span
            className={cn(
              'truncate text-[13px] font-semibold leading-[18px]',
              textMain,
            )}
          >
            {sector.name}
          </span>
          <span className={cn('hidden font-mono text-micro tnum md:inline', textSub)}>
            {t('有评分 {scored} / {total}', { scored: sector.scoredCount ?? '—', total: sector.memberCount })}
          </span>
        </span>
        <span>
          <span
            className={cn(
              'block font-mono text-[15px] font-semibold leading-5 tnum',
              textMain,
            )}
          >
            {hasReturn ? fmtPct(animated) : '—'}
          </span>
          <span className={cn('block text-micro', textSub)}>
            {t('{period}平均收益', { period: periodLabel(sector.period) })}
          </span>
        </span>
      </span>

      {sector.avgStrength !== null && (
        <span
          className="absolute inset-x-0 bottom-0 h-[3px] overflow-hidden rounded-b-md"
          aria-hidden="true"
        >
          <span
            className={cn('block h-full origin-left animate-grow-bar', barFill)}
            style={{
              width: `${Math.max(2, Math.min(100, sector.avgStrength))}%`,
              animationDelay: `${delayMs + 120}ms`,
            }}
          />
        </span>
      )}

      <span
        role="tooltip"
        className="cloud-popover pointer-events-none absolute -top-2 left-1/2 z-30 hidden w-48 -translate-x-1/2 -translate-y-full p-2.5 text-left md:group-hover:block md:group-focus-visible:block"
      >
        <span className="eyebrow block">{sector.name} {t('· 成分股汇总')}</span>
        <span className="mt-1.5 block space-y-1 text-micro">
          <span className="flex items-center justify-between gap-2">
            <span className="text-ink-500">{t('平均强度')}</span>
            <span className="font-mono text-ink-800 tnum">
              {sector.avgStrength?.toFixed(1) ?? '—'}
            </span>
          </span>
          <span className="flex items-center justify-between gap-2">
            <span className="text-ink-500">{t('强度领先')}</span>
            <span className="font-mono font-semibold text-ink-800">
              {leader
                ? `${leader.ticker} ${leader.score?.toFixed(1) ?? '—'}`
                : '—'}
            </span>
          </span>
          <span className="flex items-center justify-between gap-2 border-t border-line pt-1">
            <span className="text-ink-500">{t('统计覆盖')}</span>
            <span className="font-mono text-ink-800 tnum">
              {sector.coveredCount ?? '—'} / {sector.memberCount}
            </span>
          </span>
          <span className="flex items-center justify-between gap-2">
            <span className="text-ink-500">{t('评分覆盖')}</span>
            <span className="font-mono text-ink-800 tnum">
              {sector.scoredCount ?? '—'} / {sector.memberCount}
            </span>
          </span>
          {sector.scoreDataThrough && (
            <span className="flex items-center justify-between gap-2">
              <span className="text-ink-500">{t('评分截至')}</span>
              <span className="font-mono text-ink-800 tnum">{sector.scoreDataThrough}</span>
            </span>
          )}
        </span>
      </span>
    </button>
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
  // 入场只在挂载时播一次，列数按挂载时的视口取一次就够（换周期不重挂、不重播）。
  const [cols] = useState(gridColumns);
  return (
    <div className={GRID_CLASS} role="group" aria-label={t("板块平均收益热力矩阵")}>
      {sectors.map((sector, index) => (
        <HeatTile
          key={sector.id}
          sector={sector}
          delayMs={waveDelayMs(index, cols)}
          selected={selectedId === sector.id}
          onToggle={() => onSelect(sector.id)}
        />
      ))}
    </div>
  );
}

export function HeatMatrixSkeleton() {
  return (
    <div className={GRID_CLASS} aria-hidden="true">
      {/* 骨架数与真实目录（24 主题）对齐，避免加载完成时布局跳变（审计 2.1.16） */}
      {Array.from({ length: 24 }, (_, index) => (
        <SkeletonBlock
          key={index}
          className="h-[92px] rounded-md md:h-[108px]"
        />
      ))}
    </div>
  );
}
