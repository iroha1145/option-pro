/** Skeleton：catalog t-skel pulse + Paper Terminal shimmer bars */
import { useEffect, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { readRootDurationMs } from '@/lib/transitions';

export function SkeletonBlock({ className }: { className?: string }) {
  return <div className={cn('skeleton-shimmer rounded-sm', className)} aria-hidden="true" />;
}

export function SkeletonText({ lines = 3, className }: { lines?: number; className?: string }) {
  return (
    <div className={cn('space-y-2', className)} aria-hidden="true">
      {Array.from({ length: lines }, (_, i) => (
        <SkeletonBlock key={i} className={cn('h-3', i === lines - 1 ? 'w-2/3' : 'w-full')} />
      ))}
    </div>
  );
}

export function SkeletonCard({ className }: { className?: string }) {
  return (
    <div className={cn('card-surface t-skel p-5', className)} data-state="loading" aria-hidden="true">
      <div className="t-skel-skeleton is-pulsing">
        <SkeletonBlock className="h-3 w-24" />
        <SkeletonBlock className="mt-4 h-8 w-32" />
        <SkeletonBlock className="mt-3 h-3 w-20" />
      </div>
      <div className="t-skel-content" />
    </div>
  );
}

/**
 * catalog 14 的「reveal」半边：骨架 → 真实内容交叉淡化 + 交叉模糊。
 * loading 期间只挂骨架（in-flow、无限 pulse）；数据到达后两层短暂共存
 * （骨架转为绝对定位覆盖层淡出，内容进入文档流淡入），--reveal-dur 后
 * 骨架卸载。内容一经挂载就不再换父节点，图表/effect 不会重挂。
 */
export function SkeletonReveal({
  loading,
  skeleton,
  children,
  className,
}: {
  loading: boolean;
  skeleton: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  const [state, setState] = useState<'loading' | 'reveal' | 'done'>(loading ? 'loading' : 'done');

  useEffect(() => {
    if (loading) {
      setState('loading');
      return;
    }
    setState((prev) => (prev === 'loading' ? 'reveal' : prev));
  }, [loading]);

  useEffect(() => {
    if (state !== 'reveal') return;
    const timer = window.setTimeout(
      () => setState('done'),
      readRootDurationMs('--reveal-dur', 400),
    );
    return () => window.clearTimeout(timer);
  }, [state]);

  return (
    <div
      className={cn('t-skel', state === 'reveal' && 'is-revealed', className)}
      data-state={state}
    >
      {state !== 'done' && (
        <div className="t-skel-skeleton" aria-hidden="true">
          {skeleton}
        </div>
      )}
      <div className="t-skel-content">{state === 'loading' ? null : children}</div>
    </div>
  );
}

export function SkeletonRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn('t-skel divide-y divide-line', className)} data-state="loading" aria-hidden="true">
      <div className="t-skel-skeleton is-pulsing">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-4 px-4 py-4">
            <SkeletonBlock className="size-8 rounded-sm" />
            <SkeletonBlock className="h-3 w-16" />
            <SkeletonBlock className="ml-auto h-3 w-14" />
            <SkeletonBlock className="h-3 w-12" />
            <SkeletonBlock className="h-4 w-20" />
            <SkeletonBlock className="h-3 w-10" />
          </div>
        ))}
      </div>
      <div className="t-skel-content" />
    </div>
  );
}
