/**
 * Toast 系统（design.md §7.5 + transitions.dev toast open/close）
 * 右上 catalog `.t-toast`：升起 fade+blur+scale；open 慢 / close 快。
 * 顶部偏移让开 sticky Navbar（h-12 / md:h-16）。
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { readRootDurationMs } from '@/lib/transitions';
import { t as __t } from '../i18n/core.ts';

type ToastKind = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
  hiding?: boolean;
}

interface ToastContextValue {
  toast: (kind: ToastKind, title: string, description?: string) => void;
  success: (title: string, description?: string) => void;
  error: (title: string, description?: string) => void;
  info: (title: string, description?: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const BAR: Record<ToastKind, string> = {
  success: 'bg-up-600',
  error: 'bg-down-600',
  info: 'bg-brand-600',
};

function ToastCard({ t, onDismiss }: { t: ToastItem; onDismiss: (id: number) => void }) {
  const [entered, setEntered] = useState(false);
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

  return (
    <div
      role={t.kind === 'error' ? 'alert' : 'status'}
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
          className="rounded-sm p-1 text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95"
          aria-label={__t('关闭通知')}
        >
          <Icon name="x" size={13} />
        </button>
      </div>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);
  const hideTimers = useRef(new Map<number, number>());

  const remove = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
    const pending = hideTimers.current.get(id);
    if (pending) window.clearTimeout(pending);
    hideTimers.current.delete(id);
  }, []);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.map((t) => (t.id === id ? { ...t, hiding: true } : t)));
    const closeMs = readRootDurationMs('--toast-close', 250);
    const prev = hideTimers.current.get(id);
    if (prev) window.clearTimeout(prev);
    hideTimers.current.set(
      id,
      window.setTimeout(() => remove(id), closeMs),
    );
  }, [remove]);

  const toast = useCallback(
    (kind: ToastKind, title: string, description?: string) => {
      const id = ++idRef.current;
      setItems((prev) => [...prev.slice(-3), { id, kind, title, description }]);
      /* 错误停留更久（审计 2.5.6）：失败原因 4 秒就消失，读屏 polite 队列
         常常还没轮到它，节点已经被移除。 */
      window.setTimeout(() => dismiss(id), kind === 'error' ? 8000 : 4000);
    },
    [dismiss],
  );

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
      <div className="pointer-events-none fixed right-4 top-[calc(3rem+8px)] z-[90] flex w-[320px] max-w-[calc(100vw-32px)] flex-col gap-2 md:top-[calc(4rem+8px)]">
        {items.map((t) => (
          <ToastCard key={t.id} t={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error(__t('useToast 必须在 <ToastProvider> 内使用'));
  return ctx;
}
