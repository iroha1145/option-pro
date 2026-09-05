import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export type BadgeTone = 'neutral' | 'brand' | 'ai' | 'up' | 'down' | 'warn';

const TONES: Record<BadgeTone, string> = {
  neutral: 'bg-paper-2 text-ink-600',
  brand: 'bg-brand-50 text-brand-700',
  ai: 'bg-ai-50 text-ai-600',
  up: 'bg-up-50 text-up-700',
  down: 'bg-down-50 text-down-700',
  warn: 'bg-warn-50 text-warn-700',
};

/** Shared treatment for semantic readings, statuses and company categories. */
export default function SoftBadge({
  tone = 'neutral', size = 'sm', className, children, ...props
}: HTMLAttributes<HTMLSpanElement> & {
  tone?: BadgeTone;
  size?: 'sm' | 'md';
}) {
  return (
    <span
      {...props}
      data-soft-badge={tone}
      className={cn(
        'soft-badge inline-flex max-w-full items-center gap-1 whitespace-nowrap align-middle font-medium tnum',
        size === 'md' ? 'px-2 py-1 text-[13px] leading-[18px]' : 'px-1.5 py-0.5 text-[11px] leading-[16px]',
        TONES[tone],
        className,
      )}
    >{children}</span>
  );
}
