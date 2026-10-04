/**
 * 页头带（design.md §7.1，每页通用）
 * 左：页码 folio + eyebrow + display 标题 + 限行长说明
 * 右：元信息槽（刷新钮、更新时间、视图切换）· 底部 1px 发丝线
 */
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface PageHeaderProps {
  section: string;      // 01
  eyebrow: string;      // WATCHLIST · DELAYED 15MIN
  title: string;        // 自选观察
  description?: string; // 一句说明
  meta?: ReactNode;     // 右侧元信息
  className?: string;
}

export default function PageHeader({ section, eyebrow, title, description, meta, className }: PageHeaderProps) {
  return (
    <header
      className={cn('page-masthead flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-b border-line pb-5', className)}
    >
      <div className="flex min-w-0 flex-1 basis-72 items-start gap-3 sm:gap-4">
        <span className="page-folio">§{section}</span>
        <div className="min-w-0">
          <p className="eyebrow">{eyebrow}</p>
          <h1 className="page-title mt-1.5 font-display text-display-l text-ink-900">{title}</h1>
          {description && <p className="page-lede mt-1.5 text-body-s text-ink-500">{description}</p>}
        </div>
      </div>
      {meta && <div className="flex max-w-full flex-wrap items-center gap-x-4 gap-y-2 pb-1">{meta}</div>}
    </header>
  );
}
