/** TickerLogo：32px r-sm 字母方块，brand-50 底 brand-700 字，首字母 Serif 700 */
import { cn } from '@/lib/utils';

export default function TickerLogo({ ticker, size = 32, className }: { ticker: string; size?: number; className?: string }) {
  return (
    <span
      className={cn('inline-flex shrink-0 select-none items-center justify-center rounded-sm bg-brand-50 font-display font-bold text-brand-700', className)}
      style={{ width: size, height: size, fontSize: size * 0.5, lineHeight: 1 }}
      aria-hidden="true"
    >
      {ticker.slice(0, 1)}
    </span>
  );
}
