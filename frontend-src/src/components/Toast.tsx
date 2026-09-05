/**
 * Toast 系统（design.md §7.5 + transitions.dev toast open/close）
 * 右上 catalog `.t-toast`：升起 fade+blur+scale；open 慢 / close 快。
 * 顶部偏移让开 sticky Navbar（h-12 / md:h-16）。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { readRootDurationMs } from '@/lib/transitions';
import { t as __t } from '../i18n/core.ts';

import { ToastContext, type ToastKind, type ToastContextValue } from '@/hooks/useToast';

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
  hiding?: boolean;
}

const BAR: Record<ToastKind, string> = {
  success: 'bg-[#0B7A55]',
  error: 'bg-[#C4302B]',
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
    const timer = window.setTimeout(() => onDismiss(t.id), t.kind === 'error' ? 8000 : 4000);
    return () => window.clearTimeout(timer);
  }, [t.id, t.kind, t.hiding, hovered, focused, onDismiss, onRemove]);

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
  const nextId = useRef(0);
  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);
  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.map((item) => item.id === id ? { ...item, hiding: true } : item));
  }, []);
  const toast = useCallback((kind: ToastKind, title: string, description?: string) => {
    const id = ++nextId.current;
    // The state updater sees every queued addition, including a same-tick burst.
    setItems((prev) => {
      return [...prev.slice(-3), { id, kind, title, description }];
    });
  }, []);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast,
      success: (t, d) => toast('success', t, d),
      error: (t, d) => toast('error', t, d),
      info: (t, d) => toast('info', t, d),
    }),
    [toast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* 行间距放在 t-toast-row-inner 的 padding 里（不用 gap）：收起的行连
          同间距一起坍缩，剩余 toast 平滑上移而不是跳位。 */}
      <div data-focus-allow aria-live="off" className="pointer-events-none fixed right-4 top-[calc(3rem+8px)] z-[90] flex w-[320px] max-w-[calc(100vw-32px)] flex-col md:top-[calc(4rem+8px)]">
        {items.map((t) => (
          <ToastCard key={t.id} t={t} onDismiss={dismiss} onRemove={remove} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
