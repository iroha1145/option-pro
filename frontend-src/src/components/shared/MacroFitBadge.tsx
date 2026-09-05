/**
 * 宏观适配徽标（影子字段，不参与任何排序或评分）。
 *
 * 有分数就显示「64 · 顺风」，没读到就显示原因，不显示「—」也不显示 50。
 * 「没读到」和「中性」在这个界面里读起来完全相反，所以后端分得清的三种缺失原因
 * （无快照 / 未归板块 / 暴露观测不足）在这里也各说各话。
 */
import { cn } from '@/lib/utils';
import SoftBadge from './SoftBadge';
import {
  MACRO_TONE_LABEL,
  macroMissingReason,
  macroToneOf,
} from '@/lib/macroFit';
import { t } from '../../i18n/core.ts';

interface Props {
  score: number | null | undefined;
  /** 后端下发的中文标签；缺失时按分数本地分档（分界线与后端同源）。 */
  tailwind?: string | null;
  /** 后端 macro_shadow_status；只在没有分数时用来说明原因。 */
  status?: string | null;
  className?: string;
  /** 紧凑模式只出分数和标签，用在表格单元里。 */
  compact?: boolean;
}

export default function MacroFitBadge({
  score,
  tailwind,
  status,
  className,
  compact = false,
}: Props) {
  const tone = macroToneOf(score, tailwind);
  if (tone === null || typeof score !== 'number') {
    const reason = t(macroMissingReason(status) ?? '暂无宏观读数');
    return (
      <SoftBadge
        className={className}
        title={reason}
      >
        {compact ? t('无读数') : reason}
      </SoftBadge>
    );
  }
  return (
    <SoftBadge
      tone={tone === 'tailwind' ? 'up' : tone === 'headwind' ? 'down' : 'neutral'}
      className={cn('gap-1', className)}
    >
      <span className="tnum">{score.toFixed(1)}</span>
      <span>{t(MACRO_TONE_LABEL[tone])}</span>
    </SoftBadge>
  );
}
