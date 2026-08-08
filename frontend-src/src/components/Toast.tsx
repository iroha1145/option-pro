/**
 * Toast 系统（design.md §7.5）
 * 右上滑入 enter 200ms ease-out / exit 130ms；r-md + sh-2；成功 up-600 / 失败 down-600 / 信息 brand-600 左竖条；4s 自动消隐。
 */
import { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { t as __t } from '../i18n/core.ts';

type ToastKind = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  kind: ToastKind;
  title: string;
  description?: string;
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

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

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
      {/* live 语义放在每条 toast 上（审计 2.5.6）：插入 role=alert 的节点会被
          立即播报，错误不再排在 polite 队列里等到超时被删。 */}
      {/* 顶部偏移让开 sticky Navbar（h-12 / md:h-16）：z-[90] 的 toast 落在
          右上角会压住「退出/登录」按钮并吃掉点击 4–8 秒——典型链是「点退出 →
          弹 toast → 同位置变出的登录入口被自己压住」（审计 #63）。 */}
      <div className="pointer-events-none fixed right-4 top-[calc(3rem+8px)] z-[90] flex w-[320px] max-w-[calc(100vw-32px)] flex-col gap-2 md:top-[calc(4rem+8px)]">
        <AnimatePresence>
          {items.map((t) => (
            <motion.div
              key={t.id}
              layout="position"
              initial={{ opacity: 0, y: 14 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -8, transition: { duration: 0.13 } }}
              transition={{ duration: 0.2, ease: [0.16, 1, 0.3, 1] }}
              role={t.kind === 'error' ? 'alert' : 'status'}
              className="pointer-events-auto relative overflow-hidden rounded-md border border-line bg-card shadow-sh-2"
            >
              <span className={cn('absolute inset-y-0 left-0 w-[3px]', BAR[t.kind])} aria-hidden="true" />
              <div className="flex items-start gap-2 py-2.5 pl-4 pr-2">
                <div className="min-w-0 flex-1">
                  <p className="text-body-s font-medium text-ink-800">{t.title}</p>
                  {t.description && <p className="mt-0.5 text-caption text-ink-500">{t.description}</p>}
                </div>
                <button
                  onClick={() => dismiss(t.id)}
                  className="rounded-sm p-1 text-ink-400 transition-[transform,color,background-color] duration-fast hover:bg-paper-2 hover:text-ink-600 active:scale-95"
                  aria-label={__t("关闭通知")}
                >
                  <Icon name="x" size={13} />
                </button>
              </div>
            </motion.div>
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error(__t('useToast 必须在 <ToastProvider> 内使用'));
  return ctx;
}
