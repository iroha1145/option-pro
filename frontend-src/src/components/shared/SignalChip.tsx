/** SignalChip：无描边的柔和语义标签，文字同时保留状态含义。 */
import { cn } from '@/lib/utils';
import type { SignalType } from '@/api/types';
import SoftBadge from './SoftBadge';

const STYLE: Record<SignalType, string> = {
  breakout: 'text-brand-700 bg-brand-50',
  volume: 'text-warn-700 bg-warn-50',
  pullback: 'text-ink-500 bg-paper',
  'ma-touch': 'text-ink-500 bg-paper',
  gap: 'text-ai-600 bg-ai-50',
  'iv-spike': 'text-down-700 bg-down-50',
};

export default function SignalChip({ type, label, className }: { type: SignalType | string; label: string; className?: string }) {
  const style = (STYLE as Record<string, string>)[type] ?? STYLE.pullback;
  return (
    <SoftBadge
      className={cn(style, className)}
    >
      {label}
    </SoftBadge>
  );
}
