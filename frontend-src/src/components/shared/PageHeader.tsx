/**
 * 页头带（每页通用）
 * 左：中文标题；右：元信息槽（刷新钮、更新时间、视图切换）。
 * 2026-10-06 第二轮（参照 uiarc.dev）：去掉标题上方「§0X + 英文小标签」装饰行和页头下的发丝线，
 * 页头只留标题与状态，和下方内容靠留白分开。
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  title: string;        // 自选观察
  meta?: ReactNode;     // 右侧元信息
  className?: string;
}

export default function PageHeader({ title, meta, className }: PageHeaderProps) {
  return (
    <header
      className={cn('flex flex-wrap items-end justify-between gap-x-6 gap-y-3 pb-1', className)}
    >
      <h1 className="min-w-0 flex-1 basis-72 font-display text-display-m text-ink-900 md:text-display-l">{title}</h1>
      {meta && <div className="flex max-w-full flex-wrap items-center gap-x-4 gap-y-2 pb-1">{meta}</div>}
    </header>
  );
}
