/** ChangeBadge：Mono + 语义浅底 r-xs，↑/↓ 图标（不仅依赖颜色，§10） */
import { cn } from '@/lib/utils';
import { fmtPct } from '@/lib/format';
import Icon from '@/components/icons';

export default function ChangeBadge({
  value,
  className,
  size = 'md',
}: {
  value: number | null | undefined;
  className?: string;
  size?: 'sm' | 'md';
}) {
  /* live 缺失涨跌数据：如实显「—」中性徽标，不显 +0.00% */
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return (
      <span
        className={cn(
          'inline-flex items-center gap-1 rounded-xs bg-paper-2 font-mono text-ink-400 tnum',
          size === 'md' ? 'px-1.5 py-0.5 text-[13px] leading-[18px]' : 'px-1 py-px text-micro',
          className,
        )}
        aria-label="涨跌数据缺失"
      >
        —
      </span>
    );
  }
  const up = value >= 0;
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-xs font-mono tnum',
        size === 'md' ? 'px-1.5 py-0.5 text-[13px] leading-[18px]' : 'px-1 py-px text-micro',
        up ? 'bg-up-50 text-up-700' : 'bg-down-50 text-down-700',
        className,
      )}
      aria-label={`${up ? '涨' : '跌'} ${Math.abs(value).toFixed(2)}%`}
    >
      <Icon name={up ? 'arrow-up-right' : 'arrow-down-right'} size={12} strokeWidth={1.8} />
      {fmtPct(value)}
    </span>
  );
}
