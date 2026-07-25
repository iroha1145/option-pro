/**
 * ChangeBadge：Mono + 语义浅底 r-xs，↑/↓ 图标（不仅依赖颜色，§10）
 *
 * format='points' 用于「分数点」变化（如宏观环境的 7 日分数变化）：分数是 0–100 的
 * 分位点，把 −3.5 分渲染成 −3.5% 会读成百分比，属于事实错误。默认仍是百分比。
 */
import { cn } from '@/lib/utils';
import { fmtPct, fmtSigned } from '@/lib/format';
import Icon from '@/components/icons';

export default function ChangeBadge({
  value,
  className,
  size = 'md',
  format = 'percent',
  pointsSuffix = '分',
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
        className={cn(
          'inline-flex items-center gap-1 rounded-xs bg-paper-2 font-mono text-ink-400 tnum',
          size === 'md' ? 'px-1.5 py-0.5 text-[13px] leading-[18px]' : 'px-1 py-px text-micro',
          className,
        )}
        aria-label="涨跌数据缺失"
      >
        —
      </span>
    );
  }
  const up = value >= 0;
  const points = format === 'points';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1 rounded-xs font-mono tnum',
        size === 'md' ? 'px-1.5 py-0.5 text-[13px] leading-[18px]' : 'px-1 py-px text-micro',
        up ? 'bg-up-50 text-up-700' : 'bg-down-50 text-down-700',
        className,
      )}
      aria-label={
        points
          ? `${up ? '上升' : '下降'} ${Math.abs(value).toFixed(1)} ${pointsSuffix}`
          : `${up ? '涨' : '跌'} ${Math.abs(value).toFixed(2)}%`
      }
    >
      <Icon name={up ? 'arrow-up-right' : 'arrow-down-right'} size={12} strokeWidth={1.45} />
      {points ? `${fmtSigned(value, 1)} ${pointsSuffix}` : fmtPct(value)}
    </span>
  );
}
