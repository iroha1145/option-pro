/** SignalChip：技术事件的类别标签。2026-10-09 用户要求「突破、放量等改成多种颜色」：
    每类一种分类色（见 index.css 的 --cat-*），只表示类别，不表示好坏、不借涨跌红绿与 AI 青瓷；
    同一类事件全站同一个颜色（首页、突破雷达、关注列表、个股页）。 */
import type { SignalType } from '@/api/types';
import SoftBadge, { type BadgeTone } from './SoftBadge';

export const SIGNAL_TONE: Record<SignalType, BadgeTone> = {
  breakout: 'violet',
  volume: 'amber',
  gap: 'orange',
  pullback: 'sky',
  'ma-touch': 'brand',
  'iv-spike': 'pink',
};

export default function SignalChip({ type, label, className }: { type: SignalType | string; label: string; className?: string }) {
  const tone = (SIGNAL_TONE as Record<string, BadgeTone>)[type] ?? 'neutral';
  return (
    <SoftBadge tone={tone} className={className}>
      {label}
    </SoftBadge>
  );
}
