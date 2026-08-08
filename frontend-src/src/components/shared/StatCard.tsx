/** StatCard：Eyebrow 标签 + Data-XL count-up + 副行；右上角手绘图标 18px ink-400 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { useCountUp } from '@/hooks/useCountUp';
import Icon, { type IconName } from '@/components/icons';

interface StatCardProps {
  label: string;
  value: number;
  digits?: number;
  suffix?: string;
  icon?: IconName;
  sub?: ReactNode;
  className?: string;
  /** 数值变化时 tick-flash 的方向：undefined 不闪 */
  flash?: 'up' | 'down' | null;
}

export default function StatCard({ label, value, digits = 0, suffix, icon, sub, className, flash }: StatCardProps) {
  const animated = useCountUp(value, 900);
  return (
    <div
      className={cn(
        'card-surface tick-flash p-5',
        flash === 'up' && 'tick-flash-up',
        flash === 'down' && 'tick-flash-down',
        className,
      )}
    >
      <div className="flex items-start justify-between">
        <p className="eyebrow">{label}</p>
        {icon && <Icon name={icon} size={18} className="text-ink-400" />}
      </div>
      <p className="mt-2 font-mono text-data-xl text-ink-900 tnum">
        {animated.toFixed(digits)}
        {suffix && <span className="ml-1 text-[15px] text-ink-400">{suffix}</span>}
      </p>
      {sub && <div className="mt-1.5 text-caption text-ink-500">{sub}</div>}
    </div>
  );
}
