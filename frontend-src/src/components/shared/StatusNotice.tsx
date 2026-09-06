import type { ReactNode } from 'react';
import Icon from '@/components/icons';
import { cn } from '@/lib/utils';

/** Quiet shared surface; the icon and message retain the warning's meaning. */
export default function StatusNotice({ children, action, className }: {
  children: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div role="status" data-status-notice className={cn('card-surface flex min-w-0 items-start gap-3 px-3 py-2.5', className)}>
      <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-warn-50 text-warn-700" aria-hidden="true">
        <Icon name="clock-ny" size={15} />
      </span>
      <div className="min-w-0 flex-1 self-center text-caption leading-5 text-ink-600">{children}</div>
      {action && <div className="shrink-0 self-center">{action}</div>}
    </div>
  );
}
