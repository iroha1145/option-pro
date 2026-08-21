/** Skeleton：catalog t-skel pulse + Paper Terminal shimmer bars */
import { cn } from '@/lib/utils';

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
