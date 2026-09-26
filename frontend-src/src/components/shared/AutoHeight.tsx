/**
 * AutoHeight —— 内容高度变化时容器平滑过渡（transitions.dev 01-card-resize）。
 *
 * 容器写入测得的像素高度、由 .t-resize 补间；首帧保持 auto，不会从 0 长出来。
 * 用来替换「AnimatePresence mode="wait" + height:0↔auto」的视图切换：那种写法
 * 先把旧视图收成 0 再把新视图撑开（两段共 640ms，下方内容先上跳再下落），
 * 帧循环被节流时 mode="wait" 还会卡在旧视图上。现在只有一段高度补间，新内容
 * 由调用方按 key 重挂并淡入。
 */
import { useLayoutEffect, useRef, useState, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

export default function AutoHeight({ children, className }: { children: ReactNode; className?: string }) {
  const inner = useRef<HTMLDivElement>(null);
  const [height, setHeight] = useState<number | null>(null);

  useLayoutEffect(() => {
    const el = inner.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => setHeight(el.offsetHeight));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <div className={cn('t-resize overflow-hidden', className)} style={height === null ? undefined : { height }}>
      <div ref={inner}>{children}</div>
    </div>
  );
}
