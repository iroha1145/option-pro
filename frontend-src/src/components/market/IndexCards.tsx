/**
 * B1 指数概览（6 卡：SPX/NDX/DJI/RUT/SOX/VIX）
 * 名称+代码 · 价格直接呈现（tick-flash 提示更新）· ChangeBadge · 当日 mini sparkline
 * live 模式无指数 K 线端点 → 不渲染分时图模块
 * ?index= 指定的卡高亮（左缘 2px brand 条）并滚动定位
 */
import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { motion } from 'framer-motion';
import { isMock, type ApiError } from '@/api/client';
import type { IndexQuote } from '@/api/types';
import { getIndexIntraday } from '@/mocks/marketPulse';
import { cn } from '@/lib/utils';
import { fmtPrice } from '@/lib/format';
import ChangeBadge from '@/components/shared/ChangeBadge';
import EmptyState from '@/components/shared/EmptyState';
import { SkeletonCard } from '@/components/shared/Skeleton';
import Sparkline from '@/components/charts/Sparkline';

const IndexCard = memo(function IndexCard({
  quote,
  index,
  flash,
  focused,
  registerRef,
  onSelect,
}: {
  quote: IndexQuote;
  index: number;
  flash: 'up' | 'down' | undefined;
  focused: boolean;
  registerRef: (code: string, el: HTMLDivElement | null) => void;
  onSelect: (code: string) => void;
}) {
  /* 数据纪律：无有效价（live 快照缺失时映射为 0）显「—」，不显 0.00 */
  const hasPrice = Number.isFinite(quote.price) && quote.price > 0;
  /* count-up 减量：指数卡价格直接呈现终值，更新反馈交给 tick-flash */
  const price = hasPrice ? quote.price : 0;
  const spark = useMemo(
    () => (isMock ? getIndexIntraday(quote.code, quote.changePct) : null),
    [quote.code, quote.changePct],
  );
  return (
    /* 选中同顶部指数 tape 一致（写 ?index=）。用真 button 而不是加 onClick 的
       div：键盘可达、读屏能报出按下态，且 aria-pressed 让「已选中」不只靠颜色。 */
    <motion.button
      type="button"
      ref={(el) => registerRef(quote.code, el as HTMLDivElement | null)}
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.48, ease: [0.16, 1, 0.3, 1], delay: Math.min(index * 0.045, 0.4) }}
      onClick={() => onSelect(quote.code)}
      aria-pressed={focused}
      /* 上浮 -3px/240ms 与自选卡、热点卡同一套手感；走 whileHover 而不是 CSS
         hover:-translate-y，因为入场动画结束后 framer 会留下内联 transform，
         把 CSS 位移压掉。 */
      whileHover={{ y: -3, transition: { duration: 0.24, ease: 'easeOut' } }}
      className={cn(
        'card-surface relative block w-full overflow-hidden p-4 text-left',
        'transition-shadow duration-[240ms] ease-out hover:shadow-sh-2',
        'focus-visible:outline-none focus-visible:shadow-focus-ring',
        focused && 'shadow-[inset_2px_0_0_0_var(--brand-600),0_1px_2px_rgba(13,22,38,.05),inset_0_1px_0_rgba(255,255,255,.9)] ring-1 ring-brand-100',
      )}
      aria-label={`${quote.name} ${quote.code}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <p className="min-w-0">
          <span className="block truncate text-caption text-ink-500">{quote.name}</span>
          <span className="font-mono text-micro text-ink-400">{quote.code}</span>
        </p>
        <ChangeBadge value={quote.changePct} size="sm" />
      </div>
      <p
        className={cn(
          'mt-2 inline-block rounded-xs px-1 font-mono text-data-l text-ink-900 tnum',
          flash === 'up' && 'animate-tick-flash-up',
          flash === 'down' && 'animate-tick-flash-down',
        )}
      >
        {hasPrice ? fmtPrice(price) : '—'}
      </p>
      {spark && (
        <div className="mt-2 flex h-8 items-end justify-between gap-2">
          <Sparkline data={spark} width={132} height={30} change={quote.changePct} className="w-full" />
        </div>
      )}
    </motion.button>
  );
});

export default function IndexCards({
  data,
  loading,
  error,
  focus,
  onRetry,
  refreshing,
  onSelect,
}: {
  data: IndexQuote[] | null;
  loading: boolean;
  error: ApiError | null;
  focus: string | null;
  onRetry: () => void;
  refreshing: boolean;
  onSelect: (code: string) => void;
}) {
  /* tick-flash 差异检测（60s 轮询） */
  const [flashes, setFlashes] = useState<Record<string, 'up' | 'down'>>({});
  const prevPrices = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!data) return;
    const next: Record<string, 'up' | 'down'> = {};
    data.forEach((q) => {
      const prev = prevPrices.current[q.code];
      if (prev !== undefined && prev !== q.price) next[q.code] = q.price > prev ? 'up' : 'down';
      prevPrices.current[q.code] = q.price;
    });
    if (Object.keys(next).length) {
      const show = window.setTimeout(() => setFlashes(next), 0);
      const clear = window.setTimeout(() => setFlashes({}), 700);
      return () => {
        window.clearTimeout(show);
        window.clearTimeout(clear);
      };
    }
  }, [data]);

  /* ?index= 滚动定位 */
  const cardRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const registerRef = (code: string, el: HTMLDivElement | null) => {
    cardRefs.current[code] = el;
  };
  useEffect(() => {
    if (!focus || !data) return;
    const el = cardRefs.current[focus.toUpperCase()];
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }, [focus, data]);

  if (loading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        {Array.from({ length: 6 }, (_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    );
  }
  if (error) {
    return (
      <div className="card-surface">
        <EmptyState
          variant="error"
          image="/empty-chart.svg"
          title={error.code === 503 ? '数据暂不可用' : '加载失败'}
          description={error.code === 503 ? '暂无指数数据' : error.message}
          action={
            <button
              onClick={onRetry}
              disabled={refreshing}
              className="flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-caption font-medium text-white transition-[filter] hover:brightness-105 disabled:opacity-60"
            >
              {refreshing && <span className="size-3.5 animate-spin rounded-full border-2 border-white/40 border-t-white" />}
              重试
            </button>
          }
        />
      </div>
    );
  }
  if (!data?.length) return null;

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
      {data.map((q, i) => (
        <IndexCard
          key={q.code}
          quote={q}
          index={i}
          flash={flashes[q.code]}
          focused={focus?.toUpperCase() === q.code}
          registerRef={registerRef}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}
