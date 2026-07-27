import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import type { SectorVm } from './model';
import { heatTone, periodLabel } from './model';
import { t } from '../../i18n/core.ts';

const GRID_CLASS =
  'grid grid-cols-2 gap-2.5 md:grid-cols-4 md:gap-3 xl:grid-cols-6';

function HeatTile({
  sector,
  index,
  selected,
  onToggle,
}: {
  sector: SectorVm;
  index: number;
  selected: boolean;
  onToggle: () => void;
}) {
  const value = sector.avgReturn ?? 0;
  /* count-up 减量：热力砖涨跌直接呈现终值 */
  const animated = value;
  const tone = heatTone(value);
  const hasReturn = sector.avgReturn !== null;
  const leader = sector.leaders[0] ?? null;
  const textMain = tone.dark ? 'text-white' : 'text-ink-800';
  const textSub = tone.dark ? 'text-white/75' : 'text-ink-500';
  const barFill = tone.dark ? 'bg-white/40' : 'bg-ink-900/25';

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{
        duration: 0.48,
        ease: [0.16, 1, 0.3, 1],
        delay: index * 0.04,
      }}
      whileHover={{
        y: -3,
        transition: { duration: 0.24, ease: 'easeOut' },
      }}
      onClick={onToggle}
      aria-pressed={selected}
      aria-label={
        hasReturn
          ? t('{name}{period}平均收益 {ret}%，平均强度 {strength}', { name: sector.name, period: periodLabel(sector.period), ret: sector.avgReturn?.toFixed(2), strength: sector.avgStrength ?? t('暂无') })
          : t('{name}暂无强度聚合', { name: sector.name })
      }
      className={cn(
        'group relative h-[92px] overflow-visible rounded-md text-left shadow-sh-1 transition-shadow duration-[240ms] ease-out hover:shadow-sh-2 md:h-[108px]',
        selected && 'shadow-sh-2',
      )}
      style={{ backgroundColor: tone.bg }}
    >
      {selected && (
        <motion.span
          layoutId="sector-selected-bar"
          className="absolute left-0 top-0 z-10 h-full w-[3px] rounded-l-md bg-brand-600"
          initial={{ scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{ duration: 0.26, ease: [0.16, 1, 0.3, 1] }}
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
            {sector.coveredCount !== null ? t('{n} 只', { n: sector.coveredCount }) : t('未覆盖')}
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
              animationDelay: `${index * 40 + 120}ms`,
            }}
          />
        </span>
      )}

      <span
        role="tooltip"
        className="glass pointer-events-none absolute -top-2 left-1/2 z-30 hidden w-48 -translate-x-1/2 -translate-y-full rounded-md border border-line p-2.5 text-left shadow-sh-2 md:group-hover:block md:group-focus-visible:block"
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
        </span>
      </span>
    </motion.button>
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
  return (
    <div className={GRID_CLASS} role="group" aria-label={t("板块平均收益热力矩阵")}>
      {sectors.map((sector, index) => (
        <HeatTile
          key={sector.id}
          sector={sector}
          index={index}
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
