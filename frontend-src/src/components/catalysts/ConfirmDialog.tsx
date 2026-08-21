/** 确认弹窗（design.md §4.3 + transitions.dev modal：open 250ms / close 150ms） */
import { useEffect, useRef } from 'react';
import { useFocusTrap } from '@/hooks/useFocusTrap';
import { cn } from '@/lib/utils';
import Icon from '@/components/icons';
import {
  overlayClassName,
  overlayVisible,
  readRootDurationMs,
  useOverlayPhase,
} from '@/lib/transitions';
import { t } from '../../i18n/core.ts';

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
  confirmLabel = t('确认'),
  cancelLabel = t('取消'),
  danger = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  /* 焦点圈定（审计 2.5.7）：打开时把焦点移到「取消」（默认不选中消耗预算的
     动作），Tab 只在对话框内循环，关闭后归还给触发按钮——否则焦点还停在
     抽屉正文里，读屏用户根本不知道弹了确认框。 */
  const panelRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useFocusTrap(panelRef, open, { initialFocusRef: cancelRef });
  const closeMs = readRootDurationMs('--modal-close-dur', 150);
  const phase = useOverlayPhase(open, closeMs);
  const mounted = overlayVisible(open, phase);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, onCancel]);

  if (!mounted) return null;

  return (
    <>
      <div
        className={cn('t-backdrop fixed inset-0 z-[85] bg-[rgba(13,22,38,.28)] backdrop-blur-[2px]', phase === 'open' && 'is-open')}
        onClick={onCancel}
        aria-hidden="true"
      />
      {/* 水平居中交给外层 translate，避免 t-modal 的 scale transform 顶掉 -translate-x-1/2 */}
      <div className="fixed left-1/2 top-[24vh] z-[86] w-[400px] max-w-[calc(100vw-32px)] -translate-x-1/2">
        <div
          ref={panelRef}
          role="alertdialog"
          aria-modal="true"
          aria-label={title}
          className={cn(
            't-modal rounded-xl border border-line bg-card p-5 shadow-sh-3',
            overlayClassName(phase),
          )}
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
              ref={cancelRef}
              onClick={onCancel}
              className="rounded-md border border-line bg-card px-3.5 py-2 text-caption font-medium text-ink-600 shadow-btn transition-colors duration-fast hover:bg-paper-2"
            >
              {cancelLabel}
            </button>
            <button
              onClick={onConfirm}
              className={cn(
                'rounded-md px-3.5 py-2 text-caption font-medium text-white shadow-btn-hi transition-[filter] duration-fast hover:brightness-105',
                danger ? 'bg-down-600' : 'bg-brand-600',
              )}
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </>
  );
}
