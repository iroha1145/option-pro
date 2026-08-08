/**
 * 异动角标（桌面表格与移动卡片共用）：
 * - 量持比倍数：实心 warn 胶囊 + 呼吸 bolt（framer 呼吸，reducedMotion=user 自动降级）
 * - 全部新开仓：描边 warn 胶囊显 ∞（量持比不适用），与实心倍数胶囊一眼区分
 * - 该侧估算权利金流紧随其后（$ 紧凑格式）；缺买卖价不可估算时不显示
 */
import { motion } from 'framer-motion';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';
import { fmtCompact } from '@/lib/format';
import { t } from '../../../i18n/core.ts';
import type { VolOiState } from '../optionAnalysis.ts';

function BreathingBolt({ light }: { light?: boolean }) {
  return (
    <motion.span
      className="inline-flex"
      animate={{ opacity: [1, 0.35, 1] }}
      transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
      aria-hidden="true"
    >
      <Icon name="bolt" size={11} className={light ? 'text-white' : 'text-warn-700'} />
    </motion.span>
  );
}

export default function AlertChip({
  state,
  premium,
  align = 'start',
}: {
  state: VolOiState;
  premium: number | null;
  align?: 'start' | 'end';
}) {
  const isRatio = state.kind === 'ratio' && state.ratio > 3;
  const isNewOpening = state.kind === 'new_opening';
  if (!isRatio && !isNewOpening) return null;
  return (
    <span
      className={cn(
        'inline-flex flex-wrap items-center gap-1',
        align === 'end' && 'justify-end',
      )}
      aria-label={t('成交异动')}
    >
      {isRatio ? (
        <span className="inline-flex items-center gap-0.5 rounded-pill bg-warn-600 px-1.5 py-0.5 text-micro font-semibold leading-none text-white">
          <BreathingBolt light />
          {state.ratio.toFixed(1)}×
        </span>
      ) : (
        <span className="inline-flex items-center gap-0.5 rounded-pill border border-warn-600 px-1.5 py-0.5 text-micro font-semibold leading-none text-warn-700">
          <BreathingBolt />∞
        </span>
      )}
      {premium !== null && (
        <span className="font-mono text-micro font-medium text-warn-700 tnum">
          ${fmtCompact(premium)}
        </span>
      )}
    </span>
  );
}
