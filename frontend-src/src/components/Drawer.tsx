/**
 * Drawer 基座（design.md §7.3）
 * 右侧 translateX(100%)→0 spring-gentle；背板 rgba(13,22,38,.28)+blur(2px) 200ms 淡入；ESC/点背板关闭。
 * 移动端变全屏 bottom sheet（顶部抓手 dots-grid）。
 */
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  width?: number;
}

export default function Drawer({ open, onClose, title, children, width = 560 }: DrawerProps) {
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
            className="fixed inset-0 z-[70] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]"
            aria-hidden="true"
          />
          {/* 桌面右侧抽屉 */}
          <motion.aside
            key="panel"
            role="dialog"
            aria-modal="true"
            initial={{ x: '100%' }}
            animate={{ x: 0 }}
            exit={{ x: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className="fixed inset-y-0 right-0 z-[71] hidden w-full flex-col border-l border-line bg-card shadow-sh-3 md:flex"
            style={{ maxWidth: width }}
          >
            <div className="flex items-center justify-between border-b border-line px-6 py-4">
              <div className="min-w-0 flex-1">{title}</div>
              <button
                onClick={onClose}
                className="rounded-sm p-1.5 text-ink-400 transition-colors hover:bg-paper-2 hover:text-ink-600"
                aria-label="关闭抽屉"
              >
                <Icon name="x" size={16} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
          </motion.aside>
          {/* 移动 bottom sheet */}
          <motion.aside
            key="sheet"
            role="dialog"
            aria-modal="true"
            initial={{ y: '100%' }}
            animate={{ y: 0 }}
            exit={{ y: '100%' }}
            transition={{ type: 'spring', stiffness: 300, damping: 30 }}
            className={cn('fixed inset-x-0 bottom-0 top-14 z-[71] flex flex-col rounded-t-xl border-t border-line bg-card pb-[env(safe-area-inset-bottom)] shadow-sh-3 md:hidden')}
          >
            <div className="flex flex-col items-center pb-1 pt-2 text-ink-300">
              <Icon name="dots-grid" size={18} aria-hidden="true" />
            </div>
            <div className="flex items-center justify-between border-b border-line px-4 py-3">
              <div className="min-w-0 flex-1">{title}</div>
              <button
                onClick={onClose}
                className="rounded-sm p-1.5 text-ink-400 transition-colors hover:bg-paper-2 hover:text-ink-600"
                aria-label="关闭抽屉"
              >
                <Icon name="x" size={16} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
