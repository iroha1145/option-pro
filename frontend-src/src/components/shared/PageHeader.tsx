/**
 * 页头带（design.md §7.1，每页通用）
 * 左：§0X Mono brand-600 + Eyebrow 英文 + Serif Display-L 中文标题 + 一句说明
 * 右：元信息槽（刷新钮、更新时间、视图切换）· 底部 1px line 发丝线
 */
import type { ReactNode } from 'react';
import { motion } from 'framer-motion';
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
    <motion.header
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.56, ease: [0.16, 1, 0.3, 1] }}
      className={cn('flex flex-wrap items-end justify-between gap-x-6 gap-y-4 border-b border-line pb-5', className)}
    >
      <div>
        <p className="flex items-baseline gap-2.5">
          <span className="font-mono text-caption font-semibold text-brand-600">§{section}</span>
          <span className="eyebrow">{eyebrow}</span>
        </p>
        <h1 className="mt-2 font-display text-display-l text-ink-900">{title}</h1>
        {description && <p className="mt-1.5 text-body-s text-ink-500">{description}</p>}
      </div>
      {meta && <div className="flex items-center gap-4 pb-1">{meta}</div>}
    </motion.header>
  );
}
