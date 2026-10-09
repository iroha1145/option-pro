/**
 * 同宽占位：几种可能出现的内容叠在同一格里，格子取其中最宽的一种。
 * 读到前后、状态切换时这一格不变宽，同一行后面的内容和换行位置都不动。
 * samples 只用来撑宽度，不可见、读屏跳过；children 是当前显示的内容，默认靠左，可用 className 改对齐。
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

export default function SameWidth({ samples, children, className }: { samples: readonly ReactNode[]; children: ReactNode; className?: string }) {
  return (
    <span className={cn('inline-grid items-center justify-items-start', className)}>
      {samples.map((sample, i) => (
        <span key={i} aria-hidden="true" className="invisible col-start-1 row-start-1">{sample}</span>
      ))}
      <span className="col-start-1 row-start-1">{children}</span>
    </span>
  );
}
