import type { HTMLAttributes } from 'react';
import { cn } from '@/lib/utils';

export type BadgeTone = 'neutral' | 'brand' | 'ai' | 'up' | 'down' | 'ok' | 'danger' | 'warn' | 'amber' | 'orange';

/* 2026-10-09 Arc 改版：胶囊形、浅色底加同色细边（Arc badge 的 Ready / Needs attention 样式）。 */
const TONES: Record<BadgeTone, string> = {
  neutral: 'border-line bg-paper-2 text-ink-600',
  brand: 'border-brand-600/15 bg-brand-50 text-brand-700',
  ai: 'border-ai-600/20 bg-ai-50 text-ai-600',
  up: 'border-up-600/20 bg-up-50 text-up-700',
  down: 'border-down-600/20 bg-down-50 text-down-700',
  ok: 'border-ok-600/20 bg-ok-50 text-ok-700',
  danger: 'border-danger-600/20 bg-danger-50 text-danger-700',
  warn: 'border-warn-600/25 bg-warn-50 text-warn-700',
  /* 分类色：只给经济事件重要度（高橙、中琥珀），不表示好坏与涨跌。技术事件标签保持灰色（见 SignalChip）。 */
  amber: 'border-cat-amber-600/25 bg-cat-amber-50 text-cat-amber-700',
  orange: 'border-cat-orange-600/20 bg-cat-orange-50 text-cat-orange-700',
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
        'soft-badge inline-flex max-w-full items-center gap-1 whitespace-nowrap border align-middle font-medium tnum',
        size === 'md' ? 'px-2.5 py-0.5 text-[13px] leading-[18px]' : 'px-2 py-px text-[12px] leading-[16px]',
        TONES[tone],
        className,
      )}
    >{children}</span>
  );
}
