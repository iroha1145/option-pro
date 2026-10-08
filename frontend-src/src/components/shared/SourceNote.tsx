/** SourceNote：卡片底部的一行说明；面向普通读者，不写实现细节。mark=false 时不画行末的菱形小点。 */
import { cn } from '@/lib/utils';

export default function SourceNote({ className, text, mark = true }: { className?: string; text: string; mark?: boolean }) {
  return (
    <p className={cn('flex items-center gap-1.5 border-t border-line pt-3 text-micro text-ink-400', className)}>
      <span>{text}</span>
      {mark && <span className="text-[8px] leading-none text-ink-400" aria-hidden="true">◆</span>}
    </p>
  );
}
