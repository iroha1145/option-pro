/**
 * 指数卡（首页指数带；大盘页 B1 指数概览可直接换用）
 * 名称 + 代码 / 价格 / 涨跌徽标 + 当日走势小图（有数据才画）。
 *
 * - 传 `to` 渲染为链接（首页：跳 /market?index=）；传 `onOpen` 渲染为按钮
 *   （大盘页：打开指数详情）。两种都用同一个可访问名「{name} {code} 详情」。
 * - 无有效价（live 快照缺失时为 0）显「—」，不显 0.00；涨跌缺失交给 ChangeBadge 显「—」。
 * - 小图数据由调用方给：live 没有指数 K 线端点，组件不自己去取演示数据。
 * - 入场动画挂在外层，悬停上浮（.card-hover）挂在里层，framer 留下的内联
 *   transform 不会压掉 CSS 位移。
 * - 手机三列时卡宽约 114px：内边距、价格字号收一档，小图从 sm 起才出现。
 */
import type { Ref } from 'react';
import { Link } from 'react-router';
import { motion } from 'framer-motion';
import type { IndexQuote } from '@/api/types';
import type { FlashDirection } from '@/hooks/useTickFlash';
import { DUR_SECTION, EASE_PAPER } from '@/lib/motion';
import { fmtPrice } from '@/lib/format';
import { cn } from '@/lib/utils';
import ChangeBadge from '@/components/shared/ChangeBadge';
import { SkeletonBlock } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';
import { t } from '../../i18n/core.ts';

type Target =
  | { to: string; onOpen?: never; ref?: Ref<HTMLAnchorElement> }
  | { onOpen: () => void; to?: never; ref?: Ref<HTMLButtonElement> };

export type IndexCardProps = Target & {
  quote: IndexQuote;
  /** 入场错峰的序号 */
  index?: number;
  /** 当日走势点；没有就不画 */
  spark?: number[] | null;
  /** 价格变动闪烁（useTickFlash 的结果） */
  flash?: FlashDirection;
  /** 从 ?index= 进入时高亮的那一张 */
  focused?: boolean;
};

/* xl 起与相邻格子合成一条指标带：去掉自身边框圆角，悬停换浅底而不是加深描边。 */
const SURFACE =
  'card-surface card-hover card-glare flex h-full w-full flex-col gap-1 p-2.5 text-left sm:p-3 xl:rounded-none xl:border-0 xl:px-5 xl:py-4 xl:transition-colors xl:hover:bg-paper-2';

/** 同尺寸骨架：三行与真实卡对齐，手机三列时也不溢出卡宽 */
export function IndexCardSkeleton() {
  return (
    <div className="card-surface flex flex-col gap-1.5 p-2.5 sm:p-3 xl:rounded-none xl:border-0 xl:px-5 xl:py-4" data-state="loading" aria-hidden="true">
      <SkeletonBlock className="h-3 w-3/5" />
      <SkeletonBlock className="h-5 w-4/5 sm:h-6" />
      <SkeletonBlock className="mt-1 h-4 w-14 rounded-xs" />
    </div>
  );
}

export default function IndexCard(props: IndexCardProps) {
  const { quote, index = 0, spark, flash, focused = false, to, onOpen, ref } = props;
  const hasPrice = Number.isFinite(quote.price) && quote.price > 0;
  const label = t('{name} {code} 详情', { name: quote.name, code: quote.code });
  const surface = cn(SURFACE, focused && 'ring-1 ring-brand-100');

  const body = (
    <>
      <span className="flex min-w-0 items-baseline justify-between gap-1.5">
        <span className="min-w-0 truncate text-caption text-ink-500">{quote.name}</span>
        <span className="shrink-0 tnum text-micro text-ink-400">{quote.code}</span>
      </span>
      <span
        className={cn(
          'metric-value tick-flash self-start rounded-xs text-[17px] leading-6 text-ink-900 sm:text-data-l',
          flash === 'up' && 'tick-flash-up',
          flash === 'down' && 'tick-flash-down',
        )}
      >
        {hasPrice ? fmtPrice(quote.price) : '—'}
      </span>
      <span className="mt-auto flex items-center justify-between gap-2">
        <ChangeBadge value={quote.changePct} size="sm" />
        {spark && spark.length > 1 && quote.changePct !== null && (
          <Sparkline data={spark} width={72} height={22} change={quote.changePct} className="hidden min-w-0 sm:block" />
        )}
      </span>
    </>
  );

  return (
    <motion.div
      className="h-full min-w-0"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: DUR_SECTION, ease: EASE_PAPER, delay: Math.min(index * 0.045, 0.4) }}
    >
      {/* 联合类型保证 to 与 ref 的元素类型成对出现；解构后 TS 不再收窄，这里按分支断言 */}
      {to !== undefined ? (
        <Link ref={ref as Ref<HTMLAnchorElement> | undefined} to={to} className={surface} aria-label={label}>
          {body}
        </Link>
      ) : (
        <button ref={ref as Ref<HTMLButtonElement> | undefined} type="button" onClick={onOpen} className={surface} aria-label={label}>
          {body}
        </button>
      )}
    </motion.div>
  );
}
