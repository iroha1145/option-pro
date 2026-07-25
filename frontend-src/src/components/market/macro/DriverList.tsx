/**
 * 驱动因素 — 改善最多 / 恶化最多，各最多 3 个，按 score_change_7d 排序。
 * 缺少可比历史快照时显示诚实空态，而不是显示 0。
 */
import ChangeBadge from '@/components/shared/ChangeBadge';
import InfoHint from '@/components/shared/InfoHint';
import { MACRO_FACTOR_HINTS } from '@/lib/scoreHints';
import type { MacroDriver } from '@/api/modules/macro';

export default function DriverList({
  title,
  eyebrow,
  drivers,
  emptyText,
}: {
  title: string;
  eyebrow: string;
  drivers: MacroDriver[];
  emptyText: string;
}) {
  return (
    <section className="card-surface flex h-full flex-col p-5" aria-label={title}>
      <p className="eyebrow">{eyebrow}</p>
      <h3 className="mt-1.5 text-h3 text-ink-800">{title}</h3>
      {drivers.length === 0 ? (
        <p className="mt-4 text-body-s leading-relaxed text-ink-400">{emptyText}</p>
      ) : (
        <ul className="mt-4 flex flex-col gap-3">
          {drivers.map((driver) => {
            const hint = MACRO_FACTOR_HINTS[driver.factorId];
            return (
              <li key={driver.factorId} className="flex items-start justify-between gap-3">
                <span className="flex min-w-0 items-center gap-1 text-body-s text-ink-700">
                  <span className="truncate">{driver.nameZh}</span>
                  {hint && <InfoHint hint={hint} side="bottom" align="start" size={11} />}
                </span>
                <span className="flex shrink-0 items-center gap-2">
                  <span className="font-mono text-data-m text-ink-800 tnum">
                    {typeof driver.score === 'number' ? driver.score.toFixed(1) : '—'}
                  </span>
                  <ChangeBadge value={driver.scoreChange7d} size="sm" format="points" />
                </span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
