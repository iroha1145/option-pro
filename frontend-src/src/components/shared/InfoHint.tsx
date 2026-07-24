/**
 * InfoHint — 评分解释图标（圈 i）+ 悬停/聚焦/点按浮层
 *
 * 设计约束：
 * - 触发器是 span[role=button] 而非 <button>：评分常渲染在可点击行（motion.button）
 *   内部，真按钮会形成非法嵌套（TickerChip 同款教训）
 * - 点击/键盘触发要 stopPropagation，避免误触所在行的跳转
 * - 悬停/focus-within 用 CSS 显示；触屏无 hover，用受控 open 点按切换
 * - 文案来自 lib/scoreHints（源自后端真实算法），本组件不编内容
 */
import { useEffect, useRef, useState } from 'react';
import { cn } from '@/lib/utils';
import type { ScoreHint } from '@/lib/scoreHints';

function InfoGlyph({ size = 13 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      aria-hidden="true"
      className="shrink-0"
    >
      <circle cx="8" cy="8" r="6.6" stroke="currentColor" strokeWidth="1.3" />
      <circle cx="8" cy="5.1" r="0.9" fill="currentColor" />
      <path d="M8 7.4v3.8" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

export default function InfoHint({
  hint,
  side = 'top',
  align = 'center',
  size = 13,
  className,
}: {
  hint: ScoreHint;
  /** 浮层出现的方向；表格首行/页脚等边缘位置按需选 bottom */
  side?: 'top' | 'bottom';
  /** 水平对齐；靠近容器右缘时用 end，左缘时用 start */
  align?: 'start' | 'center' | 'end';
  size?: number;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement | null>(null);

  /* 触屏点按打开后，点页面其他位置应关闭 */
  useEffect(() => {
    if (!open) return;
    const onDocPointer = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('pointerdown', onDocPointer);
    return () => document.removeEventListener('pointerdown', onDocPointer);
  }, [open]);

  return (
    <span
      ref={rootRef}
      className={cn('group/hint relative inline-flex align-middle', className)}
      onMouseLeave={() => setOpen(false)}
    >
      <span
        role="button"
        tabIndex={0}
        aria-label={`${hint.title}：查看评分说明`}
        className={cn(
          'inline-flex cursor-help items-center rounded-full text-ink-300 outline-none transition-colors duration-fast',
          'hover:text-brand-600 focus-visible:text-brand-600',
          open && 'text-brand-600',
        )}
        onClick={(event) => {
          event.stopPropagation();
          event.preventDefault();
          setOpen((value) => !value);
        }}
        onKeyDown={(event) => {
          if (event.key === 'Enter' || event.key === ' ') {
            event.stopPropagation();
            event.preventDefault();
            setOpen((value) => !value);
          } else if (event.key === 'Escape') {
            setOpen(false);
          }
        }}
      >
        <InfoGlyph size={size} />
      </span>

      <span
        role="tooltip"
        className={cn(
          'pointer-events-none absolute z-50 w-max max-w-[300px] rounded-md border border-line bg-card px-3 py-2.5 text-left shadow-sh-3',
          'opacity-0 transition-opacity duration-fast',
          'group-hover/hint:pointer-events-auto group-hover/hint:opacity-100',
          'group-focus-within/hint:pointer-events-auto group-focus-within/hint:opacity-100',
          open && 'pointer-events-auto opacity-100',
          side === 'top' ? 'bottom-full mb-1.5' : 'top-full mt-1.5',
          align === 'center' && 'left-1/2 -translate-x-1/2',
          align === 'start' && 'left-0',
          align === 'end' && 'right-0',
        )}
      >
        <span className="block text-caption font-semibold text-ink-800">{hint.title}</span>
        <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-600">
          {hint.body}
        </span>
        {hint.note && (
          <span className="mt-1 block whitespace-normal text-micro leading-relaxed text-ink-400">
            {hint.note}
          </span>
        )}
      </span>
    </span>
  );
}
