/**
 * 页头带（design.md §7.1，每页通用）
 * 左：§0X Mono brand-600 + Eyebrow 英文 + Display-L 中文标题
 * 右：元信息槽（刷新钮、更新时间、视图切换）· 底部 1px line 发丝线
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  section: string;      // 01
  eyebrow: string;      // WATCHLIST · DELAYED 15MIN
  title: string;        // 自选观察
  meta?: ReactNode;     // 右侧元信息
  className?: string;
}

export default function PageHeader({ section, eyebrow, title, meta, className }: PageHeaderProps) {
  return (
    <header
      className={cn('flex flex-wrap items-end justify-between gap-x-6 gap-y-3 border-b border-line pb-4 md:gap-y-4 md:pb-5', className)}
    >
      <div className="min-w-0 flex-1 basis-72">
        <p className="flex items-baseline gap-2.5">
          <span className="font-mono text-caption font-medium text-brand-600">§{section}</span>
          <span className="eyebrow">{eyebrow}</span>
        </p>
        <h1 className="mt-1.5 font-display text-display-m text-ink-900 md:mt-2 md:text-display-l">{title}</h1>
      </div>
      {meta && <div className="flex max-w-full flex-wrap items-center gap-x-4 gap-y-2 pb-1">{meta}</div>}
    </header>
  );
}
