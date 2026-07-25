/**
 * 单个模块卡 — 中文名 + 英文短名 + Score + 7 日变化 + 有效因子数 + 数据截止 + 小分数条。
 * 色阶复用全站 strengthBarClass，不给模块分配固定鲜艳色。
 */
import { motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import { strengthBarClass } from '@/components/shared/StrengthBar';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { MACRO_MODULE_HINTS } from '@/lib/scoreHints';
import type { MacroModule } from '@/api/modules/macro';

export default function ModuleCard({
  module,
  index = 0,
}: {
  module: MacroModule;
  index?: number;
}) {
  const hasScore = typeof module.score === 'number' && Number.isFinite(module.score);
  const hint = MACRO_MODULE_HINTS[module.moduleId];

  return (
    <motion.article
      initial={{ opacity: 0, y: 10 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.3 }}
      transition={{ duration: 0.45, ease: [0.16, 1, 0.3, 1], delay: index * 0.035 }}
      className="card-surface flex min-w-0 flex-col p-4"
      aria-label={`${module.nameZh} 模块`}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1 truncate text-h3 text-ink-800">
            {module.nameZh}
            {hint && <InfoHint hint={hint} side="bottom" align="start" size={12} />}
          </p>
          <p className="mt-0.5 font-mono text-[10px] uppercase tracking-wider text-ink-400">
            {module.nameEn}
          </p>
        </div>
        <span className="shrink-0 font-mono text-data-l text-ink-900 tnum">
          {hasScore ? (module.score as number).toFixed(1) : '—'}
        </span>
      </div>

      <div className="mt-3 h-1 overflow-hidden rounded-pill bg-line" role="presentation">
        {hasScore && (
          <span
            className={cn(
              'block h-full origin-left rounded-pill animate-grow-bar',
              strengthBarClass(module.score as number),
            )}
            style={{ width: `${Math.max(2, Math.min(100, module.score as number))}%` }}
          />
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <ChangeBadge value={module.scoreChange7d} size="sm" format="points" />
        <span className="font-mono text-micro text-ink-400 tnum">
          {module.validFactorCount === null
            ? '有效因子 —'
            : `有效因子 ${module.validFactorCount}/${module.totalFactorCount ?? '—'}`}
        </span>
      </div>
      <p className="mt-2 font-mono text-micro text-ink-400 tnum">
        截止 {module.dataThrough ?? '—'}
      </p>
      {!hasScore && (
        <p className="mt-2 text-micro leading-relaxed text-ink-400">
          有效因子不足{module.minimumValidFactors ?? ''}个门槛，本模块不出分（不按 50 补齐）。
        </p>
      )}
    </motion.article>
  );
}
