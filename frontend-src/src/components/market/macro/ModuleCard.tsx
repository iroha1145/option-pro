/**
 * 单个模块卡 — 中文名 + 英文短名 + Score + 7 日变化 + 有效因子数 + 数据截止 + 小分数条。
 * 色阶复用全站 strengthBarClass，不给模块分配固定鲜艳色。
 */
import { cn } from '@/lib/utils';
import { strengthBarClass } from '@/lib/strengthColor';
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { MACRO_MODULE_HINTS } from '@/lib/scoreHints';
import type { MacroModule } from '@/api/modules/macro';
import { t } from '../../../i18n/core.ts';

export default function ModuleCard({
  module,
}: {
  module: MacroModule;
  /** 保留兼容旧调用；入场 stagger 已移除，不再需要序号 */
  index?: number;
}) {
  const hasScore = typeof module.score === 'number' && Number.isFinite(module.score);
  const hint = MACRO_MODULE_HINTS[module.moduleId];

  return (
    /* 后续区块 rise-in 减量：直接呈现 */
    <article
      className="card-surface flex min-w-0 flex-col p-4"
      aria-label={t('{name} 模块', { name: t(module.nameZh) })}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="flex items-center gap-1 truncate text-h3 text-ink-800">
            {t(module.nameZh)}
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

      <div className="mt-3 h-[3px] overflow-hidden rounded-pill bg-line" role="presentation">
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
            ? t('有效因子 —')
            : t('有效因子 {valid}/{total}', { valid: module.validFactorCount, total: module.totalFactorCount ?? '—' })}
        </span>
      </div>
      <p className="mt-2 font-mono text-micro text-ink-400 tnum">
        {t('截止')} {module.dataThrough ?? '—'}
      </p>
      {!hasScore && (
        <p className="mt-2 text-micro leading-relaxed text-ink-400">
          {t('有效因子不足')} {module.minimumValidFactors ?? ''} {t('个门槛，本模块不出分（不按 50 补齐）。')}
        </p>
      )}
    </article>
  );
}
