/**
 * 信号类型的中文标签。
 *
 * 住在 lib 而不是 mocks 里，因为 **live 模式真的读它**：突破映射
 * （api/modules/breakouts.ts）无条件用它给事件取名。放在 mocks 里，它就成了唯一
 * 把整个 fixture 模块钉在 live 依赖图里的东西 —— 一份 live 会用的数据，本来就不是
 * mock 数据。
 */
import type { SignalType } from '@/api/types';
import { t } from '../i18n/core.ts';

export const SIGNAL_LABELS: Record<SignalType, string> = {
  breakout: t('突破'),
  volume: t('放量'),
  pullback: t('回踩'),
  'ma-touch': t('触均线'),
  gap: t('跳空'),
  'iv-spike': t('IV 异动'),
};

export const SIGNAL_TYPES = Object.keys(SIGNAL_LABELS) as SignalType[];
