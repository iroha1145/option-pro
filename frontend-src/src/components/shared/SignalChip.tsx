/** SignalChip：中性的灰色标签。突破、放量、跳空这些是技术事件的类别，不是状态，
    不借品牌、警示或 AI 色；靠文字区分，突破略深一档便于扫读。
    2026-10-09 试过每类一种分类色（突破紫、放量琥珀、跳空橙、回踩天蓝），用户看过后要求改回灰色，不要再按类别着色。 */
import { cn } from '@/lib/utils';
import type { SignalType } from '@/api/types';
import SoftBadge from './SoftBadge';

const STYLE: Record<SignalType, string> = {
  breakout: 'text-ink-800 bg-paper-2',
  volume: 'text-ink-600 bg-paper-2',
  pullback: 'text-ink-600 bg-paper-2',
  'ma-touch': 'text-ink-600 bg-paper-2',
  gap: 'text-ink-600 bg-paper-2',
  'iv-spike': 'text-ink-600 bg-paper-2',
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
