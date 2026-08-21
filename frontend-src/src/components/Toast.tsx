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

  /* 行高的 0fr↔1fr 只能在 inner overflow:hidden 时收缩；到位后放开 overflow，
     让 sh-2 阴影不被裁。收起时（open=false）立即回到 hidden 再开始坍缩。 */
  useEffect(() => {
    if (!open) {
      setSettled(false);
      return;
    }
    const timer = window.setTimeout(
      () => setSettled(true),
      readRootDurationMs('--toast-open', 350),
    );
    return () => window.clearTimeout(timer);
  }, [open]);

  return (
    <div className={cn('t-toast-row', open && 'is-open', settled && 'is-settled')}>
      <div className="t-toast-row-inner">
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
      </div>
    </div>
  );
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);
  const hideTimers = useRef(new Map<number, number>());
  const itemsRef = useRef<ToastItem[]>([]);
  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

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
      setItems((prev) => [...prev, { id, kind, title, description }]);
      /* 上限 4 条：第 5 条到来让最老的走完整退场动画（行坍缩+淡出），
         而不是像 slice 那样被瞬间抽走。镜像 ref 最多滞后一帧，只影响
         并发风暴里短暂多出一条，随下一条到来即校正。 */
      const visible = itemsRef.current.filter((t) => !t.hiding);
      if (visible.length >= 4) dismiss(visible[0].id);
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
      {/* 行间距放在 t-toast-row-inner 的 padding 里（不用 gap）：收起的行连
          同间距一起坍缩，剩余 toast 平滑上移而不是跳位。 */}
      <div className="pointer-events-none fixed right-4 top-[calc(3rem+8px)] z-[90] flex w-[320px] max-w-[calc(100vw-32px)] flex-col md:top-[calc(4rem+8px)]">
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
