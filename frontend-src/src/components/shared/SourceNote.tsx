/** SourceNote：发丝线上「来源：Optix Research · 延迟行情 ◆」 */
import { cn } from '@/lib/utils';

export default function SourceNote({ className, text = '来源：Optix Research · 延迟行情' }: { className?: string; text?: string }) {
  return (
    <p className={cn('flex items-center gap-1.5 border-t border-line pt-3 text-micro text-ink-400', className)}>
      <span>{text}</span>
      <span className="text-[8px] leading-none text-ink-400" aria-hidden="true">◆</span>
    </p>
  );
}
