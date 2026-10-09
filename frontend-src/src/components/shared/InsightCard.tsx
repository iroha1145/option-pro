/**
 * Insight Cards 卡片语言（beautifului.dev · Insight Cards）落到 Paper Terminal：
 * 白纸卡片 → 方向箭头 + 标题 + 右侧口径胶囊 → 收进一层的图台（自带表头：
 * 左边写参照口径、右边放切换）→ 底部大数值 + 涨跌 + **比较基准**。
 *
 * 拆成两件导出：价格块用在个股头部（那里没有图），图台用在已经有自己卡片的
 * 图上（CTA/宏观）。
 *
 * 「比较基准」不是装饰：只给 +2.57% 而不说跟谁比，读者只能猜。参考图把
 * 「vs 3 months」写在数值旁边，这条纪律照搬。
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import ChangeBadge from '@/components/shared/ChangeBadge';

import { toneOf } from '@/lib/insightTone';

/**
 * 图台：暖白纸面 + 发丝边，顶部一行表头（左 = 参照口径，右 = 切换控件）。
 * label 留空就只画框——已有自己标题的图（宏观历史）走这条。
 */
export function InsightFrame({
  label,
  action,
  className,
  children,
}: {
  label?: string;
  action?: ReactNode;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className={cn('insight-frame rounded-lg border border-line bg-card-warm p-2', className)}>
      {(label || action) && (
        <div className="mb-1 flex min-h-6 items-center gap-2 px-1">
          {label && <span className="min-w-0 truncate text-micro text-ink-400">{label}</span>}
          {action && <div className="ml-auto shrink-0">{action}</div>}
        </div>
      )}
      {children}
    </div>
  );
}

/**
 * 数值块：大字读数 + 涨跌徽标 + 绝对变动 + 比较基准。
 * value 已格式化好（带不带 $ 由调用点定），changePct/change 允许缺失——
 * 缺就交给 ChangeBadge 显「—」，绝不补 0。
 */
export function InsightValue({
  value,
  suffix,
  changePct,
  change,
  basis,
  size = 'md',
  className,
}: {
  value: ReactNode;
  suffix?: string;
  changePct?: number | null;
  change?: number | null;
  basis?: string;
  size?: 'md' | 'xl';
  className?: string;
}) {
  const hasChange = typeof change === 'number' && Number.isFinite(change);
  const tone = toneOf(change ?? changePct);
  return (
    <div className={cn('flex flex-wrap items-baseline gap-x-2.5 gap-y-1', className)}>
      <span
        className={cn(
          'metric-value text-ink-900',
          size === 'xl' ? 'text-[clamp(30px,10vw,44px)] leading-none' : 'text-data-l leading-tight',
        )}
      >
        {value}
      </span>
      {suffix && <span className="text-body-s text-ink-500">{suffix}</span>}
      {changePct !== undefined && <ChangeBadge value={changePct} size={size === 'xl' ? 'md' : 'sm'} />}
      {/* 涨跌额与 ChangeBadge 同样只用着色文字，不加浅色底块（2026-10-06 第二轮） */}
      {hasChange && (
        <span
          className={cn(
            'change-badge tnum',
            size === 'xl' ? 'text-[14px] leading-[18px]' : 'text-[12px] leading-[16px]',
            tone === 'up' ? 'text-up-700' : tone === 'down' ? 'text-down-700' : 'text-ink-500',
          )}
        >
          {change > 0 ? '+' : change < 0 ? '−' : ''}
          {fmtPrice(Math.abs(change))}
        </span>
      )}
      {/* 大号读数在手机上把比较基准放到下一行：价格、涨跌额读到后变宽，基准留在同一行会被推走或折行 */}
      {basis && <span className={cn('text-micro text-ink-400', size === 'xl' && 'max-sm:basis-full')}>{basis}</span>}
    </div>
  );
}
