/**
 * ChangeBadge：无描边语义浅底、等宽数字，↑/↓ 图标（不仅依赖颜色）
 *
 * format='points' 用于「分数点」变化（如宏观环境的 7 日分数变化）：分数是 0–100 的
 * 分位点，把 −3.5 分渲染成 −3.5% 会读成百分比，属于事实错误。默认仍是百分比。
 */
import { cn } from '@/lib/utils';
import { fmtPct, fmtSigned } from '@/lib/format';
import Icon from '@/components/icons';
import SoftBadge from './SoftBadge';
import NumberTicker from './NumberTicker';
import { t } from '../../i18n/core.ts';

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
      <SoftBadge
        size={size}
        className={cn(
          'change-badge text-ink-400',
          className,
        )}
        aria-label={t("涨跌数据缺失")}
      >
        —
      </SoftBadge>
    );
  }
  /* 平盘是第三种事实，不是「涨」。旧写法 value >= 0 让 0.00% 显示绿色 ↑ 并读成
     「涨 0.00%」（GPT-5.6-Pro 审计 P2-8）。 */
  const direction = value > 0 ? 'up' : value < 0 ? 'down' : 'flat';
  const points = format === 'points';
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
    <SoftBadge
      size={size}
      tone={direction === 'flat' ? 'neutral' : direction}
      className={cn(
        'change-badge',
        className,
      )}
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
    </SoftBadge>
  );
}
