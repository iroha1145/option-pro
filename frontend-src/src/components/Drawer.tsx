/**
 * Drawer 基座（design.md §7.3）
 * 右侧 translateX(100%)→0 · iOS 感 cubic-bezier(.32,.72,0,1) 300ms（tween，不用 spring）；
 * 背板 rgba(13,22,38,.45)+blur(2px) · 淡出 200ms；exit ≈ enter 的 65%（200ms）；ESC/点背板关闭。
 * 移动端变全屏 bottom sheet（同曲线 translateY，顶部抓手 dots-grid）。
 */
import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { useIsMobile } from '@/hooks/use-mobile';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import { t } from '../i18n/core.ts';

/** iOS 感抽屉缓动（--ease-drawer） */
const EASE_DRAWER = [0.32, 0.72, 0, 1] as [number, number, number, number];
const ENTER = { duration: 0.3, ease: EASE_DRAWER } as const;
const EXIT = { duration: 0.2, ease: EASE_DRAWER } as const;

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  /** dialog 的可访问名（审计 2.5.4）：title 是任意节点、不参与名字计算，
      读屏没有它只能念出一个匿名 "dialog"。 */
  label?: string;
  children: ReactNode;
  width?: number;
}

export default function Drawer({ open, onClose, title, label, children, width = 560 }: DrawerProps) {
  /* 单实例（审计 2.6.5）：以前桌面/移动两块面板同时挂载、仅靠 CSS 断点互斥，
     children 整棵渲染两遍——组件各自持有独立定时器与图表实例，framer 的
     layoutId 也在两棵树里撞名；DOM 中还同时存在两个 aria-modal dialog。
     现在由 useIsMobile（matchMedia 同步初值）选择唯一形态。 */
  const isMobile = useIsMobile();
  const panelsRef = useRef<HTMLDivElement>(null);
  useFocusTrap(panelsRef, open);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = '';
    };
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onClose}
            className="fixed inset-0 z-[70] bg-[rgba(13,22,38,.45)] backdrop-blur-[2px]"
            aria-hidden="true"
          />
          <div ref={panelsRef} className="contents">
            {/* 桌面=右侧抽屉（translateX），移动=bottom sheet（translateY）；
                key 固定，跨断点仅切换姿态与样式，children 不重挂 */}
            <motion.aside
              key="panel"
              role="dialog"
              aria-modal="true"
              aria-label={label}
              initial={isMobile ? { y: '100%' } : { x: '100%' }}
              animate={isMobile ? { y: 0, x: 0 } : { x: 0, y: 0 }}
              exit={isMobile ? { y: '100%', transition: EXIT } : { x: '100%', transition: EXIT }}
              transition={ENTER}
              className={cn(
                'fixed z-[71] flex flex-col bg-card shadow-sh-3',
                isMobile
                  ? 'inset-x-0 bottom-0 top-14 rounded-t-xl border-t border-line pb-[env(safe-area-inset-bottom)]'
                  : 'inset-y-0 right-0 w-full border-l border-line',
              )}
              style={isMobile ? undefined : { maxWidth: width }}
            >
              {isMobile && (
                <div className="flex flex-col items-center pb-1 pt-2 text-ink-300">
                  <Icon name="dots-grid" size={18} aria-hidden="true" />
                </div>
              )}
              <div
                className={cn(
                  'flex items-center justify-between border-b border-line',
                  isMobile ? 'px-4 py-3' : 'px-6 py-4',
                )}
              >
                <div className="min-w-0 flex-1">{title}</div>
                <button
                  onClick={onClose}
                  className="rounded-sm p-1.5 text-ink-400 transition-colors hover:bg-paper-2 hover:text-ink-600"
                  aria-label={t("关闭抽屉")}
                >
                  <Icon name="x" size={16} />
                </button>
              </div>
              <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
            </motion.aside>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
