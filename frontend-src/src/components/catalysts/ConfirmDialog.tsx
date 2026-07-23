/** 确认弹窗（design.md §4.3 模态：scale(.96)→1 + fade，spring-pop；ESC/背板关闭） */
import { useEffect } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            onClick={onCancel}
            className="fixed inset-0 z-[85] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]"
            aria-hidden="true"
          />
          <motion.div
            role="alertdialog"
            aria-modal="true"
            aria-label={title}
            initial={{ opacity: 0, scale: 0.96 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: 0.96, transition: { duration: 0.16 } }}
            transition={{ type: 'spring', stiffness: 520, damping: 32 }}
            className="fixed left-1/2 top-[24vh] z-[86] w-[400px] max-w-[calc(100vw-32px)] -translate-x-1/2 rounded-xl border border-line bg-card p-5 shadow-sh-3"
          >
            <div className="flex items-start gap-3">
              <span
                className={cn(
                  'mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-md',
                  danger ? 'bg-down-50 text-down-600' : 'bg-ai-50 text-ai-600',
                )}
              >
                <Icon name="spark-ai" size={18} />
              </span>
              <div className="min-w-0 flex-1">
                <h3 className="text-h3 text-ink-900">{title}</h3>
                {description && <p className="mt-1.5 text-body-s leading-relaxed text-ink-500">{description}</p>}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={onCancel}
                className="rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 transition-colors duration-fast hover:bg-paper-2"
              >
                {cancelLabel}
              </button>
              <button
                onClick={onConfirm}
                className={cn(
                  'rounded-md px-3.5 py-2 text-caption font-medium text-white transition-[filter] duration-fast hover:brightness-105',
                  danger ? 'bg-down-600' : 'bg-brand-600',
                )}
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </>
      )}
    </AnimatePresence>
  );
}
