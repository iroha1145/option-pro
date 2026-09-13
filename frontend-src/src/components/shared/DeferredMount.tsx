/**
 * 折线以下才挂载的占位：首屏先占高度，空闲或进入视口后再渲染子树。
 * 手动刷新（refreshToken>0）立即挂载，避免「刷新了下面还是旧的」。
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';

export default function DeferredMount({
  children,
  refreshToken = 0,
  minHeightClass = 'min-h-[8rem]',
}: {
  children: ReactNode;
  refreshToken?: number;
  minHeightClass?: string;
}) {
  const [ready, setReady] = useState(refreshToken > 0);
  const nodeRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (refreshToken > 0) setReady(true);
  }, [refreshToken]);

  useEffect(() => {
    if (ready) return;
    const node = nodeRef.current;
    let idleId = 0;
    let timeoutId = 0;
    const arm = () => setReady(true);

    if (typeof IntersectionObserver !== 'undefined' && node) {
      const observer = new IntersectionObserver(
        (entries) => {
          if (entries.some((entry) => entry.isIntersecting)) arm();
        },
        { rootMargin: '240px 0px' },
      );
      observer.observe(node);
      return () => observer.disconnect();
    }

    const idle = (globalThis as Window).requestIdleCallback;
    if (typeof idle === 'function') {
      idleId = idle(arm, { timeout: 400 });
      return () => (globalThis as Window).cancelIdleCallback?.(idleId);
    }
    timeoutId = window.setTimeout(arm, 1);
    return () => window.clearTimeout(timeoutId);
  }, [ready]);

  return (
    <div ref={nodeRef} className={ready ? undefined : minHeightClass}>
      {ready ? children : <div aria-hidden="true" />}
    </div>
  );
}
