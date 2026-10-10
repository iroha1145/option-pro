/**
 * ChangeBadge：涨跌色文字 + ↑/↓ 图标（不仅依赖颜色），等宽数字。
 * 2026-10-06 第二轮（参照 uiarc.dev）：去掉浅色底块，只留着色文字——列表里一排排
 * 绿底红底的小块是页面上最重的色块，颜色仍然只表达涨跌。
 *
 * format='points' 用于「分数点」变化（如宏观环境的 7 日分数变化）：分数是 0–100 的
 * 分位点，把 −3.5 分渲染成 −3.5% 会读成百分比，属于事实错误。默认仍是百分比。
 */
import { cn } from '@/lib/utils';
import { fmtPct, fmtSigned } from '@/lib/format';
import Icon from '@/components/icons';
import NumberTicker from './NumberTicker';
import { t } from '../../i18n/core.ts';

const TONE_TEXT = {
  up: 'text-up-700',
  down: 'text-down-700',
  flat: 'text-ink-500',
} as const;

const SIZE_TEXT = {
  sm: 'text-[12px] leading-[16px]',
  md: 'text-[14px] leading-[18px]',
} as const;

export default function ChangeBadge({
  value,
  className,
  size = 'md',
  format = 'percent',
  pointsSuffix = t('分'),
}: {
  value: number | null | undefined;
  className?: string;
  size?: 'sm' | 'md';
  format?: 'percent' | 'points';
  pointsSuffix?: string;
}) {
  /* live 缺失涨跌数据：如实显「—」中性徽标，不显 +0.00% */
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return (
      <span
        className={cn('change-badge inline-flex items-center tnum text-ink-400', SIZE_TEXT[size], className)}
        aria-label={t("涨跌数据缺失")}
      >
        —
      </span>
    );
  }
  /* 平盘是第三种事实，不是「涨」。旧写法 value >= 0 让 0.00% 显示绿色 ↑ 并读成
     「涨 0.00%」（GPT-5.6-Pro 审计 P2-8）。 */
  const points = format === 'points';
  const percentText = points ? null : fmtPct(value);
  const direction = value > 0 ? 'up' : value < 0 && percentText !== '+0.00%' ? 'down' : 'flat';
  const magnitude = points
    ? `${Math.abs(value).toFixed(1)} ${pointsSuffix}`
    : `${Math.abs(value).toFixed(2)}%`;
  const label =
    direction === 'flat'
      ? points
        ? t('持平 0 {suffix}', { suffix: pointsSuffix })
        : t('持平 0.00%')
      : points
        ? `${direction === 'up' ? t('上升') : t('下降')} ${magnitude}`
        : `${direction === 'up' ? t('涨') : t('跌')} ${magnitude}`;
  const text = points
    ? direction === 'flat'
      ? `0.0 ${pointsSuffix}`
      : `${fmtSigned(value, 1)} ${pointsSuffix}`
    : direction === 'flat'
      ? '0.00%'
      : fmtPct(value);
  return (
    <span
      data-change={direction}
      className={cn('change-badge inline-flex items-center gap-0.5 tnum', SIZE_TEXT[size], TONE_TEXT[direction], className)}
      aria-label={label}
    >
      <Icon
        name={
          direction === 'up'
            ? 'arrow-up-right'
            : direction === 'down'
              ? 'arrow-down-right'
              : 'minus'
        }
        size={12}
        strokeWidth={1.45}
      />
      <NumberTicker text={text} />
    </span>
  );
}
