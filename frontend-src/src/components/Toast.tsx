/**
 * Toast 系统（design.md §7.5 + transitions.dev toast open/close）
 * 右上 catalog `.t-toast`：升起 fade+blur+scale；open 慢 / close 快。
 * 顶部偏移让开仍留在视口里的顶栏、指数条和演示条，避免压住行情。
 */
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { readRootDurationMs } from '@/lib/transitions';
import { t as __t } from '../i18n/core.ts';

import { ToastContext, type ToastAction, type ToastKind, type ToastContextValue, type ToastOptions } from '@/hooks/useToast';

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
  action?: ToastAction;
  hiding?: boolean;
}

/** 仍压在视口顶部的壳层底边。滚走的指数条不再占位。 */
function shellChromeBottom(): number | null {
  if (typeof document === 'undefined') return null;
  const nodes = [
    document.querySelector('header'),
    document.querySelector('.marquee-track'),
    document.querySelector('[data-demo-banner]'),
  ];
  let bottom = 0;
  let found = false;
  for (const node of nodes) {
    if (!(node instanceof HTMLElement)) continue;
    const rect = node.getBoundingClientRect();
    if (rect.bottom <= 0 || rect.height <= 0) continue;
    found = true;
    bottom = Math.max(bottom, rect.bottom);
  }
  return found ? bottom + 8 : null;
}

const BAR: Record<ToastKind, string> = {
  success: 'bg-ok-700',
  error: 'bg-danger-700',
  info: 'bg-brand-600',
};

function ToastCard({ t, onDismiss, onRemove }: { t: ToastItem; onDismiss: (id: number) => void; onRemove: (id: number) => void }) {
  const [entered, setEntered] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [settled, setSettled] = useState(false);
  useEffect(() => {
    let inner = 0;
    const outer = window.requestAnimationFrame(() => {
      inner = window.requestAnimationFrame(() => setEntered(true));
    });
    return () => {
      window.cancelAnimationFrame(outer);
      window.cancelAnimationFrame(inner);
    };
  }, []);
  const open = entered && !t.hiding;

  // Pausing while reading or focusing a notice also cancels its old timer.
  // Component ownership cleans up every timer on dismissal/provider unmount.
  useEffect(() => {
    if (t.hiding) {
      const timer = window.setTimeout(() => onRemove(t.id), readRootDurationMs('--toast-close', 250));
      return () => window.clearTimeout(timer);
    }
    if (hovered || focused) return;
    // 错误和带操作（如撤销）的提示多留一会儿，给人时间读完或点到
    const timer = window.setTimeout(() => onDismiss(t.id), t.kind === 'error' || t.action ? 8000 : 4000);
    return () => window.clearTimeout(timer);
  }, [t.id, t.kind, t.action, t.hiding, hovered, focused, onDismiss, onRemove]);

  return (
    <div
      className={cn('t-toast-row', open && 'is-open', open && settled && 'is-settled')}
      onTransitionEnd={(event) => {
        if (event.target === event.currentTarget && open) setSettled(true);
      }}
    >
      <div className="t-toast-row-inner">
        <div
          role={t.kind === 'error' ? 'alert' : 'status'}
          aria-atomic="true"
          inert={t.hiding || undefined}
          onMouseEnter={() => setHovered(true)}
          onMouseLeave={() => setHovered(false)}
          onFocusCapture={() => setFocused(true)}
          onBlurCapture={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node)) setFocused(false);
          }}
          className={cn(
            't-toast pointer-events-auto relative overflow-hidden rounded-md border border-line bg-card shadow-sh-2',
            open && 'is-open',
          )}
          data-open={open ? 'true' : 'false'}
        >
          <span className={cn('absolute inset-y-0 left-0 w-[3px]', BAR[t.kind])} aria-hidden="true" />
          <div className="flex items-start gap-2 py-2.5 pl-4 pr-2">
            <div className="min-w-0 flex-1">
              <p className="text-body-s font-medium text-ink-800">{t.title}</p>
              {t.description && <p className="mt-0.5 text-caption text-ink-500">{t.description}</p>}
            </div>
            {t.action && (
              <button
                type="button"
                onClick={() => {
                  t.action?.onClick();
                  onDismiss(t.id);
                }}
                className="control-button shrink-0 self-center"
              >
                {t.action.label}
              </button>
            )}
            <button
              onClick={() => onDismiss(t.id)}
              className="flex size-8 shrink-0 items-center justify-center rounded-sm text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95"
              aria-label={__t('关闭通知')}
            >
              <Icon name="x" size={13} />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const [chromeTop, setChromeTop] = useState<number | null>(null);
  useLayoutEffect(() => {
    const measure = () => setChromeTop(shellChromeBottom());
    measure();
    window.addEventListener('scroll', measure, { passive: true });
    window.addEventListener('resize', measure);
    const observed = [
      document.querySelector('header'),
      document.querySelector('.marquee-track'),
      document.querySelector('[data-demo-banner]'),
    ].filter((node): node is HTMLElement => node instanceof HTMLElement);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observed.forEach((node) => observer?.observe(node));
    return () => {
      window.removeEventListener('scroll', measure);
      window.removeEventListener('resize', measure);
      observer?.disconnect();
    };
  }, []);
  const nextId = useRef(0);
  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);
  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.map((item) => item.id === id ? { ...item, hiding: true } : item));
  }, []);
  const toast = useCallback((kind: ToastKind, title: string, description?: string, options?: ToastOptions) => {
    const id = ++nextId.current;
    // The state updater sees every queued addition, including a same-tick burst.
    setItems((prev) => {
      return [...prev.slice(-3), { id, kind, title, description, action: options?.action }];
    });
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (t, d, o) => toast('success', t, d, o),
      error: (t, d, o) => toast('error', t, d, o),
      info: (t, d, o) => toast('info', t, d, o),
    }),
    [toast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* 行间距放在 t-toast-row-inner 的 padding 里（不用 gap）：收起的行连
          同间距一起坍缩，剩余 toast 平滑上移而不是跳位。 */}
      <div
        data-focus-allow
        aria-live="off"
        className={cn(
          'pointer-events-none fixed right-4 z-[90] flex w-[320px] max-w-[calc(100vw-32px)] flex-col',
          chromeTop == null && 'top-[calc(3rem+8px)] md:top-[calc(4rem+8px)]',
        )}
        style={chromeTop == null ? undefined : { top: chromeTop }}
      >
        {items.map((t) => (
          <ToastCard key={t.id} t={t} onDismiss={dismiss} onRemove={remove} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
