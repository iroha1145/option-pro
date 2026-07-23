/** SourceNote：只说明研究口径；具体供应方由各数据卡按接口字段展示。 */
import { cn } from '@/lib/utils';

export default function SourceNote({ className, text = '研究界面 · 延迟行情；具体来源以接口返回为准' }: { className?: string; text?: string }) {
  return (
    <p className={cn('flex items-center gap-1.5 border-t border-line pt-3 text-micro text-ink-400', className)}>
      <span>{text}</span>
      <span className="text-[8px] leading-none text-ink-400" aria-hidden="true">◆</span>
    </p>
  );
}
