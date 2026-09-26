/**
 * Spinner —— 全站唯一的加载圈（shadcn/ui Spinner：单独出现时 role="status" + 可读名）。
 *
 * 此前 30 多处各写一串 `size-3.5 animate-spin rounded-full border-2 …`，尺寸、
 * 描边粗细和配色各自漂移。tone 三档：
 *   on-accent  实心主按钮里（白圈）
 *   brand      卡片、面板、浮层里（群青圈）
 *   muted      次级位置（发丝线底 + 群青头）
 * 放在带文字的按钮里时默认 aria-hidden（按钮文字已说明状态）；单独出现时传 label。
 */
import { cn } from '@/lib/utils';

export type SpinnerTone = 'brand' | 'on-accent' | 'muted';

const TONE: Record<SpinnerTone, string> = {
  brand: 'border-brand-100 border-t-brand-600',
  'on-accent': 'border-on-accent/40 border-t-on-accent',
  muted: 'border-line-strong border-t-brand-600',
};

export default function Spinner({
  size = 14,
  tone = 'brand',
  label,
  className,
}: {
  size?: number;
  tone?: SpinnerTone;
  /** 可读名；缺省视为装饰（aria-hidden），由外层文字说明状态。 */
  label?: string;
  className?: string;
}) {
  return (
    <span
      role={label ? 'status' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={cn(
        'inline-block shrink-0 animate-spin rounded-full',
        size <= 12 ? 'border-[1.5px]' : 'border-2',
        TONE[tone],
        className,
      )}
      style={{ width: size, height: size }}
    />
  );
}
