/** SignalChip：技术事件的类别标签，颜色取 lib/signalTone（每类一种分类色，全站一致）。 */
import type { SignalType } from '@/api/types';
import { SIGNAL_TONE } from '@/lib/signalTone';
import SoftBadge, { type BadgeTone } from './SoftBadge';

export default function SignalChip({ type, label, className }: { type: SignalType | string; label: string; className?: string }) {
  const tone = (SIGNAL_TONE as Record<string, BadgeTone>)[type] ?? 'neutral';
  return (
    <SoftBadge tone={tone} className={className}>
      {label}
    </SoftBadge>
  );
}
