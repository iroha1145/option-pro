/**
 * B1 板块热力矩阵（sectors.md：11 砖，桌面 6+5 两行，砖高约 108px）
 * - 砖底色 = §1.7 涨跌色阶连续映射（heatTone）；深底反白
 * - 砖内：板块名 Sans 13 600 + 涨跌幅 Mono 15 600（count-up 900ms）
 *   + 底部 3px 强度条（白/黑 30% 透明度自适应底）+ 右上角成分股数 Micro
 * - hover：translateY(-3px) + sh-2（160ms）+ 毛玻璃 tooltip（领涨 / 领跌 / 平均强度）
 * - 点击：左缘 3px brand-600 选中条（260ms 滑入），再次点击取消
 * - 进场：stagger 40ms rise-in + 底色 600ms 渐显错峰
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import { useCountUp } from '@/hooks/useCountUp';
import Icon from '@/components/icons';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import type { SectorVm } from './model';
import { heatTone } from './model';

const GRID_CLASS = 'grid grid-cols-2 gap-2.5 md:grid-cols-4 md:gap-3 xl:grid-cols-6';

/* ---------- 单砖 ---------- */
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
  const pct = sector.changePct ?? 0;
  const animated = useCountUp(pct, 900);
  const tone = heatTone(pct);
  const hasData = sector.changePct !== null;
  const avgStrength = (() => {
    const vals = sector.constituents.map((c) => c.strengthScore).filter((v): v is number => v !== null);
    if (sector.strengthScore !== null) return sector.strengthScore;
    return vals.length ? Math.round(vals.reduce((a, b) => a + b, 0) / vals.length) : null;
  })();

  const textMain = tone.dark ? 'text-white' : 'text-ink-800';
  const textSub = tone.dark ? 'text-white/75' : 'text-ink-500';
  const barFill = tone.dark ? 'bg-white/40' : 'bg-ink-900/25';

  return (
    <motion.button
      type="button"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: index * 0.04 }}
      whileHover={{ y: -3, transition: { duration: 0.16, ease: [0.22, 1, 0.36, 1] } }}
      onClick={onToggle}
      aria-pressed={selected}
      aria-label={
        hasData
          ? `${sector.name} ${pct >= 0 ? '涨' : '跌'} ${Math.abs(pct).toFixed(2)}%，成分 ${sector.count} 只，强度 ${sector.strengthScore ?? '—'}`
          : `${sector.name} 快照缺失`
      }
      className={cn(
        'group relative h-[88px] overflow-visible rounded-md text-left shadow-sh-1 transition-shadow duration-fast hover:shadow-sh-2 md:h-[108px]',
        selected && 'shadow-sh-2',
      )}
      style={{ backgroundColor: tone.bg }}
    >
      {/* 选中条：左缘 3px brand-600，260ms 滑入 */}
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
        <span className="flex items-start justify-between gap-1.5">
          <span className={cn('truncate text-[13px] font-semibold leading-[18px]', textMain)}>{sector.name}</span>
          <span className={cn('hidden font-mono text-micro tnum md:inline', textSub)}>{sector.count}只</span>
        </span>
        <span className={cn('font-mono text-[15px] font-semibold leading-5 tnum', textMain)}>
          {hasData ? fmtPct(animated) : '—'}
        </span>
      </span>

      {/* 底部 3px 强度条（简化 StrengthBar，30% 透明度自适应底） */}
      {sector.strengthScore !== null && (
        <span className="absolute inset-x-0 bottom-0 h-[3px] overflow-hidden rounded-b-md" aria-hidden="true">
          <span
            className={cn('block h-full origin-left animate-grow-bar', barFill)}
            style={{ width: `${Math.max(2, Math.min(100, sector.strengthScore))}%`, animationDelay: `${index * 40 + 120}ms` }}
          />
        </span>
      )}

      {/* 毛玻璃 tooltip：领涨 / 领跌 / 平均强度（≥md 才启用；移动端点击直接下钻，避免粘性遮挡） */}
      <span
        role="tooltip"
        className="glass pointer-events-none absolute -top-2 left-1/2 z-30 hidden w-44 -translate-x-1/2 -translate-y-full rounded-md border border-line p-2.5 text-left shadow-sh-2 md:group-hover:block md:group-focus-visible:block"
      >
        <span className="eyebrow block">{sector.name} · 明细</span>
        <span className="mt-1.5 block space-y-1 text-micro">
          <span className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-up-700">
              <Icon name="arrow-up-right" size={11} strokeWidth={1.8} />
              领涨 <span className="font-mono font-semibold">{sector.leader?.ticker ?? '—'}</span>
            </span>
            <span className="font-mono text-up-700 tnum">{sector.leader ? fmtPct(sector.leader.changePct) : '—'}</span>
          </span>
          <span className="flex items-center justify-between gap-2">
            <span className="flex items-center gap-1 text-down-700">
              <Icon name="arrow-down-right" size={11} strokeWidth={1.8} />
              领跌 <span className="font-mono font-semibold">{sector.laggard?.ticker ?? '—'}</span>
            </span>
            <span className="font-mono text-down-700 tnum">{sector.laggard ? fmtPct(sector.laggard.changePct) : '—'}</span>
          </span>
          <span className="flex items-center justify-between gap-2 border-t border-line pt-1">
            <span className="text-ink-500">平均强度分</span>
            <span className="font-mono text-ink-800 tnum">{avgStrength ?? '—'}</span>
          </span>
        </span>
      </span>
    </motion.button>
  );
}

/* ---------- 矩阵 ---------- */
interface HeatMatrixProps {
  sectors: SectorVm[];
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export default function HeatMatrix({ sectors, selectedId, onSelect }: HeatMatrixProps) {
  return (
    <div className={GRID_CLASS} role="group" aria-label="板块热力矩阵">
      {sectors.map((s, i) => (
        <HeatTile key={s.id} sector={s} index={i} selected={selectedId === s.id} onToggle={() => onSelect(s.id)} />
      ))}
    </div>
  );
}

/** B1 加载骨架：11 块 shimmer，保持网格形状 */
export function HeatMatrixSkeleton() {
  return (
    <div className={GRID_CLASS} aria-hidden="true">
      {Array.from({ length: 11 }, (_, i) => (
        <SkeletonBlock key={i} className="h-[88px] rounded-md md:h-[108px]" />
      ))}
    </div>
  );
}
