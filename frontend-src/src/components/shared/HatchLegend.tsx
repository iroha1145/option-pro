/** HatchLegend：▨ 预估 / ▮ 实际 图注（§6-3） */
import { cn } from '@/lib/utils';
import { t } from '../../i18n/core.ts';

export default function HatchLegend({ className, actual = t('实际'), estimate = t('预估') }: { className?: string; actual?: string; estimate?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-3 text-micro text-ink-400', className)}>
      <span className="inline-flex items-center gap-1">
        <span className="inline-block size-2.5 rounded-[2px] bg-brand-600" aria-hidden="true" />
        {actual}
      </span>
      <span className="inline-flex items-center gap-1">
        <span
          className="inline-block size-2.5 rounded-[2px] border border-brand-400"
          style={{ backgroundImage: 'repeating-linear-gradient(45deg, rgba(46,70,224,.55) 0 1.2px, transparent 1.2px 4px)' }}
          aria-hidden="true"
        />
        {estimate}
      </span>
    </span>
  );
}
