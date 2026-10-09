import type { SignalType } from '@/api/types';
import type { BadgeTone } from '@/components/shared/SoftBadge';

/** 技术事件的分类色（2026-10-09 用户要求「突破、放量等改成多种颜色」）：只表示类别，不表示好坏，
    不借涨跌红绿与 AI 青瓷；同一类事件全站同一个颜色。 */
export const SIGNAL_TONE: Record<SignalType, BadgeTone> = {
  breakout: 'violet',
  volume: 'amber',
  gap: 'orange',
  pullback: 'sky',
  'ma-touch': 'brand',
  'iv-spike': 'pink',
};
