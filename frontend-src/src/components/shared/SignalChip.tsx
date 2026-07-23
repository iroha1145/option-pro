/** SignalChip：信号类型小签，hairline 框 r-xs（突破=brand、放量=warn、回踩=ink…） */
import { cn } from '@/lib/utils';
import type { SignalType } from '@/api/types';

const STYLE: Record<SignalType, string> = {
  breakout: 'border-brand-400/60 text-brand-600 bg-brand-50',
  volume: 'border-warn-600/50 text-warn-600 bg-warn-50',
  pullback: 'border-line-strong text-ink-500 bg-card-warm',
  'ma-touch': 'border-line-strong text-ink-500 bg-card-warm',
  gap: 'border-ai-600/40 text-ai-600 bg-ai-50',
  'iv-spike': 'border-down-600/40 text-down-700 bg-down-50',
};

export default function SignalChip({ type, label, className }: { type: SignalType | string; label: string; className?: string }) {
  const style = (STYLE as Record<string, string>)[type] ?? STYLE.pullback;
  return (
    <span
      className={cn('inline-flex items-center whitespace-nowrap rounded-xs border px-1.5 py-px text-micro font-medium leading-[16px]', style, className)}
    >
      {label}
    </span>
  );
}
