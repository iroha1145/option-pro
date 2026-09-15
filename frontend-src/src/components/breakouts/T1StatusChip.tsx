import { cn } from '@/lib/utils';
import { t1StatusPresentation, type T1Status } from '@/lib/t1Status';
export type { T1Status };

export default function T1StatusChip({
  status,
  className,
}: {
  status: T1Status;
  className?: string;
}) {
  const presentation = t1StatusPresentation(status);
  if (!presentation) return null;
  return (
    <span className={cn('radar-chip', presentation.className, className)}>
      {presentation.label}
    </span>
  );
}
